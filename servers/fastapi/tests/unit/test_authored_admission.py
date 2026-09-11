from __future__ import annotations

from functools import wraps

import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import authored_admission as gate
from services.authored_admission_capture import capture_once
from services.authored_admission_context import context_from_source, digest_html
from services.authored_admission_policy import CONSUMED_GLOBAL_ATTRIBUTES, SOURCE_BLOCK_ATTRIBUTE
from services.authored_admission_receipt import (
    AdmissionCaptureStore,
    AdmissionReceiptError,
    require_admission_receipt,
)
from services.authored_admission_session import AdmissionSession, _SESSION, check_producer
from services.authored_admission_shadow import AdmissionShadow
from services.authored_rollout_profile import RolloutProfile, current_rollout_profile, rollout_profile_scope
from services.source_content.browser_readback import BrowserSourceReadbackResult, deterministic_source_qid
from utils.renderer_markup_policy import GLOBAL_ATTRIBUTES, SERVER_STAMPED_ATTRIBUTES
from utils.slide_capture_result import RenderCapture


def sync_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


MANIFEST = {
    "units": [
        {
            "sourceUnitId": "u1",
            "sourceIndex": 0,
            "blocks": [
                {"sourceBlockId": "b1", "kind": "text", "order": 0, "payload": {"text": "원문 제목"}},
            ],
        }
    ]
}
OUTLINE = {"slides": [{"sourceUnitId": "u1", "sourceIndex": 0, "readingOrder": ["b1"]}]}
HTML = "<!doctype html><html><head><style>body{margin:0}</style></head><body><h1>원문 제목</h1></body></html>"


def context(**kwargs):
    values = dict(
        presentation_id="p1",
        operation_id="op1",
        slide_index=0,
        source_unit_id="u1",
        policy_revision=current_rollout_profile().policy_revision,
        fenced_revision=4,
        attempt_token="attempt-1",
        reference_mode="source_content_restyle",
        source_reflow=True,
        terminal_fallback_eligible=False,
        bindings=(("b1", "원문 제목"),),
    )
    values.update(kwargs)
    return context_from_source(manifest=MANIFEST, projection=OUTLINE, **values)


def metrics(html, **changes):
    element = {
        "sourceBlockId": "b1",
        "qid": deterministic_source_qid("b1"),
        "semanticRole": "title",
        "renderedText": "원문 제목",
        "bbox": {"x": 20, "y": 20, "width": 200, "height": 24},
        "cssVisible": True,
        "effectiveOpacity": 1,
        "fontLoaded": True,
        "clipped": False,
        "glyphClipped": False,
        "lineBoxClipped": False,
        "occludedFraction": 0,
        "continuousOccludedFraction": 0,
        "computedStyle": {"display": "block", "visibility": "visible"},
        "measurement": {"occlusion": True},
        "occlusionMeasurementEvidence": {"status": "measured"},
    }
    element.update(changes)
    return {
        "width": 1280,
        "height": 720,
        "fontsReady": True,
        "source_digest": digest_html(html),
        "render_digest": "sha256:" + "a" * 64,
        "elements": [element],
    }


@pytest.fixture(autouse=True)
def legacy_representation(monkeypatch):
    """Pin the attempt's rollout profile the way a real attempt start does."""
    with rollout_profile_scope(RolloutProfile()):
        monkeypatch.setattr(gate, "text_step", gate.bind_current_source)
        yield


def test_context_is_immutable_and_pins_source_digest():
    pinned = context()
    with pytest.raises(FrozenInstanceError):
        pinned.slide_index = 2
    with pytest.raises(ValueError, match="manifest_digest_mismatch"):
        replace(pinned, manifest_digest="changed")
    assert pinned.readback_documents() == (MANIFEST, OUTLINE)
    assert replace(pinned, attempt_token="other").identity_digest != pinned.identity_digest


@pytest.mark.parametrize("mode", ["ordinary", "verbatim", "terminal"])
@pytest.mark.parametrize("predicate", ["raw", "static", "integrity", "visibility", "telemetry", "diagnostic"])
@pytest.mark.parametrize("flagged", [False, True])
def test_three_mode_table(monkeypatch, mode, predicate, flagged):
    ctx = context(
        html_repr="captured",
        source_reflow=mode != "ordinary",
        terminal_fallback_eligible=mode == "terminal",
        already_admitted_flags=("source_readback:visibility",) if flagged else (),
    )
    html = gate.admit_slide_html(context(), HTML).html
    sample = metrics(html)
    if predicate == "raw":
        html += "\x00"
    elif predicate == "static":
        html = html.replace("원문 제목", "변경됨")
        sample = metrics(html)
    else:
        result = BrowserSourceReadbackResult(
            1,
            1,
            **{
                "integrity": {"integrity_failures": ("b1",)},
                "visibility": {"visibility_failures": ("b1",)},
                "telemetry": {"telemetry_failures": ("b1",)},
                "diagnostic": {"diagnostic_error": "missing capture"},
            }[predicate],
        )
        monkeypatch.setattr(gate, "inspect_browser_source_readback", lambda **_: result)
    admission = gate.admit_slide_html(ctx, html, metrics=sample, png=b"png")
    expected = "rejected"
    if mode == "ordinary" and predicate != "raw":
        expected = "admitted"
    elif mode == "terminal" and flagged and predicate == "visibility":
        expected = "flag_only"
    assert admission.verdict == expected
    if mode != "ordinary" and predicate in {"telemetry", "diagnostic"}:
        assert admission.retryable


