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
from typing import Any, NamedTuple
import unicodedata
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
    _BODY_FONT_FLOOR_PX,
    _COMPONENT_OVERLAY_PADDING_PX,
    _COMPONENT_VISUAL_GAP_PX,
    _FOOTER_BODY_GAP_PX,
    _FOOTER_LANE_HEIGHT_PX,
    _FLOW_ICON_HEIGHT_PX,
    _FLOW_MEDIA_HEIGHT_PX,
    _FLOW_PROCESS_MEDIA_HEIGHT_PX,
    _MEANINGFUL_IMAGE_MIN_AREA_RATIO,
    _MEANINGFUL_IMAGE_MIN_HEIGHT_PX,
    _MEANINGFUL_IMAGE_MIN_WIDTH_PX,
    _MEANINGFUL_IMAGE_SCALE_FLOOR,
    _SLIDE_HEIGHT_PX,
    _SLIDE_WIDTH_PX,
    _TEXT_LINE_BOX_BREATHING_PX,
    _TITLE_STACK_GAP_PX,
    _normalized_source_semantic,
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


# Single source of truth for the numbers that appear in the emitted flow CSS.
# Both `_append_semantic_block` (which writes the CSS) and
# `_estimated_flow_block_height` (which predicts the rendered height of that
# CSS) read these, so the two can no longer drift apart.
_TITLE_LINE_HEIGHT = 1.18
_BODY_LINE_HEIGHT = 1.32
# Card chrome: `padding:10px 12px` plus a 1px border on every side.
_CARD_PADDING_X_PX = 12.0
_CARD_PADDING_Y_PX = 10.0
_CARD_BORDER_PX = 1.0
_CARD_STACK_GAP_PX = 5.0
# Media card chrome: `padding:10px`, a 1px border, and a fixed media column.
_MEDIA_CARD_PADDING_PX = 10.0
_MEDIA_CARD_MEDIA_COLUMN_PX = 52.0
_MEDIA_CARD_COLUMN_GAP_PX = 10.0
_MEDIA_CARD_IMAGE_PX = 48.0
_MEDIA_CARD_TEXT_GAP_PX = 4.0
# Process step grid: `gap:5px 10px` around an `auto minmax(0,1fr)` track pair.
_PROCESS_STEP_ROW_GAP_PX = 5.0
_PROCESS_STEP_COLUMN_GAP_PX = 10.0
_PROCESS_BODY_GAP_PX = 4.0
# Plain stacked text wrapper: `gap:4px`.
_TEXT_STACK_GAP_PX = 4.0
# Table matrix cells: `gap:1px 10px`, `padding:1px 6px`, `border-left:2px`
# and a `line-height:1.08!important` that overrides the inherited text role.
_TABLE_ROW_GAP_PX = 1.0
_TABLE_COLUMN_GAP_PX = 10.0
_TABLE_CELL_PADDING_X_PX = 6.0
_TABLE_CELL_PADDING_Y_PX = 1.0
_TABLE_CELL_BORDER_LEFT_PX = 2.0
_TABLE_CELL_LINE_HEIGHT = 1.08

# Semantic roles the emitter special-cases. Kept explicit so that role
# classification stays total: an unlisted role must never silently inherit the
# caller's slot role.
_PROCESS_STEP_HEADING_ROLES = frozenset(
    {"title", "heading", "step-title", "step-heading"}
)
_PROCESS_STEP_NUMBER_ROLES = frozenset({"process-number", "step-number"})
_TITLE_TEXT_ROLES = frozenset({"title", "heading", "section-heading"})
_NUMERIC_EMPHASIS_ROLES = frozenset({"process-number", "step-number", "kpi-value"})
_FLOOR_FONT_ROLES = frozenset({"eyebrow", "kpi-label", "compact-footer"})
_BODY_TEXT_ROLES = frozenset(
    {"body", "lede", "step-title", "step-heading"}
    | _NUMERIC_EMPHASIS_ROLES
    | _FLOOR_FONT_ROLES
)


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
        f"font-weight:{weight};"
        f"line-height:{_TITLE_LINE_HEIGHT if role == 'title' else _BODY_LINE_HEIGHT};"
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
    semantic = _normalized_source_semantic(payload={}, data=unit.data)
    semantic_role = str(semantic.get("role") or unit.semantic_role or "").lower()
    group_kind = str(semantic.get("group-kind") or "").lower()
    if group_kind == "table-matrix" or semantic_role.startswith("table-"):
        return "table-cell"
    if semantic_role == "title":
        return "title-candidate"
    if group_kind == "compact-footer" or semantic_role == "compact-footer":
        return "compact-footer"
    unit_id = unit.content_unit_id
    if unit_id.endswith(":key-message"):
        return "title-candidate"
    for kind in ("title-candidate", "table-cell", "paragraph", "bullet"):
        if f":{kind}-" in unit_id:
            return kind
    return "paragraph"


def _unit_semantic(unit: SealedContentUnit) -> dict[str, object]:
    return _normalized_source_semantic(payload={}, data=unit.data)


def _unit_group_id(unit: SealedContentUnit) -> str:
    return str(_unit_semantic(unit).get("group-id") or "")


def _unit_group_kind(unit: SealedContentUnit) -> str:
    return str(_unit_semantic(unit).get("group-kind") or "body-flow")


def _unit_semantic_role(unit: SealedContentUnit) -> str:
    return str(_unit_semantic(unit).get("role") or unit.semantic_role or "body")


def _flow_blocks(units: list[SealedContentUnit]) -> list[list[SealedContentUnit]]:
    """Retain each digest-bound semantic group as one compositor block."""

    blocks: list[list[SealedContentUnit]] = []
    index = 0
    while index < len(units):
        group_id = _unit_group_id(units[index])
        end = index + 1
        if group_id:
            while end < len(units) and _unit_group_id(units[end]) == group_id:
                end += 1
        elif _unit_boundary_kind(units[index]) == "table-cell":
            while end < len(units) and _unit_boundary_kind(units[end]) == "table-cell":
                end += 1
        blocks.append(units[index:end])
        index = end
    return blocks


def _semantic_nonnegative_int(unit: SealedContentUnit, key: str) -> int | None:
    value = _unit_semantic(unit).get(key)
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return None
    return resolved if resolved >= 0 else None


def _semantic_positive_int(
    unit: SealedContentUnit, key: str, *, default: int = 1
) -> int:
    resolved = _semantic_nonnegative_int(unit, key)
    return resolved if resolved is not None and resolved > 0 else default


def _semantic_boolean(unit: SealedContentUnit, key: str) -> bool:
    value = _unit_semantic(unit).get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _flow_table_matrix_shape(
    block: list[SealedContentUnit],
) -> tuple[int, int] | None:
    has_metadata = any(
        _semantic_nonnegative_int(item, "table-row") is not None
        or _semantic_nonnegative_int(item, "table-column") is not None
        for item in block
    )
    if not has_metadata:
        return None
    if any(
        _semantic_nonnegative_int(item, "table-row") is None
        or _semantic_nonnegative_int(item, "table-column") is None
        for item in block
    ):
        raise SourceNativeGenerationError("source_native_table_matrix_invalid")
    rows = max(
        int(_semantic_nonnegative_int(item, "table-row") or 0)
        + _semantic_positive_int(item, "table-row-span")
        for item in block
    )
    columns = max(
        int(_semantic_nonnegative_int(item, "table-column") or 0)
        + _semantic_positive_int(item, "table-column-span")
        for item in block
    )
    occupied: set[tuple[int, int]] = set()
    for item in block:
        row = int(_semantic_nonnegative_int(item, "table-row") or 0)
        column = int(_semantic_nonnegative_int(item, "table-column") or 0)
        row_span = _semantic_positive_int(item, "table-row-span")
        column_span = _semantic_positive_int(item, "table-column-span")
        for row_index in range(row, row + row_span):
            for column_index in range(column, column + column_span):
                position = (row_index, column_index)
                if position in occupied:
                    raise SourceNativeGenerationError(
                        "source_native_table_matrix_invalid"
                    )
                occupied.add(position)
    if rows <= 0 or columns <= 0:
        raise SourceNativeGenerationError("source_native_table_matrix_invalid")
    return rows, columns


def _flow_table_columns(block: list[SealedContentUnit]) -> int:
    """Column count the emitted table wrapper declares for one block."""

    matrix_shape = _flow_table_matrix_shape(block)
    if matrix_shape is not None:
        return matrix_shape[1]
    return max(2, min(4, math.ceil(len(block) ** 0.5)))


def _flow_table_cell_width(base_cell_width: float, column_span: int) -> float:
    """Content width of one emitted table cell.

    Cells are laid out content-box, so the horizontal padding and the left
    border come out of the grid track before the text is measured.
    """

    return max(
        1.0,
        base_cell_width * column_span
        + _TABLE_COLUMN_GAP_PX * (column_span - 1)
        - 2.0 * _TABLE_CELL_PADDING_X_PX
        - _TABLE_CELL_BORDER_LEFT_PX,
    )


def _flow_table_cell_height(
    unit: SealedContentUnit,
    *,
    cell_width: float,
    font_px: float,
) -> float:
    """Height of one emitted table cell under its `line-height:1.08!important`."""

    return (
        _estimated_text_lines(
            _unit_render_text(unit), width_px=cell_width, font_px=font_px
        )
        * font_px
        * _TABLE_CELL_LINE_HEIGHT
        + 2.0 * _TABLE_CELL_PADDING_Y_PX
    )


