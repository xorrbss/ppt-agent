"""Executable canvas-admission baseline (docs/canvas-expansion-admission-spec-20260929.md §4).

Runs the real admission functions over requests built with the real boundary
models; a payload a model rejects is a row (``422 validation:<loc>:<type>``,
plus the message for value errors). ``generationMode`` is sent only when it is
not the ``auto`` default (the v2 engine choice depends on whether it was sent).
Paths stop at canvas admission and touch neither HTTP nor a DB:

* ``sync`` - ``api/v1/ppt/endpoints/presentation_generate.py`` ``:446`` then
  ``:508-545``. Skipped ``:447-506`` (identity, content/file/reference
  admission needing a DB, n_slides bounds no axis varies).
* ``async`` - same file ``:1751-1764``. Skipped ``:1747`` (model preset).
* ``v2`` - ``services/public_v2_orchestrator.py``: wire model validation;
  ``:650`` actor context; ``:724-728`` real ``_resolve_generation_route_receipt``
  with the installed resolver (pinned design pack treated as registered, see
  ``_v2_context``); ``:733-796`` shaping copied verbatim; ``:804-811`` canvas
  admission on the raw style id. Skipped ``:700-723`` (DB; the request's
  ``slide_count`` is used) and ``:798-803`` (master pin resolution, DB).

Outcomes: ``admitted``; ``<status> <detail>`` for HTTPException and
PublicV2OrchestrationError; ``ERR <ExceptionType>: <msg>`` for anything
unmapped. ``--write`` regenerates the fixture; ``--check`` exits 1 on any
difference and prints the differing rows.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import inspect
import itertools
import json
import os
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from fastapi import HTTPException  # noqa: E402
from pydantic import ValidationError  # noqa: E402

import services.authored_vnext.generation_route as generation_route  # noqa: E402
import utils.authored_styles as style_catalogue  # noqa: E402
from models.public_v2_contracts import PresentationGenerationRequestV2  # noqa: E402
from services.public_v2_orchestration_types import PublicRequestActor, PublicV2OrchestrationError  # noqa: E402
from models.generate_presentation_request import GeneratePresentationRequest  # noqa: E402
from services.authored_canvas import validate_authored_canvas_admission  # noqa: E402
from services.authored_presentation_service import AUTHORED_TEMPLATE  # noqa: E402
from services.compatibility_migration.authored_vnext_contract import require_authored_vnext_creation_admission  # noqa: E402,E501
from services.public_generation_route import effective_generation_engine as _effective_generation_engine  # noqa: E402
from services.public_v2_orchestrator import _actor_context, _public_error, _resolve_generation_route_receipt  # noqa: E402
from services.svg_generation_admission import require_svg_creation_admission  # noqa: E402

FIXTURE_PATH = FASTAPI_ROOT / "tests" / "fixtures" / "canvas_admission_baseline.json"
SCHEMA_VERSION = "canvas-admission-baseline/v1"
EXACT_ENV_PREFIX = "AUTHORED_EXACT_"

CANVASES: dict[str, dict[str, str]] = {
    "16:9": {"aspect_ratio": "16:9"}, "9:16": {"aspect_ratio": "9:16"}, "9:13": {"aspect_ratio": "9:13"},
    "A4-L": {"page_size": "A4", "orientation": "landscape"}, "A4-P": {"page_size": "A4", "orientation": "portrait"},
    "1:1": {"aspect_ratio": "1:1"}, "21:9": {"aspect_ratio": "21:9"},
}
STYLES = ("default", "kbs-company-proposal", "osb-smart-banking", "hyundai-culture-center", "no-such-style")
# raster_reconstruction is model-valid only for this style; without it every
# raster row would be a boundary rejection and the route itself unexercised.
RASTER_EXTRA_STYLES = ("screenshot-blue",)
STRATEGIES = ("omitted", "svg", "authored")
PINS = ("free", "pack", "master")
SOURCES = ("topic", "restyle", "raster")
EXTRA_FREE_TOPIC_MODES = ("brand-style", "fidelity-first")
CALLERS = ("sync", "async", "v2")
KEY_FIELDS = ("caller", "canvas", "style", "strategy", "pins", "source", "mode")

_DIGEST = "sha256:" + "a" * 64
_HEX = "b" * 64
DESIGN_PACK_PIN = {"designPackId": "baseline-pack", "version": 1, "digest": _DIGEST}
MASTER_PIN = {"masterId": "master:baseline", "revision": 1, "digest": _DIGEST}
MANIFEST_PIN = {"manifestId": "manifest-baseline", "digest": _DIGEST}
CONTENT_POLICY = {"text": "verbatim"}
RASTER_APPROVAL_PIN = {
    "operationId": str(uuid.UUID(int=2)), "analysisId": str(uuid.UUID(int=3)), "analysisRevision": 1,
    "analysisDigest": _HEX, "sourceSha256": _HEX, "approvedRegionIds": [], "approvalDigest": _HEX,
}
_SNAPSHOT_ID = str(uuid.UUID(int=1))
_MAPPED_PREFIXES = ("document_canvas", "authored_canvas", "authored_portrait", "design_pack_canvas")


def axes() -> dict[str, Any]:
    return {
        "caller": list(CALLERS), "canvas": dict(CANVASES), "style": list(STYLES),
        "rasterExtraStyle": list(RASTER_EXTRA_STYLES), "strategy": list(STRATEGIES),
        "pins": {"free": "generationMode omitted (auto)", "pack": "registered-template + designPackPin", "master": "masterPin"},  # noqa: E501
        "source": {"topic": None, "restyle": "source_content_restyle, verbatim, pptx", "raster": "raster_reconstruction"},  # noqa: E501
        "mode": {"freeTopicExtra": list(EXTRA_FREE_TOPIC_MODES)},
    }


def combinations() -> Iterator[tuple[str, ...]]:
    for canvas, strategy, pins, source in itertools.product(CANVASES, STRATEGIES, PINS, SOURCES):
        styles = STYLES + (RASTER_EXTRA_STYLES if source == "raster" else ())
        modes: tuple[str, ...] = ("registered-template",) if pins == "pack" else ("auto",)
        if pins == "free" and source == "topic":
            modes += EXTRA_FREE_TOPIC_MODES
        for style, mode in itertools.product(styles, modes):
            yield canvas, style, strategy, pins, source, mode


def payload(caller: str, canvas: str, style: str, strategy: str, pins: str, source: str, mode: str) -> dict:
    fields: dict[str, Any] = {"content": "Canvas admission baseline", "authored_style": style, **CANVASES[canvas]}
    fields.update({"slide_count": 5} if caller == "v2" else {"n_slides": 5, "export_as": "pptx"})
    if mode != "auto":
        fields["generationMode"] = mode
    if strategy != "omitted":
        fields["strategy"] = strategy
    if pins == "pack":
        fields["designPackPin"] = dict(DESIGN_PACK_PIN)
    elif pins == "master":
        fields["masterPin"] = dict(MASTER_PIN)
    if source == "restyle":
        fields.update(sourceMode="source_content_restyle", sourceContentManifestPin=dict(MANIFEST_PIN),
                      contentPolicy=dict(CONTENT_POLICY))
    elif source == "raster":
        fields.update(sourceMode="raster_reconstruction", rasterApprovalPin=dict(RASTER_APPROVAL_PIN),
                      reference_snapshot_ids=[_SNAPSHOT_ID])
    return fields


def _validation_text(prefix: str, error: ValidationError) -> str:
    first = error.errors()[0]
    text = f"{prefix}:{'.'.join(str(part) for part in first['loc'])}:{first['type']}"
    if first["type"] == "value_error":
        text += ":" + str(first["msg"]).removeprefix("Value error, ")
    return text


def describe(error: BaseException) -> str:
    if isinstance(error, HTTPException):
        detail = error.detail
        if isinstance(detail, dict):
            detail = detail.get("code") or json.dumps(detail, sort_keys=True)
        return f"{error.status_code} {detail}"
    if isinstance(error, PublicV2OrchestrationError):
        return f"{error.status_code} {error.reason_code}"
    if isinstance(error, ValidationError):
        return _validation_text("ERR ValidationError", error)
    return f"ERR {type(error).__name__}: {error}"


def _v1_canvas_admission(request: Any) -> None:
    """presentation_generate.py:533-545 == :1752-1764 (textually identical)."""
    try:
        canvas_style_id = (
            style_catalogue.resolve_authored_style(request.authored_style).id
            if request.aspect_ratio != "16:9"
            else request.authored_style or "default"
        )
        validate_authored_canvas_admission(request, style_id=canvas_style_id)
    except ValueError as error:
        if str(error).startswith(_MAPPED_PREFIXES):
            raise HTTPException(status_code=422, detail=str(error)) from error
        raise


def run_sync(request: Any) -> None:
    """presentation_generate.py:446 then :508-545."""
    require_authored_vnext_creation_admission(request)
    if request.strategy not in {None, "authored", "svg"}:
        raise HTTPException(status_code=400, detail="generation_strategy_conflict")
    if request.generation_engine == "deck-ir":
        raise HTTPException(status_code=400, detail="generation_engine_deck_ir_retired")
    if request.strategy == "svg":
        require_svg_creation_admission(request)
    if request.aspect_ratio != "16:9" and str(request.template or "").strip().lower() != AUTHORED_TEMPLATE:
        raise HTTPException(status_code=422, detail="authored_portrait_combination_not_supported")
    request.template = AUTHORED_TEMPLATE
    _v1_canvas_admission(request)


def run_async(request: Any) -> None:
    """presentation_generate.py:1751-1764."""
    require_authored_vnext_creation_admission(request)
    _v1_canvas_admission(request)


async def run_v2(request: PresentationGenerationRequestV2) -> None:
    """public_v2_orchestrator.py:~728-779, calling the orchestrator's own request bridge."""
    from services.public_v2_request_bridge import resolve_canvas_style_id, resolve_internal_generation_request

    slide_count = request.slide_count
    receipt = await _resolve_generation_route_receipt(request, slide_count=slide_count, resolver=None)
    source_native_route = receipt.selected_route == "source-native"
    internal_request = resolve_internal_generation_request(
        request,
        slide_count=slide_count,
        reference_ids=[uuid.UUID(value) for value in request.reference_snapshot_ids],
        internal_generation_mode=(
            "registered-template" if source_native_route
            else "free-design" if request.source_mode in {"source_content_restyle", "raster_reconstruction"}
            else request.generation_mode
        ),
        internal_design_pack_pin=(
            receipt.design_pack_pin.model_dump(mode="json", by_alias=True)
            if receipt.design_pack_pin is not None else None
        ),
        effective_generation_engine="authored" if source_native_route else _effective_generation_engine(request),
    )
    try:
        validate_authored_canvas_admission(internal_request, style_id=resolve_canvas_style_id(internal_request))
    except ValueError as error:
        if str(error).startswith(_MAPPED_PREFIXES):
            raise _public_error(422, str(error)) from error
        raise