def test_fixed_gate_order_and_post_expansion_policy(monkeypatch):
    calls = []
    monkeypatch.setattr(gate, "raw_diagnostics_step", lambda _: calls.append("raw"))
    monkeypatch.setattr(gate, "validate_authored_slide_html", lambda html, **_: calls.append("policy") or html)
    monkeypatch.setattr(gate, "text_step", lambda ctx, html: calls.append("text") or gate.TextStepResult(html))
    monkeypatch.setattr(
        gate,
        "inspect_browser_source_readback",
        lambda **_: calls.append("readback") or BrowserSourceReadbackResult(1, 1),
    )
    assert gate.admit_slide_html(context(html_repr="token"), HTML).verdict == "admitted"
    assert calls == ["raw", "policy", "text", "policy"]
    calls.clear()
    ordinary = context(html_repr="captured", source_reflow=False)
    assert gate.admit_slide_html(ordinary, HTML, metrics={}).verdict == "admitted"
    assert calls == ["raw", "policy", "policy"]


def test_duplicate_attributes_fail_before_rewrite(monkeypatch):
    normalizer = AsyncMock()
    monkeypatch.setattr(gate, "validate_authored_slide_html", normalizer)
    admission = gate.admit_slide_html(context(), '<div id="a" ID="b">text</div>')
    assert admission.verdict == "rejected" and admission.predicate == "raw_diagnostics"
    normalizer.assert_not_called()


def test_text_step_normalizations_are_carried_in_receipt(monkeypatch):
    entry = {"rule": "source_placeholder_expanded", "blocks": 1, "deviations": 0}
    monkeypatch.setattr(gate, "text_step", lambda ctx, html: gate.TextStepResult(html, normalizations=(entry,)))
    admission = gate.admit_slide_html(context(source_reflow=False, html_repr="token"), HTML)
    assert entry in admission.normalizations
    assert entry in admission.receipt.as_dict()["normalizations"]


@pytest.mark.parametrize(
    "run,change",
    [
        ("run-13", {"sourceBlockId": ""}),
        ("run-14", {"cssVisible": False}),
        ("run-17", {"renderedText": "changed"}),
        ("run-19", {"sourceBlockId": "wrong-id"}),
        ("run-23", {"renderedText": "short title changed"}),
    ],
)
def test_synthetic_historical_failure_classes_remain_rejected(run, change):
    html = gate.admit_slide_html(context(), HTML).html
    result = gate.admit_slide_html(context(html_repr="captured"), html, metrics=metrics(html, **change))
    assert result.verdict == "rejected", run


def test_run18_void_end_tag_keeps_existing_normalization_semantics():
    result = gate.admit_slide_html(context(), HTML.replace("</h1>", "</h1></img>"))
    assert result.verdict == "admitted" and "</img>" not in result.html


def test_real_captured_integrity_and_html_binding():
    expanded = gate.admit_slide_html(context(), HTML)
    assert expanded.verdict == "admitted"
    captured = context(html_repr="captured")
    assert gate.admit_slide_html(captured, expanded.html, metrics=metrics(expanded.html)).verdict == "admitted"
    damaged = metrics(expanded.html, renderedText="손상된 원문")
    assert gate.admit_slide_html(captured, expanded.html, metrics=damaged).predicate == "rendered_integrity"
    assert gate.admit_slide_html(captured, HTML, metrics=metrics(HTML)).verdict == "rejected"


@pytest.mark.parametrize(
    "change,code",
    [
        ({"html": "tampered"}, "html_digest_mismatch"),
        ({"attempt_token": "stale"}, "context_mismatch"),
        ({"fenced_revision": 3}, "context_mismatch"),
        ({"slide_index": 1}, "context_mismatch"),
        ({"operation_id": "other"}, "context_mismatch"),
        ({"png": b"other"}, "png_digest_mismatch"),
        ({"metrics": {}}, "metrics_digest_mismatch"),
    ],
)
def test_writer_receipt_mismatch(change, code):
    html = gate.admit_slide_html(context(), HTML).html
    captured = gate.admit_slide_html(context(html_repr="captured"), html, metrics=metrics(html), png=b"png")
    kwargs = dict(
        html=html,
        stage="render",
        slide_index=0,
        attempt_token="attempt-1",
        fenced_revision=4,
        operation_id="op1",
        metrics=metrics(html),
        png=b"png",
    )
    require_admission_receipt(captured.receipt, **kwargs)
    with pytest.raises(AdmissionReceiptError, match=code):
        require_admission_receipt(captured.receipt, **dict(kwargs, **change))