def _fixed_flow_table_height(
    block: list[SealedContentUnit],
    *,
    column_width: float,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
) -> tuple[float, int]:
    shape = _flow_table_matrix_shape(block)
    if shape is None:
        raise SourceNativeGenerationError("source_native_table_matrix_unavailable")
    rows, columns = shape
    base_cell_width = max(
        1.0, (column_width - (columns - 1) * _TABLE_COLUMN_GAP_PX) / columns
    )
    row_heights = [0.0] * rows
    for unit in block:
        column_span = _semantic_positive_int(unit, "table-column-span")
        cell_height = _flow_table_cell_height(
            unit,
            cell_width=_flow_table_cell_width(base_cell_width, column_span),
            font_px=_semantic_text_metrics(
                unit,
                slot=slot,
                adaptive_scale=adaptive_scale,
                fallback_role=role,
            ).font_px,
        )
        per_row_height = cell_height / _semantic_positive_int(unit, "table-row-span")
        start = int(_semantic_nonnegative_int(unit, "table-row") or 0)
        for row_index in range(
            start, start + _semantic_positive_int(unit, "table-row-span")
        ):
            row_heights[row_index] = max(row_heights[row_index], per_row_height)
    return sum(row_heights) + _TABLE_ROW_GAP_PX * max(0, rows - 1), columns


class _SemanticTextMetrics(NamedTuple):
    """The text geometry `_semantic_text_css` emits for one sealed unit."""

    text_role: str
    font_px: float
    line_height: float
    css_suffix: str


def _semantic_text_role(unit: SealedContentUnit, fallback: str) -> str:
    """Classify every role the emitter special-cases, never by omission."""

    semantic_role = _unit_semantic_role(unit).lower()
    if semantic_role in _TITLE_TEXT_ROLES:
        return "title"
    if semantic_role in _BODY_TEXT_ROLES:
        return "body"
    if fallback in {"title", "headline", "heading"}:
        return "title"
    return "body"


def _semantic_text_metrics(
    unit: SealedContentUnit,
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    fallback_role: str,
) -> _SemanticTextMetrics:
    """Resolve font size, line height and CSS overrides exactly once.

    `_semantic_text_css` renders this result and the flow estimator measures
    it, so the predicted height of a text child cannot diverge from the CSS
    that Chromium actually lays out.
    """

    semantic_role = _unit_semantic_role(unit).lower()
    group_kind = _unit_group_kind(unit).lower()
    body_px = _flow_font_px(slot, role="body", adaptive_scale=adaptive_scale)
    # A process step heading is rendered with the body line rhythm so that the
    # step number and its heading share one grid row cleanly.
    process_header = (
        group_kind == "process-step" and semantic_role in _PROCESS_STEP_HEADING_ROLES
    )
    text_role = "body" if process_header else _semantic_text_role(unit, fallback_role)
    line_height = _TITLE_LINE_HEIGHT if text_role == "title" else _BODY_LINE_HEIGHT

    def resolved(font_px: float, css_suffix: str) -> _SemanticTextMetrics:
        return _SemanticTextMetrics(text_role, font_px, line_height, css_suffix)

    if process_header:
        font_px = max(20.0, body_px * 1.08)
        return resolved(font_px, f";font-size:{font_px:.2f}px;font-weight:700")
    if semantic_role == "title":
        return resolved(
            _flow_font_px(slot, role=text_role, adaptive_scale=adaptive_scale),
            ";font-weight:750",
        )
    if semantic_role in {"heading", "section-heading"}:
        font_px = max(20.0, body_px * 1.18)
        return resolved(font_px, f";font-size:{font_px:.2f}px;font-weight:700")
    if semantic_role in _NUMERIC_EMPHASIS_ROLES:
        font_px = max(24.0, body_px * 1.35)
        return resolved(font_px, f";font-size:{font_px:.2f}px;font-weight:800")
    if semantic_role in _FLOOR_FONT_ROLES:
        return resolved(
            _BODY_FONT_FLOOR_PX,
            f";font-size:{_BODY_FONT_FLOOR_PX:.2f}px;font-weight:650",
        )
    if semantic_role == "lede":
        font_px = max(18.0, body_px)
        return resolved(font_px, f";font-size:{font_px:.2f}px;font-weight:500")
    return resolved(
        _flow_font_px(slot, role=text_role, adaptive_scale=adaptive_scale), ""
    )


def _semantic_text_css(
    unit: SealedContentUnit,
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    fallback_role: str,
) -> str:
    metrics = _semantic_text_metrics(
        unit,
        slot=slot,
        adaptive_scale=adaptive_scale,
        fallback_role=fallback_role,
    )
    return (
        _flow_text_css(slot, role=metrics.text_role, adaptive_scale=adaptive_scale)
        + metrics.css_suffix
    )