RUNNERS: dict[str, tuple[Any, Any]] = {"sync": (GeneratePresentationRequest, run_sync), "async": (
    GeneratePresentationRequest, run_async), "v2": (PresentationGenerationRequestV2, run_v2)}


async def outcome(caller: str, combo: tuple[str, ...]) -> str:
    model, runner = RUNNERS[caller]
    try:
        request = model.model_validate(payload(caller, *combo))
    except ValidationError as error:
        return _validation_text("422 validation", error)
    try:
        result = runner(request)
        if inspect.isawaitable(result):
            await result
    except Exception as error:  # every crash is a recorded finding, never hidden
        return describe(error)
    return "admitted"


@contextmanager
def _build_context() -> Iterator[None]:
    """Memoized catalogue reads plus the v2 orchestrator's actor context.

    * ``load_authored_styles`` re-parses every packaged YAML per call (~0.6 s;
      a build took 7.5 min). The catalogue is static for a run, so the YAML
      read and loaded list are memoized (callers get copies); the lookup logic
      of ``resolve_authored_style`` still runs every time. Verified identical.
    * The installed v2 route resolver needs the owner ``submit_generation``
      sets. Its ``load_pinned_design_pack`` reads the owner's pack store
      (storage state, like the master pin), so the pinned pack is treated as
      registered after the real ``normalize_design_pack_pin``; sync/async rows
      likewise assume later pin checks pass. No sync/async step reads either.
    """
    parsed: dict[str, Any] = {}
    loaded: dict[Any, list] = {}
    originals = (style_catalogue._read_style_mapping, style_catalogue.load_authored_styles,
                 generation_route.load_pinned_design_pack)

    def read(source: Path) -> Any:
        if str(source) not in parsed:
            parsed[str(source)] = originals[0](source)
        return copy.deepcopy(parsed[str(source)])

    def load(styles_directory: Any = None) -> list:
        if styles_directory not in loaded:
            loaded[styles_directory] = originals[1](styles_directory)
        return list(loaded[styles_directory])

    def registered(*, owner_id: Any, design_pack_pin: object) -> Any:
        pin = generation_route.normalize_design_pack_pin(design_pack_pin)
        return SimpleNamespace(manifest=SimpleNamespace(
            pack_id=pin["designPackId"], package_version=f"{pin['version']}.0.0", package_digest=pin["digest"]))

    actor = PublicRequestActor(owner_id=uuid.UUID(int=1), tenant_id="baseline", correlation_id="canvas-baseline")
    style_catalogue._read_style_mapping, style_catalogue.load_authored_styles = read, load
    generation_route.load_pinned_design_pack = registered
    try:
        with _actor_context(actor):
            yield
    finally:
        style_catalogue._read_style_mapping, style_catalogue.load_authored_styles = originals[:2]
        generation_route.load_pinned_design_pack = originals[2]