def test_rejected_capture_can_only_be_nonpublishable_evidence():
    html = gate.admit_slide_html(context(), HTML).html
    sample = metrics(html, cssVisible=False)
    admission = gate.admit_slide_html(context(html_repr="captured"), html, metrics=sample, png=b"png")
    kwargs = dict(
        html=html,
        stage="render",
        slide_index=0,
        attempt_token="attempt-1",
        fenced_revision=4,
        metrics=sample,
        png=b"png",
    )
    with pytest.raises(AdmissionReceiptError, match="receipt_rejected"):
        require_admission_receipt(admission.receipt, **kwargs)
    evidence = replace(admission.receipt, persistence_class="evidence")
    require_admission_receipt(evidence, **kwargs)
    assert evidence.as_dict()["publishable"] is False


@sync_async
async def test_checkpoint_writer_refuses_unadmitted_html_before_io(monkeypatch):
    """B1: a producer without a session is admitted at the writer, not exempted.

    ``enforce_receipts`` falls back to the global knob when no admission session
    is open, which is how the authored-vNext legacy bridge and the workspace
    page-remake handlers reach ``complete_stage``. The writer obtains their
    receipt by admitting the HTML on the same predicate, so bytes the gate
    rejects still never reach the database -- ``job_id`` here is not a real job,
    so any database access would fail loudly instead.
    """

    from services import authored_checkpoint_state as state
    from services import authored_admission_checkpoint as checkpoint

    monkeypatch.setattr(checkpoint, "enforce_receipts", lambda: True)
    with pytest.raises(AdmissionReceiptError, match="receipt_rejected"):
        await state.complete_stage(
            job_id="job",
            slide_index=0,
            stage="author",
            attempt_token="attempt-1",
            fencing_token=4,
            html=HTML.replace("<h1>", '<h1 id="a" id="b">'),
        )


def test_same_profile_capture_reuse_is_attempt_bound():
    html = gate.admit_slide_html(context(), HTML).html
    ctx = context(html_repr="captured")
    sample = metrics(html)
    captured = gate.admit_slide_html(ctx, html, metrics=sample, png=b"png")
    store = AdmissionCaptureStore()
    store.remember(captured.receipt, sample, b"png")
    assert store.reuse(ctx, html).receipt.capture_reused
    assert store.reuse(replace(ctx, capture_profile="other"), html) is None
    assert store.reuse(replace(ctx, attempt_token="other"), html) is None
    assert store.reuse(ctx, html + " ") is None


@sync_async
async def test_capture_once_coalesces_identical_concurrent_calls():
    html = gate.admit_slide_html(context(), HTML).html
    sample = metrics(html)
    capture = AsyncMock(
        return_value=RenderCapture.from_payload(
            html_digest=digest_html(html),
            png=b"png",
            browser_metrics=sample,
            runtime_counters={},
        )
    )
    session = AdmissionSession(mode="enforce", contexts=(context(),))
    token = _SESSION.set(session)
    try:
        await asyncio.gather(*(capture_once(html, index=0, capture=capture) for _ in range(2)))
        assert capture.await_count == 1
        assert session.capture_reuses == 1
        from services.authored_admission_checkpoint import prepare_checkpoint_write

        prepared = prepare_checkpoint_write(
            dict(
                html=html,
                stage="render",
                slide_index=0,
                browser_metrics=sample,
                png=b"png",
            )
        )
        assert prepared["admission_receipt"].capture_reused
        assert prepared["admission_receipt"].as_dict()["metricsStorage"] == "checkpoint_bundle"
        await capture_once(html, index=0, capture=capture, fit=False)
        assert capture.await_count == 2
    finally:
        _SESSION.reset(token)


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
def test_shadow_keeps_legacy_verdict_and_enforce_uses_gate_only(mode):
    session = AdmissionSession(mode=mode, contexts=(context(),))
    token = _SESSION.set(session)
    calls = []
    try:

        def legacy(html):
            calls.append(html)
            return "legacy result"

        if mode == "shadow":
            result, admission = check_producer(HTML + "\x00", index=0, producer="author", legacy=legacy)
            assert result == "legacy result" and admission.verdict == "rejected"
            assert session.shadow.as_dict()["rows"][0]["existingVerdict"] == "admitted"
        else:
            with pytest.raises(gate.BrowserSourceReadbackError):
                check_producer(HTML + "\x00", index=0, producer="author", legacy=legacy)
            assert not calls
    finally:
        _SESSION.reset(token)