def _append_semantic_block(
    parent: object,
    block: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
    image_assets: Mapping[str, Mapping[str, object]] | None = None,
    component_kind: str | None = None,
) -> None:
    if not block:
        return
    group_kind = _unit_group_kind(block[0]).lower()
    group_id = _unit_group_id(block[0])
    evidence_card = any(_is_meaningful_flow_image(item) for item in block)
    process_media_height = max(
        (
            _FLOW_ICON_HEIGHT_PX
            if _unit_image_kind(item) == "icon"
            else _FLOW_PROCESS_MEDIA_HEIGHT_PX
        )
        for item in block
        if item.kind == "image"
    ) if group_kind == "process-step" and any(item.kind == "image" for item in block) else 0.0
    wrapper = lxml_html.Element("div")
    wrapper.set("data-qid", f"source-semantic-group:{group_id or block[0].content_unit_id}")
    wrapper.set("data-source-semantic-group-id", group_id)
    wrapper.set("data-source-semantic-group-kind", group_kind)
    if component_kind:
        wrapper.set("data-source-component-kind", component_kind)
    if evidence_card:
        wrapper.set("data-allow-overlap", "true")
    wrapper.set("data-content-unit-ids", "|".join(item.content_unit_id for item in block))
    wrapper.set("data-content-unit-digests", "|".join(item.content_digest for item in block))
    card_like = group_kind in {"media-card", "process-step", "kpi"}
    if group_kind == "compact-footer":
        wrapper.set(
            "style",
            "display:flex!important;align-items:center;gap:16px;height:23px;"
            "min-width:0;overflow:hidden;white-space:nowrap",
        )
    elif evidence_card:
        # The gallery gives the card its definite height explicitly; anywhere
        # else the card is sized by its content, so no percentage height.
        wrapper.set(
            "style",
            "position:relative!important;display:block!important;min-width:0;min-height:0;"
            "flex:0 0 auto;padding:0;box-sizing:border-box;"
            "border:1px solid rgba(37,99,235,.24);"
            "border-radius:12px;background:rgba(255,255,255,.045);"
            "overflow:hidden;break-inside:avoid",
        )
    elif group_kind == "media-card":
        wrapper.set(
            "style",
            "display:grid!important;"
            f"grid-template-columns:{_MEDIA_CARD_MEDIA_COLUMN_PX:.0f}px minmax(0,1fr);"
            f"gap:{_MEDIA_CARD_COLUMN_GAP_PX:.0f}px;"
            "align-items:start;min-width:0;min-height:0;flex:0 0 auto;"
            f"padding:{_MEDIA_CARD_PADDING_PX:.0f}px;box-sizing:border-box;"
            f"border:{_CARD_BORDER_PX:.0f}px solid rgba(37,99,235,.22);border-radius:12px;"
            "background:rgba(255,255,255,.045);overflow:hidden;break-inside:avoid",
        )
    elif group_kind == "process-step":
        wrapper.set(
            "style",
            "display:grid!important;grid-template-columns:auto minmax(0,1fr);"
            + (
                f"grid-template-rows:{process_media_height:.3f}px auto auto;"
                if process_media_height > 0.0
                else "grid-template-rows:auto auto;"
            )
            + f"gap:{_PROCESS_STEP_ROW_GAP_PX:.0f}px {_PROCESS_STEP_COLUMN_GAP_PX:.0f}px;"
            "align-content:start;min-width:0;"
            "flex:0 0 auto;"
            f"padding:{_CARD_PADDING_Y_PX:.0f}px {_CARD_PADDING_X_PX:.0f}px;"
            f"box-sizing:border-box;border:{_CARD_BORDER_PX:.0f}px solid rgba(37,99,235,.22);"
            "border-radius:12px;background:rgba(255,255,255,.045);"
            "overflow:hidden;break-inside:avoid",
        )
    elif card_like:
        wrapper.set(
            "style",
            "display:flex!important;flex:0 0 auto;flex-direction:column;"
            f"gap:{_CARD_STACK_GAP_PX:.0f}px;min-width:0;"
            f"padding:{_CARD_PADDING_Y_PX:.0f}px {_CARD_PADDING_X_PX:.0f}px;"
            f"box-sizing:border-box;border:{_CARD_BORDER_PX:.0f}px solid rgba(37,99,235,.22);"
            "border-radius:12px;background:rgba(255,255,255,.045);"
            "overflow:hidden;break-inside:avoid",
        )
    elif group_kind == "title-stack":
        wrapper.set(
            "style",
            "display:flex!important;flex:0 0 auto;flex-direction:column;"
            f"gap:{_TITLE_STACK_GAP_PX:.0f}px;min-width:0;"
            "overflow:hidden;break-inside:avoid",
        )
    else:
        wrapper.set(
            "style",
            "display:flex!important;flex:0 0 auto;flex-direction:column;"
            f"gap:{_TEXT_STACK_GAP_PX:.0f}px;min-width:0;"
            "overflow:hidden;break-inside:avoid",
        )

    if group_kind == "table-matrix" or _unit_boundary_kind(block[0]) == "table-cell":
        matrix_shape = _flow_table_matrix_shape(block)
        columns = _flow_table_columns(block)
        wrapper.set(
            "style",
            "display:grid!important;width:100%;min-width:0;flex:0 0 auto;break-inside:avoid;"
            f"grid-template-columns:repeat({columns},minmax(0,1fr));"
            f"gap:{_TABLE_ROW_GAP_PX:.0f}px {_TABLE_COLUMN_GAP_PX:.0f}px;overflow:hidden",
        )
        for unit in block:
            child = lxml_html.Element("div")
            child.text = _unit_render_text(unit)
            child.set("data-qid", f"source-flow:{unit.content_unit_id}")
            child.set("data-source-flow-unit-id", unit.content_unit_id)
            child.set("data-source-boundary-kind", "table-cell")
            child.set("data-quality-unintentional", "true")
            position = ""
            if matrix_shape is not None:
                row = int(_semantic_nonnegative_int(unit, "table-row") or 0) + 1
                column = int(_semantic_nonnegative_int(unit, "table-column") or 0) + 1
                position = (
                    f"grid-row:{row}/span {_semantic_positive_int(unit, 'table-row-span')};"
                    f"grid-column:{column}/span {_semantic_positive_int(unit, 'table-column-span')};"
                )
            header = _semantic_boolean(unit, "table-header")
            child.set(
                "style",
                position
                + "display:flex;align-items:center;min-width:0;"
                + f"padding:{_TABLE_CELL_PADDING_Y_PX:.0f}px {_TABLE_CELL_PADDING_X_PX:.0f}px;"
                + ("background:rgba(37,99,235,.12);font-weight:700;" if header else "")
                + f"border-left:{_TABLE_CELL_BORDER_LEFT_PX:.0f}px solid rgba(37,99,235,.35);"
                "overflow-wrap:break-word;"
                f"word-break:keep-all;line-height:{_TABLE_CELL_LINE_HEIGHT}!important;"
                + _semantic_text_css(
                    unit,
                    slot=slot,
                    adaptive_scale=adaptive_scale,
                    fallback_role=role,
                ),
            )
            wrapper.append(child)
        parent.append(wrapper)
        return

    text_container = wrapper
    process_body_container = None
    if group_kind == "process-step" and not evidence_card:
        process_body_container = lxml_html.Element("div")
        process_body_container.set(
            "style",
            f"grid-column:1/-1;grid-row:{3 if process_media_height > 0.0 else 2};"
            "display:flex!important;flex-direction:column;"
            f"gap:{_PROCESS_BODY_GAP_PX:.0f}px;min-width:0;min-height:0;overflow:visible",
        )
    if evidence_card:
        text_container = lxml_html.Element("div")
        text_container.set(
            "style",
            "position:absolute;left:8px;right:8px;bottom:8px;z-index:2;"
            "display:flex!important;flex-direction:column;gap:3px;max-height:38%;"
            "padding:8px 10px;box-sizing:border-box;border-radius:8px;"
            "background:rgba(15,23,42,.86);color:#fff;overflow:hidden",
        )
    elif group_kind == "media-card":
        text_container = lxml_html.Element("div")
        text_container.set(
            "style",
            "grid-column:2;display:flex!important;flex-direction:column;"
            f"gap:{_MEDIA_CARD_TEXT_GAP_PX:.0f}px;"
            "min-width:0;overflow:hidden",
        )
    assets = image_assets or {}
    for index, unit in enumerate(block):
        if unit.kind == "image":
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
            semantic = _unit_semantic(unit)
            image.set("data-source-image-kind", str(semantic.get("image-kind") or "media"))
            image.set(
                "data-source-image-aspect-ratio",
                str(semantic.get("image-aspect-ratio") or ""),
            )
            image.set(
                "style",
                (
                    f"display:block;width:{'48px' if _unit_image_kind(unit) == 'icon' else '100%'};"
                    f"height:{_FLOW_ICON_HEIGHT_PX if _unit_image_kind(unit) == 'icon' else _FLOW_PROCESS_MEDIA_HEIGHT_PX:.3f}px;"
                    "min-width:0;min-height:0;grid-column:1/-1;grid-row:1;align-self:center;"
                    "justify-self:center;object-fit:contain;overflow:hidden"
                    if group_kind == "process-step" and not evidence_card
                    else
                    f"display:block;width:{_MEDIA_CARD_IMAGE_PX:.0f}px;"
                    f"height:{_MEDIA_CARD_IMAGE_PX:.0f}px;min-width:0;min-height:0;"
                    "grid-column:1;grid-row:1;align-self:start;object-fit:contain;overflow:hidden"
                    if group_kind == "media-card" and not evidence_card
                    else f"display:block;width:{_FLOW_ICON_HEIGHT_PX:.3f}px;"
                    f"height:{_FLOW_ICON_HEIGHT_PX:.3f}px;min-width:0;min-height:0;"
                    "align-self:center;object-fit:contain;overflow:hidden"
                    if not evidence_card and _unit_image_kind(unit) == "icon"
                    else f"display:block;width:100%;height:{_FLOW_MEDIA_HEIGHT_PX:.3f}px;"
                    "min-width:0;min-height:0;align-self:center;object-fit:contain;overflow:hidden"
                    if not evidence_card
                    else "display:block;width:100%;height:100%;min-width:0;min-height:0;"
                    "object-fit:contain;overflow:hidden"
                ),
            )
            if evidence_card:
                image.set("data-allow-overlap", "true")
            wrapper.insert(0, image)
            continue
        semantic_role = _unit_semantic_role(unit).lower()
        element = "h2" if semantic_role in {"title", "heading", "section-heading"} else "p"
        child = lxml_html.Element(element)
        child.text = _unit_render_text(unit)
        child.set("data-qid", f"source-flow:{unit.content_unit_id}")
        child.set("data-source-flow-unit-id", unit.content_unit_id)
        child.set("data-source-boundary-kind", _unit_boundary_kind(unit))
        child.set("data-quality-unintentional", "true")
        if evidence_card:
            child.set("data-allow-overlap", "true")
        process_grid_style = ""
        if group_kind == "process-step":
            process_header_row = 2 if process_media_height > 0.0 else 1
            if semantic_role in {"process-number", "step-number"}:
                process_grid_style = (
                    f"grid-column:1;grid-row:{process_header_row};align-self:start;"
                )
            elif semantic_role in {
                "title",
                "heading",
                "step-title",
                "step-heading",
            }:
                process_grid_style = (
                    f"grid-column:2;grid-row:{process_header_row};align-self:center;"
                )
        child.set(
            "style",
            "margin:0;box-sizing:border-box;"
            f"padding-bottom:{_TEXT_LINE_BOX_BREATHING_PX:.3f}px;"
            "overflow-wrap:break-word;word-break:keep-all;"
            + process_grid_style
            + _semantic_text_css(
                unit,
                slot=slot,
                adaptive_scale=adaptive_scale,
                fallback_role=role,
            ),
        )
        target_container = (
            process_body_container
            if process_body_container is not None
            and semantic_role
            not in {
                "process-number",
                "step-number",
                "title",
                "heading",
                "step-title",
                "step-heading",
            }
            else text_container
        )
        target_container.append(child)
    if process_body_container is not None and len(process_body_container):
        wrapper.append(process_body_container)
    if text_container is not wrapper:
        wrapper.append(text_container)
    parent.append(wrapper)


def _append_flow_text_units(
    parent: object,
    units: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
    column_width: float | None = None,
) -> None:
    del column_width  # Geometry was already selected for whole atomic blocks.
    for block in _flow_blocks(units):
        _append_semantic_block(
            parent,
            block,
            slot=slot,
            adaptive_scale=adaptive_scale,
            role=role,
        )


def _estimated_text_advance_px(text: str, *, font_px: float) -> float:
    """Advance width of one unwrapped run, shared by wrapping and max-content."""

    total = 0.0
    for character in text:
        if character.isspace():
            total += font_px * 0.33
        elif unicodedata.east_asian_width(character) in {"W", "F", "A"}:
            total += font_px
        elif character.isupper():
            total += font_px * 0.64
        elif character.isdigit():
            total += font_px * 0.56
        else:
            total += font_px * 0.54
    return total


def _estimated_text_lines(text: str, *, width_px: float, font_px: float) -> int:
    def advance(value: str) -> float:
        return _estimated_text_advance_px(value, font_px=font_px)

    normalized = " ".join(text.split())
    if not normalized:
        return 1
    space = font_px * 0.33
    lines = 1
    occupied = 0.0
    for word in normalized.split(" "):
        word_width = advance(word)
        separator = space if occupied > 0 else 0.0
        if occupied > 0 and occupied + separator + word_width > width_px:
            lines += 1
            occupied = 0.0
            separator = 0.0
        if word_width <= width_px:
            occupied += separator + word_width
            continue
        remaining = word_width
        while remaining > width_px:
            if occupied > 0:
                lines += 1
                occupied = 0.0
            remaining -= width_px
            if remaining > 0:
                lines += 1
        occupied = remaining
    return lines


def _select_flow_table_layout(
    cells: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
    column_width: float,
) -> tuple[int, float]:
    """Report the table grid `_append_semantic_block` actually emits."""

    readable_floor_px = SOURCE_NATIVE_MIN_BODY_FONT_PT / 0.75
    if role not in {"title", "headline", "heading"} and (
        _flow_font_px(slot, role=role, adaptive_scale=adaptive_scale)
        < readable_floor_px
    ):
        raise SourceNativeGenerationError("source_native_text_readability_failed")
    if _flow_table_matrix_shape(cells) is not None:
        estimated_height, columns = _fixed_flow_table_height(
            cells,
            column_width=column_width,
            slot=slot,
            adaptive_scale=adaptive_scale,
            role=role,
        )
        return columns, estimated_height
    # Unshaped table blocks fall back to the same square-ish auto-flow grid the
    # emitter derives, so estimation measures the grid that gets rendered.
    columns = _flow_table_columns(cells)
    rows = math.ceil(len(cells) / columns)
    base_cell_width = max(
        1.0, (column_width - (columns - 1) * _TABLE_COLUMN_GAP_PX) / columns
    )
    cell_width = _flow_table_cell_width(base_cell_width, 1)
    heights = [
        _flow_table_cell_height(
            unit,
            cell_width=cell_width,
            font_px=_semantic_text_metrics(
                unit,
                slot=slot,
                adaptive_scale=adaptive_scale,
                fallback_role=role,
            ).font_px,
        )
        for unit in cells
    ]
    estimated_height = (
        sum(max(heights[row * columns : (row + 1) * columns]) for row in range(rows))
        + _TABLE_ROW_GAP_PX * max(0, rows - 1)
    )
    return columns, estimated_height


class _SemanticBlockChrome(NamedTuple):
    """Padding, border and gap the wrapper CSS writes for one group kind."""

    width_inset: float
    height_inset: float
    stack_gap: float


