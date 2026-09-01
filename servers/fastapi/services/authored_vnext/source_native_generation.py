"""Concrete provider -> Narrative IR -> source frame -> Chromium -> CAS path."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
import copy
import hashlib
import json
import logging
import math
import time
from typing import Any
import uuid

from lxml import html as lxml_html
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.workspace import WorkspaceOperation
from services.authored_checkpoint_service import (
    initialize_checkpoints,
    reserve_provider_attempt,
)
from services.authored_vnext.deck_ir.integration import DeckIrIntegrationError
from services.authored_vnext.deck_ir.product_adapter import (
    ProductDeckIrExecutionAdapter,
)
from services.authored_vnext.design_pack import RegisteredDesignPack
from services.authored_vnext.composition import (
    CompositionError,
    SealedContentUnit,
    assert_composition_plan,
    build_content_integrity_page,
    build_composition_plan,
    canonical_digest,
    sealed_units_for_slide,
)
from services.authored_vnext.composition_capacity import (
    largest_fitting_text_scale,
    source_flow_slots,
)
from services.authored_vnext.narrative_ir.content_graph import (
    ContentGraphValidationError,
    seal_content_integrity_receipt,
    seal_preserve_source_input,
    validate_content_integrity_receipt_binding,
)
from services.authored_vnext.generation_route import (
    GenerationRouteError,
    load_pinned_design_pack,
)
from services.authored_vnext.narrative_ir import run_narrative_ir_operation
from services.authored_vnext.narrative_ir.checkpoint import (
    NarrativeCheckpointError,
    NarrativeCheckpointRepository,
)
from services.authored_vnext.narrative_ir.contracts import (
    NarrativeCheckpointV1,
    NarrativeDraftV1,
    NarrativeIRV1,
    ProviderCallReservationV1,
    model_payload,
)
from services.authored_vnext.narrative_ir.frame_search import DesignPackSnapshotV1
from services.authored_vnext.preserve_source_orchestration import (
    build_preserve_checkpoint,
    build_preserve_source_plan,
    zero_call_route,
)
from services.authored_vnext.source_unitization import (
    SourceUnitizationError,
    build_manifest_source_content_admission,
    build_source_content_admission,
)
from services.authored_vnext.source_verbatim_capacity import (
    CapacityAdmissionError,
    assert_source_verbatim_capacity_admission,
    build_source_verbatim_capacity_admission,
)
from services.authored_vnext.slide_quality_acceptance import (
    seal_slide_quality_acceptance,
    validate_slide_quality_acceptance_receipt,
)
from services.database import async_session_maker
from utils.authored_dom_quality import (
    EXACT_RECONSTRUCTION,
    inspect_authored_dom,
    normalize_browser_metrics,
)
from utils.slide_capture import render_html_to_png_with_metrics


logger = logging.getLogger(__name__)
SOURCE_NATIVE_MIN_BODY_FONT_PT = 12.0


def _record_generation_stage(request: object, stage: str, elapsed_ms: int) -> None:
    recorder = getattr(request, "_authored_generation_timing_recorder", None)
    record = getattr(recorder, "record_stage_ms", None)
    if callable(record):
        record(stage, max(0, elapsed_ms))


class SourceNativeGenerationError(RuntimeError):
    code = "source_native_generation_failed"
    retryable = False

    def __init__(self, code: str | None = None) -> None:
        resolved = code or self.code
        super().__init__(resolved)
        self.code = resolved


def _digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _attempt_binding_digest(checkpoint: object) -> str:
    direct = getattr(checkpoint, "attempt_binding_digest", None)
    if isinstance(direct, str) and direct.startswith("sha256:"):
        return direct
    operation_id = getattr(checkpoint, "operation_id", None)
    job_id = getattr(checkpoint, "job_id", None)
    attempt_id = getattr(checkpoint, "attempt_id", None)
    fencing_token = getattr(checkpoint, "fencing_token", None)
    if (
        not isinstance(operation_id, uuid.UUID)
        or not isinstance(job_id, uuid.UUID)
        or not isinstance(attempt_id, str)
        or not isinstance(fencing_token, int)
    ):
        raise SourceNativeGenerationError("source_native_attempt_binding_missing")
    return canonical_digest(
        {
            "operationId": str(operation_id),
            "jobId": str(job_id),
            "attemptId": attempt_id,
            "fencingToken": fencing_token,
        }
    )


def _route_pin(route_receipt: Mapping[str, object]) -> Mapping[str, object]:
    pin = route_receipt.get("designPackPin") or route_receipt.get("design_pack_pin")
    if not isinstance(pin, Mapping):
        raise SourceNativeGenerationError("design_pack_pin_required")
    return pin


class _PostgresCheckpointRepository(NarrativeCheckpointRepository):
    def __init__(
        self,
        operation_id: uuid.UUID,
        *,
        fencing_token: int,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._operation_id = operation_id
        self._fencing_token = fencing_token
        self._session_factory = session_factory

    async def load(self, operation_id: uuid.UUID) -> NarrativeCheckpointV1 | None:
        if operation_id != self._operation_id:
            raise NarrativeCheckpointError("narrative_checkpoint_operation_mismatch")
        async with self._session_factory() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            raw = (
                operation.request_payload.get("narrative_checkpoint")
                if operation is not None and isinstance(operation.request_payload, Mapping)
                else None
            )
            return NarrativeCheckpointV1.model_validate(raw) if isinstance(raw, Mapping) else None

    async def commit(
        self,
        checkpoint: NarrativeCheckpointV1,
        *,
        expected_checkpoint_digest: str | None,
    ) -> NarrativeCheckpointV1:
        async with self._session_factory() as session:
            operation = await session.get(WorkspaceOperation, self._operation_id)
            if (
                operation is None
                or operation.state != "running"
                or operation.fencing_token != self._fencing_token
            ):
                raise NarrativeCheckpointError("narrative_checkpoint_stale_fence")
            payload = dict(operation.request_payload or {})
            current = payload.get("narrative_checkpoint")
            current_digest = (
                current.get("checkpointDigest") if isinstance(current, Mapping) else None
            )
            if current_digest != expected_checkpoint_digest:
                raise NarrativeCheckpointError("narrative_checkpoint_cas_conflict")
            payload["narrative_checkpoint"] = model_payload(checkpoint)
            operation.request_payload = payload
            session.add(operation)
            await session.commit()
        return checkpoint


async def _commit_preserve_source_checkpoint(
    *,
    operation_id: uuid.UUID,
    fencing_token: int,
    checkpoint: Mapping[str, object],
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> None:
    """Persist the provider-free authority under the active operation fence."""

    async with session_factory() as session:
        operation = await session.get(WorkspaceOperation, operation_id)
        if (
            operation is None
            or operation.state != "running"
            or operation.fencing_token != fencing_token
        ):
            raise NarrativeCheckpointError("preserve_source_checkpoint_stale_fence")
        payload = dict(operation.request_payload or {})
        existing = payload.get("preserve_source_checkpoint")
        if isinstance(existing, Mapping):
            existing_input = existing.get("preserveSourceInput")
            incoming_input = checkpoint.get("preserveSourceInput")
            if (
                not isinstance(existing_input, Mapping)
                or not isinstance(incoming_input, Mapping)
                or existing_input.get("inputDigest") != incoming_input.get("inputDigest")
            ):
                raise NarrativeCheckpointError("preserve_source_checkpoint_cas_conflict")
        payload["preserve_source_checkpoint"] = dict(checkpoint)
        operation.request_payload = payload
        session.add(operation)
        await session.commit()


def _source_json(package: RegisteredDesignPack) -> Mapping[str, object]:
    source_entries = [
        item for item in package.manifest.inventory if item.role == "source"
    ]
    if len(source_entries) != 1 or package.manifest.source_kind != "authored":
        raise SourceNativeGenerationError("source_native_authored_pack_required")
    path = package.root.joinpath(*source_entries[0].path.split("/"))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceNativeGenerationError("design_pack_source_invalid") from error
    if not isinstance(raw, Mapping):
        raise SourceNativeGenerationError("design_pack_source_invalid")
    return raw


async def _source_image_occurrence_assets(
    *, request: object, owner_id: uuid.UUID
) -> dict[str, dict[str, object]]:
    manifest = getattr(request, "_source_content_manifest", None)
    if manifest is None:
        return {}
    try:
        manifest_owner_id = uuid.UUID(str(getattr(manifest, "owner_id")))
        manifest_id = str(getattr(manifest, "manifest_id"))
    except (AttributeError, TypeError, ValueError) as error:
        raise SourceNativeGenerationError("source_content_owner_required") from error
    if manifest_owner_id != owner_id or not manifest_id:
        raise SourceNativeGenerationError("source_content_owner_required")
    from services.source_content.manifest_store import (
        load_source_content_manifest_assets,
    )

    async with async_session_maker() as session:
        sealed_assets = await load_source_content_manifest_assets(
            session,
            owner_id=owner_id,
            manifest_id=manifest_id,
        )
    occurrences: dict[str, dict[str, object]] = {}
    for source_unit in getattr(manifest, "units", ()):
        for block in getattr(source_unit, "blocks", ()):
            if getattr(block, "kind", None) != "image":
                continue
            occurrence_id = str(getattr(block, "source_block_id", "") or "")
            payload = getattr(block, "payload", {})
            asset_id = str(payload.get("assetId") or payload.get("asset_id") or "")
            declared_digest = str(
                payload.get("contentDigest") or payload.get("content_digest") or ""
            )
            sealed = sealed_assets.get(asset_id)
            if (
                not occurrence_id
                or sealed is None
                or declared_digest != str(sealed.get("digest") or "")
            ):
                raise SourceNativeGenerationError(
                    "source_content_image_occurrence_mismatch"
                )
            media_type = str(sealed.get("media_type") or "")
            content = bytes(sealed.get("content") or b"")
            if not media_type.startswith("image/") or not content:
                raise SourceNativeGenerationError(
                    "source_content_image_occurrence_mismatch"
                )
            occurrences[occurrence_id] = {
                "assetId": asset_id,
                "assetDigest": declared_digest,
                "mediaType": media_type,
                "dataUrl": (
                    f"data:{media_type};base64,"
                    + base64.b64encode(content).decode("ascii")
                ),
            }
    expected = int(getattr(getattr(manifest, "counts", None), "image_occurrences", 0))
    if len(occurrences) != expected:
        raise SourceNativeGenerationError("source_content_image_occurrence_mismatch")
    return occurrences


def _frame_semantic(index: int) -> str:
    return f"source-frame-{index + 1:03d}"


def _pack_snapshot(
    package: RegisteredDesignPack, *, source_verbatim: bool = False
) -> DesignPackSnapshotV1:
    frames = []
    for frame in package.structure.frames:
        density = {"low": "sparse", "medium": "medium", "high": "dense"}[frame.density]
        frames.append(
            {
                "frameId": frame.frame_id,
                "frameDigest": frame.source_digest,
                "sourcePartDigest": frame.source_digest,
                "sourceNative": True,
                "roles": [frame.role],
                "dataRepresentations": [
                    "none", "metric", "table", "chart", "timeline",
                    "process", "comparison", "image", "quote",
                ],
                "densities": [density],
                "frameSemantics": [_frame_semantic(frame.source_index)],
                "maxSupportingPoints": 1000,
                "supportsSourceOwnedContent": True,
                "slotContracts": (
                    source_flow_slots(frame)
                    if source_verbatim
                    else [
                        slot.model_dump(mode="json", by_alias=True)
                        for slot in frame.slots
                    ]
                ),
                "executable": getattr(frame, "editability", "source-native") == "source-native",
            }
        )
    return DesignPackSnapshotV1(
        design_pack_id=package.manifest.pack_id,
        version=int(package.manifest.package_version.split(".", 1)[0]),
        manifest_digest=package.manifest.package_digest,
        frame_registry_digest=package.structure.structure_digest,
        frames=frames,
    )


class _StructuredNarrativeProvider:
    single_physical_call = True

    def __init__(
        self,
        *,
        slides_markdown: list[str] | None,
        package: RegisteredDesignPack,
        admission_frame_ids: tuple[str, ...] = (),
    ) -> None:
        self._slides_markdown = slides_markdown or []
        self._frames = list(package.structure.frames)
        self._admission_frame_ids = admission_frame_ids

    async def generate(self, request: Mapping[str, object]) -> Mapping[str, object]:
        admitted_graph = request.get("admittedContentGraph")
        if isinstance(admitted_graph, Mapping):
            pages = admitted_graph.get("pages")
            if (
                isinstance(pages, list)
                and len(pages) == len(self._admission_frame_ids)
                and self._frames
            ):
                frames_by_id = {item.frame_id: item for item in self._frames}
                slides: list[dict[str, object]] = []
                for index, page in enumerate(pages):
                    frame = frames_by_id.get(self._admission_frame_ids[index])
                    if frame is None:
                        return {}
                    if not isinstance(page, Mapping):
                        return {}
                    units = page.get("contentUnits")
                    if not isinstance(units, list) or not units:
                        return {}
                    text_units = [
                        item
                        for item in units
                        if isinstance(item, Mapping)
                        and isinstance(item.get("payload"), Mapping)
                    ]
                    texts = [
                        str((item.get("payload") or {}).get("text") or "").strip()
                        for item in text_units
                    ]
                    texts = [item for item in texts if item]
                    if not texts:
                        return {}
                    title_units = [
                        item
                        for item in text_units
                        if ":title-candidate-" in str(item.get("contentUnitId") or "")
                    ]
                    if len(title_units) != 1:
                        return {}
                    title = str(
                        (title_units[0].get("payload") or {}).get("text") or ""
                    ).strip()
                    if not title:
                        return {}
                    title_id = str(title_units[0].get("contentUnitId") or "")
                    supporting = [
                        str((item.get("payload") or {}).get("text") or "").strip()
                        for item in text_units
                        if str(item.get("contentUnitId") or "") != title_id
                        and str((item.get("payload") or {}).get("text") or "").strip()
                    ]
                    density = {
                        "low": "sparse",
                        "medium": "medium",
                        "high": "dense",
                    }[frame.density]
                    slides.append(
                        {
                            "slideKey": f"slide-{index + 1:03d}",
                            "sequenceIndex": index,
                            "role": frame.role,
                            "keyMessage": title,
                            "supportingPoints": supporting[:24],
                            "dataRepresentation": "none",
                            "data": [],
                            "sourceOwnership": [],
                            "frameSemantics": [_frame_semantic(frame.source_index)],
                            "density": density,
                            "contentUnitIds": [],
                        }
                    )
                source_order = admitted_graph.get("sourceUnitOrder")
                return {
                    "schemaVersion": "narrative-ir-draft/v1",
                    "title": slides[0]["keyMessage"],
                    "language": str(request.get("language") or "ko-KR")[:64],
                    "slides": slides,
                    "sourceUnitOrder": (
                        source_order if isinstance(source_order, list) else []
                    ),
                }

        from llmai import get_client
        from llmai.shared import JSONSchemaResponse, SystemMessage, UserMessage
        from utils.llm_config import enforce_llm_egress_policy, get_llm_config
        from utils.llm_provider import get_model
        from utils.llm_utils import extract_structured_content, get_generate_kwargs
        from utils.schema_utils import prepare_schema_for_validation

        schema = prepare_schema_for_validation(
            NarrativeDraftV1.model_json_schema(), strict=False
        )
        frame_assignments = [
            {
                "sequenceIndex": index,
                "role": frame.role,
                "frameSemantic": _frame_semantic(index),
                "density": {"low": "sparse", "medium": "medium", "high": "dense"}[frame.density],
            }
            for index, frame in enumerate(self._frames)
        ]
        source_slides = self._slides_markdown
        messages = (
            SystemMessage(
                content=(
                    "Return only Narrative IR draft JSON. Own meaning, story order, key messages, "
                    "supporting points and data semantics only. Never output HTML, CSS, JavaScript, "
                    "URLs, coordinates, positioning, renderer decisions or safety policy. Return "
                    "exactly slideCount slides in sequence. Use concise Korean business language."
                )
            ),
            UserMessage(
                content=json.dumps(
                    {
                        "task": dict(request),
                        "sourceSlides": source_slides,
                        "serverFrameAssignments": frame_assignments,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            ),
        )
        enforce_llm_egress_policy()
        response_format = JSONSchemaResponse(
            name="narrative_ir_draft",
            json_schema=schema,
            strict=False,
        )
        response = await asyncio.to_thread(
            get_client(config=get_llm_config()).generate,
            **get_generate_kwargs(get_model(), messages, response_format=response_format),
        )
        raw: object = response if isinstance(response, Mapping) else extract_structured_content(
            getattr(response, "content", None)
        )
        if not isinstance(raw, Mapping):
            return {}
        slides = raw.get("slides")
        if not isinstance(slides, list) or len(slides) != len(self._frames):
            return dict(raw)
        normalized_slides: list[dict[str, object]] = []
        for index, (item, frame) in enumerate(zip(slides, self._frames, strict=True)):
            if not isinstance(item, Mapping):
                return dict(raw)
            density = {"low": "sparse", "medium": "medium", "high": "dense"}[frame.density]
            supporting = item.get("supportingPoints") or item.get("supporting_points") or []
            if not isinstance(supporting, list):
                supporting = []
            key_message = item.get("keyMessage") or item.get("key_message")
            if not isinstance(key_message, str) or not key_message.strip():
                key_message = (
                    self._slides_markdown[index][:400]
                    if index < len(self._slides_markdown)
                    else f"핵심 메시지 {index + 1}"
                )
            data_representation = item.get("dataRepresentation") or item.get("data_representation") or "none"
            data = item.get("data") or []
            source_ownership = item.get("sourceOwnership") or item.get("source_ownership") or []
            normalized_slides.append(
                {
                    "slideKey": f"slide-{index + 1:03d}",
                    "sequenceIndex": index,
                    "role": frame.role,
                    "keyMessage": key_message.strip()[:4000],
                    "supportingPoints": [str(value).strip()[:4000] for value in supporting[:24] if str(value).strip()],
                    "dataRepresentation": data_representation,
                    "data": data if isinstance(data, list) else [],
                    "sourceOwnership": source_ownership if isinstance(source_ownership, list) else [],
                    "frameSemantics": [_frame_semantic(index)],
                    "density": density,
                }
            )
        return {
            "schemaVersion": "narrative-ir-draft/v1",
            "title": str(raw.get("title") or "Presentation")[:4000],
            "language": str(raw.get("language") or request.get("language") or "ko-KR")[:64],
            "slides": normalized_slides,
            "sourceUnitOrder": raw.get("sourceUnitOrder") or raw.get("source_unit_order") or [],
        }

def _protected_dom_digest(document: object, editable_qids: set[str]) -> str:
    protected: list[dict[str, str]] = []
    for node in document.xpath("//*[@data-qid]"):
        qid = str(node.get("data-qid") or "").strip()
        inside_editable_scope = any(
            str(ancestor.get("data-qid") or "").strip() in editable_qids
            for ancestor in node.iterancestors()
        )
        if qid and qid not in editable_qids and not inside_editable_scope:
            clone = copy.deepcopy(node)
            for editable in clone.xpath(".//*[@data-qid]"):
                editable_qid = str(editable.get("data-qid") or "").strip()
                if editable_qid not in editable_qids:
                    continue
                sentinel = lxml_html.Element("source-native-editable-slot")
                sentinel.set("data-qid", editable_qid)
                sentinel.tail = editable.tail
                parent = editable.getparent()
                if parent is not None:
                    parent.replace(editable, sentinel)
            protected.append(
                {
                    "qid": qid,
                    "html": lxml_html.tostring(clone, encoding="unicode", method="html"),
                }
            )
    return canonical_digest(protected)


def _unit_render_text(unit: SealedContentUnit) -> str:
    if unit.text.strip():
        return " ".join(unit.text.split())
    if unit.data not in (None, [], {}):
        return json.dumps(unit.data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ""


def _flow_mapping(slot: Mapping[str, object], key: str) -> Mapping[str, object]:
    value = slot.get(key)
    if isinstance(value, Mapping):
        return value
    raw = slot.get("raw")
    if isinstance(raw, Mapping):
        value = raw.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _flow_number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _flow_role(slot: Mapping[str, object]) -> str:
    return str(
        slot.get("sourceFlowRole")
        or slot.get("semanticRole")
        or "body"
    ).strip().lower()


def _flow_font_px(
    slot: Mapping[str, object], *, role: str, adaptive_scale: float
) -> float:
    style = _flow_mapping(slot, "textStyle")
    declared = _flow_number(style.get("fontSize"), 0.0)
    title_like = role in {"title", "headline", "heading"}
    # CSS pixels convert to points at 0.75pt/px. Dense verbatim source pages
    # retain a 12pt floor while title defaults preserve a clear hierarchy.
    default = 40.0 if title_like else 22.0
    minimum = 32.0 if title_like else (SOURCE_NATIVE_MIN_BODY_FONT_PT / 0.75)
    resolved = max(minimum, max(minimum, declared or default) * adaptive_scale)
    if resolved < minimum - 0.01:
        raise SourceNativeGenerationError("source_native_text_readability_failed")
    return resolved


def _flow_text_css(
    slot: Mapping[str, object], *, role: str, adaptive_scale: float
) -> str:
    style = _flow_mapping(slot, "textStyle")
    font_px = _flow_font_px(slot, role=role, adaptive_scale=adaptive_scale)
    family = str(
        style.get("eastAsianFont")
        or style.get("latinFont")
        or "Arial, sans-serif"
    ).replace("'", "")
    weight = max(100, min(1000, int(_flow_number(style.get("fontWeight"), 700 if role == "title" else 500))))
    alignment = str(style.get("alignment") or "left")
    if alignment not in {"left", "center", "right", "justify"}:
        alignment = "left"
    declared_color = str(style.get("color") or "").strip()
    # Authored Design Packs can intentionally leave a slot color unspecified
    # so that it inherits the local panel/container foreground.  Supplying a
    # dark fallback here breaks that contract on retained dark chrome.  Only a
    # sealed canonical color may override the container's computed color.
    color = declared_color if declared_color.startswith("#") else "inherit"
    return (
        f"font-family:'{family}';font-size:{font_px:.2f}px;"
        f"font-weight:{weight};line-height:{1.18 if role == 'title' else 1.32};"
        f"text-align:{alignment};color:{color};letter-spacing:-0.01em"
    )


def _flow_overlay_css(slot: Mapping[str, object]) -> str:
    bounds = _flow_mapping(slot, "bounds")
    spacing = _flow_mapping(slot, "spacing")
    x = _flow_number(bounds.get("x"))
    y = _flow_number(bounds.get("y"))
    width = _flow_number(bounds.get("width"))
    height = _flow_number(bounds.get("height"))
    if width <= 0 or height <= 0:
        raise SourceNativeGenerationError("source_native_flow_geometry_invalid")
    inset_left = max(0.0, _flow_number(spacing.get("insetLeft")))
    inset_top = max(0.0, _flow_number(spacing.get("insetTop")))
    inset_right = max(0.0, _flow_number(spacing.get("insetRight")))
    inset_bottom = max(0.0, _flow_number(spacing.get("insetBottom")))
    surface = str(slot.get("sourceFlowSurface") or "")
    background = (
        "background:inherit!important;color:inherit!important;"
        if surface == "container-inherited"
        else "background:transparent!important;"
    )
    return (
        "position:absolute!important;box-sizing:border-box!important;"
        f"left:{x:.3f}px!important;top:{y:.3f}px!important;"
        f"width:{width:.3f}px!important;height:{height:.3f}px!important;"
        f"padding:{inset_top:.3f}px {inset_right:.3f}px {inset_bottom:.3f}px {inset_left:.3f}px!important;"
        f"margin:0!important;{background}"
        "min-width:0!important;min-height:0!important;overflow:hidden!important"
    )


def _unit_boundary_kind(unit: SealedContentUnit) -> str:
    unit_id = unit.content_unit_id
    for kind in ("title-candidate", "table-cell", "paragraph", "bullet"):
        if f":{kind}-" in unit_id:
            return kind
    return "paragraph"


def _append_flow_text_units(
    parent: object,
    units: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
) -> None:
    index = 0
    while index < len(units):
        unit = units[index]
        boundary = _unit_boundary_kind(unit)
        if boundary == "table-cell":
            end = index + 1
            while end < len(units) and _unit_boundary_kind(units[end]) == "table-cell":
                end += 1
            cells = units[index:end]
            grid = lxml_html.Element("div")
            grid.set("data-qid", f"source-flow-table:{cells[0].content_unit_id}")
            columns = max(2, min(4, math.ceil(len(cells) ** 0.5)))
            grid.set(
                "style",
                "display:grid!important;break-inside:avoid;margin:0 0 8px;"
                f"grid-template-columns:repeat({columns},minmax(0,1fr));gap:4px 10px",
            )
            for cell in cells:
                child = lxml_html.Element("span")
                child.text = _unit_render_text(cell)
                child.set("data-qid", f"source-flow:{cell.content_unit_id}")
                child.set("data-source-flow-unit-id", cell.content_unit_id)
                child.set("data-source-boundary-kind", "table-cell")
                child.set("data-quality-unintentional", "true")
                child.set(
                    "style",
                    "display:block;min-width:0;padding:3px 6px;"
                    "border-left:2px solid rgba(30,82,155,.35);"
                    "overflow-wrap:break-word;word-break:keep-all;"
                    + _flow_text_css(slot, role=role, adaptive_scale=adaptive_scale),
                )
                grid.append(child)
            parent.append(grid)
            index = end
            continue
        unit_role = "title" if boundary == "title-candidate" or role == "title" else role
        child = lxml_html.Element("h2" if unit_role == "title" else "p")
        child.text = _unit_render_text(unit)
        child.set("data-qid", f"source-flow:{unit.content_unit_id}")
        child.set("data-source-flow-unit-id", unit.content_unit_id)
        child.set("data-source-boundary-kind", boundary)
        child.set("data-quality-unintentional", "true")
        child.set(
            "style",
            f"margin:0 0 {10.0 if unit_role == 'title' else 6.0:.1f}px;"
            "break-inside:avoid;overflow-wrap:break-word;word-break:keep-all;"
            + _flow_text_css(slot, role=unit_role, adaptive_scale=adaptive_scale),
        )
        parent.append(child)
        index += 1


def _append_fixed_text_columns(
    parent: object,
    units: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
    columns_count: int,
    column_gap: float,
) -> None:
    """Keep atomic source units inside an explicit, bounded column grid."""

    blocks: list[list[SealedContentUnit]] = []
    index = 0
    while index < len(units):
        end = index + 1
        if _unit_boundary_kind(units[index]) == "table-cell":
            while end < len(units) and _unit_boundary_kind(units[end]) == "table-cell":
                end += 1
        blocks.append(units[index:end])
        index = end
    resolved_columns = max(1, min(columns_count, len(blocks)))
    parent.set(
        "style",
        "display:grid!important;width:100%;height:100%;min-width:0;min-height:0;"
        f"grid-template-columns:repeat({resolved_columns},minmax(0,1fr));"
        f"column-gap:{column_gap:.3f}px;overflow:hidden",
    )
    minimum, extra = divmod(len(blocks), resolved_columns)
    cursor = 0
    parent_qid = str(parent.get("data-qid") or "source-flow-columns")
    for column_index in range(resolved_columns):
        block_count = minimum + (1 if column_index < extra else 0)
        selected_blocks = blocks[cursor : cursor + block_count]
        cursor += block_count
        column = lxml_html.Element("div")
        column.set("data-qid", f"{parent_qid}--column-{column_index + 1:02d}")
        column.set(
            "style",
            "display:flex!important;flex-direction:column;min-width:0;min-height:0;"
            "overflow:hidden",
        )
        _append_flow_text_units(
            column,
            [unit for block in selected_blocks for unit in block],
            slot=slot,
            adaptive_scale=adaptive_scale,
            role=role,
        )
        parent.append(column)


def _patch_exact_slot(
    node: object,
    units: list[SealedContentUnit],
    *,
    slot: Mapping[str, object] | None = None,
    image_assets: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    if not units:
        node.text = None
        for child in node.iterdescendants():
            child.text = None
            child.tail = None
        for attribute in ("src", "srcset", "href", "poster"):
            node.attrib.pop(attribute, None)
        node.set("aria-hidden", "true")
        node.set("data-source-native-empty", "true")
        node.set("style", "display:none!important")
        node.set("data-content-unit-ids", "")
        node.set("data-content-unit-digests", "")
        return
    image_units = [item for item in units if item.kind == "image"]
    text_units = [item for item in units if item.kind != "image"]
    if image_units and text_units:
        if slot is None or str(slot.get("sourceFlowLayout") or "") != "text":
            raise SourceNativeGenerationError("source_native_mixed_image_slot")
        adaptive_scale = largest_fitting_text_scale(slot, text_units)
        if adaptive_scale is None:
            raise SourceNativeGenerationError("source_native_text_readability_failed")
        assets = image_assets or {}
        for child in list(node):
            node.remove(child)
        node.text = None
        wrapper = lxml_html.Element("div")
        wrapper.set("data-qid", f"{str(node.get('data-qid') or 'source-flow')}--mixed")
        text_weight = max(1.0, sum(len(_unit_render_text(item)) for item in text_units) / 220.0)
        image_weight = max(1.0, float(len(image_units)))
        image_fraction = max(0.28, min(0.48, image_weight / (text_weight + image_weight)))
        wrapper.set(
            "style",
            "display:grid!important;height:100%;width:100%;min-width:0;min-height:0;overflow:hidden;"
            f"grid-template-columns:minmax(0,{(1.0 - image_fraction):.4f}fr) minmax(0,{image_fraction:.4f}fr);"
            "column-gap:14px",
        )
        text_panel = lxml_html.Element("div")
        text_panel.set(
            "data-qid",
            f"{str(node.get('data-qid') or 'source-flow')}--mixed-text",
        )
        hints = _flow_mapping(slot, "layoutHints")
        text_columns = max(1, min(2, int(_flow_number(hints.get("columnCount"), 1))))
        role = _flow_role(slot)
        _append_fixed_text_columns(
            text_panel,
            text_units,
            slot=slot,
            adaptive_scale=adaptive_scale,
            role=role,
            columns_count=text_columns,
            column_gap=14.0,
        )
        image_panel = lxml_html.Element("div")
        image_panel.set(
            "data-qid",
            f"{str(node.get('data-qid') or 'source-flow')}--mixed-images",
        )
        image_columns = max(1, min(3, math.ceil(len(image_units) ** 0.5)))
        image_rows = max(1, math.ceil(len(image_units) / image_columns))
        image_panel.set(
            "style",
            "display:grid!important;min-width:0;min-height:0;overflow:hidden;"
            f"grid-template-columns:repeat({image_columns},minmax(0,1fr));"
            f"grid-template-rows:repeat({image_rows},minmax(0,1fr));gap:8px",
        )
        for index, unit in enumerate(image_units):
            occurrence_id = unit.asset_occurrence_id or ""
            asset = assets.get(occurrence_id)
            if (
                not occurrence_id
                or asset is None
                or str(asset.get("assetDigest") or "") != unit.asset_digest
            ):
                raise SourceNativeGenerationError("source_content_image_occurrence_mismatch")
            image = lxml_html.Element("img")
            image.set("src", str(asset["dataUrl"]))
            image.set("alt", "")
            image.set("data-qid", f"source-flow-image:{occurrence_id}")
            image.set("data-source-block-id", occurrence_id)
            image.set("data-asset-digest", str(asset["assetDigest"]))
            image.set("data-source-occurrence-index", str(index))
            image.set(
                "style",
                "display:block;width:100%;height:100%;min-width:0;min-height:0;"
                "object-fit:contain;overflow:hidden",
            )
            image_panel.append(image)
        wrapper.extend((text_panel, image_panel))
        node.append(wrapper)
        existing_style = str(node.get("style") or "").rstrip(";")
        node.set(
            "style",
            f"{existing_style};{_flow_overlay_css(slot)};display:block!important;"
            "white-space:normal!important;z-index:50!important",
        )
        node.set("data-asset-occurrence-ids", "|".join(item.asset_occurrence_id or "" for item in image_units))
        node.set("data-content-unit-ids", "|".join(item.content_unit_id for item in units))
        node.set("data-content-unit-digests", "|".join(item.content_digest for item in units))
        return
    if image_units:
        assets = image_assets or {}
        tag = str(getattr(node, "tag", "")).lower()
        if tag == "img" and len(units) != 1:
            raise SourceNativeGenerationError("source_native_image_slot_capacity_exceeded")
        for child in list(node):
            node.remove(child)
        node.text = None
        if tag != "img":
            existing_style = str(node.get("style") or "").rstrip(";")
            hints = _flow_mapping(slot or {}, "layoutHints")
            columns = max(
                1,
                min(
                    len(units),
                    int(_flow_number(hints.get("columnCount"), min(2, len(units))))
                ),
            )
            rows = max(
                1,
                int(_flow_number(hints.get("rowCount"), 1)),
                (len(units) + columns - 1) // columns,
            )
            column_gap = max(0.0, _flow_number(hints.get("columnGap"), 8.0))
            row_gap = max(0.0, _flow_number(hints.get("rowGap"), 8.0))
            grid_style = (
                "display:grid!important;"
                f"grid-template-columns:repeat({columns},minmax(0,1fr));"
                f"grid-template-rows:repeat({rows},minmax(0,1fr));"
                f"column-gap:{column_gap:.3f}px;row-gap:{row_gap:.3f}px;overflow:hidden"
            )
            node.set(
                "style",
                f"{existing_style};{grid_style}" if existing_style else grid_style,
            )
        for index, unit in enumerate(units):
            occurrence_id = unit.asset_occurrence_id or ""
            asset = assets.get(occurrence_id)
            if (
                not occurrence_id
                or asset is None
                or str(asset.get("assetDigest") or "") != unit.asset_digest
            ):
                raise SourceNativeGenerationError(
                    "source_content_image_occurrence_mismatch"
                )
            image = node if tag == "img" else lxml_html.Element("img")
            image.set("src", str(asset["dataUrl"]))
            image.set("alt", "")
            image.set("data-source-block-id", occurrence_id)
            image.set("data-asset-digest", str(asset["assetDigest"]))
            image.set("data-source-occurrence-index", str(index))
            if image is not node:
                slot_qid = str(node.get("data-qid") or "source-image")
                image.set("data-qid", f"{slot_qid}--occurrence-{index + 1:03d}")
            image.set(
                "style",
                "display:block;width:100%;height:100%;min-width:0;min-height:0;"
                "object-fit:contain;overflow:hidden",
            )
            if image is not node:
                node.append(image)
        node.attrib.pop("hidden", None)
        node.attrib.pop("data-source-native-empty", None)
        if node.get("aria-hidden") == "true":
            node.attrib.pop("aria-hidden", None)
        node.set(
            "data-asset-occurrence-ids",
            "|".join(item.asset_occurrence_id or "" for item in units),
        )
        node.set("data-content-unit-ids", "|".join(item.content_unit_id for item in units))
        node.set("data-content-unit-digests", "|".join(item.content_digest for item in units))
        return
    texts = [_unit_render_text(item) for item in units]
    rendered = "\n".join(item for item in texts if item)
    if not rendered:
        raise SourceNativeGenerationError("source_native_empty_content_unit")
    if slot is None:
        raise SourceNativeGenerationError("source_native_slot_contract_missing")
    adaptive_scale = largest_fitting_text_scale(slot, units)
    if adaptive_scale is None:
        raise SourceNativeGenerationError("source_native_text_capacity_exceeded")
    source_flow = str((slot or {}).get("sourceFlowLayout") or "")
    # Preserve the editable element tree while clearing all template copy in
    # that exact content scope. Nested slots are applied parent-first later.
    node.text = None
    for child in node.iterdescendants():
        child.text = None
        child.tail = None
    if source_flow == "text":
        for child in list(node):
            node.remove(child)
        role = _flow_role(slot)
        hints = _flow_mapping(slot, "layoutHints")
        columns_count = max(1, min(4, int(_flow_number(hints.get("columnCount"), 1))))
        column_gap = max(0.0, _flow_number(hints.get("columnGap"), 18.0))
        columns = lxml_html.Element("div")
        flow_qid = str(node.get("data-qid") or "source-flow")
        columns.set("data-qid", f"{flow_qid}--columns")
        _append_fixed_text_columns(
            columns,
            units,
            slot=slot,
            adaptive_scale=adaptive_scale,
            role=role,
            columns_count=columns_count,
            column_gap=column_gap,
        )
        node.append(columns)
    else:
        node.text = rendered
    if adaptive_scale < 0.999 and source_flow != "text":
        existing_style = str(node.get("style") or "").rstrip(";")
        adaptive_style = (
            f"font-size:{adaptive_scale * 100:.3f}%!important;"
            "line-height:1.15!important;overflow:hidden!important"
        )
        node.set(
            "style",
            f"{existing_style};{adaptive_style}"
            if existing_style
            else adaptive_style,
        )
    if source_flow == "text":
        existing_style = str(node.get("style") or "").rstrip(";")
        node.set(
            "style",
            f"{existing_style};{_flow_overlay_css(slot)};display:block!important;"
            "white-space:normal!important;z-index:50!important",
        )
    node.set("data-content-unit-ids", "|".join(item.content_unit_id for item in units))
    node.set("data-content-unit-digests", "|".join(item.content_digest for item in units))


def _apply_composition_plan(
    raw_html: str,
    *,
    plan: Mapping[str, object],
    units: list[SealedContentUnit],
    slots: list[dict[str, object]],
    image_assets: Mapping[str, Mapping[str, object]] | None = None,
) -> tuple[str, list[dict[str, object]], str, str]:
    try:
        document = lxml_html.document_fromstring(raw_html)
    except (ValueError, TypeError) as error:
        raise SourceNativeGenerationError("design_pack_html_invalid") from error
    by_id = {item.content_unit_id: item for item in units}
    by_slot: dict[str, list[SealedContentUnit]] = {}
    for assignment in plan.get("assignments", ()):
        if not isinstance(assignment, Mapping):
            continue
        unit = by_id.get(str(assignment.get("contentUnitId") or ""))
        if unit is not None:
            by_slot.setdefault(str(assignment.get("slotId") or ""), []).append(unit)
    editable_qids = {
        str(slot.get("patchHandle", {}).get("elementId") or "")
        for slot in slots
        if isinstance(slot.get("patchHandle"), Mapping)
    }
    editable_qids.discard("")
    # Source-flow anchors are isolated overlays rooted at body. Remove only the
    # registry-declared editable qids from the retained template tree so hidden
    # placeholder images cannot enter Chromium quality telemetry. Non-editable
    # template chrome remains available to the protected-DOM digest.
    body_nodes = document.xpath("//body")
    body = body_nodes[0] if body_nodes else document
    source_flow_enabled = any(slot.get("sourceFlowLayout") for slot in slots)
    if source_flow_enabled:
        for qid in editable_qids:
            originals = document.xpath("//*[@data-qid=$qid]", qid=qid)
            if len(originals) != 1:
                raise SourceNativeGenerationError("design_pack_patch_handle_not_exact")
            original = originals[0]
            original.attrib.pop("data-qid", None)
            original.set("aria-hidden", "true")
            original.set("style", "display:none!important")
            original.text = None
            for template_node in (original, *original.iterdescendants()):
                template_node.text = None
                template_node.tail = None
                for attribute in ("src", "srcset", "href", "poster"):
                    template_node.attrib.pop(attribute, None)
    for slot in slots:
        flow_kind = str(slot.get("sourceFlowLayout") or "")
        if flow_kind not in {"text", "images"}:
            continue
        handle = slot.get("patchHandle")
        handle = handle if isinstance(handle, Mapping) else {}
        qid = str(handle.get("elementId") or "")
        overlay = lxml_html.Element("section")
        overlay.set("data-qid", qid)
        overlay.set("data-semantic-role", f"source-flow-{flow_kind}")
        overlay.set(
            "style",
            _flow_overlay_css(slot)
            + (";z-index:51!important" if flow_kind == "images" else ";z-index:50!important"),
        )
        body.append(overlay)
    protected_before = _protected_dom_digest(document, editable_qids)
    semantic: list[dict[str, object]] = []
    targets: list[tuple[int, str, Mapping[str, object], object, list[SealedContentUnit]]] = []
    for slot in slots:
        if source_flow_enabled and not slot.get("sourceFlowLayout"):
            continue
        slot_id = str(slot.get("slotId") or "")
        assigned = by_slot.get(slot_id, [])
        handle = slot.get("patchHandle")
        if not isinstance(handle, Mapping) or handle.get("kind") not in {"authored-qid", "scene-element"}:
            raise SourceNativeGenerationError("design_pack_patch_handle_invalid")
        qid = str(handle.get("elementId") or "")
        nodes = document.xpath("//*[@data-qid=$qid]", qid=qid)
        if len(nodes) != 1:
            raise SourceNativeGenerationError("design_pack_patch_handle_not_exact")
        depth = sum(1 for _ in nodes[0].iterancestors())
        targets.append((depth, slot_id, slot, nodes[0], assigned))
    for _, slot_id, slot, node, assigned in sorted(
        targets, key=lambda item: (item[0], item[1])
    ):
        _patch_exact_slot(
            node,
            assigned,
            slot=slot,
            image_assets=image_assets,
        )
        if not assigned:
            continue
        handle = slot["patchHandle"]
        qid = str(handle.get("elementId") or "")
        semantic.append(
            {
                "qid": qid,
                "kind": "text" if all(item.kind in {"text", "title", "body", "quote"} for item in assigned) else assigned[0].kind,
                "semanticRole": slot.get("semanticRole"),
                "text": "\n".join(_unit_render_text(item) for item in assigned),
                "contentUnitIds": [item.content_unit_id for item in assigned],
                "contentDigests": [item.content_digest for item in assigned],
                "slotId": slot_id,
            }
        )
    serialized = lxml_html.tostring(document, encoding="unicode", method="html")
    protected_after = _protected_dom_digest(document, editable_qids)
    if protected_before != protected_after:
        raise SourceNativeGenerationError("source_native_protected_element_changed")
    if not semantic:
        raise SourceNativeGenerationError("source_native_composition_empty")
    return serialized, semantic, protected_before, protected_after


def _assert_source_image_readback(
    *,
    rendered_html: str,
    units: list[SealedContentUnit],
    browser_metrics: Mapping[str, object],
) -> None:
    expected = {
        item.asset_occurrence_id: item.asset_digest
        for item in units
        if item.asset_occurrence_id and item.asset_digest
    }
    if not expected:
        return
    document = lxml_html.document_fromstring(rendered_html)
    observed_dom: dict[str, str] = {}
    for node in document.xpath("//img[@data-source-block-id]"):
        occurrence_id = str(node.get("data-source-block-id") or "")
        digest = str(node.get("data-asset-digest") or "")
        if occurrence_id in observed_dom:
            raise SourceNativeGenerationError(
                "source_content_image_occurrence_duplicate"
            )
        if not str(node.get("src") or "").startswith("data:image/"):
            raise SourceNativeGenerationError(
                "source_content_image_occurrence_mismatch"
            )
        observed_dom[occurrence_id] = digest
    if observed_dom != expected:
        raise SourceNativeGenerationError("source_content_image_occurrence_mismatch")
    raw_elements = browser_metrics.get("elements")
    if not isinstance(raw_elements, list):
        raise SourceNativeGenerationError("source_native_browser_metrics_invalid")
    observed_metrics: dict[str, Mapping[str, object]] = {}
    for item in raw_elements:
        if not isinstance(item, Mapping):
            continue
        occurrence_id = str(item.get("source_block_id") or "")
        if occurrence_id:
            if occurrence_id in observed_metrics:
                raise SourceNativeGenerationError(
                    "source_content_image_occurrence_duplicate"
                )
            observed_metrics[occurrence_id] = item
    if set(observed_metrics) != set(expected):
        raise SourceNativeGenerationError("source_content_image_occurrence_mismatch")
    for item in observed_metrics.values():
        image = item.get("image")
        if not isinstance(image, Mapping):
            raise SourceNativeGenerationError("source_content_image_readback_failed")
        if (
            image.get("complete") is not True
            or int(image.get("naturalWidth") or image.get("natural_width") or 0) <= 0
            or int(image.get("naturalHeight") or image.get("natural_height") or 0) <= 0
            or item.get("css_visible") is False
            or float(item.get("effective_opacity", 1) or 0) <= 0
            or float(item.get("viewport_intersection_ratio", 0) or 0) < 0.999
            or bool(
                item.get("clipped")
                or item.get("glyph_clipped")
                or item.get("line_box_clipped")
            )
        ):
            raise SourceNativeGenerationError("source_content_image_readback_failed")


def _quality_bbox(value: object) -> dict[str, float]:
    raw = value if isinstance(value, Mapping) else {}
    return {
        "x": _flow_number(raw.get("x")),
        "y": _flow_number(raw.get("y")),
        "width": max(0.0, _flow_number(raw.get("width"))),
        "height": max(0.0, _flow_number(raw.get("height"))),
    }


def _bbox_area(value: Mapping[str, float]) -> float:
    return max(0.0, value["width"]) * max(0.0, value["height"])


def _geometry_fidelity(
    expected: Mapping[str, float], observed: Mapping[str, float]
) -> float:
    width = max(1.0, expected["width"])
    height = max(1.0, expected["height"])
    error = (
        abs(expected["x"] - observed["x"]) / width
        + abs(expected["y"] - observed["y"]) / height
        + abs(expected["width"] - observed["width"]) / width
        + abs(expected["height"] - observed["height"]) / height
    ) / 4.0
    return max(0.0, min(1.0, 1.0 - error))


def _centroid_score(
    expected: list[Mapping[str, float]],
    observed: list[Mapping[str, float]],
    *,
    viewport_width: float,
    viewport_height: float,
) -> float:
    def centroid(boxes: list[Mapping[str, float]]) -> tuple[float, float] | None:
        weighted = [(_bbox_area(item), item) for item in boxes if _bbox_area(item) > 0]
        total = sum(weight for weight, _ in weighted)
        if total <= 0:
            return None
        return (
            sum(weight * (item["x"] + item["width"] / 2.0) for weight, item in weighted) / total,
            sum(weight * (item["y"] + item["height"] / 2.0) for weight, item in weighted) / total,
        )

    expected_center = centroid(expected)
    observed_center = centroid(observed)
    if expected_center is None or observed_center is None:
        return 0.0
    dx = (expected_center[0] - observed_center[0]) / max(1.0, viewport_width)
    dy = (expected_center[1] - observed_center[1]) / max(1.0, viewport_height)
    return max(0.0, min(1.0, 1.0 - (dx * dx + dy * dy) ** 0.5 * 2.0))


def _build_slide_quality_observation(
    *,
    slide_index: int,
    slide_digest: str,
    normalized_metrics: Mapping[str, object],
    slots: list[dict[str, object]],
    report: object,
    frame_role: str,
    expected_protected_digest: str,
    observed_protected_digest: str,
) -> dict[str, object]:
    raw_elements = normalized_metrics.get("elements")
    elements = [item for item in raw_elements if isinstance(item, Mapping)] if isinstance(raw_elements, list) else []
    viewport = normalized_metrics.get("viewport")
    viewport = viewport if isinstance(viewport, Mapping) else {}
    viewport_width = max(1.0, _flow_number(viewport.get("width"), 1280.0))
    viewport_height = max(1.0, _flow_number(viewport.get("height"), 720.0))
    slide_area = viewport_width * viewport_height
    by_qid = {
        str(item.get("qid") or ""): item
        for item in elements
        if str(item.get("qid") or "")
    }
    candidate_slots = [
        item
        for item in slots
        if item.get("sourceFlowLayout") in {"text", "images"}
        and item.get("acceptedKinds")
    ]
    # Every registered flow region is rendered so its QID remains measurable,
    # but unassigned regions are deliberately hidden with a zero-size browser
    # box.  Template fidelity is about occupied source regions; averaging those
    # empty regions in made an exact overlay look like a 4/7 match.
    active_slots: list[dict[str, object]] = []
    for item in candidate_slots:
        handle = item.get("patchHandle")
        handle = handle if isinstance(handle, Mapping) else {}
        metric = by_qid.get(str(handle.get("elementId") or ""))
        if metric is None:
            continue
        if metric.get("css_visible") is False or _bbox_area(_quality_bbox(metric.get("bbox"))) <= 0:
            continue
        active_slots.append(item)
    expected_boxes: list[Mapping[str, float]] = []
    observed_boxes: list[Mapping[str, float]] = []
    geometry_scores: list[float] = []
    matched_slots = 0
    for slot in active_slots:
        handle = slot.get("patchHandle")
        handle = handle if isinstance(handle, Mapping) else {}
        qid = str(handle.get("elementId") or "")
        expected = _quality_bbox(slot.get("bounds"))
        expected_boxes.append(expected)
        metric = by_qid.get(qid)
        if metric is None:
            geometry_scores.append(0.0)
            continue
        observed = _quality_bbox(metric.get("bbox"))
        observed_boxes.append(observed)
        matched_slots += 1
        geometry_scores.append(_geometry_fidelity(expected, observed))

    unit_elements = [
        item
        for item in elements
        if str(item.get("qid") or "").startswith("source-flow:")
    ]
    title_elements = [
        item
        for item in unit_elements
        if ":title-candidate-" in str(item.get("qid") or "")
    ]
    body_elements = [item for item in unit_elements if item not in title_elements]
    body_fonts_pt = [
        _flow_number(item.get("font_size")) * 0.75
        for item in body_elements
        if _flow_number(item.get("font_size")) > 0
    ]
    title_fonts_pt = [
        _flow_number(item.get("font_size")) * 0.75
        for item in title_elements
        if _flow_number(item.get("font_size")) > 0
    ]
    minimum_body_font_pt = min(body_fonts_pt) if body_fonts_pt else SOURCE_NATIVE_MIN_BODY_FONT_PT
    font_score = min(1.0, minimum_body_font_pt / SOURCE_NATIVE_MIN_BODY_FONT_PT)
    contrast_scores = [
        min(1.0, _flow_number(item.get("contrast_ratio"), 4.5) / 4.5)
        for item in unit_elements
        if item.get("contrast_ratio") is not None
    ]
    contrast_score = min(contrast_scores) if contrast_scores else 1.0
    issues = list(getattr(report, "issues", ()) or ())
    actionable_issues = [
        issue
        for issue in issues
        if str(getattr(issue, "severity", "")).lower() in {"high", "medium"}
    ]
    # Raw owned-paint probes include transparent flow ancestors and fragmented
    # column boxes.  The canonical DOM inspector resolves those relationships;
    # only its actionable occlusion/overlap finding is a readability defect.
    occlusion_score = 0.0 if any(
        any(token in str(getattr(issue, "category", "")).lower() for token in ("occlusion", "overlap"))
        for issue in actionable_issues
    ) else 1.0
    readability = max(0.0, min(1.0, (font_score + contrast_score + occlusion_score) / 3.0))
    title_font = max(title_fonts_pt) if title_fonts_pt else 0.0
    body_font = sum(body_fonts_pt) / len(body_fonts_pt) if body_fonts_pt else SOURCE_NATIVE_MIN_BODY_FONT_PT
    hierarchy = min(1.0, title_font / max(1.0, body_font * 1.35)) if title_elements else 0.0
    clipped_count = sum(
        1
        for issue in actionable_issues
        if "clip" in str(getattr(issue, "category", "")).lower()
    )
    off_canvas_count = sum(
        1
        for issue in actionable_issues
        if any(
            token in str(getattr(issue, "category", "")).lower()
            for token in ("off_canvas", "off-canvas", "outside_canvas")
        )
    )
    wrapping_count = sum(
        1
        for issue in actionable_issues
        if any(
            token in str(getattr(issue, "category", "")).lower()
            for token in ("wrap", "overflow")
        )
    )
    overlap_count = sum(
        1
        for issue in actionable_issues
        if "overlap" in str(getattr(issue, "category", "")).lower()
    )

    content_boxes = [_quality_bbox(item.get("bbox")) for item in unit_elements]
    balance_score = _centroid_score(
        expected_boxes,
        content_boxes or observed_boxes,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
    )
    occupied = min(slide_area, sum(_bbox_area(item) for item in content_boxes))
    occupied_ratio = occupied / slide_area
    sparse_allowed = str(frame_role).lower() in {"cover", "section", "closing"}
    lower = 0.07 if sparse_allowed else 0.14
    upper = 0.72
    if occupied_ratio < lower:
        whitespace_score = occupied_ratio / lower
    elif occupied_ratio > upper:
        whitespace_score = max(0.0, (1.0 - occupied_ratio) / (1.0 - upper))
    else:
        whitespace_score = 1.0

    image_elements = [item for item in elements if str(item.get("source_block_id") or "")]
    image_size_scores: list[float] = []
    image_crop_scores: list[float] = []
    undersized = 0
    bad_crop = 0
    slot_boxes_by_qid = {
        str((slot.get("patchHandle") or {}).get("elementId") or ""): _quality_bbox(
            by_qid[str((slot.get("patchHandle") or {}).get("elementId") or "")].get("bbox")
        )
        for slot in active_slots
        if isinstance(slot.get("patchHandle"), Mapping)
        and str((slot.get("patchHandle") or {}).get("elementId") or "") in by_qid
    }
    image_group_boxes: dict[str, Mapping[str, float]] = {}
    images_by_slot: dict[str, list[Mapping[str, object]]] = {}
    for item in image_elements:
        parent_qid = str(item.get("parent_qid") or "")
        parent_metric = by_qid.get(parent_qid)
        if parent_metric is not None:
            parent_box = _quality_bbox(parent_metric.get("bbox"))
            if _bbox_area(parent_box) > 0:
                image_group_boxes[parent_qid] = parent_box
                images_by_slot.setdefault(parent_qid, []).append(item)
                continue
        candidates = [
            qid
            for qid in slot_boxes_by_qid
            if parent_qid == qid or parent_qid.startswith(qid + "--")
        ]
        slot_qid = max(candidates, key=len) if candidates else ""
        images_by_slot.setdefault(slot_qid, []).append(item)
    for item in image_elements:
        parent_qid = str(item.get("parent_qid") or "")
        if parent_qid in image_group_boxes:
            slot_qid = parent_qid
        else:
            candidates = [
                qid
                for qid in slot_boxes_by_qid
                if parent_qid == qid or parent_qid.startswith(qid + "--")
            ]
            slot_qid = max(candidates, key=len) if candidates else ""
        group = images_by_slot.get(slot_qid, [item])
        group_box = image_group_boxes.get(
            slot_qid,
            slot_boxes_by_qid.get(
                slot_qid,
                {"x": 0.0, "y": 0.0, "width": viewport_width, "height": viewport_height},
            ),
        )
        group_area = _bbox_area(group_box)
        fair_cell_area = group_area / max(1, len(group))
        relative_cell_share = _bbox_area(_quality_bbox(item.get("bbox"))) / max(1.0, fair_cell_area)
        # Mixed text/image regions intentionally reserve 28-48% of their slot
        # for images.  A source occurrence using at least a quarter of its fair
        # cell is meaningfully visible; below one eighth is a hard undersize.
        image_size_scores.append(min(1.0, relative_cell_share / 0.25))
        if relative_cell_share < 0.125:
            undersized += 1
        image = item.get("image")
        image = image if isinstance(image, Mapping) else {}
        crop_ok = bool(
            image.get("complete") is True
            and _flow_number(image.get("naturalWidth")) > 0
            and _flow_number(image.get("naturalHeight")) > 0
            and _flow_number(item.get("viewport_intersection_ratio"), 0.0) >= 0.999
        )
        image_crop_scores.append(1.0 if crop_ok else 0.0)
        if not crop_ok:
            bad_crop += 1

    geometry_score = sum(geometry_scores) / len(geometry_scores) if geometry_scores else 0.0
    slot_score = matched_slots / len(active_slots) if active_slots else 0.0
    return {
        "slideIndex": slide_index,
        "slideDigest": slide_digest,
        "browserObserved": True,
        "browserEngine": "chromium",
        "viewport": {"width": viewport_width, "height": viewport_height},
        "readabilityScore": round(readability, 6),
        "titleBodyHierarchyScore": round(hierarchy, 6),
        "bodyTextElementCount": len(body_elements),
        "minimumBodyFontPt": round(minimum_body_font_pt, 4),
        "templateMinimumBodyFontPt": SOURCE_NATIVE_MIN_BODY_FONT_PT,
        "wrappingViolationCount": wrapping_count,
        "clippedElementCount": clipped_count,
        "prohibitedOverlapCount": overlap_count,
        "offCanvasElementCount": off_canvas_count,
        "balanceScore": round(balance_score, 6),
        "whitespaceScore": round(whitespace_score, 6),
        "meaningfulImageCount": len(image_elements),
        "meaningfulImageSizeScore": round(sum(image_size_scores) / len(image_size_scores), 6) if image_size_scores else 1.0,
        "imageCropFidelityScore": round(sum(image_crop_scores) / len(image_crop_scores), 6) if image_crop_scores else 1.0,
        "undersizedMeaningfulImageCount": undersized,
        "badImageCropCount": bad_crop,
        "templateSlotFidelityScore": round(slot_score, 6),
        "templateGeometryFidelityScore": round(geometry_score, 6),
        "expectedProtectedChromeDigest": expected_protected_digest,
        "observedProtectedChromeDigest": observed_protected_digest,
    }


def _complete_semantic_elements(
    assigned: list[dict[str, object]],
    browser_metrics: Mapping[str, object],
) -> list[dict[str, object]]:
    """Bind every declared QID while retaining Content Graph meaning."""

    assigned_by_qid = {
        str(item.get("qid") or ""): item for item in assigned if item.get("qid")
    }
    elements = browser_metrics.get("elements")
    if not isinstance(elements, list):
        raise SourceNativeGenerationError("source_native_browser_metrics_invalid")
    complete: list[dict[str, object]] = []
    observed: set[str] = set()
    for item in elements:
        if not isinstance(item, Mapping):
            continue
        qid = str(item.get("qid") or "").strip()
        if not qid or qid in observed:
            continue
        observed.add(qid)
        bound = assigned_by_qid.get(qid)
        if bound is not None:
            complete.append(dict(bound))
            continue
        owned_text = " ".join(str(item.get("owned_text") or "").split())
        semantic_role = str(item.get("semantic_role") or "").strip()
        tag = str(item.get("tag") or "").lower()
        if tag in {"html", "body", "script", "style"}:
            # Renderer instrumentation is injected under body during capture;
            # it is neither authored visible copy nor semantic content.
            owned_text = ""
        if owned_text:
            if not semantic_role:
                semantic_role = (
                    "title" if tag in {"h1", "h2", "h3", "h4"} else "body"
                )
            complete.append(
                {
                    "qid": qid,
                    "kind": "text",
                    "semanticRole": semantic_role,
                    "text": owned_text,
                    "contentUnitIds": [],
                    "contentDigests": [],
                    "slotId": None,
                }
            )
        else:
            complete.append(
                {
                    "qid": qid,
                    "kind": "shape",
                    "semanticRole": semantic_role or "decoration",
                    "text": "",
                    "contentUnitIds": [],
                    "contentDigests": [],
                    "slotId": None,
                }
            )
    if set(assigned_by_qid) - observed or not complete:
        raise SourceNativeGenerationError("authored_semantic_qid_mismatch")
    return complete


def _replace_frame_text(
    raw_html: str,
    slide: object,
    *,
    frame: object | None = None,
    narrative: object | None = None,
) -> tuple[str, list[dict[str, object]]]:
    """Compatibility wrapper using semantic qid handles, never DOM ordering."""

    if frame is None:
        document = lxml_html.document_fromstring(raw_html)
        qids = [str(node.get("data-qid")) for node in document.xpath("//*[@data-qid]")]
        slots = []
        for qid in qids:
            role = "title" if any(token in qid.lower() for token in ("title", "headline", "heading")) else "body"
            slots.append(
                {
                    "slotId": f"compat:{qid}",
                    "semanticRole": role,
                    "acceptedKinds": ["text"],
                    "required": False,
                    "capacityEnvelope": {"maxCharacters": 10000, "maxLines": 1000, "contentArea": 10_000_000},
                    "patchHandle": {"kind": "authored-qid", "elementId": qid},
                    "extractionConfidence": 1.0,
                    "bounds": {"width": 1000, "height": 500},
                }
            )
        class _CompatFrame:
            frame_id = "compat-frame"
            source_digest = canonical_digest(raw_html)
            pass
        _CompatFrame.slots = slots
        frame = _CompatFrame()
    if narrative is None:
        class _CompatNarrative:
            sealed_content_units: tuple[object, ...] = ()
            content_graph_digest = canonical_digest(str(getattr(slide, "slide_key", "slide")))
        narrative = _CompatNarrative()
    plan, units, slots = build_composition_plan(narrative=narrative, slide=slide, frame=frame)
    try:
        assert_composition_plan(plan)
    except CompositionError as error:
        raise SourceNativeGenerationError(error.code) from error
    html, semantic, _, _ = _apply_composition_plan(raw_html, plan=plan, units=units, slots=slots)
    return html, semantic


class _SourceNativeExecutor:
    execution_mode = "source-native"

    def __init__(
        self,
        *,
        package: RegisteredDesignPack,
        request: object,
        generation_job: object,
        context: object,
        public_route_receipt: Mapping[str, object],
        capacity_admission_receipt: Mapping[str, object] | None = None,
    ) -> None:
        self._package = package
        self._request = request
        self._generation_job = generation_job
        self._context = context
        self._public_route_receipt = public_route_receipt
        self._capacity_admission_receipt = dict(capacity_admission_receipt or {})

    async def execute(self, *, narrative: object, selection: object, route: object, checkpoint: object) -> Mapping[str, object]:
        source = _source_json(self._package)
        source_frames = {
            f"frame:{item.get('id')}": item
            for item in source.get("frames", [])
            if isinstance(item, Mapping) and item.get("id")
        }
        frame_contracts = {
            item.frame_id: item for item in self._package.structure.frames
        }
        captures: dict[str, object] = {}
        compiled: list[dict[str, object]] = []
        page_evidence: list[dict[str, object]] = []
        composition_plans: list[dict[str, object]] = []
        integrity_pages: list[dict[str, object]] = []
        quality_observations: list[dict[str, object]] = []
        source_text_occurrences: list[dict[str, str]] = []
        source_image_occurrences: list[dict[str, str]] = []
        selected_slides = getattr(selection, "slides")
        admitted_frame_ids = [
            str(item.get("selectedFrameId") or "")
            for item in self._capacity_admission_receipt.get("pages", ())
            if isinstance(item, Mapping)
        ]
        selected_frame_ids = [
            str(getattr(item, "selected_frame_id", "") or "")
            for item in selected_slides
        ]
        if admitted_frame_ids and selected_frame_ids != admitted_frame_ids:
            raise SourceNativeGenerationError(
                "source_verbatim_capacity_frame_binding_mismatch"
            )
        claim = getattr(self._context, "claim")
        owner_id = getattr(claim, "owner_id", None)
        if not isinstance(owner_id, uuid.UUID):
            raise SourceNativeGenerationError("source_native_operation_binding_invalid")
        image_assets = await _source_image_occurrence_assets(
            request=self._request,
            owner_id=owner_id,
        )
        author_elapsed_ms = 0
        render_elapsed_ms = 0
        quality_elapsed_ms = 0
        narrative_slides = tuple(getattr(narrative, "slides", ()))
        narrative_digest = getattr(narrative, "narrative_digest", None)
        generation_lane = str(getattr(narrative, "generation_lane", "") or "")
        operation_id = getattr(narrative, "operation_id", None)
        if generation_lane not in {"preserve-source", "new-authoring"} or not isinstance(
            operation_id, uuid.UUID
        ):
            raise SourceNativeGenerationError("source_native_generation_lane_invalid")
        for narrative_slide, selected in zip(narrative_slides, selected_slides, strict=True):
            author_started_at = time.monotonic()
            frame = source_frames.get(selected.selected_frame_id)
            frame_contract = frame_contracts.get(selected.selected_frame_id)
            if (
                not isinstance(frame, Mapping)
                or not isinstance(frame.get("html"), str)
                or frame_contract is None
            ):
                raise SourceNativeGenerationError("source_native_frame_payload_missing")
            try:
                plan, units, slots = build_composition_plan(
                    narrative=narrative,
                    slide=narrative_slide,
                    frame=frame_contract,
                )
                assert_composition_plan(plan)
            except CompositionError as error:
                raise SourceNativeGenerationError(error.code) from error
            for unit in units:
                occurrence = {
                    "sourceBlockId": unit.asset_occurrence_id or unit.content_unit_id,
                    "contentDigest": unit.asset_digest or unit.content_digest,
                }
                if unit.asset_occurrence_id:
                    source_image_occurrences.append(occurrence)
                else:
                    source_text_occurrences.append(occurrence)
            html, semantic, protected_before, protected_after = _apply_composition_plan(
                str(frame["html"]),
                plan=plan,
                units=units,
                slots=slots,
                image_assets=image_assets,
            )
            author_elapsed_ms += round((time.monotonic() - author_started_at) * 1000)
            render_started_at = time.monotonic()
            capture = await render_html_to_png_with_metrics(html)
            render_elapsed_ms += round((time.monotonic() - render_started_at) * 1000)
            quality_started_at = time.monotonic()
            normalized = normalize_browser_metrics(dict(capture.browser_metrics or {}))
            _assert_source_image_readback(
                rendered_html=html,
                units=units,
                browser_metrics=normalized,
            )
            integrity_page = build_content_integrity_page(
                plan=plan,
                units=units,
                rendered_html=html,
                browser_metrics=normalized,
                protected_digest_before=protected_before,
                protected_digest_after=protected_after,
            )
            if (
                integrity_page["unmappedRequiredUnits"]
                or integrity_page["duplicateRequiredUnits"]
                or integrity_page["transformedRequiredUnits"]
                or integrity_page["hiddenRequiredUnits"]
                or integrity_page["offCanvasRequiredUnits"]
                or integrity_page["pageOrderError"]
                or integrity_page["expectedContentUnitIds"] != integrity_page["boundContentUnitIds"]
                or integrity_page["expectedContentUnitIds"] != integrity_page["renderedContentUnitIds"]
                or integrity_page["expectedDigest"] != integrity_page["boundDigest"]
                or integrity_page["expectedDigest"] != integrity_page["renderedDigest"]
                or integrity_page["expectedAssetOccurrences"] != integrity_page["renderedAssetOccurrences"]
            ):
                logger.warning(
                    "Source-native content integrity gate failed slide=%s details=%s",
                    narrative_slide.slide_key,
                    integrity_page,
                )
                raise SourceNativeGenerationError("content_integrity_failed")
            report = inspect_authored_dom(
                html,
                dict(capture.browser_metrics or {}),
                reference_mode=EXACT_RECONSTRUCTION,
                strict_structural_off_canvas=True,
            )
            # Visual evaluation is strictly downstream from content integrity.
            # Muted source contrast is retained as review evidence, while
            # structural and content defects remain blocking.
            blocking = [
                issue
                for issue in report.issues
                if issue.severity in {"high", "medium"}
                and issue.category != "contrast"
            ]
            if (
                not capture.png
                or normalized["viewport"] != {"width": 1280, "height": 720}
                or not normalized["fonts_ready"]
                or report.telemetry_status != "available"
                or blocking
            ):
                logger.warning(
                    "Source-native Chromium quality gate failed slide=%s viewport=%s fonts_ready=%s telemetry=%s issues=%s",
                    narrative_slide.slide_key,
                    normalized.get("viewport"),
                    normalized.get("fonts_ready"),
                    report.telemetry_status,
                    [
                        issue.to_dict()
                        for issue in blocking[:20]
                    ],
                )
                raise SourceNativeGenerationError("source_native_browser_quality_failed")
            quality_observations.append(
                _build_slide_quality_observation(
                    slide_index=int(narrative_slide.sequence_index),
                    slide_digest=str(capture.png_digest),
                    normalized_metrics=normalized,
                    slots=slots,
                    report=report,
                    frame_role=str(getattr(frame_contract, "role", "content") or "content"),
                    expected_protected_digest=protected_before,
                    observed_protected_digest=protected_after,
                )
            )
            semantic = _complete_semantic_elements(
                semantic, dict(capture.browser_metrics or {})
            )
            captures[narrative_slide.slide_key] = capture
            composition_plans.append(plan)
            integrity_pages.append(integrity_page)
            compiled.append(
                {
                    "slideKey": narrative_slide.slide_key,
                    "html": html,
                    "semantic": {"elements": semantic},
                    "sourceFrameDigest": selected.selected_frame_digest,
                    "compositionPlanDigest": plan["planDigest"],
                }
            )
            page_evidence.append(
                {
                    "slideKey": narrative_slide.slide_key,
                    "frameId": selected.selected_frame_id,
                    "htmlDigest": capture.html_digest,
                    "pngDigest": capture.png_digest,
                    "metricsDigest": capture.metrics_digest,
                    "blockingIssueCount": 0,
                }
            )
            quality_elapsed_ms += round((time.monotonic() - quality_started_at) * 1000)
        _record_generation_stage(self._request, "author", author_elapsed_ms)
        _record_generation_stage(self._request, "render", render_elapsed_ms)
        _record_generation_stage(self._request, "quality", quality_elapsed_ms)
        content_graph_digest = str(
            getattr(narrative, "content_graph_digest", None)
            or _digest(
                [
                    item.payload()
                    for slide in narrative_slides
                    for item in sealed_units_for_slide(narrative, slide)
                ]
            )
        )
        deck_ir = {
            "schemaVersion": "source-native-publication-deck/v1",
            "title": narrative.title,
            "language": narrative.language,
            "story": {"arc": [slide.role for slide in narrative_slides]},
            "designLanguage": {
                "grammarId": "source-native",
                "paletteId": self._package.structure.structure_digest,
                "typeScaleId": self._package.structure.structure_digest,
            },
            "slides": [
                {
                    "slideKey": slide.slide_key,
                    "role": slide.role,
                    "claim": slide.key_message,
                    "components": [
                        {
                            "elementKey": f"content-{slide.sequence_index + 1}",
                            "kind": "text",
                            "payload": {"text": slide.key_message},
                        }
                    ],
                }
                for slide in narrative_slides
            ],
        }
        quality_subject_digest = canonical_digest(
            {
                "schemaVersion": "source-native-quality-subject/v1",
                "designPackStructureDigest": self._package.structure.structure_digest,
                "contentGraphDigest": content_graph_digest,
                "compiledSlides": [
                    {
                        "slideKey": item["slideKey"],
                        "sourceFrameDigest": item["sourceFrameDigest"],
                        "compositionPlanDigest": item["compositionPlanDigest"],
                    }
                    for item in compiled
                ],
                "pageEvidenceDigest": _digest(page_evidence),
            }
        )
        quality_model = seal_slide_quality_acceptance(
            subject_digest=quality_subject_digest,
            browser_observations=quality_observations,
            expected_slide_count=len(narrative_slides),
            slide_score_threshold=90.0,
            minimum_body_font_pt=SOURCE_NATIVE_MIN_BODY_FONT_PT,
        )
        if (
            not quality_model.document_quality_passed
            or not validate_slide_quality_acceptance_receipt(
                quality_model,
                expected_subject_digest=quality_subject_digest,
                expected_observation_set_digest=quality_model.observation_set_digest,
            )
        ):
            logger.warning(
                "Source-native document quality gate failed slides=%s blockers=%s minimum=%s",
                list(quality_model.failing_slide_indexes),
                list(quality_model.blocker_codes),
                quality_model.minimum_slide_score,
            )
            logger.warning(
                "Source-native document quality diagnostics=%s",
                [
                    {
                        "slideIndex": decision.slide_index,
                        "score": decision.score,
                        "components": dict(decision.component_scores),
                        "issues": [issue.code for issue in decision.issues],
                        "observation": quality_observations[decision.slide_index],
                    }
                    for decision in quality_model.slides
                    if not decision.passed
                ],
            )
            raise SourceNativeGenerationError("source_native_document_quality_failed")
        quality = quality_model.to_dict()
        manifest = getattr(self._request, "_source_content_manifest", None)
        manifest_units = tuple(getattr(manifest, "units", ()) or ())
        manifest_blocks = [
            block
            for source_unit in manifest_units
            for block in tuple(getattr(source_unit, "blocks", ()) or ())
        ]
        excluded_counts = {
            "tables": sum(
                1 for item in manifest_blocks if str(getattr(item, "kind", "")) == "table"
            ),
            "charts": sum(
                1 for item in manifest_blocks if str(getattr(item, "kind", "")) == "chart"
            ),
            "original-layout": len(manifest_units),
        }
        source_inventory_unsigned = {
            "schemaVersion": "source-content-text-image-inventory/v1",
            "sourceMode": "text-image",
            "sourceCompleteClaim": False,
            "preserved": ["text", "image-occurrences"],
            "excluded": ["tables", "charts", "original-layout"],
            "excludedSourceObjectCounts": excluded_counts,
            "textOccurrences": source_text_occurrences,
            "imageOccurrences": source_image_occurrences,
        }
        source_content_quality_evidence = {
            **source_inventory_unsigned,
            "textOccurrenceCount": len(source_text_occurrences),
            "imageOccurrenceCount": len(source_image_occurrences),
            "occurrenceInventoryDigest": canonical_digest(source_inventory_unsigned),
            "status": "passed",
            "required": True,
        }
        source_revision = getattr(claim, "expected_revision", None)
        route_receipt_digest = self._public_route_receipt.get("receiptDigest")
        if (
            not isinstance(source_revision, int)
            or not isinstance(route_receipt_digest, str)
        ):
            raise SourceNativeGenerationError("source_native_operation_binding_invalid")
        sealed_result_revision = source_revision + 1
        provider_call_budget = getattr(route, "provider_call_budget")
        provider_call_budget_digest = canonical_digest(
            model_payload(provider_call_budget)
        )
        attempt_binding_digest = _attempt_binding_digest(checkpoint)
        preserve_source_input_digest = getattr(
            narrative, "preserve_source_input_digest", None
        )
        try:
            integrity_model = seal_content_integrity_receipt(
                operation_id=operation_id,
                attempt_binding_digest=attempt_binding_digest,
                source_revision=source_revision,
                result_revision=sealed_result_revision,
                generation_lane=generation_lane,
                content_graph_digest=content_graph_digest,
                route_strategy_receipt_digest=route_receipt_digest,
                narrative_digest=narrative_digest,
                preserve_source_input_digest=preserve_source_input_digest,
                provider_call_budget=provider_call_budget,
                pages=integrity_pages,
            )
            integrity_model = validate_content_integrity_receipt_binding(
                integrity_model,
                operation_id=operation_id,
                attempt_binding_digest=attempt_binding_digest,
                source_revision=source_revision,
                result_revision=sealed_result_revision,
                generation_lane=generation_lane,
                content_graph_digest=content_graph_digest,
                route_strategy_receipt_digest=route_receipt_digest,
                narrative_digest=narrative_digest,
                preserve_source_input_digest=preserve_source_input_digest,
                provider_call_budget_digest=provider_call_budget_digest,
            )
        except ContentGraphValidationError as error:
            raise SourceNativeGenerationError(error.code) from error
        integrity = model_payload(integrity_model)
        payload = {
            "compiledSlides": compiled,
            "deckIr": deck_ir,
            "generationQualityReceipt": quality,
            "contentIntegrityReceipt": integrity,
            "sourceContentQualityEvidence": source_content_quality_evidence,
            "compositionPlans": composition_plans,
            "sourceVerbatimCapacityAdmission": self._capacity_admission_receipt,
            "strategyReceipt": dict(self._public_route_receipt),
            "styleProfileDigest": self._package.structure.structure_digest,
            "retainedReferenceDigest": self._package.manifest.source_digest,
            "requirementCoverageReceipt": {"status": "passed"},
            "executionEvidence": {
                "route": "source-native",
                "generationLane": generation_lane,
                "narrativeDigest": narrative_digest,
                "preserveSourceInputDigest": getattr(
                    narrative, "preserve_source_input_digest", None
                ),
                "checkpointDigest": getattr(checkpoint, "checkpoint_digest"),
                "frameSelectionReceiptDigest": getattr(selection, "receipt_digest"),
                "sourceVerbatimCapacityAdmissionDigest": self._capacity_admission_receipt.get(
                    "capacityAdmissionDigest"
                ),
                "providerCallBudget": model_payload(getattr(route, "provider_call_budget")),
            },
            "publicationRoute": "source-native",
        }
        publication_started_at = time.monotonic()
        publication = await ProductDeckIrExecutionAdapter()._publish(
            payload=payload,
            request=self._request,
            generation_job=self._generation_job,
            context=self._context,
            captures=captures,
        )
        _record_generation_stage(
            self._request,
            "publication",
            round((time.monotonic() - publication_started_at) * 1000),
        )
        result_revision = publication.get("revision")
        if result_revision != sealed_result_revision:
            raise SourceNativeGenerationError("source_native_operation_binding_invalid")
        try:
            validate_content_integrity_receipt_binding(
                integrity,
                operation_id=operation_id,
                attempt_binding_digest=attempt_binding_digest,
                source_revision=source_revision,
                result_revision=sealed_result_revision,
                generation_lane=generation_lane,
                content_graph_digest=content_graph_digest,
                route_strategy_receipt_digest=route_receipt_digest,
                narrative_digest=narrative_digest,
                preserve_source_input_digest=preserve_source_input_digest,
                provider_call_budget_digest=provider_call_budget_digest,
            )
        except ContentGraphValidationError as error:
            raise SourceNativeGenerationError(error.code) from error
        return {
            "publication": dict(publication),
            "generationQualityReceipt": quality,
            "contentIntegrityReceipt": integrity,
            "generationLane": generation_lane,
            "narrativeIrDigest": narrative_digest,
            "preserveSourceInputDigest": getattr(
                narrative, "preserve_source_input_digest", None
            ),
            "frameSelectionReceiptDigest": getattr(selection, "receipt_digest"),
        }


class _FallbackExecutor:
    execution_mode = "server-owned-authored-scene"
    provider_html_enabled = False

    async def execute(self, **_: object) -> Mapping[str, object]:
        raise SourceNativeGenerationError("source_native_whole_operation_fallback_required")


async def run_source_native_generation(
    *,
    request: object,
    generation_job: object,
    context: object,
    route_receipt: Mapping[str, object],
) -> Mapping[str, object]:
    outline_started_at = time.monotonic()
    claim = getattr(context, "claim")
    operation_id = getattr(claim, "operation_id", None)
    owner_id = getattr(claim, "owner_id", None)
    attempt_token = getattr(claim, "attempt_token", None)
    fencing_token = getattr(claim, "fencing_token", None)
    job_id = getattr(generation_job, "id", None)
    if (
        not all(isinstance(item, uuid.UUID) for item in (operation_id, owner_id, job_id))
        or not isinstance(attempt_token, str)
        or not isinstance(fencing_token, int)
    ):
        raise SourceNativeGenerationError("source_native_operation_binding_invalid")
    try:
        package = load_pinned_design_pack(
            owner_id=owner_id,
            design_pack_pin=_route_pin(route_receipt),
        )
    except GenerationRouteError as error:
        raise SourceNativeGenerationError(error.code) from error
    slide_count = getattr(request, "n_slides", None)
    if (
        not isinstance(slide_count, int)
        or slide_count <= 0
        or not package.structure.frames
    ):
        raise SourceNativeGenerationError("design_pack_slide_count_mismatch")

    brief = str(getattr(request, "content", "") or "")
    source_mode = getattr(request, "source_mode", None)
    try:
        if source_mode == "source_content_restyle":
            manifest = getattr(request, "_source_content_manifest", None)
            outline = getattr(request, "_source_document_outline", None)
            if manifest is None or outline is None:
                raise SourceUnitizationError("source_content_manifest_invalid")
            admission = build_manifest_source_content_admission(
                manifest=manifest,
                outline=outline,
                frames=package.structure.frames,
                expected_slide_count=slide_count,
            )
        else:
            admission = build_source_content_admission(
                brief=brief,
                slide_count=slide_count,
                frames=package.structure.frames,
            )
    except SourceUnitizationError as error:
        raise SourceNativeGenerationError(error.code) from error
    admitted_content_graph = admission.content_graph if admission is not None else None
    capacity_receipt: Mapping[str, object] | None = None
    admission_frame_ids: tuple[str, ...] = ()
    if admitted_content_graph is not None:
        capacity_receipt = build_source_verbatim_capacity_admission(
            content_graph=admitted_content_graph,
            frames=package.structure.frames,
            frame_structure_digest=package.structure.structure_digest,
            expected_slide_count=slide_count,
        )
        try:
            assert_source_verbatim_capacity_admission(capacity_receipt)
        except CapacityAdmissionError as error:
            raise SourceNativeGenerationError(error.code) from error
        admission_frame_ids = tuple(
            str(item.get("selectedFrameId") or "")
            for item in capacity_receipt.get("pages", ())
            if isinstance(item, Mapping)
        )
        if len(admission_frame_ids) != slide_count or any(
            not item for item in admission_frame_ids
        ):
            raise SourceNativeGenerationError(
                "source_verbatim_capacity_frame_binding_mismatch"
            )

    _record_generation_stage(
        request,
        "outline",
        round((time.monotonic() - outline_started_at) * 1000),
    )

    async def reserve(purpose: str, logical: int, physical: int) -> ProviderCallReservationV1:
        del physical
        await reserve_provider_attempt(
            job_id=job_id,
            slide_index=0,
            attempt_token=attempt_token,
            ordinal=logical,
        )
        return ProviderCallReservationV1(
            reservation_id=f"{job_id}:narrative:{logical}",
            purpose=purpose,
            logical_ordinal=logical,
            physical_ordinal=logical,
        )

    executor = _SourceNativeExecutor(
        package=package,
        request=request,
        generation_job=generation_job,
        context=context,
        public_route_receipt=route_receipt,
        capacity_admission_receipt=capacity_receipt,
    )
    if source_mode == "source_content_restyle":
        if admitted_content_graph is None or capacity_receipt is None:
            raise SourceNativeGenerationError("preserve_source_content_graph_missing")
        try:
            preserve_input = seal_preserve_source_input(
                operation_id=operation_id,
                content_graph=admitted_content_graph,
            )
            preserve_plan, preserve_selection = build_preserve_source_plan(
                preserve_input=preserve_input,
                capacity_receipt=capacity_receipt,
                frames=package.structure.frames,
                language=getattr(request, "language", None),
            )
            preserve_checkpoint, checkpoint_payload = build_preserve_checkpoint(
                preserve_input=preserve_input,
                operation_id=operation_id,
                job_id=job_id,
                attempt_token=attempt_token,
                fencing_token=fencing_token,
                strategy_receipt_digest=str(route_receipt.get("receiptDigest")),
                selection_receipt_digest=preserve_selection.receipt_digest,
            )
            await _commit_preserve_source_checkpoint(
                operation_id=operation_id,
                fencing_token=fencing_token,
                checkpoint=checkpoint_payload,
            )
            execution = await executor.execute(
                narrative=preserve_plan,
                selection=preserve_selection,
                route=zero_call_route(
                    strategy_receipt_digest=str(route_receipt.get("receiptDigest"))
                ),
                checkpoint=preserve_checkpoint,
            )
        except (ContentGraphValidationError, ValueError) as error:
            code = getattr(error, "code", str(error))
            raise SourceNativeGenerationError(str(code)) from error
        return {
            "schemaVersion": "preserve-source-operation-result/v1",
            "generationLane": "preserve-source",
            "preserveSourceInputDigest": preserve_input.input_digest,
            "checkpointDigest": preserve_checkpoint.checkpoint_digest,
            "frameSelectionReceiptDigest": preserve_selection.receipt_digest,
            "providerCallBudget": model_payload(
                zero_call_route(
                    strategy_receipt_digest=str(route_receipt.get("receiptDigest"))
                ).provider_call_budget
            ),
            **dict(execution),
        }

    await initialize_checkpoints(
        job_id=job_id,
        attempt_token=attempt_token,
        roles=["narrative"] * slide_count,
    )
    source_unit_ids = (
        tuple(
            item.source_unit_id
            for item in getattr(admitted_content_graph, "source_unit_order", ())
        )
        if admitted_content_graph is not None
        else ()
    )
    try:
        result = await run_narrative_ir_operation(
            operation_id=operation_id,
            job_id=job_id,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            strategy_receipt_digest=str(route_receipt.get("receiptDigest")),
            brief=brief,
            slide_count=slide_count,
            language=getattr(request, "language", None),
            source_unit_ids=source_unit_ids,
            content_graph=admitted_content_graph,
            provider=_StructuredNarrativeProvider(
                slides_markdown=getattr(request, "slides_markdown", None),
                package=package,
                admission_frame_ids=admission_frame_ids,
            ),
            reserve=reserve,
            design_pack=_pack_snapshot(
                package, source_verbatim=admitted_content_graph is not None
            ),
            source_native_executor=executor,
            authored_scene_fallback_executor=_FallbackExecutor(),
            checkpoint_repository=_PostgresCheckpointRepository(
                operation_id,
                fencing_token=fencing_token,
            ),
        )
    except (NarrativeCheckpointError, DeckIrIntegrationError) as error:
        raise SourceNativeGenerationError(str(error)) from error
    mapped = result.to_mapping()
    execution = mapped.get("execution")
    if isinstance(execution, Mapping):
        mapped.update(dict(execution))
    return mapped


__all__ = ["SourceNativeGenerationError", "run_source_native_generation"]