def test_shadow_bound_and_stable_error_codes():
    from services.authored_generation_observability import log_authored_slide_failure_observation

    shadow = AdmissionShadow(max_rows=1)
    for producer in ("author", "author_batch"):
        shadow.record(
            producer=producer,
            existing_verdict="admitted",
            existing_code="",
            gate_verdict="rejected",
            gate_code="source_placeholder_missing",
        )
    assert shadow.as_dict()["dropped"] == 1
    assert len(json.dumps(shadow.as_dict())) < 1024
    observation = log_authored_slide_failure_observation(
        stage="author",
        code="source_placeholder_missing",
        slide_number=1,
        retryable=False,
    )
    assert observation["code"] == "source_placeholder_missing"


# Explicitly grandfathered dead readers; none is renderer permission.
KNOWN_DEAD_READS = {
    "services/source_content/browser_readback.py": {"data-source-image", "data-decorative", "data-decoration"},
    "utils/slide_capture_fit_observe.py": {"data-role"},
}


def test_consumer_attribute_vocabulary_is_in_renderer_policy():
    root = Path(__file__).resolve().parents[2]
    consumers = [
        *root.glob("utils/slide_capture_fit*.py"),
        root / "services/source_content/browser_readback.py",
        root / "services/authored_terminal_slide_policy.py",
        root / "services/authored_flag_classification.py",
    ]
    assert CONSUMED_GLOBAL_ATTRIBUTES <= GLOBAL_ATTRIBUTES
    for path in consumers:
        source = path.read_text(encoding="utf-8")
        names = set(re.findall(r"(?:getAttribute|hasAttribute|attrs\.get)\(['\"](data-[a-z-]+)['\"]", source))
        relative = path.relative_to(root).as_posix()
        assert names <= GLOBAL_ATTRIBUTES | set(SERVER_STAMPED_ATTRIBUTES) | KNOWN_DEAD_READS.get(relative, set()), (
            relative
        )


# --- N1/N2: the session-less writer admission -------------------------------

#: A 1x1 PNG, the smallest asset a renderer-produced CSS ``url(data:)`` can
#: carry.  The static-output policy re-encodes it; the authored policy refuses
#: the declaration outright, which is exactly the disagreement under test.
ONE_PIXEL_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
#: Shaped like renderer output, not like authored HTML: the authored policy
#: rejects it and the static-output policy admits it.
RENDERER_PROFILE_HTML = (
    "<!doctype html><html><head><style>body{margin:0}"
    f".scene{{background:url(data:image/png;base64,{ONE_PIXEL_PNG})}}"
    '</style></head><body><div class="scene">장면</div></body></html>'
)
#: Refused by every profile, so the gate must still refuse it.
UNRENDERABLE_HTML = (
    "<!doctype html><html><head><style>body{margin:0}</style></head>"
    "<body><iframe></iframe></body></html>"
)


def vnext_preview_html(root):
    """A real ``build_preview`` document, reached through the master service."""

    from models.authored_vnext.master.contract import DeckMasterContract
    from models.authored_vnext.master.operations import (
        CreateDraftRequest,
        MasterCasRequest,
        PreviewRequest,
    )
    from services.authored_vnext.master.file_repository import (
        FileAuthoredVNextMasterRepository,
    )
    from services.authored_vnext.master.service import AuthoredVNextMasterService
    from tests.support.authored_vnext_master_fixture import (
        long_title_slide_payload,
        master_contract_payload,
    )

    service = AuthoredVNextMasterService(
        FileAuthoredVNextMasterRepository(root / "authored-vnext-store")
    )
    draft = service.create_draft(
        CreateDraftRequest(
            contract=DeckMasterContract.model_validate(master_contract_payload())
        )
    )
    published = service.publish(
        draft.master_id,
        MasterCasRequest(expected_revision=draft.revision, expected_digest=draft.digest),
    )
    return service.preview(
        PreviewRequest(
            master_id=published.master_id,
            master_revision=published.revision,
            master_digest=published.digest,
            slide=long_title_slide_payload(),
        )
    ).html


def enforcing_gate(monkeypatch):
    from services import authored_admission_checkpoint as checkpoint

    monkeypatch.setattr(checkpoint, "enforce_receipts", lambda: True)
    return checkpoint


def author_checkpoint_write(checkpoint, html, **changes):
    kwargs = dict(
        admission_receipt=None,
        html=html,
        stage="author",
        slide_index=0,
        attempt_token="attempt-1",
        fencing_token=4,
    )
    kwargs.update(changes)
    return checkpoint.admit_checkpoint_write(**kwargs)