def build_rows() -> list[dict[str, str]]:
    async def collect() -> list[dict[str, str]]:
        return [{**dict(zip(KEY_FIELDS, (caller, *combo), strict=True)), "outcome": await outcome(caller, combo)}
                for caller in CALLERS for combo in combinations()]

    with _build_context():
        rows = asyncio.run(collect())
    return sorted(rows, key=lambda row: tuple(row[field] for field in KEY_FIELDS))


def build_document() -> dict[str, Any]:
    return {"header": {"schemaVersion": SCHEMA_VERSION, "axes": axes()}, "rows": build_rows()}


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, sort_keys=True, ensure_ascii=False, indent=1) + "\n"


def diff_documents(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    differences = ["header differs"] if expected.get("header") != actual.get("header") else []

    def keyed(document: dict[str, Any]) -> dict[tuple, str]:
        return {tuple(row[field] for field in KEY_FIELDS): row["outcome"] for row in document.get("rows", [])}

    old, new = keyed(expected), keyed(actual)
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            differences.append(f"{' | '.join(key)}: fixture={old.get(key)!r} current={new.get(key)!r}")
    return differences


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Canvas admission baseline fixture")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--write", action="store_true", help="regenerate the fixture")
    action.add_argument("--check", action="store_true", help="exit 1 if the fixture differs")
    args = parser.parse_args(argv)
    for name in [name for name in os.environ if name.startswith(EXACT_ENV_PREFIX)]:
        del os.environ[name]  # pinned environment: no private exact presets
    document = build_document()
    if args.write:
        FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE_PATH.write_text(render(document), encoding="utf-8", newline="\n")
        print(f"wrote {len(document['rows'])} rows to {FIXTURE_PATH}")
        return 0
    differences = diff_documents(json.loads(FIXTURE_PATH.read_text(encoding="utf-8")), document)
    for line in differences:
        print(line)
    print(f"{len(differences)} difference(s) across {len(document['rows'])} rows")
    return 1 if differences else 0


if __name__ == "__main__":
    raise SystemExit(main())
