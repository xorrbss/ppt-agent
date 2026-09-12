"""Loopback harness for the authored worker lane (ARCH-06).

The real user path is submit -> outline -> authored batch authoring in waves
-> markup admission -> gates -> terminal fallback -> publish -> receipts.  The
older loopback harness (``tests/integration/test_authored_checkpoint_http_flow.py``)
stubs ``author_planned_slide``, which is exactly the seam that bypasses the
batch wave loop in ``services/authored_presentation_pipeline.py``.  This
harness therefore stubs one level deeper -- the llmai client inside
``utils/llm_calls/author_batch.py`` -- so the wave scheduler, the batch
policy, per-slide admission (``validate_authored_slide_html``), checkpoints,
publication and the attempt-timing receipt all run for real.  Chromium is
replaced the way the pipeline/terminal-fallback unit tests replace it (no
browser in CI): PNG bytes plus a DOM-derived browser observation.

Used by ``tests/integration/test_authored_worker_lane_e2e*.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import re
import socket
import sys
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from types import SimpleNamespace
from typing import Any, Callable

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from enums.llm_provider import LLMProvider
from models.authored_header_contract import AuthoredHeaderContract, default_header_contract
from models.sql.presentation import (
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission, PublicPolicyProfile
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import WorkspaceOperation
from services import authored_batch_policy as batch_policy_mod
from services import authored_generation_preflight as preflight_mod
from services import authored_presentation_generation as generation_mod
from services import authored_presentation_pipeline as pipeline_mod
from services import authored_wave_scheduler as wave_scheduler_mod
from services import authored_presentation_service as authored_service
from services import authored_terminal_slide_fallback as terminal_mod
from services import database
from services import presentation_generation_job_service as jobs
from services import public_generation_deadline_policy as deadline_policy
from services import public_operation_admission as admission_mod
from services import workspace_operation_runner as workspace_runner
from services.authored_generation_timing_receipt import (
    load_generation_attempt_timing_receipts,
)
from services.authored_provider_rate_profile import (
    TOKEN_PROFILE_NAME,
    resolve_authored_rate_profile,
)
from services.presentation_generation_operation_handler import (
    register_presentation_generation_operation_handler,
)
from utils.llm_calls import author_batch as author_batch_mod
from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls import author_slide as author_slide_mod
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_tenant_id,
    reset_current_trace_id,
    reset_request_scope_active,
    set_current_owner_id,
    set_current_tenant_id,
    set_current_trace_id,
    set_request_scope_active,
)

_LABEL_LINE_RATIO = 1.4
_SLIDE_INDEX_PATTERN = re.compile(r"BEGIN REQUEST FOR SLIDE INDEX (\d+)")
#: The single-slide prompt (``author_slide_prompt``) names its slide in
#: human, 1-based form instead; the D7 rescue authors through that path.
_SINGLE_SLIDE_PATTERN = re.compile(r"Author slide (\d+) of \d+")
_STATUS_TIMEOUT_SECONDS = 120.0

_STUB_MODEL = "stub-model"
_PRICING_REVISION = "pricing-worker-lane-e2e"


def _pricing_catalog() -> str:
    return json.dumps(
        {
            "schemaVersion": "public-provider-pricing-catalog/v1",
            "revision": _PRICING_REVISION,
            "entries": [
                {
                    "providerId": LLMProvider.CODEX.value,
                    "modelId": _STUB_MODEL,
                    "inputMicrounitsPerMillionTokens": 100,
                    "outputMicrounitsPerMillionTokens": 200,
                }
            ],
        }
    )


#: Stripped by ``utils/authored_html_normalize.py`` before admission (A2), so a
#: persisted slide must never carry it even though the provider emitted it.
_NORMALIZED_DECLARATION = "break-inside: avoid;"
#: Outside the renderer CSS allowlist and NOT on the normaliser's whitelist, so
#: admission rejects it on every attempt.
_FORBIDDEN_DECLARATION = "content: 'x';"


# --- authored HTML fixtures ---------------------------------------------------


def _round_px(value: float) -> int:
    return int(math.floor(value + 0.5))


def _band_px(contract: AuthoredHeaderContract) -> int:
    """Derive the band the way the header brief tells the model to derive it."""

    block = 0
    if contract.eyebrow.enabled:
        block += (
            _round_px(contract.eyebrow.font_size_px * _LABEL_LINE_RATIO)
            + contract.eyebrow_gap_px
        )
    block += _round_px(contract.title.font_size_px * contract.title.line_height)
    band = contract.header_top_px + block + contract.title_gap_px
    return min(max(band, contract.band_min_px), contract.band_max_px)


def _header_band_html(contract: AuthoredHeaderContract, title: str) -> str:
    band = _band_px(contract)
    tick = (
        '<span data-header-part="tick"></span>'
        if contract.eyebrow.enabled and contract.eyebrow.tick.enabled
        else ""
    )
    eyebrow = (
        '<span data-header-part="eyebrow">Section</span>'
        if contract.eyebrow.enabled
        else ""
    )
    divider = '<div data-header-part="divider"></div>' if contract.divider.enabled else ""
    right = ""
    if contract.right_slot.mode in {"summary", "requirementsAndSummary"}:
        right = '<div data-header-part="right"><p>Summary</p></div>'
    return (
        f'<div data-header-part="band" data-header-band="{band}" '
        f'style="height:{band}px">'
        f"{tick}{eyebrow}"
        f'<h1 data-header-part="title">{title}</h1>'
        f"{right}{divider}</div>"
    )


def _slide_document(
    contract: AuthoredHeaderContract,
    index: int,
    *,
    body_style_extra: str = "",
    omit_header: bool = False,
) -> str:
    header = "" if omit_header else _header_band_html(contract, f"Slide {index + 1}")
    body_style = f"margin-top:{contract.content_gap_px}px;{body_style_extra}"
    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'></head>"
        f"<body>{header}"
        f'<div data-header-part="body" style="{body_style}">'
        f"<p>Body of slide {index + 1}</p></div></body></html>"
    )


def _wrap_batch_response(htmls: dict[int, str]) -> str:
    return "".join(
        f'<authored-slide index="{index}">{html}</authored-slide>'
        for index, html in sorted(htmls.items())
    )


#: The authored viewport every DOM bounds observation is expressed in
#: (``services/authored_composition_policy.py``: "viewport 1280x720"). A
#: smaller stand-in PNG used to be fine because no worker-lane scenario needed
#: to crop it, but ``build_editable_hybrid_scene``'s raster-island fallback
#: (``services/authored_vnext/semantic/editable_scene.py::_raster_asset``)
#: crops this exact PNG using viewport-space bounds, so a stand-in smaller
#: than the viewport makes ``left``/``top`` legitimately exceed the image's
#: own width/height and PIL's ``crop`` raises "Coordinate 'lower' is less
#: than 'upper'" -- not a defect in the code under test, a resolution
#: mismatch in this stand-in.
_VIEWPORT_SIZE = (1280, 720)


def _png(seed: str) -> bytes:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    output = io.BytesIO()
    Image.new("RGB", _VIEWPORT_SIZE, (digest[0], digest[1], digest[2])).save(
        output, format="PNG"
    )
    return output.getvalue()


# --- stub provider ------------------------------------------------------------


@dataclass
class ProviderCall:
    indices: tuple[int, ...]
    ordinal_by_index: dict[int, int]
    in_flight_at_start: int


@dataclass
class StubProvider:
    """The llmai client seen by ``author_slides_html``.

    ``html_for(index, ordinal)`` returns the document for one slide on its
    ``ordinal``-th provider call (1-based); ``raise_for(indices, call_number)``
    may return an exception to raise for the whole physical call.
    """

    html_for: Callable[[int, int], str]
    raise_for: Callable[[tuple[int, ...], int], BaseException | None] = (
        lambda _indices, _call: None
    )
    calls: list[ProviderCall] = field(default_factory=list)
    calls_by_index: dict[int, int] = field(default_factory=dict)
    max_in_flight: int = 0
    _in_flight: int = 0

    def generate(self, **kwargs: Any) -> Any:
        indices = self._requested_indices(kwargs)
        self._in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            ordinals: dict[int, int] = {}
            for index in indices:
                self.calls_by_index[index] = self.calls_by_index.get(index, 0) + 1
                ordinals[index] = self.calls_by_index[index]
            call = ProviderCall(indices, ordinals, self._in_flight - 1)
            self.calls.append(call)
            # Give the sibling call of the same wave a chance to start so the
            # in-flight counter can observe wave concurrency.
            time.sleep(0.05)
            error = self.raise_for(indices, len(self.calls))
            if error is not None:
                raise error
            return SimpleNamespace(
                content=_wrap_batch_response(
                    {index: self.html_for(index, ordinals[index]) for index in indices}
                )
            )
        finally:
            self._in_flight -= 1

    @staticmethod
    def _requested_indices(kwargs: dict[str, Any]) -> tuple[int, ...]:
        user_message = kwargs["messages"][1]
        texts = [
            getattr(part, "text", "")
            for part in user_message.content
            if getattr(part, "text", None)
        ]
        found = tuple(
            int(match.group(1))
            for text in texts
            for match in _SLIDE_INDEX_PATTERN.finditer(text)
        )
        assert found, "batch prompt did not name its slide indexes"
        return found


@dataclass
class SingleSlideStub:
    """The llmai client seen by ``author_slide.author_slide_html``.

    The batch wave is not the only lane that reaches a provider: the D7
    author-stage rescue authors ONE slide through ``author_planned_slide``,
    which builds the single-slide prompt and resolves its own client. Without
    this the rescue call would die resolving a real provider config, and a test
    could not tell "the rescue was attempted" from "the rescue never ran".

    Counts land in the same ``StubProvider.calls_by_index`` as the batch calls,
    so a slide's total provider cost stays one number.
    """

    provider: StubProvider
    calls: list[int] = field(default_factory=list)

    def generate(self, **kwargs: Any) -> Any:
        index = self._requested_index(kwargs)
        self.calls.append(index)
        ordinal = self.provider.calls_by_index.get(index, 0) + 1
        self.provider.calls_by_index[index] = ordinal
        return SimpleNamespace(content=self.provider.html_for(index, ordinal))

    @staticmethod
    def _requested_index(kwargs: dict[str, Any]) -> int:
        for message in kwargs["messages"]:
            content = getattr(message, "content", None)
            texts = (
                [content]
                if isinstance(content, str)
                else [
                    getattr(part, "text", "")
                    for part in (content or ())
                    if getattr(part, "text", None)
                ]
            )
            for text in texts:
                match = _SINGLE_SLIDE_PATTERN.search(text)
                if match:
                    return int(match.group(1)) - 1
        raise AssertionError("single-slide prompt did not name its slide")


# --- wave / policy observation -------------------------------------------------


@dataclass
class PolicyClock:
    """The clock handed to the batch policy, skippable by the test.

    ``skip`` models wall time the scheduler is expected to spend in a
    cooldown.  A real ``asyncio.sleep`` of a cooldown-sized duration advances
    it instead (see ``_install_sleep_recorder``), so the same observations
    hold whether or not the production wait has landed.
    """

    offset: float = 0.0

    def now(self) -> float:
        return time.monotonic() + self.offset

    def skip(self, seconds: float) -> None:
        self.offset += seconds


@dataclass
class WaveRecorder:
    clock: PolicyClock
    waves: list[tuple[tuple[int, ...], ...]] = field(default_factory=list)
    states_after_wave: list[batch_policy_mod.AuthoredBatchPolicyState] = field(
        default_factory=list
    )
    scheduler_skips: list[float] = field(default_factory=list)
    _pending_state: batch_policy_mod.AuthoredBatchPolicyState | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        real_plan = batch_policy_mod.plan_authored_batch_wave
        real_advance = batch_policy_mod.advance_authored_batch_policy

        def plan(pending, state):
            if self._pending_state is not None:
                self.states_after_wave.append(self._pending_state)
            remaining = state.cooldown_remaining(self.clock.now())
            if remaining > 0:
                # Stand in for the scheduler's cooldown wait so the recovery
                # rule is observable today; a landed wait already zeroed it.
                self.scheduler_skips.append(remaining)
                self.clock.skip(remaining + 0.001)
            wave = real_plan(pending, state)
            self.waves.append(tuple(batch.slide_indices for batch in wave.batches))
            return wave

        def advance(state, **kwargs):
            kwargs.setdefault("now", self.clock.now())
            result = real_advance(state, **kwargs)
            self._pending_state = result
            return result

        # The wave loop has moved between modules (pipeline -> wave scheduler)
        # and may move again; patch every module that imported the names so the
        # observation does not depend on who owns the loop today.
        patched = 0
        for module in (pipeline_mod, wave_scheduler_mod, author_deck_mod):
            if hasattr(module, "plan_authored_batch_wave"):
                monkeypatch.setattr(module, "plan_authored_batch_wave", plan)
                monkeypatch.setattr(module, "advance_authored_batch_policy", advance)
                patched += 1
        assert patched, "no module exposes the authored batch wave planner"

    def final_state(self) -> batch_policy_mod.AuthoredBatchPolicyState | None:
        return self._pending_state


#: Modules a cooldown wait may be issued from, whichever scheduler lands it.
_SCHEDULER_MODULE_PREFIXES = ("services.authored_", "utils.llm_calls.author_")
#: The function that performs the cooldown wait, in whichever module owns it.
#: Only this sleep advances the policy clock: see ``_install_sleep_recorder``.
_COOLDOWN_WAIT_FUNCTIONS = frozenset({"wait_out_cooldown"})


def _install_sleep_recorder(
    monkeypatch: pytest.MonkeyPatch, clock: PolicyClock
) -> list[float]:
    """Record cooldown-sized sleeps wherever the authored scheduler issues them.

    Patched on the ``asyncio`` module itself so the check does not depend on
    which module ends up owning the wave loop.  Only callers inside the
    authored scheduler modules are intercepted: the workspace runner's own
    heartbeat/poll sleeps must keep their real duration.
    """

    recorded: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(delay, *args, **kwargs):
        frame = sys._getframe(1)
        caller = frame.f_globals.get("__name__", "")
        if delay >= 1.0 and caller.startswith(_SCHEDULER_MODULE_PREFIXES):
            recorded.append(float(delay))
            # Only the cooldown wait moves the policy clock. The other authored
            # sleeps in this window are the generation progress poller's, and
            # they return immediately here: skipping the clock for them too
            # would run the policy's clock ahead of the scheduler's, so the
            # cooldown the scheduler then measures is inflated by every poll.
            if frame.f_code.co_name in _COOLDOWN_WAIT_FUNCTIONS:
                clock.skip(float(delay))
            return await real_sleep(0)
        return await real_sleep(delay, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


# --- loopback harness -----------------------------------------------------------


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


async def _wait_until(predicate, *, deadline_seconds: float):
    deadline = time.monotonic() + deadline_seconds
    while True:
        result = await predicate()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for the worker lane")
        await asyncio.sleep(0.02)


async def _start_server(app: FastAPI):
    port = _free_tcp_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            access_log=False,
            log_level="critical",
            lifespan="off",
        )
    )
    task = asyncio.create_task(server.serve())

    async def started():
        return server.started

    await _wait_until(started, deadline_seconds=5)
    return server, task, f"http://127.0.0.1:{port}"


def _bind_lane_database(monkeypatch: pytest.MonkeyPatch, *, engine, maker) -> None:
    """Rebind every module-level ``async_session_maker``/``sql_engine`` binding.

    Function defaults captured at definition time are deliberately left alone:
    they keep pointing at the process engine, which the harness seeds too.
    """

    original_maker = database.async_session_maker
    original_engine = database.sql_engine
    monkeypatch.setattr(database, "async_session_maker", maker)
    monkeypatch.setattr(database, "sql_engine", engine)
    for module in list(sys.modules.values()):
        try:
            bound_maker = getattr(module, "async_session_maker", None)
            bound_engine = getattr(module, "sql_engine", None)
        except Exception:  # lazy module proxies
            continue
        if bound_maker is original_maker:
            monkeypatch.setattr(module, "async_session_maker", maker)
        if bound_engine is original_engine:
            monkeypatch.setattr(module, "sql_engine", engine)


def _policy_profile() -> PublicPolicyProfile:
    """The default public policy row with a ceiling above the slide formula."""

    return PublicPolicyProfile(
        version=admission_mod.PUBLIC_DEFAULT_POLICY_VERSION,
        max_slides=admission_mod.PUBLIC_DEFAULT_MAX_SLIDES,
        global_active_generation=10,
        global_queued_generation=10,
        owner_active_generation=10,
        owner_queued_generation=10,
        tenant_active_generation=10,
        tenant_queued_generation=10,
        global_active_export=10,
        global_queued_export=10,
        owner_active_export=10,
        owner_queued_export=10,
        tenant_active_export=10,
        tenant_queued_export=10,
        global_active_resource_units=10**9,
        global_queued_resource_units=10**9,
        tenant_active_resource_units=10**9,
        tenant_queued_resource_units=10**9,
        owner_active_resource_units=10**9,
        owner_queued_resource_units=10**9,
        retry_request_limit=100,
        estimated_cost_limit_microunits=10**9,
        actual_cost_limit_microunits=10**9,
        generation_deadline_seconds=deadline_policy.PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
        export_deadline_seconds=deadline_policy.PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
    )


def _install_request_principal(app: FastAPI, owner_id) -> None:
    """Establish the verified request principal the auth middleware would.

    The public v2 admission bound to ``/generate/async`` refuses a request
    with no server-established owner (``verified_principal_required``); the
    loopback app carries no auth middleware, so the same context is set here.
    """

    @app.middleware("http")
    async def principal_context(request, call_next):
        owner_token = set_current_owner_id(owner_id)
        tenant_token = set_current_tenant_id("default")
        trace_token = set_current_trace_id(f"worker-lane-{owner_id.hex}")
        scope_token = set_request_scope_active(True)
        try:
            return await call_next(request)
        finally:
            reset_request_scope_active(scope_token)
            reset_current_trace_id(trace_token)
            reset_current_tenant_id(tenant_token)
            reset_current_owner_id(owner_token)


class _CleanReport:
    telemetry_status = "available"
    issues: list[Any] = []
    fit_summary = None
    needs_attention = False

    def to_dict(self) -> dict[str, Any]:
        return {"issues": [], "telemetryStatus": self.telemetry_status}


class _DomObserver(HTMLParser):
    """Derive the browser telemetry Chromium's probe would report for a page.

    One entry per ``data-qid`` element with its stamped semantic role, its
    directly owned text and its descendant text, every font loaded at a size
    above the floor.  The QualityReceipt v2 capacity gate refuses anything
    that is not a real per-element observation, so a token dict cannot stand
    in for the browser here.
    """

    _VOID = {"meta", "br", "hr", "img", "col", "link", "input"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[dict[str, Any] | None] = []
        self.records: list[dict[str, Any]] = []

    def _open(self, tag: str, attrs) -> dict[str, Any] | None:
        mapped = {key: (value or "") for key, value in attrs}
        qid = mapped.get("data-qid", "").strip()
        if not qid:
            return None
        record = {
            "qid": qid,
            "tag": tag,
            "semantic_role": mapped.get("data-semantic-role", "").strip(),
            "owned": [],
            "rendered": [],
        }
        self.records.append(record)
        return record

    def handle_starttag(self, tag, attrs):
        record = self._open(tag, attrs)
        if tag not in self._VOID:
            self._stack.append(record)

    def handle_startendtag(self, tag, attrs):
        self._open(tag, attrs)

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        if not self._stack:
            return
        owner = self._stack[-1]
        if owner is not None:
            owner["owned"].append(data)
        for record in self._stack:
            if record is not None:
                record["rendered"].append(data)


def _observe_dom(html: str) -> dict[str, Any]:
    observer = _DomObserver()
    observer.feed(html)
    observer.close()
    elements = []
    for record in observer.records:
        owned = re.sub(r"\s+", " ", "".join(record["owned"])).strip()
        rendered = re.sub(r"\s+", " ", "".join(record["rendered"])).strip()
        elements.append(
            {
                "qid": record["qid"],
                "tag": record["tag"],
                "semantic_role": record["semantic_role"],
                "rendered_text": rendered,
                "owned_text": owned,
                "has_owned_text": bool(owned),
                "font_size": 18,
                "font_loaded": True,
                "css_visible": True,
            }
        )
    return {"elements": elements}


def _install_chromium_and_gate_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """No browser in CI: PNG bytes, DOM-derived telemetry and a clean report."""

    async def fake_render(html: str, *args, **kwargs) -> bytes:
        return _png(html)

    async def fake_ensure_metrics(html: str, *args, **kwargs):
        return _observe_dom(html)

    async def fake_capture(html: str, *args, **kwargs):
        return SimpleNamespace(png=_png(html), browser_metrics=_observe_dom(html))

    # ``_sync_runtime_dependencies`` copies these from the service facade into
    # the pipeline/generation modules on every call, so patch the facade.
    monkeypatch.setattr(authored_service, "render_html_to_png", fake_render)
    monkeypatch.setattr(
        authored_service, "render_html_to_png_with_metrics", fake_capture
    )
    monkeypatch.setattr(authored_service, "find_chrome", lambda: True)
    for module in (generation_mod, pipeline_mod, terminal_mod, preflight_mod):
        monkeypatch.setattr(
            module, "inspect_authored_dom", lambda *a, **k: _CleanReport(), raising=False
        )
        monkeypatch.setattr(
            module, "blocking_safety_issues", lambda _report: (), raising=False
        )
        monkeypatch.setattr(
            module, "capacity_violations", lambda *a, **k: (), raising=False
        )
        monkeypatch.setattr(
            module, "ensure_browser_metrics", fake_ensure_metrics, raising=False
        )
        monkeypatch.setattr(
            module, "get_cached_browser_metrics", _observe_dom, raising=False
        )
    monkeypatch.setattr(terminal_mod, "render_html_to_png_with_metrics", fake_capture)


def _install_provider(
    monkeypatch: pytest.MonkeyPatch, provider: StubProvider
) -> None:
    """Replace only the SDK transport underneath ``author_slides_html``."""

    from utils import llm_provider

    async def no_rate_slot() -> None:
        return None

    monkeypatch.setattr(author_batch_mod, "get_client", lambda **_kwargs: provider)
    monkeypatch.setattr(author_batch_mod, "get_llm_config", lambda: {})
    monkeypatch.setattr(author_batch_mod, "get_model", lambda: _STUB_MODEL)
    monkeypatch.setattr(author_batch_mod, "get_llm_provider", lambda: LLMProvider.CODEX)
    monkeypatch.setattr(author_batch_mod, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(author_batch_mod, "_wait_for_author_rate_slot", no_rate_slot)
    # The rescue's simplified attempt takes the single-slide lane, which
    # resolves its own client/config/model. Stub the same seams there.
    single = SingleSlideStub(provider)
    monkeypatch.setattr(author_slide_mod, "get_client", lambda **_kwargs: single)
    monkeypatch.setattr(author_slide_mod, "get_llm_config", lambda: {})
    monkeypatch.setattr(author_slide_mod, "get_model", lambda: _STUB_MODEL)
    monkeypatch.setattr(author_slide_mod, "get_llm_provider", lambda: LLMProvider.CODEX)
    monkeypatch.setattr(author_slide_mod, "enforce_llm_egress_policy", lambda: None)
    monkeypatch.setattr(author_slide_mod, "_wait_for_author_rate_slot", no_rate_slot)
    monkeypatch.setattr(llm_provider, "get_llm_provider", lambda *a, **k: LLMProvider.CODEX)
    monkeypatch.setattr(llm_provider, "get_model", lambda *a, **k: _STUB_MODEL)


@dataclass
class LaneOutcome:
    task_id: str
    status: dict[str, Any]
    job: PresentationGenerationJob
    presentation: PresentationModel | None
    slides: list[SlideModel]
    receipts: tuple[Any, ...]
    operation: WorkspaceOperation | None
    admission: PublicOperationAdmission | None


async def _run_worker_lane(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    slide_count: int,
    provider: StubProvider,
    contract: AuthoredHeaderContract,
    terminal_statuses: frozenset[str] = frozenset({"completed", "error"}),
    gate_override: Callable[[pytest.MonkeyPatch], None] | None = None,
) -> LaneOutcome:
    from api.v1.ppt.endpoints import presentation_generate as endpoint

    # Two SQLite databases, on purpose.  The lane itself (endpoint, runner,
    # generation handler, checkpoints, receipts -- every module that binds
    # ``services.database.async_session_maker`` by name) runs on a private
    # engine.  The sidecar services that captured the session maker as a
    # function default at import time (progress pollers, account/asset
    # maintenance, the account-deletion fence) can only ever reach the process
    # engine, so that one is created and seeded as well.  Putting everything on
    # one SQLite file is not an option here: the generation job's own session
    # keeps SQLite's single writer lock across long stages, and with every
    # sidecar writer queued on the same file the runner's heartbeat loses its
    # 30 s busy timeout (measured 2026-09-05; production runs PostgreSQL).
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-worker-lane.db'}",
        connect_args={"timeout": 30},
    )
    async with engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA journal_mode=WAL")
        await connection.run_sync(SQLModel.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    sidecar_engine = database.sql_engine
    sidecar_maker = database.async_session_maker
    async with sidecar_engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    _bind_lane_database(monkeypatch, engine=engine, maker=maker)

    async def session_override():
        async with maker() as session:
            yield session

    # The compatibility route binds every request to the v2 public admission:
    # it verifies the principal against an active account row and pins the
    # deadline from the default policy profile, which migrations normally seed.
    owner_id = uuid.uuid4()
    for factory in (maker, sidecar_maker):
        async with factory() as session:
            session.add(
                User(
                    id=owner_id,
                    username=f"worker-lane-{owner_id.hex}",
                    hashed_password="test",
                )
            )
            if (
                await session.get(
                    PublicPolicyProfile, admission_mod.PUBLIC_DEFAULT_POLICY_VERSION
                )
                is None
            ):
                session.add(_policy_profile())
            await session.commit()

    app = FastAPI()
    app.dependency_overrides[database.get_async_session] = session_override
    app.include_router(endpoint.PRESENTATION_GENERATE_ROUTER, prefix="/api/v1/ppt")
    _install_request_principal(app, owner_id)

    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(tmp_path / "checkpoints"))
    monkeypatch.setenv(
        "AUTHORED_QUALITY_RECEIPT_DIRECTORY", str(tmp_path / "quality-receipts")
    )
    monkeypatch.setenv("DISABLE_IMAGE_GENERATION", "true")
    # Publication hands the export to the durable artifact authority; the
    # development backend keeps it on the local filesystem and is only
    # constructed under ``ENVIRONMENT=local``.
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", "local-development")
    monkeypatch.setenv(
        "PUBLIC_WORKER_OBJECT_STORE_ROOT", str((tmp_path / "object-store").resolve())
    )
    monkeypatch.setenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY", "a1" * 32)
    # The personal-token provider: the auto profile derives ``token`` from it,
    # the provider resolver admits it, and the public fence prices its calls
    # from the catalog below (an exact provider/model match is mandatory).
    monkeypatch.setenv("LLM", LLMProvider.CODEX.value)
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", TOKEN_PROFILE_NAME)
    monkeypatch.setenv("PUBLIC_PROVIDER_PRICING_REVISION", _PRICING_REVISION)
    monkeypatch.setenv("PUBLIC_PROVIDER_PRICING_CATALOG", _pricing_catalog())

    async def fake_brief(*_args, **_kwargs):
        return SimpleNamespace(to_prompt_context=lambda: "deterministic content brief")

    async def fake_outline(*_args, **_kwargs):
        yield json.dumps(
            {
                "slides": [
                    {"content": f"Slide {index + 1}\nPoint A\nPoint B"}
                    for index in range(slide_count)
                ]
            }
        )

    async def no_op(*_args, **_kwargs):
        return None

    async def fake_header_contract(*_args, **_kwargs):
        return contract

    monkeypatch.setattr(endpoint, "generate_content_brief", fake_brief)
    monkeypatch.setattr(endpoint, "generate_ppt_outline", fake_outline)
    monkeypatch.setattr(
        endpoint.MEM0_PRESENTATION_MEMORY_SERVICE, "store_generation_context", no_op
    )
    monkeypatch.setattr(
        endpoint.MEM0_PRESENTATION_MEMORY_SERVICE, "store_generated_outlines", no_op
    )
    monkeypatch.setattr(authored_service, "resolve_header_contract", fake_header_contract)
    _install_chromium_and_gate_stubs(monkeypatch)
    if gate_override is not None:
        # Applied after the always-clean stubs above so a scenario can put a
        # real gate function (or a synthetic-but-genuine observation) back on
        # top of them for one deliberately defective slide, without having to
        # duplicate the rest of ``_install_chromium_and_gate_stubs``.
        gate_override(monkeypatch)
    _install_provider(monkeypatch, provider)

    generation_handler = register_presentation_generation_operation_handler(replace=True)
    monkeypatch.setattr(generation_handler, "_session_factory", maker)
    runner = workspace_runner.WorkspaceOperationRunner(
        session_factory=maker, poll_seconds=0.5
    )
    monkeypatch.setattr(workspace_runner, "_workspace_operation_runner", runner)
    await runner.start()

    server = None
    server_task = None
    try:
        server, server_task, base_url = await _start_server(app)
        payload = {
            "content": "Worker lane e2e deck",
            "template": "authored",
            "strategy": "authored",
            "n_slides": slide_count,
            "language": "English",
            "include_title_slide": False,
            "vision_qa": False,
            "export_as": "pptx",
        }
        # Publication assembles the export inline; a status read can queue
        # behind it for longer than httpx's 5 s default.
        async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
            submitted = await client.post(
                "/api/v1/ppt/presentation/generate/async",
                json=payload,
                headers={"Idempotency-Key": f"worker-lane-{owner_id.hex}"},
            )
            # The compatibility route answers 202 with the admitted operation id.
            assert submitted.status_code in {200, 202}, submitted.text
            task_id = submitted.json()["id"]
            assert submitted.json().get("operation_id")

            last_status: dict[str, Any] = {}

            async def terminal_status():
                try:
                    response = await client.get(
                        f"/api/v1/ppt/presentation/status/{task_id}"
                    )
                except httpx.TransportError:
                    return None
                assert response.status_code == 200, response.text
                body = response.json()
                last_status.clear()
                last_status.update(body)
                return body if body["status"] in terminal_statuses else None

            try:
                status = await _wait_until(
                    terminal_status, deadline_seconds=_STATUS_TIMEOUT_SECONDS
                )
            except AssertionError as error:
                raise AssertionError(
                    f"worker lane did not settle: last status={last_status!r}; "
                    f"provider calls={[call.indices for call in provider.calls]!r}"
                ) from error

        last_job_state: list[str | None] = [None]

        async def job_settled():
            async with maker() as session:
                job = await session.scalar(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.task_id == task_id
                    )
                )
                last_job_state[0] = job.state if job is not None else None
                return job if job is not None and job.state in {"published", "failed"} else None

        try:
            job = await _wait_until(job_settled, deadline_seconds=30)
        except AssertionError as error:
            raise AssertionError(
                f"job did not settle: state={last_job_state[0]!r}; status={status!r}"
            ) from error
        async with maker() as session:
            presentation = await session.get(PresentationModel, job.presentation_id)
            slides = list(
                (
                    await session.execute(
                        select(SlideModel)
                        .where(SlideModel.presentation == job.presentation_id)
                        .order_by(SlideModel.index)
                    )
                ).scalars()
            )
            operation = (
                await session.get(WorkspaceOperation, job.workspace_operation_id)
                if job.workspace_operation_id is not None
                else None
            )
            admission = (
                await session.scalar(
                    select(PublicOperationAdmission).where(
                        PublicOperationAdmission.operation_id == job.workspace_operation_id
                    )
                )
                if job.workspace_operation_id is not None
                else None
            )
        # The handler seals the attempt-timing receipt after the job row has
        # already reached its terminal state, so give it a moment to land.
        async def receipts_written():
            return load_generation_attempt_timing_receipts(job.id) or None

        try:
            receipts = await _wait_until(receipts_written, deadline_seconds=15)
        except AssertionError:
            receipts = ()
        return LaneOutcome(
            task_id, status, job, presentation, slides, receipts, operation, admission
        )
    finally:
        await runner.stop()
        for active in list(jobs._active_jobs.values()):
            if not active.done():
                active.cancel()
        if jobs._active_jobs:
            await asyncio.gather(*list(jobs._active_jobs.values()), return_exceptions=True)
        jobs._active_jobs.clear()
        if server is not None:
            server.should_exit = True
        if server_task is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(server_task, timeout=5)
        await engine.dispose()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _default_contract() -> AuthoredHeaderContract:
    return default_header_contract(primary="#2563EB")


def _token_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AUTHORED_PROVIDER_RATE_PROFILE", TOKEN_PROFILE_NAME)
    profile = resolve_authored_rate_profile()
    assert profile.name == TOKEN_PROFILE_NAME
    assert (
        profile.slides_per_call,
        profile.initial_wave_concurrency,
        profile.steady_wave_concurrency,
        profile.recovery_successes,
    ) == (1, 1, 2, 3)
    return profile


def _expected_wave_shapes(slide_count: int, profile) -> list[tuple[tuple[int, ...], ...]]:
    """The shapes the batch policy must schedule when every call succeeds."""

    shapes: list[tuple[tuple[int, ...], ...]] = []
    pending = list(range(slide_count))
    size = profile.slides_per_call
    concurrency = profile.initial_wave_concurrency
    while pending:
        batches = []
        for _ in range(concurrency):
            if not pending:
                break
            batches.append(tuple(pending[:size]))
            pending = pending[size:]
        shapes.append(tuple(batches))
        concurrency = profile.steady_wave_concurrency
    return shapes