@pytest.mark.parametrize("document", ["vnext_preview", "renderer_profile", "authored"])
def test_rendered_bytes_are_admissible_at_a_session_less_writer(document, tmp_path, monkeypatch):
    """N1: the gate and the renderer select the profile with the same function.

    A vNext / page-remake producer renders its slide and then writes it to a
    checkpoint outside ``admission_scope``.  Judging that document by the strict
    authored policy while the renderer had accepted it under the static-output
    policy would refuse a deck the product had already rendered.  Whatever
    ``select_render_profile`` admits, ``admit_checkpoint_write`` admits.
    """

    from utils.renderer_profile import STATIC_OUTPUT_PROFILE, select_render_profile

    html = {
        "vnext_preview": lambda: vnext_preview_html(tmp_path),
        "renderer_profile": lambda: RENDERER_PROFILE_HTML,
        "authored": lambda: HTML,
    }[document]()
    assert SOURCE_BLOCK_ATTRIBUTE not in html
    profile = select_render_profile(html)
    assert profile.renderable

    checkpoint = enforcing_gate(monkeypatch)
    admitted, receipt = author_checkpoint_write(checkpoint, html)
    assert receipt.verdict == "admitted"
    assert receipt.html_digest == digest_html(admitted)
    if profile.name == STATIC_OUTPUT_PROFILE:
        # Renderer-profile bytes are admitted, never rewritten: the checkpoint
        # holds the document the renderer rendered.
        assert admitted == html


def test_session_less_admission_still_refuses_policy_violating_html(monkeypatch):
    """N1's inverse: sharing the selection widens no policy."""

    from utils.renderer_profile import select_render_profile

    assert select_render_profile(UNRENDERABLE_HTML).renderable is False
    checkpoint = enforcing_gate(monkeypatch)
    with pytest.raises(AdmissionReceiptError, match="receipt_rejected"):
        author_checkpoint_write(checkpoint, UNRENDERABLE_HTML)


def test_capture_and_gate_share_one_profile_selection():
    """The renderer's own admission is that same selection, not a copy of it."""

    from utils import slide_capture
    from utils.renderer_profile import select_render_profile

    captured = slide_capture._admit_render_source(RENDERER_PROFILE_HTML)
    selected = select_render_profile(RENDERER_PROFILE_HTML).require()
    assert captured.source_digest == selected.source_digest
    assert captured.node_count == selected.node_count
    assert captured.asset_digests == selected.asset_digests


def test_session_less_receipt_is_admissible_but_never_publishable(monkeypatch):
    """N2: an identity the writer never observed cannot read back as published."""

    from services.authored_checkpoint_html import checkpoint_stage_html

    checkpoint = enforcing_gate(monkeypatch)
    admitted, receipt = author_checkpoint_write(checkpoint, RENDERER_PROFILE_HTML)
    assert receipt.identity == "incomplete"
    payload = receipt.as_dict()
    assert payload["identity"] == "incomplete"
    assert payload["publishable"] is False

    class _Checkpoint:
        attempt_token = "attempt-1"
        illustrated_html = admitted
        author_html = admitted
        render_status = "completed"

        def __init__(self, admission):
            self.error = {"admission": admission}

    assert checkpoint_stage_html(_Checkpoint(payload), publishable=True) is None
    # The same bytes with a session's identity publish exactly as before.
    session_identity = dict(payload, identity="complete", publishable=True)
    assert checkpoint_stage_html(_Checkpoint(session_identity), publishable=True) == admitted
    # A receipt written before the field existed was minted under a session.
    legacy_identity = {key: value for key, value in session_identity.items() if key != "identity"}
    assert checkpoint_stage_html(_Checkpoint(legacy_identity), publishable=True) == admitted


def test_verbatim_html_is_never_admitted_without_a_session(monkeypatch):
    """N2: a verbatim deck's guarantee is its manifest, so refuse, do not default.

    The ordinary-authored column the session-less path has to assume skips the
    rendered-integrity readback, which is the only thing holding a verbatim
    deck to its source.  Admitting there would be a silent downgrade, so the
    source bindings in the bytes are refused with a code instead.
    """

    bound = gate.admit_slide_html(context(), HTML).html
    assert SOURCE_BLOCK_ATTRIBUTE in bound
    checkpoint = enforcing_gate(monkeypatch)
    with pytest.raises(AdmissionReceiptError, match="session_identity_required"):
        author_checkpoint_write(checkpoint, bound)


# --- R7/B4: one frozen rollout profile per attempt -------------------------


def test_the_five_switches_are_read_once_and_frozen(monkeypatch):
    """Every consumer reads the attempt's profile, not the live accessors."""

    from services import authored_repair_loop as loop
    from services.source_content.placeholder import prompt, text_step
    from services import authored_rollout_profile as rollout
    from utils import get_env_programme

    reads: list[str] = []
    for name in (
        "get_authored_admission_gate_mode",
        "get_authored_html_normalization",
        "is_authored_verbatim_placeholder_authoring_enabled",
        "is_authored_verbatim_single_repair_loop_enabled",
        "get_authored_verbatim_repair_loop_model_calls",
    ):
        original = getattr(get_env_programme, name)
        monkeypatch.setattr(
            rollout, name, lambda *a, _n=name, _f=original, **k: reads.append(_n) or _f(*a, **k)
        )

    profile = RolloutProfile(
        gate_mode="enforce",
        html_normalization="tree",
        placeholder_authoring=True,
        single_repair_loop=True,
        repair_loop_model_calls=2,
    )
    with rollout_profile_scope(profile):
        assert gate.placeholder_authoring_enabled() is True
        assert loop.single_repair_loop_enabled(source_reflow=True) is True
        assert loop.repair_loop_model_calls() == 2
        assert prompt.current_rollout_profile() is profile
        assert text_step.current_rollout_profile() is profile
    assert reads == []
    assert rollout.resolve_rollout_profile() == RolloutProfile()
    assert sorted(set(reads)) == sorted(
        {
            "get_authored_admission_gate_mode",
            "get_authored_html_normalization",
            "is_authored_verbatim_placeholder_authoring_enabled",
            "is_authored_verbatim_single_repair_loop_enabled",
            "get_authored_verbatim_repair_loop_model_calls",
        }
    )