def _semantic_block_chrome(group_kind: str) -> _SemanticBlockChrome:
    """Mirror the wrapper box model `_append_semantic_block` emits."""

    if group_kind == "media-card":
        # `padding:10px` and a 1px border on both sides; the text container
        # sits behind the fixed media column and its grid column gap.
        return _SemanticBlockChrome(
            2.0 * (_MEDIA_CARD_PADDING_PX + _CARD_BORDER_PX)
            + _MEDIA_CARD_MEDIA_COLUMN_PX
            + _MEDIA_CARD_COLUMN_GAP_PX,
            2.0 * (_MEDIA_CARD_PADDING_PX + _CARD_BORDER_PX),
            _MEDIA_CARD_TEXT_GAP_PX,
        )
    if group_kind in {"process-step", "kpi"}:
        # `padding:10px 12px` and a 1px border on every side.
        return _SemanticBlockChrome(
            2.0 * (_CARD_PADDING_X_PX + _CARD_BORDER_PX),
            2.0 * (_CARD_PADDING_Y_PX + _CARD_BORDER_PX),
            _PROCESS_STEP_ROW_GAP_PX
            if group_kind == "process-step"
            else _CARD_STACK_GAP_PX,
        )
    if group_kind == "title-stack":
        return _SemanticBlockChrome(0.0, 0.0, _TITLE_STACK_GAP_PX)
    return _SemanticBlockChrome(0.0, 0.0, _TEXT_STACK_GAP_PX)


def _estimated_text_child_height(
    unit: SealedContentUnit,
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    fallback_role: str,
    width_px: float,
) -> float:
    """Height of one emitted text child: line boxes plus its padding-bottom."""

    metrics = _semantic_text_metrics(
        unit,
        slot=slot,
        adaptive_scale=adaptive_scale,
        fallback_role=fallback_role,
    )
    return (
        _estimated_text_lines(
            _unit_render_text(unit),
            width_px=max(1.0, width_px),
            font_px=metrics.font_px,
        )
        * metrics.font_px
        * metrics.line_height
        + _TEXT_LINE_BOX_BREATHING_PX
    )


def _emitted_flow_image_height(
    unit: SealedContentUnit, *, group_kind: str
) -> float:
    """Height the image CSS declares for one flow image child."""

    if group_kind == "media-card":
        return _MEDIA_CARD_IMAGE_PX
    if _unit_image_kind(unit) == "icon":
        return _FLOW_ICON_HEIGHT_PX
    if group_kind == "process-step":
        return _FLOW_PROCESS_MEDIA_HEIGHT_PX
    return _FLOW_MEDIA_HEIGHT_PX


def _estimated_flow_block_height(
    block: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
    column_width: float,
) -> float:
    group_kind = _unit_group_kind(block[0]).lower()
    if group_kind == "compact-footer":
        return _FOOTER_LANE_HEIGHT_PX
    if group_kind == "table-matrix" or _unit_boundary_kind(block[0]) == "table-cell":
        _, estimated_height = _select_flow_table_layout(
            block,
            slot=slot,
            adaptive_scale=adaptive_scale,
            role=role,
            column_width=column_width,
        )
        return estimated_height
    chrome = _semantic_block_chrome(group_kind)
    content_width = max(1.0, column_width - chrome.width_inset)
    if group_kind == "process-step":
        media_height = 0.0
        number_width = 0.0
        header_heights: list[float] = []
        heading_units: list[SealedContentUnit] = []
        body_heights: list[float] = []
        for unit in block:
            if unit.kind == "image":
                media_height = max(
                    media_height,
                    _emitted_flow_image_height(unit, group_kind=group_kind),
                )
                continue
            semantic_role = _unit_semantic_role(unit).lower()
            if semantic_role in _PROCESS_STEP_NUMBER_ROLES:
                metrics = _semantic_text_metrics(
                    unit,
                    slot=slot,
                    adaptive_scale=adaptive_scale,
                    fallback_role=role,
                )
                # `grid-template-columns:auto minmax(0,1fr)` sizes the number
                # column to the unwrapped number itself.
                number_width = max(
                    number_width,
                    _estimated_text_advance_px(
                        _unit_render_text(unit), font_px=metrics.font_px
                    ),
                )
                header_heights.append(
                    metrics.font_px * metrics.line_height
                    + _TEXT_LINE_BOX_BREATHING_PX
                )
            elif semantic_role in _PROCESS_STEP_HEADING_ROLES:
                heading_units.append(unit)
            else:
                # The body container spans `grid-column:1/-1`, so body units
                # are laid out across the whole content width.
                body_heights.append(
                    _estimated_text_child_height(
                        unit,
                        slot=slot,
                        adaptive_scale=adaptive_scale,
                        fallback_role=role,
                        width_px=content_width,
                    )
                )
        heading_width = content_width - number_width - _PROCESS_STEP_COLUMN_GAP_PX
        header_heights.extend(
            _estimated_text_child_height(
                unit,
                slot=slot,
                adaptive_scale=adaptive_scale,
                fallback_role=role,
                width_px=heading_width,
            )
            for unit in heading_units
        )
        body_height = sum(body_heights) + _PROCESS_BODY_GAP_PX * max(
            0, len(body_heights) - 1
        )
        # `grid-template-rows` declares its tracks unconditionally, so the row
        # gap applies between every declared track whether or not it is empty.
        declared_rows = 3 if media_height > 0.0 else 2
        return (
            media_height
            + max(header_heights, default=0.0)
            + body_height
            + chrome.height_inset
            + _PROCESS_STEP_ROW_GAP_PX * (declared_rows - 1)
        )
    text_heights = [
        _estimated_text_child_height(
            unit,
            slot=slot,
            adaptive_scale=adaptive_scale,
            fallback_role=role,
            width_px=content_width,
        )
        for unit in block
        if unit.kind != "image"
    ]
    image_heights = [
        _emitted_flow_image_height(unit, group_kind=group_kind)
        for unit in block
        if unit.kind == "image"
    ]
    if group_kind == "media-card":
        # The fixed media column and the text container share grid row 1.
        content_height = max(
            max(image_heights, default=0.0),
            sum(text_heights)
            + chrome.stack_gap * max(0, len(text_heights) - 1),
        )
    else:
        # `wrapper.insert(0, image)` stacks images ahead of the text inside the
        # same flex column, so every child contributes one gap.
        stacked = image_heights + text_heights
        content_height = sum(stacked) + chrome.stack_gap * max(0, len(stacked) - 1)
    return content_height + chrome.height_inset


def _balanced_contiguous_ends(weights: list[float], columns: int) -> tuple[int, ...]:
    prefix = [0.0]
    for weight in weights:
        prefix.append(prefix[-1] + weight)
    states: dict[tuple[int, int], tuple[float, float, tuple[int, ...]]] = {}
    for end in range(1, len(weights) + 1):
        load = prefix[end]
        states[(1, end)] = (load, load * load, (end,))
    for count in range(2, columns + 1):
        for end in range(count, len(weights) + 1):
            candidates = []
            for split in range(count - 1, end):
                previous = states[(count - 1, split)]
                load = prefix[end] - prefix[split]
                candidates.append(
                    (
                        max(previous[0], load),
                        previous[1] + load * load,
                        previous[2] + (end,),
                    )
                )
            states[(count, end)] = min(
                candidates,
                key=lambda item: (
                    round(item[0], 6),
                    round(item[1], 6),
                    item[2],
                ),
            )
    return states[(columns, len(weights))][2]