@sync_async
async def test_an_attempt_resolves_its_profile_once_at_the_scope(monkeypatch):
    from services import authored_admission_session as sessions
    from services import authored_rollout_profile as rollout

    calls = []
    monkeypatch.setattr(
        sessions,
        "resolve_rollout_profile",
        lambda: calls.append(1) or RolloutProfile(gate_mode="enforce"),
    )

    @sessions.admission_scope
    async def run():
        assert sessions.current_session().mode == "enforce"
        return rollout.current_rollout_profile()

    outer = rollout.pinned_rollout_profile()
    assert (await run()).gate_mode == "enforce"
    assert calls == [1]
    # The pin is attempt-local: nothing leaks out of the attempt that made it.
    assert rollout.pinned_rollout_profile() == outer != RolloutProfile(gate_mode="enforce")


@sync_async
async def test_a_resumed_attempt_reuses_the_profile_its_job_pinned():
    """A knob flipped between attempts cannot re-represent a half-built deck."""

    from services import authored_admission_session as sessions
    from services.authored_rollout_profile import (
        ROLLOUT_PROFILE_PAYLOAD_KEY,
        RolloutProfileError,
        current_rollout_profile,
    )

    pinned = RolloutProfile(html_normalization="tree", placeholder_authoring=True)
    job = SimpleNamespace(
        request_payload={ROLLOUT_PROFILE_PAYLOAD_KEY: pinned.as_dict()},
        workspace_operation_id=None,
        attempt_token="attempt-1",
        _workspace_fencing_token=4,
    )

    @sessions.admission_scope
    async def run(generation_job):
        sessions.configure_admission(
            request=SimpleNamespace(
                _source_content_authored_reflow=True,
                _source_content_manifest=MANIFEST,
                _source_document_outline=OUTLINE,
            ),
            presentation_id="p1",
            generation_job=generation_job,
            profile=SimpleNamespace(strict_dom_gate=True),
            reference_mode="source_content_restyle",
            slide_count=1,
            bindings=None,
            image_bindings=None,
        )
        session = sessions.current_session()
        return current_rollout_profile(), session.contexts[0].policy_revision

    active, revision = await run(job)
    assert active == pinned and revision == pinned.policy_revision
    assert revision != RolloutProfile().policy_revision

    broken = SimpleNamespace(
        request_payload={ROLLOUT_PROFILE_PAYLOAD_KEY: {"gate_mode": "audit"}},
        workspace_operation_id=None,
        attempt_token="attempt-1",
        _workspace_fencing_token=4,
    )
    with pytest.raises(RolloutProfileError, match="rollout_profile_invalid"):
        await run(broken)


def test_the_single_repair_loop_is_refused_outside_an_enforcing_gate(monkeypatch):
    """One bounded repair is only meaningful when the gate's verdict is acted on.

    Under ``shadow`` the admission gate records a verdict nothing enforces, so a
    single-loop profile spends its one repair budget on advice that was never
    enforced and publishes anyway. The combination is refused where a profile is
    resolved -- from the environment and from a job's durable pin alike.
    """

    from services import authored_rollout_profile as rollout
    from services.authored_rollout_profile import (
        RolloutProfileError,
        rollout_profile_from_payload,
    )

    monkeypatch.setattr(
        rollout, "is_authored_verbatim_single_repair_loop_enabled", lambda: True
    )
    monkeypatch.setattr(rollout, "get_authored_admission_gate_mode", lambda: "shadow")
    with pytest.raises(RolloutProfileError, match="rollout_profile_invalid") as refused:
        rollout.resolve_rollout_profile()
    assert refused.value.code == "authored_rollout_profile_invalid"
    assert refused.value.retryable is False
    assert "gate_mode" in refused.value.detail

    with pytest.raises(RolloutProfileError, match="rollout_profile_invalid"):
        rollout_profile_from_payload(
            RolloutProfile(single_repair_loop=True).as_dict()
        )

    monkeypatch.setattr(rollout, "get_authored_admission_gate_mode", lambda: "enforce")
    enforced = rollout.resolve_rollout_profile()
    assert (enforced.gate_mode, enforced.single_repair_loop) == ("enforce", True)
    assert rollout_profile_from_payload(enforced.as_dict()) == enforced