def _append_fixed_text_columns(
    parent: object,
    units: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    role: str,
    columns_count: int,
    column_gap: float,
    width_fraction: float = 1.0,
    height_fraction: float = 1.0,
) -> float:
    """Keep atomic source units inside an explicit, bounded column grid."""

    footer_units = [
        item
        for item in units
        if _unit_group_kind(item).lower() == "compact-footer"
        or _unit_semantic_role(item).lower() == "compact-footer"
    ]
    body_units = [item for item in units if item not in footer_units]
    blocks = _flow_blocks(body_units)
    if not blocks and not footer_units:
        return adaptive_scale
    initial_columns = max(1, min(columns_count, len(blocks)))
    bounds = _flow_mapping(slot, "bounds")
    spacing = _flow_mapping(slot, "spacing")
    content_width = max(
        1.0,
        _flow_number(bounds.get("width"), 1280.0)
        - max(0.0, _flow_number(spacing.get("insetLeft")))
        - max(0.0, _flow_number(spacing.get("insetRight"))),
    ) * max(0.05, min(1.0, width_fraction))
    usable_height = max(
        1.0,
        _flow_number(bounds.get("height"), 720.0)
        - max(0.0, _flow_number(spacing.get("insetTop")))
        - max(0.0, _flow_number(spacing.get("insetBottom"))),
    ) * max(0.05, min(1.0, height_fraction))
    if footer_units and body_units:
        usable_height -= _FOOTER_LANE_HEIGHT_PX + _FOOTER_BODY_GAP_PX
        if usable_height <= 0:
            raise SourceNativeGenerationError("source_native_footer_capacity_exceeded")
    # The overlay is itself the hard geometry boundary and Chromium performs
    # the downstream overflow check, so admission may use the full measured
    # content height instead of discarding an arbitrary ten-percent reserve.
    target_height = usable_height
    capacity = _flow_mapping(slot, "capacityEnvelope")
    minimum_scale = min(
        adaptive_scale,
        max(
            0.1,
            _flow_number(capacity.get("minimumTextScale"), 0.70),
        ),
    )

    def evaluate(
        candidate_columns: int, candidate_scale: float
    ) -> tuple[list[float], tuple[int, ...], float]:
        column_width = max(
            1.0,
            (
                content_width
                - column_gap * max(0, candidate_columns - 1)
            )
            / candidate_columns,
        )
        candidate_weights = [
            _estimated_flow_block_height(
                block,
                slot=slot,
                adaptive_scale=candidate_scale,
                role=role,
                column_width=column_width,
            )
            for block in blocks
        ]
        candidate_ends = _balanced_contiguous_ends(
            candidate_weights, candidate_columns
        )
        start = 0
        candidate_maximum = 0.0
        for end in candidate_ends:
            candidate_maximum = max(
                candidate_maximum, sum(candidate_weights[start:end])
            )
            start = end
        return candidate_weights, candidate_ends, candidate_maximum

    if not blocks:
        parent.set(
            "style",
            "display:flex!important;align-items:flex-end;width:100%;height:100%;"
            "min-width:0;min-height:0;overflow:hidden",
        )
        for footer_block in _flow_blocks(footer_units):
            _append_semantic_block(
                parent,
                footer_block,
                slot=slot,
                adaptive_scale=adaptive_scale,
                role="body",
            )
        return adaptive_scale

    options: list[
        tuple[float, int, list[float], tuple[int, ...], float]
    ] = []
    fixed_table_matrix = any(
        _flow_table_matrix_shape(block) is not None
        for block in blocks
        if _unit_group_kind(block[0]).lower() == "table-matrix"
        or _unit_boundary_kind(block[0]) == "table-cell"
    )
    column_candidates = (
        (1,)
        if fixed_table_matrix
        else range(1, min(4, columns_count, len(blocks)) + 1)
    )
    for candidate_columns in column_candidates:
        minimum_result = evaluate(candidate_columns, minimum_scale)
        if minimum_result[2] > target_height:
            continue
        maximum_result = evaluate(candidate_columns, adaptive_scale)
        if maximum_result[2] <= target_height:
            options.append(
                (
                    adaptive_scale,
                    candidate_columns,
                    maximum_result[0],
                    maximum_result[1],
                    maximum_result[2],
                )
            )
            continue
        lower = minimum_scale
        upper = adaptive_scale
        lower_result = minimum_result
        for _ in range(18):
            midpoint = (lower + upper) / 2.0
            midpoint_result = evaluate(candidate_columns, midpoint)
            if midpoint_result[2] <= target_height:
                lower = midpoint
                lower_result = midpoint_result
            else:
                upper = midpoint
        options.append(
            (
                lower,
                candidate_columns,
                lower_result[0],
                lower_result[1],
                lower_result[2],
            )
        )
    if not options:
        raise SourceNativeGenerationError("source_native_text_capacity_exceeded")
    effective_scale, resolved_columns, weights, ends, _ = max(
        options,
        key=lambda item: (round(item[0], 6), -item[1]),
    )
    resolved_column_width = max(
        1.0,
        (
            content_width
            - column_gap * max(0, resolved_columns - 1)
        )
        / resolved_columns,
    )
    parent.set(
        "style",
        (
            "display:grid!important;width:100%;height:100%;min-width:0;min-height:0;"
            f"grid-template-rows:minmax(0,1fr) {_FOOTER_BODY_GAP_PX:.3f}px {_FOOTER_LANE_HEIGHT_PX:.3f}px;"
            "overflow:hidden"
            if footer_units
            else "display:block!important;width:100%;height:100%;min-width:0;min-height:0;overflow:hidden"
        ),
    )
    grid_parent = parent
    if footer_units:
        grid_parent = lxml_html.Element("div")
        grid_parent.set("data-qid", f"{str(parent.get('data-qid') or 'source-flow-columns')}--body")
        parent.append(grid_parent)
    grid_parent.set(
        "style",
        "display:grid!important;width:100%;height:100%;min-width:0;min-height:0;"
        f"grid-template-columns:repeat({resolved_columns},minmax(0,1fr));"
        f"column-gap:{column_gap:.3f}px;overflow:hidden",
    )
    start = 0
    parent_qid = str(parent.get("data-qid") or "source-flow-columns")
    for column_index, end in enumerate(ends):
        selected_blocks = blocks[start:end]
        start = end
        column = lxml_html.Element("div")
        column.set("data-qid", f"{parent_qid}--column-{column_index + 1:02d}")
        column.set(
            "style",
            "display:flex!important;flex-direction:column;min-width:0;min-height:0;"
            "overflow:hidden",
        )
        for block in selected_blocks:
            _append_semantic_block(
                column,
                block,
                slot=slot,
                adaptive_scale=effective_scale,
                role=role,
            )
        grid_parent.append(column)
    if footer_units:
        spacer = lxml_html.Element("div")
        spacer.set("aria-hidden", "true")
        parent.append(spacer)
        footer_lane = lxml_html.Element("div")
        footer_lane.set(
            "style",
            "display:flex!important;align-items:center;gap:16px;height:23px;"
            "min-width:0;overflow:hidden;white-space:nowrap",
        )
        for footer_block in _flow_blocks(footer_units):
            _append_semantic_block(
                footer_lane,
                footer_block,
                slot=slot,
                adaptive_scale=effective_scale,
                role="body",
            )
        parent.append(footer_lane)
    return effective_scale


def _unit_image_kind(unit: SealedContentUnit) -> str:
    return str(_unit_semantic(unit).get("image-kind") or "media").strip().lower()


def _unit_image_aspect(unit: SealedContentUnit) -> float | None:
    value = _unit_semantic(unit).get("image-aspect-ratio")
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    return resolved if resolved > 0 else None


def _is_meaningful_flow_image(unit: SealedContentUnit) -> bool:
    if unit.kind != "image" or _unit_image_kind(unit) == "icon":
        return False
    evidence = (
        f"{_unit_semantic_role(unit)} {_unit_group_kind(unit)} {_unit_image_kind(unit)}"
    ).lower()
    return any(
        token in evidence
        for token in ("ui", "evidence", "screenshot", "product-screen")
    )


def _meaningful_image_geometry_pass(
    unit: SealedContentUnit, *, cell_width: float, cell_height: float
) -> bool:
    if not _is_meaningful_flow_image(unit):
        return True
    aspect = _unit_image_aspect(unit)
    if aspect is None or cell_width <= 0 or cell_height <= 0:
        return False
    if cell_width / cell_height >= aspect:
        rendered_height = cell_height
        rendered_width = rendered_height * aspect
    else:
        rendered_width = cell_width
        rendered_height = rendered_width / aspect
    dimension_pass = (
        rendered_width >= _MEANINGFUL_IMAGE_MIN_WIDTH_PX
        and rendered_height >= _MEANINGFUL_IMAGE_MIN_HEIGHT_PX
    )
    area_scale_pass = (
        rendered_width * rendered_height
        >= _SLIDE_WIDTH_PX * _SLIDE_HEIGHT_PX * _MEANINGFUL_IMAGE_MIN_AREA_RATIO
        and min(
            rendered_width / _SLIDE_WIDTH_PX,
            rendered_height / _SLIDE_HEIGHT_PX,
        )
        >= _MEANINGFUL_IMAGE_SCALE_FLOOR
    )
    return dimension_pass or area_scale_pass


def _component_preference(group_kind: str, *, has_image: bool) -> tuple[str, ...]:
    if group_kind == "title-stack":
        return ("titleBand", "introBand", "bodyFlow")
    if group_kind == "media-card" or has_image:
        return ("evidenceGallery", "cardGrid", "bodyFlow")
    if group_kind == "process-step":
        return ("cardGrid", "tableMatrix", "bodyFlow")
    if group_kind == "kpi":
        return ("dataGrid", "cardGrid", "bodyFlow")
    if group_kind == "table-matrix":
        return ("tableMatrix", "dataGrid", "bodyFlow")
    if group_kind == "compact-footer":
        return ("footerBand",)
    return ("bodyFlow", "cardGrid")