def test_a_second_disagreeing_pin_is_a_coded_rejection():
    from services.authored_rollout_profile import RolloutProfileError, pin_rollout_profile

    first = RolloutProfile()
    second = RolloutProfile(gate_mode="enforce")
    with rollout_profile_scope(first):
        pin_rollout_profile(first, replacing=first)
        with pytest.raises(RolloutProfileError, match="rollout_profile_mismatch"):
            pin_rollout_profile(second, replacing=first)


def test_the_receipt_states_its_representation_and_a_real_policy_revision():
    from services.authored_admission_policy import POLICY_REVISION_BASE

    expanded = gate.admit_slide_html(context(), HTML)
    captured = gate.admit_slide_html(
        context(html_repr="captured"), expanded.html, metrics=metrics(expanded.html)
    )
    assert expanded.receipt.as_dict()["htmlRepr"] == "expanded"
    assert captured.receipt.as_dict()["htmlRepr"] == "captured"
    revision = expanded.receipt.as_dict()["policyRevision"]
    assert revision.startswith(POLICY_REVISION_BASE + "+")
    assert revision != "authored-admission/v1"
    # The rollout profile is part of the revision, so two runs under different
    # switches never mint receipts that read as one another's.
    assert RolloutProfile(html_normalization="tree").policy_revision != revision


def test_evidence_from_another_policy_revision_is_refused(monkeypatch):
    from services.authored_checkpoint_html import checkpoint_stage_html

    html = gate.admit_slide_html(context(), HTML).html
    captured = gate.admit_slide_html(
        context(html_repr="captured"), html, metrics=metrics(html), png=b"png"
    )
    kwargs = dict(
        html=html,
        stage="render",
        slide_index=0,
        attempt_token="attempt-1",
        fenced_revision=4,
        metrics=metrics(html),
        png=b"png",
    )
    require_admission_receipt(captured.receipt, **kwargs)
    stale = replace(
        captured.receipt, context=replace(captured.receipt.context, policy_revision="authored-admission/v1")
    )
    with pytest.raises(AdmissionReceiptError, match="policy_revision_mismatch"):
        require_admission_receipt(stale, **kwargs)

    checkpoint = enforcing_gate(monkeypatch)
    assert checkpoint is not None

    class _Checkpoint:
        attempt_token = "attempt-1"
        illustrated_html = html
        author_html = html
        render_status = "completed"

        def __init__(self, admission):
            self.error = {"admission": admission}

    payload = dict(captured.receipt.as_dict(), persistenceClass="publishable", publishable=True)
    assert checkpoint_stage_html(_Checkpoint(payload), publishable=True) == html
    stale_payload = dict(payload, policyRevision="authored-admission/v1")
    assert checkpoint_stage_html(_Checkpoint(stale_payload), publishable=True) is None


def test_canonical_json_normalizes_frozen_ledger_metrics():
    """The digest is the same bytes whichever stage produced the telemetry.

    Publication gates the deck evidence ledger's *retained* metrics, which that
    ledger freezes into nested ``MappingProxyType``; every other producer hands
    the gate Chromium's plain ``dict``.  ``json.dumps`` raised ``TypeError`` on
    the first, which is the uncoded ``authored_admission_internal_error`` the
    2026-09-12 live series recorded at publication on a deck that published.
    """

    from services.authored_admission_context import canonical_json
    from services.authored_evidence_ledger import _freeze

    plain = metrics(HTML)
    frozen = _freeze(plain)
    assert not isinstance(frozen, dict)
    assert canonical_json(frozen) == canonical_json(plain)

    session = AdmissionSession(mode="shadow", contexts=(context(),))
    html = gate.admit_slide_html(context(), HTML).html
    admitted = session.admit(session.context(0, captured=True), html, metrics=_freeze(metrics(html)), png=b"png")
    assert admitted.verdict == "admitted"
    assert admitted.receipt.metrics_digest == digest_html(canonical_json(metrics(html)))


def test_canonical_json_still_fails_closed_on_non_finite_telemetry():
    from services.authored_admission_context import canonical_json

    with pytest.raises(ValueError):
        canonical_json({"width": float("nan")})


def test_shadow_row_names_the_predicate_block_count_and_gate_exception():
    shadow = AdmissionShadow()
    shadow.record(
        producer="publication",
        existing_verdict="unobserved",
        existing_code="",
        gate_verdict="error",
        gate_code="authored_admission_internal_error",
        error=TypeError("Object of type mappingproxy is not JSON serializable"),
    )
    shadow.record(
        producer="render",
        existing_verdict="admitted",
        existing_code="",
        gate_verdict="rejected",
        gate_code="source_content_browser_readback_failed",
        predicate="visibility",
        block_count=2,
    )
    error_row, rejected_row = sorted(shadow.as_dict()["rows"], key=lambda row: row["producer"])
    assert error_row["gateError"] == "TypeError"
    assert re.fullmatch(r"[0-9a-f]{16}", error_row["gateErrorDigest"])
    assert rejected_row["predicate"] == "visibility" and rejected_row["blockCount"] == 2
    assert rejected_row["gateError"] == "" and rejected_row["gateErrorDigest"] == ""
    # The row must stay content-free: no source text, no attempt token.
    assert "원문" not in json.dumps(shadow.as_dict(), ensure_ascii=False)