def _append_component_canvas(
    parent: object,
    units: list[SealedContentUnit],
    *,
    slot: Mapping[str, object],
    adaptive_scale: float,
    image_assets: Mapping[str, Mapping[str, object]] | None,
) -> None:
    surfaces = slot.get("sourceFlowComponentSurfaces")
    if not isinstance(surfaces, list) or not surfaces:
        raise SourceNativeGenerationError("source_native_component_graph_missing")
    expected_digest = str(slot.get("sourceFlowComponentGraphDigest") or "")
    if not expected_digest or canonical_digest(surfaces) != expected_digest:
        raise SourceNativeGenerationError("source_native_component_graph_digest_mismatch")

    blocks = _flow_blocks(units)
    if not blocks:
        raise SourceNativeGenerationError("source_native_component_units_missing")
    surface_kinds = {
        str(item.get("componentKind") or "")
        for item in surfaces
        if isinstance(item, Mapping)
    }
    root = lxml_html.Element("div")
    root_qid = str(parent.get("data-qid") or "source-components")
    root.set("data-qid", f"{root_qid}--components")
    root.set("data-source-component-graph-digest", expected_digest)
    root_gap = (
        _FOOTER_BODY_GAP_PX
        if any(
            _unit_group_kind(block[0]).lower() == "compact-footer"
            for block in blocks
        )
        else 12.0
    )
    root.set(
        "style",
        "display:flex!important;flex-direction:column;width:100%;height:100%;"
        f"min-width:0;min-height:0;gap:{root_gap:.3f}px;overflow:hidden",
    )

    footer_blocks = [
        block for block in blocks if _unit_group_kind(block[0]).lower() == "compact-footer"
    ]
    body_blocks = [block for block in blocks if block not in footer_blocks]
    table_blocks = [
        block
        for block in body_blocks
        if _unit_group_kind(block[0]).lower() == "table-matrix"
        or _unit_boundary_kind(block[0]) == "table-cell"
    ]
    visual_blocks = [
        block
        for block in body_blocks
        if any(item.kind == "image" for item in block)
        or _unit_group_kind(block[0]).lower() in {"media-card", "process-step", "kpi"}
    ]
    plain_blocks = [
        block
        for block in body_blocks
        if block not in table_blocks and block not in visual_blocks
    ]
    evidence_blocks = [
        block
        for block in visual_blocks
        if any(_is_meaningful_flow_image(item) for item in block)
    ]
    secondary_visual_blocks = [
        block for block in visual_blocks if block not in evidence_blocks
    ]

    selected_component_kinds: dict[int, str] = {}
    for block in blocks:
        group_kind = _unit_group_kind(block[0]).lower()
        has_image = any(item.kind == "image" for item in block)
        preferences = _component_preference(group_kind, has_image=has_image)
        selected = next((kind for kind in preferences if kind in surface_kinds), "bodyFlow")
        selected_component_kinds[id(block)] = selected

    summary_bounds = _flow_mapping(slot, "bounds")
    summary_spacing = _flow_mapping(slot, "spacing")
    summary_width = max(
        1.0,
        _flow_number(summary_bounds.get("width"), _SLIDE_WIDTH_PX)
        - _flow_number(summary_spacing.get("insetLeft"))
        - _flow_number(summary_spacing.get("insetRight")),
    )
    summary_height = max(
        1.0,
        _flow_number(summary_bounds.get("height"), _SLIDE_HEIGHT_PX)
        - _flow_number(summary_spacing.get("insetTop"))
        - _flow_number(summary_spacing.get("insetBottom")),
    )
    body_height_fraction = (
        max(
            1.0,
            summary_height - _FOOTER_LANE_HEIGHT_PX - _FOOTER_BODY_GAP_PX,
        )
        / summary_height
        if footer_blocks
        else 1.0
    )

    plain_fraction = 0.0
    selected_summary: object | None = None
    if plain_blocks and (table_blocks or visual_blocks) and not evidence_blocks:
        plain_units = [unit for block in plain_blocks for unit in block]
        for candidate_fraction in (
            0.08,
            0.12,
            0.16,
            0.20,
            0.25,
            0.30,
            0.35,
            0.40,
            0.45,
            0.50,
            0.55,
            0.60,
            0.65,
            0.70,
            0.75,
        ):
            candidate_summary = lxml_html.Element("div")
            candidate_summary.set("data-qid", f"{root_qid}--component-summary")
            width_fraction = 1.0
            height_fraction = candidate_fraction
            if not table_blocks:
                overlay_padding = _COMPONENT_OVERLAY_PADDING_PX * 2.0
                width_fraction = max(
                    0.05, (summary_width - overlay_padding) / summary_width
                )
                height_fraction = max(
                    0.05,
                    (
                        summary_height * candidate_fraction - overlay_padding
                    )
                    / summary_height,
                )
            try:
                _append_fixed_text_columns(
                    candidate_summary,
                    plain_units,
                    slot=slot,
                    adaptive_scale=adaptive_scale,
                    role="body",
                    columns_count=max(1, min(4, len(plain_blocks))),
                    column_gap=16.0,
                    width_fraction=width_fraction,
                    height_fraction=height_fraction,
                )
            except SourceNativeGenerationError as error:
                if error.code != "source_native_text_capacity_exceeded":
                    raise
                continue
            plain_fraction = candidate_fraction
            selected_summary = candidate_summary
            break
        if selected_summary is None:
            raise SourceNativeGenerationError("source_native_text_capacity_exceeded")
        if table_blocks:
            selected_summary.set(
                "style",
                str(selected_summary.get("style") or "")
                + f";flex:0 0 {plain_fraction * 100.0:.3f}%;min-width:0;min-height:0;overflow:hidden",
            )
            root.append(selected_summary)
        else:
            root.set(
                "style",
                str(root.get("style") or "") + ";position:relative!important",
            )
            selected_summary.set("data-allow-overlap", "true")
            selected_summary.set(
                "style",
                str(selected_summary.get("style") or "")
                + ";position:absolute!important;left:8px;right:8px;bottom:8px;z-index:4;"
                f"height:{plain_fraction * 100.0:.3f}%;"
                f"padding:{_COMPONENT_OVERLAY_PADDING_PX:.3f}px;box-sizing:border-box;"
                "min-width:0;min-height:0;overflow:hidden;border-radius:12px;"
                "background:rgba(15,23,42,.82);color:#fff;backdrop-filter:blur(2px)",
            )
            for element in selected_summary.iterdescendants():
                element.set("data-allow-overlap", "true")

    bounds = _flow_mapping(slot, "bounds")
    spacing = _flow_mapping(slot, "spacing")
    width = max(
        1.0,
        _flow_number(bounds.get("width"), _SLIDE_WIDTH_PX)
        - _flow_number(spacing.get("insetLeft"))
        - _flow_number(spacing.get("insetRight")),
    )
    height = max(
        1.0,
        _flow_number(bounds.get("height"), _SLIDE_HEIGHT_PX)
        - _flow_number(spacing.get("insetTop"))
        - _flow_number(spacing.get("insetBottom")),
    )
    if plain_blocks and (table_blocks or visual_blocks) and not evidence_blocks:
        if table_blocks:
            height = max(1.0, height * (1.0 - plain_fraction) - 12.0)

    if table_blocks:
        capacity = _flow_mapping(slot, "capacityEnvelope")
        minimum_scale = max(
            0.1, min(1.0, _flow_number(capacity.get("minimumTextScale"), 0.70))
        )

        def table_height(scale: float) -> float:
            return sum(
                _estimated_flow_block_height(
                    block,
                    slot=slot,
                    adaptive_scale=scale,
                    role="body",
                    column_width=width,
                )
                for block in table_blocks
            ) + 10.0 * max(0, len(table_blocks) - 1)

        if table_height(minimum_scale) > height:
            raise SourceNativeGenerationError(
                "source_native_table_matrix_capacity_exceeded"
            )
        if table_height(adaptive_scale) <= height:
            table_scale = adaptive_scale
        else:
            lower = minimum_scale
            upper = adaptive_scale
            for _ in range(18):
                midpoint = (lower + upper) / 2.0
                if table_height(midpoint) <= height:
                    lower = midpoint
                else:
                    upper = midpoint
            table_scale = lower
        table_panel = lxml_html.Element("div")
        table_panel.set("data-qid", f"{root_qid}--component-table")
        table_panel.set(
            "style",
            "display:flex!important;flex:1;flex-direction:column;gap:10px;"
            "min-width:0;min-height:0;overflow:hidden",
        )
        for block in table_blocks:
            _append_semantic_block(
                table_panel,
                block,
                slot=slot,
                adaptive_scale=table_scale,
                role="body",
                image_assets=image_assets,
                component_kind=selected_component_kinds[id(block)],
            )
        root.append(table_panel)
    elif evidence_blocks:
        root.set(
            "style",
            str(root.get("style") or "") + ";position:relative!important",
        )
        evidence_count = len(evidence_blocks)
        evidence_width_fraction = (
            1.0
            if evidence_count == 1
            else 0.60
            if evidence_count == 2
            else 0.55
            if evidence_count == 3
            else 0.52
        )
        evidence_height_fraction = 1.0 if evidence_count == 1 else 0.82
        evidence_cell_width = width * evidence_width_fraction
        evidence_cell_height = height * evidence_height_fraction
        if not all(
            _meaningful_image_geometry_pass(
                unit,
                cell_width=evidence_cell_width,
                cell_height=evidence_cell_height,
            )
            for block in evidence_blocks
            for unit in block
            if unit.kind == "image"
        ):
            raise SourceNativeGenerationError(
                "source_native_meaningful_image_capacity_exceeded"
            )
        overlap_percent = (
            0.0
            if evidence_count <= 1
            else (evidence_count * evidence_width_fraction - 1.0)
            / (evidence_count - 1)
            * 100.0
        )
        gallery = lxml_html.Element("div")
        gallery.set("data-qid", f"{root_qid}--evidence-gallery")
        gallery.set("data-allow-overlap", "true")
        gallery.set(
            "style",
            "position:absolute!important;inset:0;z-index:1;display:flex!important;"
            "align-items:center;justify-content:center;min-width:0;min-height:0;overflow:hidden",
        )
        capacity = _flow_mapping(slot, "capacityEnvelope")
        evidence_scale = max(
            0.1, min(1.0, _flow_number(capacity.get("minimumTextScale"), 0.70))
        )
        for index, block in enumerate(evidence_blocks):
            _append_semantic_block(
                gallery,
                block,
                slot=slot,
                adaptive_scale=evidence_scale,
                role="body",
                image_assets=image_assets,
                component_kind=selected_component_kinds[id(block)],
            )
            evidence_wrapper = gallery[-1]
            evidence_wrapper.set(
                "style",
                str(evidence_wrapper.get("style") or "")
                + f";flex:0 0 {evidence_width_fraction * 100.0:.3f}%;"
                f"height:{evidence_height_fraction * 100.0:.3f}%;"
                + (
                    f"margin-left:-{overlap_percent:.3f}%;"
                    if index > 0
                    else ""
                )
                + f"z-index:{index + 1}",
            )
        root.append(gallery)

        overlay_blocks = [*plain_blocks, *secondary_visual_blocks]
        if overlay_blocks:
            horizontal_gap = 12.0
            vertical_gap = 8.0
            maximum_overlay_columns = min(
                3,
                len(overlay_blocks),
                max(1, int((width + horizontal_gap) // 280.0)),
            )
            overlay_height = max(1.0, height - 16.0)
            overlay_options: list[
                tuple[float, int, float, tuple[int, ...]]
            ] = []

            def overlay_layout(
                scale: float, columns: int
            ) -> tuple[float, tuple[int, ...]]:
                overlay_width = max(
                    1.0,
                    (
                        width
                        - 16.0
                        - horizontal_gap * max(0, columns - 1)
                    )
                    / columns,
                )
                weights = [
                    _estimated_flow_block_height(
                        block,
                        slot=slot,
                        adaptive_scale=scale,
                        role="body",
                        column_width=overlay_width,
                    )
                    for block in overlay_blocks
                ]
                weighted = [value + vertical_gap for value in weights]
                ends = _balanced_contiguous_ends(weighted, columns)
                start = 0
                maximum = 0.0
                for end in ends:
                    maximum = max(
                        maximum,
                        max(0.0, sum(weighted[start:end]) - vertical_gap),
                    )
                    start = end
                return maximum, ends

            for columns in range(1, maximum_overlay_columns + 1):
                minimum_height, minimum_ends = overlay_layout(
                    evidence_scale, columns
                )
                if minimum_height > overlay_height:
                    continue
                maximum_height, maximum_ends = overlay_layout(
                    adaptive_scale, columns
                )
                if maximum_height <= overlay_height:
                    overlay_options.append(
                        (
                            adaptive_scale,
                            columns,
                            maximum_height,
                            maximum_ends,
                        )
                    )
                    continue
                lower = evidence_scale
                upper = adaptive_scale
                selected_height = minimum_height
                selected_ends = minimum_ends
                for _ in range(18):
                    midpoint = (lower + upper) / 2.0
                    midpoint_height, midpoint_ends = overlay_layout(
                        midpoint, columns
                    )
                    if midpoint_height <= overlay_height:
                        lower = midpoint
                        selected_height = midpoint_height
                        selected_ends = midpoint_ends
                    else:
                        upper = midpoint
                overlay_options.append(
                    (lower, columns, selected_height, selected_ends)
                )
            if not overlay_options:
                raise SourceNativeGenerationError(
                    "source_native_text_capacity_exceeded"
                )
            overlay_scale, overlay_columns, _, overlay_ends = max(
                overlay_options,
                key=lambda item: (
                    round(item[0], 6),
                    item[1],
                    -round(item[2], 6),
                ),
            )
            overlay = lxml_html.Element("div")
            overlay.set("data-qid", f"{root_qid}--evidence-overlay")
            overlay.set("data-allow-overlap", "true")
            overlay.set(
                "style",
                "position:absolute!important;inset:8px;z-index:3;padding:8px;box-sizing:border-box;"
                "display:grid!important;min-width:0;min-height:0;overflow:hidden;"
                f"grid-template-columns:repeat({overlay_columns},minmax(0,1fr));"
                f"gap:0 {horizontal_gap:.3f}px;border-radius:12px;"
                "background:linear-gradient(180deg,rgba(15,23,42,.30),rgba(15,23,42,.72));"
                "color:#fff;backdrop-filter:blur(1.5px)",
            )
            start = 0
            for column_index, end in enumerate(overlay_ends):
                column = lxml_html.Element("div")
                column.set(
                    "data-qid",
                    f"{root_qid}--evidence-overlay-column-{column_index + 1}",
                )
                column.set("data-allow-overlap", "true")
                column.set(
                    "style",
                    "display:flex!important;flex-direction:column;gap:8px;"
                    "min-width:0;min-height:0;overflow:hidden",
                )
                for block in overlay_blocks[start:end]:
                    _append_semantic_block(
                        column,
                        block,
                        slot=slot,
                        adaptive_scale=overlay_scale,
                        role="body",
                        image_assets=image_assets,
                        component_kind=selected_component_kinds[id(block)],
                    )
                overlay.append(column)
                start = end
            for element in overlay.iterdescendants():
                element.set("data-allow-overlap", "true")
            root.append(overlay)
    elif visual_blocks:
        gap = _COMPONENT_VISUAL_GAP_PX
        maximum_columns = min(
            4,
            len(visual_blocks),
            max(1, int((width + gap) // 280.0)),
        )
        capacity = _flow_mapping(slot, "capacityEnvelope")
        minimum_scale = max(
            0.1, min(1.0, _flow_number(capacity.get("minimumTextScale"), 0.70))
        )
        maximum_scale = 1.0

        def visual_layout(
            scale: float, columns: int
        ) -> tuple[float, tuple[int, ...]]:
            column_width = max(
                1.0,
                (width - gap * max(0, columns - 1)) / columns,
            )
            weights = [
                _estimated_flow_block_height(
                    block,
                    slot=slot,
                    adaptive_scale=scale,
                    role="body",
                    column_width=column_width,
                )
                for block in visual_blocks
            ]
            weighted = [value + gap for value in weights]
            ends = _balanced_contiguous_ends(weighted, columns)
            start = 0
            maximum = 0.0
            for end in ends:
                maximum = max(
                    maximum,
                    max(0.0, sum(weighted[start:end]) - gap),
                )
                start = end
            return maximum, ends

        def visual_row_layout(scale: float, columns: int) -> float:
            column_width = max(
                1.0,
                (width - gap * max(0, columns - 1)) / columns,
            )
            weights = [
                _estimated_flow_block_height(
                    block,
                    slot=slot,
                    adaptive_scale=scale,
                    role="body",
                    column_width=column_width,
                )
                for block in visual_blocks
            ]
            row_heights = [
                max(weights[start : start + columns])
                for start in range(0, len(weights), columns)
            ]
            return sum(row_heights) + gap * max(0, len(row_heights) - 1)

        options: list[tuple[float, int, float, str, tuple[int, ...]]] = []
        for columns in range(1, maximum_columns + 1):
            for packing in ("columns", "rows"):
                def evaluate(scale: float) -> tuple[float, tuple[int, ...]]:
                    if packing == "rows":
                        return visual_row_layout(scale, columns), ()
                    return visual_layout(scale, columns)

                minimum_height, minimum_ends = evaluate(minimum_scale)
                if minimum_height > height:
                    continue
                maximum_height, maximum_ends = evaluate(maximum_scale)
                if maximum_height <= height:
                    options.append(
                        (
                            maximum_scale,
                            columns,
                            maximum_height,
                            packing,
                            maximum_ends,
                        )
                    )
                    continue
                lower = minimum_scale
                upper = maximum_scale
                selected_height = minimum_height
                selected_ends = minimum_ends
                for _ in range(18):
                    midpoint = (lower + upper) / 2.0
                    midpoint_height, midpoint_ends = evaluate(midpoint)
                    if midpoint_height <= height:
                        lower = midpoint
                        selected_height = midpoint_height
                        selected_ends = midpoint_ends
                    else:
                        upper = midpoint
                options.append(
                    (lower, columns, selected_height, packing, selected_ends)
                )
        if not options:
            raise SourceNativeGenerationError("source_native_text_capacity_exceeded")
        visual_scale, selected_columns, _, selected_packing, selected_ends = max(
            options,
            key=lambda item: (
                round(item[0], 6),
                item[1],
                -round(item[2], 6),
                1 if item[3] == "columns" else 0,
            ),
        )
        grid = lxml_html.Element("div")
        grid.set("data-qid", f"{root_qid}--component-grid")
        grid.set(
            "style",
            "display:grid!important;flex:1;min-width:0;min-height:0;overflow:hidden;"
            f"grid-template-columns:repeat({selected_columns},minmax(0,1fr));"
            "grid-auto-rows:max-content;align-content:start;"
            f"gap:{gap:.3f}px",
        )
        if selected_packing == "rows":
            for block in visual_blocks:
                _append_semantic_block(
                    grid,
                    block,
                    slot=slot,
                    adaptive_scale=visual_scale,
                    role="body",
                    image_assets=image_assets,
                    component_kind=selected_component_kinds[id(block)],
                )
        else:
            start = 0
            for column_index, end in enumerate(selected_ends):
                column = lxml_html.Element("div")
                column.set(
                    "data-qid",
                    f"{root_qid}--component-column-{column_index + 1}",
                )
                column.set(
                    "style",
                    f"display:flex!important;flex-direction:column;gap:{gap:.3f}px;"
                    "min-width:0;min-height:0;overflow:hidden",
                )
                for block in visual_blocks[start:end]:
                    _append_semantic_block(
                        column,
                        block,
                        slot=slot,
                        adaptive_scale=visual_scale,
                        role="body",
                        image_assets=image_assets,
                        component_kind=selected_component_kinds[id(block)],
                    )
                grid.append(column)
                start = end
        root.append(grid)
        if selected_summary is not None:
            root.append(selected_summary)
    elif plain_blocks:
        plain = lxml_html.Element("div")
        plain.set("data-qid", f"{root_qid}--component-body")
        _append_fixed_text_columns(
            plain,
            [unit for block in plain_blocks for unit in block],
            slot=slot,
            adaptive_scale=adaptive_scale,
            role="body",
            columns_count=max(1, min(3, len(plain_blocks))),
            column_gap=16.0,
            height_fraction=body_height_fraction,
        )
        plain.set(
            "style",
            str(plain.get("style") or "")
            + ";flex:1 1 0;height:auto!important",
        )
        root.append(plain)

    if footer_blocks:
        footer_lane = lxml_html.Element("div")
        footer_lane.set(
            "style",
            "display:flex!important;align-items:center;gap:16px;height:23px;"
            "flex:0 0 23px;"
            "min-width:0;overflow:hidden;white-space:nowrap",
        )
        for block in footer_blocks:
            _append_semantic_block(
                footer_lane,
                block,
                slot=slot,
                adaptive_scale=adaptive_scale,
                role="body",
                image_assets=image_assets,
                component_kind="footerBand",
            )
        root.append(footer_lane)
    parent.append(root)


def _patch_exact_slot(
    node: object,
    units: list[SealedContentUnit],
    *,
    slot: Mapping[str, object] | None = None,
    image_assets: Mapping[str, Mapping[str, object]] | None = None,
    component_adaptive_scale: float = 1.0,
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
    flow_kind = str((slot or {}).get("sourceFlowLayout") or "")
    if flow_kind == "components":
        if slot is None:
            raise SourceNativeGenerationError("source_native_slot_contract_missing")
        for child in list(node):
            node.remove(child)
        node.text = None
        _append_component_canvas(
            node,
            units,
            slot=slot,
            adaptive_scale=component_adaptive_scale,
            image_assets=image_assets,
        )
        existing_style = str(node.get("style") or "").rstrip(";")
        node.set(
            "style",
            f"{existing_style};{_flow_overlay_css(slot)};display:block!important;"
            "white-space:normal!important;z-index:50!important",
        )
        node.set(
            "data-asset-occurrence-ids",
            "|".join(item.asset_occurrence_id or "" for item in image_units),
        )
        node.set("data-content-unit-ids", "|".join(item.content_unit_id for item in units))
        node.set("data-content-unit-digests", "|".join(item.content_digest for item in units))
        return
    if image_units and text_units:
        if slot is None or str(slot.get("sourceFlowLayout") or "") != "text":
            raise SourceNativeGenerationError("source_native_mixed_image_slot")
        # The source-flow renderer performs the authoritative bounded
        # width/height/column search below.  The legacy character-area helper
        # is intentionally not used as a pre-gate here because it can reject
        # layouts that the exact geometry search fits at the readable floor.
        maximum_scale = 1.0
        assets = image_assets or {}
        for child in list(node):
            node.remove(child)
        node.text = None
        flow_qid = str(node.get("data-qid") or "source-flow")
        wrapper = lxml_html.Element("div")
        wrapper.set("data-qid", f"{flow_qid}--mixed")
        text_weight = max(1.0, sum(len(_unit_render_text(item)) for item in text_units) / 220.0)
        image_weight = max(1.0, float(len(image_units)))
        image_fraction = max(0.28, min(0.48, image_weight / (text_weight + image_weight)))
        hints = _flow_mapping(slot, "layoutHints")
        text_columns = max(1, min(2, int(_flow_number(hints.get("columnCount"), 1))))
        role = _flow_role(slot)
        vertical_image_columns = max(1, min(4, len(image_units)))
        vertical_image_rows = max(
            1, math.ceil(len(image_units) / vertical_image_columns)
        )
        vertical_image_fraction = max(
            0.20, min(0.42, 0.16 + 0.08 * vertical_image_rows)
        )
        candidates: list[tuple[float, str, object, float]] = []
        for layout, width_fraction, height_fraction in (
            ("horizontal", 1.0 - image_fraction, 1.0),
            ("vertical", 1.0, 1.0 - vertical_image_fraction),
        ):
            candidate_panel = lxml_html.Element("div")
            candidate_panel.set("data-qid", f"{flow_qid}--mixed-text")
            try:
                readable_scale = _append_fixed_text_columns(
                    candidate_panel,
                    text_units,
                    slot=slot,
                    adaptive_scale=maximum_scale,
                    role=role,
                    columns_count=text_columns,
                    column_gap=14.0,
                    width_fraction=width_fraction,
                    height_fraction=height_fraction,
                )
            except SourceNativeGenerationError as error:
                if error.code != "source_native_text_capacity_exceeded":
                    raise
                continue
            candidates.append(
                (readable_scale, layout, candidate_panel, height_fraction)
            )
        if not candidates:
            raise SourceNativeGenerationError("source_native_text_readability_failed")
        _readable_scale, selected_layout, text_panel, text_fraction = max(
            candidates,
            key=lambda item: (
                round(item[0], 6),
                item[1] == ("vertical" if len(image_units) >= 4 else "horizontal"),
                item[1],
            ),
        )
        if selected_layout == "vertical":
            wrapper.set(
                "style",
                "display:grid!important;height:100%;width:100%;min-width:0;min-height:0;overflow:hidden;"
                f"grid-template-rows:minmax(0,{text_fraction:.4f}fr) minmax(0,{(1.0 - text_fraction):.4f}fr);"
                "row-gap:14px",
            )
        else:
            wrapper.set(
                "style",
                "display:grid!important;height:100%;width:100%;min-width:0;min-height:0;overflow:hidden;"
                f"grid-template-columns:minmax(0,{(1.0 - image_fraction):.4f}fr) minmax(0,{image_fraction:.4f}fr);"
                "column-gap:14px",
            )
        image_panel = lxml_html.Element("div")
        image_panel.set(
            "data-qid",
            f"{str(node.get('data-qid') or 'source-flow')}--mixed-images",
        )
        image_columns = (
            vertical_image_columns
            if selected_layout == "vertical"
            else max(1, min(3, math.ceil(len(image_units) ** 0.5)))
        )
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
    source_flow = str((slot or {}).get("sourceFlowLayout") or "")
    adaptive_scale = (
        1.0
        if source_flow == "text"
        else largest_fitting_text_scale(slot, units)
    )
    if adaptive_scale is None:
        raise SourceNativeGenerationError("source_native_text_capacity_exceeded")
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
    renderer_geometry_scale: float = 1.0,
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
        if flow_kind not in {"text", "images", "components"}:
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
            component_adaptive_scale=renderer_geometry_scale,
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
                "sourceFlowLayout": slot.get("sourceFlowLayout"),
                "componentKind": slot.get("sourceFlowComponentKind"),
                "componentGraphDigest": slot.get("sourceFlowComponentGraphDigest"),
                "semanticGroupIds": list(
                    dict.fromkeys(
                        group_id
                        for item in assigned
                        for group_id in (_unit_group_id(item),)
                        if group_id
                    )
                ),
            }
        )
    expected_groups: dict[str, list[str]] = {}
    for unit in units:
        group_id = _unit_group_id(unit)
        if group_id:
            expected_groups.setdefault(group_id, []).append(unit.content_unit_id)
    for group_id, expected_unit_ids in expected_groups.items():
        wrappers = document.xpath(
            "//*[@data-source-semantic-group-id=$group_id]", group_id=group_id
        )
        if len(wrappers) != 1:
            raise SourceNativeGenerationError("source_native_semantic_group_not_atomic")
        observed_unit_ids = [
            item
            for item in str(wrappers[0].get("data-content-unit-ids") or "").split("|")
            if item
        ]
        if observed_unit_ids != expected_unit_ids:
            raise SourceNativeGenerationError("source_native_semantic_group_not_atomic")
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
        if item.get("sourceFlowLayout") in {"text", "images", "components"}
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
        or str(item.get("qid") or "").endswith(":key-message")
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
        image_kind = str(item.get("source_image_kind") or "media").strip().lower()
        meaningful = image_kind != "icon" and image_kind in {
            "evidence",
            "ui",
            "screenshot",
            "product-screen",
        }
        if meaningful:
            image_box = _quality_bbox(item.get("bbox"))
            rendered_width = image_box["width"]
            rendered_height = image_box["height"]
            rendered_area = _bbox_area(image_box)
            slide_scale = min(
                rendered_width / viewport_width,
                rendered_height / viewport_height,
            )
            dimension_ratio = min(
                rendered_width / _MEANINGFUL_IMAGE_MIN_WIDTH_PX,
                rendered_height / _MEANINGFUL_IMAGE_MIN_HEIGHT_PX,
            )
            area_ratio = rendered_area / max(
                1.0, slide_area * _MEANINGFUL_IMAGE_MIN_AREA_RATIO
            )
            scale_ratio = slide_scale / _MEANINGFUL_IMAGE_SCALE_FLOOR
            image_size_scores.append(
                min(1.0, max(dimension_ratio, min(area_ratio, scale_ratio)))
            )
            dimension_pass = (
                rendered_width >= _MEANINGFUL_IMAGE_MIN_WIDTH_PX
                and rendered_height >= _MEANINGFUL_IMAGE_MIN_HEIGHT_PX
            )
            area_scale_pass = (
                rendered_area >= slide_area * _MEANINGFUL_IMAGE_MIN_AREA_RATIO
                and slide_scale >= _MEANINGFUL_IMAGE_SCALE_FLOOR
            )
            if not (dimension_pass or area_scale_pass):
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
        "meaningfulImageCount": len(image_size_scores),
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
        capacity_pages = tuple(
            item
            for item in self._capacity_admission_receipt.get("pages", ())
            if isinstance(item, Mapping)
        )
        for slide_index, (narrative_slide, selected) in enumerate(
            zip(narrative_slides, selected_slides, strict=True)
        ):
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
            renderer_geometry_scale = 1.0
            if capacity_pages:
                expected_content_unit_ids = tuple(
                    str(item)
                    for item in capacity_pages[slide_index].get(
                        "expectedContentUnitIds", ()
                    )
                )
                rendered_content_unit_ids = tuple(
                    unit.content_unit_id for unit in units
                )
                if expected_content_unit_ids != rendered_content_unit_ids:
                    raise SourceNativeGenerationError(
                        "source_verbatim_capacity_scale_binding_invalid"
                    )
                try:
                    renderer_geometry_scale = float(
                        capacity_pages[slide_index]["selectedRendererGeometryScale"]
                    )
                except (IndexError, KeyError, TypeError, ValueError) as error:
                    raise SourceNativeGenerationError(
                        "source_verbatim_capacity_scale_binding_invalid"
                    ) from error
                if not 0.1 <= renderer_geometry_scale <= 1.0:
                    raise SourceNativeGenerationError(
                        "source_verbatim_capacity_scale_binding_invalid"
                    )
            html, semantic, protected_before, protected_after = _apply_composition_plan(
                str(frame["html"]),
                plan=plan,
                units=units,
                slots=slots,
                image_assets=image_assets,
                renderer_geometry_scale=renderer_geometry_scale,
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
                blocking_qids = {
                    str(qid)
                    for issue in blocking
                    for qid in (getattr(issue, "qids", ()) or ())
                    if str(qid)
                }
                metric_by_qid = {
                    str(item.get("qid") or ""): item
                    for item in normalized.get("elements", ())
                    if isinstance(item, Mapping) and str(item.get("qid") or "")
                }
                diagnostic_qids = set(blocking_qids)
                frontier = set(blocking_qids)
                for _ in range(4):
                    frontier = {
                        str(metric_by_qid[qid].get("parent_qid") or "")
                        for qid in frontier
                        if qid in metric_by_qid
                        and str(metric_by_qid[qid].get("parent_qid") or "")
                    }
                    diagnostic_qids.update(frontier)
                diagnostics = [
                    {
                        key: item.get(key)
                        for key in (
                            "qid",
                            "parent_qid",
                            "bbox",
                            "clientHeight",
                            "scrollHeight",
                            "glyph_clipped",
                            "line_box_clipped",
                            "glyph_clip_cause",
                            "text_fragments",
                            "font_size",
                            "computed_style",
                        )
                    }
                    for qid in sorted(diagnostic_qids)
                    if (item := metric_by_qid.get(qid)) is not None
                ]
                logger.warning(
                    "Source-native Chromium quality gate failed slide=%s viewport=%s fonts_ready=%s telemetry=%s issues=%s metrics=%s",
                    narrative_slide.slide_key,
                    normalized.get("viewport"),
                    normalized.get("fonts_ready"),
                    report.telemetry_status,
                    [
                        issue.to_dict()
                        for issue in blocking[:20]
                    ],
                    diagnostics,
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