def test_shadow_row_bounds_an_unnameable_exception_class():
    from services.authored_admission_shadow import bounded_error

    class _Bad(Exception):
        pass

    _Bad.__qualname__ = "not a class name"
    name, digest = bounded_error(_Bad("boom"))
    assert name == "authored_admission_uncoded_error" and len(digest) == 16
    assert bounded_error(None) == ("", "")


AUTHOR_FIXTURES = (
    Path(__file__).resolve().parents[4]
    / "artifacts"
    / "verbatim-design-measurements-20260911"
    / "fixtures"
)


def _author_fixtures():
    """Stored author-stage documents with their one-unit manifest and bindings."""

    if not AUTHOR_FIXTURES.is_dir():
        return []
    cases = []
    for run in sorted(AUTHOR_FIXTURES.iterdir()):
        for slide in sorted(run.iterdir()) if run.is_dir() else ():
            document = slide / "html-author.txt"
            if not document.is_file():
                continue
            manifest = json.loads((slide / "manifest.json").read_text(encoding="utf-8"))
            projection = json.loads((slide / "projection.json").read_text(encoding="utf-8"))
            blocks = {str(block.get("sourceBlockId")): block for block in manifest.get("blocks", ())}
            bindings = tuple(
                (block_id, (blocks[block_id].get("payload") or {})["text"])
                for block_id in projection
                if isinstance((blocks.get(block_id, {}).get("payload") or {}).get("text"), str)
                and (blocks[block_id]["payload"]["text"]).strip()
            )
            if bindings:
                cases.append((f"{run.name}/{slide.name}", document.read_text(encoding="utf-8"), manifest, bindings))
    return cases


def _entry_point_context(manifest, bindings):
    slide = {
        "sourceUnitId": manifest["sourceUnitId"],
        "sourceIndex": 0,
        "readingOrder": [block_id for block_id, _ in bindings],
    }
    return context_from_source(
        manifest={"units": [manifest]},
        projection={"slides": [slide]},
        presentation_id="p1",
        operation_id="op1",
        slide_index=0,
        source_unit_id=str(manifest["sourceUnitId"]),
        policy_revision=current_rollout_profile().policy_revision,
        fenced_revision=0,
        attempt_token="attempt-1",
        reference_mode="source_content_restyle",
        source_reflow=True,
        terminal_fallback_eligible=False,
        allow_illustration_slots=True,
        bindings=bindings,
    )


def _raw_and_expanded_entries_agree(html, manifest, bindings):
    """Admit one document at both author entry points, and re-admit the first.

    ``provider_raw_html`` enters at the top of ``_postprocess_slide`` in the
    ``token`` state and ``provider_expanded_html`` at the bottom in the
    ``expanded`` state.  Under enforce the raw entry also *returns* the gate's
    stamped document into the middle of that function, so the expanded entry
    re-admits bytes the gate has already bound -- the re-entrancy shadow never
    exercises.  Both are asserted here.
    """

    pinned = _entry_point_context(manifest, bindings)
    raw = gate.admit_slide_html(pinned.for_state("token"), html)
    expanded = gate.admit_slide_html(pinned.for_state("expanded"), html)
    rebound = gate.admit_slide_html(pinned.for_state("expanded"), raw.html)
    return (
        (raw.verdict, raw.code, raw.block_ids, raw.html)
        == (expanded.verdict, expanded.code, expanded.block_ids, expanded.html)
        and (rebound.verdict, rebound.code, rebound.html) == (raw.verdict, raw.code, raw.html)
    ), raw


@pytest.mark.parametrize("normalization", ["legacy", "tree"])
def test_raw_entry_point_binds_exactly_as_the_expanded_entry_point(normalization):
    """Design section 3.2 replay: the ``token`` entry adds no strictness.

    With placeholder authoring off -- the rollout default -- ``admit_slide_html``
    runs the identical steps in both states, so the raw entry cannot reject a
    document the expanded entry admits, and its stamped output is the expanded
    entry's own.  The stored run-13/14/17/18/19/23 author documents are the
    corpus; the inline document keeps the assertion running where they are not
    checked out.
    """

    with rollout_profile_scope(RolloutProfile(html_normalization=normalization)):
        agreed, raw = _raw_and_expanded_entries_agree(
            HTML, MANIFEST["units"][0], (("b1", "원문 제목"),)
        )
        assert agreed and raw.verdict == "admitted" and SOURCE_BLOCK_ATTRIBUTE in raw.html
        disagreed = [
            name
            for name, html, manifest, bindings in _author_fixtures()
            if not _raw_and_expanded_entries_agree(html, manifest, bindings)[0]
        ]
        assert disagreed == []
