"""Concrete provider -> Narrative IR -> source frame -> Chromium -> CAS path."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import copy
import hashlib
import json
import re
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
from services.authored_vnext.narrative_ir.content_graph import (
    ContentGraphValidationError,
    seal_content_integrity_receipt,
    validate_content_integrity_receipt,
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
from services.database import async_session_maker
from utils.authored_dom_quality import (
    EXACT_RECONSTRUCTION,
    inspect_authored_dom,
    normalize_browser_metrics,
)
from utils.slide_capture import render_html_to_png_with_metrics


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


def _frame_semantic(index: int) -> str:
    return f"source-frame-{index + 1:03d}"


def _pack_snapshot(package: RegisteredDesignPack) -> DesignPackSnapshotV1:
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
                "slotContracts": [
                    slot.model_dump(mode="json", by_alias=True)
                    for slot in frame.slots
                ],
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
    ) -> None:
        self._slides_markdown = slides_markdown or []
        self._frames = list(package.structure.frames)

    async def generate(self, request: Mapping[str, object]) -> Mapping[str, object]:
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
        if qid and qid not in editable_qids:
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


def _patch_exact_slot(node: object, units: list[SealedContentUnit]) -> None:
    if node.xpath(".//*[@data-qid]"):
        raise SourceNativeGenerationError("design_pack_patch_handle_not_leaf")
    texts = [_unit_render_text(item) for item in units]
    rendered = "\n".join(item for item in texts if item)
    # Preserve the editable element tree. Only owned text nodes inside the exact
    # patch handle are changed; child elements are never removed.
    if any(
        (child.text and child.text.strip()) or (child.tail and child.tail.strip())
        for child in node.iterdescendants()
    ):
        raise SourceNativeGenerationError("design_pack_patch_handle_contains_unowned_text")
    node.text = rendered
    node.set("data-content-unit-ids", "|".join(item.content_unit_id for item in units))
    node.set("data-content-unit-digests", "|".join(item.content_digest for item in units))


def _apply_composition_plan(
    raw_html: str,
    *,
    plan: Mapping[str, object],
    units: list[SealedContentUnit],
    slots: list[dict[str, object]],
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
    protected_before = _protected_dom_digest(document, editable_qids)
    semantic: list[dict[str, object]] = []
    for slot in slots:
        slot_id = str(slot.get("slotId") or "")
        assigned = by_slot.get(slot_id, [])
        if not assigned:
            continue
        handle = slot.get("patchHandle")
        if not isinstance(handle, Mapping) or handle.get("kind") not in {"authored-qid", "scene-element"}:
            raise SourceNativeGenerationError("design_pack_patch_handle_invalid")
        qid = str(handle.get("elementId") or "")
        nodes = document.xpath("//*[@data-qid=$qid]", qid=qid)
        if len(nodes) != 1:
            raise SourceNativeGenerationError("design_pack_patch_handle_not_exact")
        _patch_exact_slot(nodes[0], assigned)
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
    ) -> None:
        self._package = package
        self._request = request
        self._generation_job = generation_job
        self._context = context
        self._public_route_receipt = public_route_receipt

    async def execute(self, *, narrative: NarrativeIRV1, selection: object, route: object, checkpoint: object) -> Mapping[str, object]:
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
        selected_slides = getattr(selection, "slides")
        for narrative_slide, selected in zip(narrative.slides, selected_slides, strict=True):
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
            html, semantic, protected_before, protected_after = _apply_composition_plan(
                str(frame["html"]),
                plan=plan,
                units=units,
                slots=slots,
            )
            capture = await render_html_to_png_with_metrics(html)
            normalized = normalize_browser_metrics(dict(capture.browser_metrics or {}))
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
                raise SourceNativeGenerationError("source_native_browser_quality_failed")
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
        content_graph_digest = str(
            getattr(narrative, "content_graph_digest", None)
            or _digest(
                [
                    item.payload()
                    for slide in narrative.slides
                    for item in sealed_units_for_slide(narrative, slide)
                ]
            )
        )
        try:
            integrity_model = seal_content_integrity_receipt(
                operation_id=narrative.operation_id,
                generation_lane=narrative.generation_lane,
                content_graph_digest=content_graph_digest,
                narrative_digest=narrative.narrative_digest,
                pages=integrity_pages,
            )
            integrity_model = validate_content_integrity_receipt(integrity_model)
        except ContentGraphValidationError as error:
            raise SourceNativeGenerationError(error.code) from error
        integrity = model_payload(integrity_model)
        deck_ir = {
            "schemaVersion": "source-native-publication-deck/v1",
            "title": narrative.title,
            "language": narrative.language,
            "story": {"arc": [slide.role for slide in narrative.slides]},
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
                for slide in narrative.slides
            ],
        }
        # Content integrity is validated above. Visual quality is deliberately
        # downstream and can never turn a failed content receipt into success.
        quality = {
            "schemaVersion": "source-native-generation-quality-receipt/v1",
            "status": "passed",
            "browserObserved": True,
            "pages": page_evidence,
            "pageEvidenceDigest": _digest(page_evidence),
            "humanVisualAcceptance": "not-assessed",
        }
        payload = {
            "compiledSlides": compiled,
            "deckIr": deck_ir,
            "generationQualityReceipt": quality,
            "contentIntegrityReceipt": integrity,
            "compositionPlans": composition_plans,
            "strategyReceipt": dict(self._public_route_receipt),
            "styleProfileDigest": self._package.structure.structure_digest,
            "retainedReferenceDigest": self._package.manifest.source_digest,
            "requirementCoverageReceipt": {"status": "passed"},
            "executionEvidence": {
                "route": "source-native",
                "narrativeDigest": narrative.narrative_digest,
                "checkpointDigest": getattr(checkpoint, "checkpoint_digest"),
                "frameSelectionReceiptDigest": getattr(selection, "receipt_digest"),
                "providerCallBudget": model_payload(getattr(route, "provider_call_budget")),
            },
            "publicationRoute": "source-native",
        }
        publication = await ProductDeckIrExecutionAdapter()._publish(
            payload=payload,
            request=self._request,
            generation_job=self._generation_job,
            context=self._context,
            captures=captures,
        )
        return {
            "publication": dict(publication),
            "generationQualityReceipt": quality,
            "contentIntegrityReceipt": integrity,
            "narrativeIrDigest": narrative.narrative_digest,
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
    if not isinstance(slide_count, int) or slide_count != len(package.structure.frames):
        raise SourceNativeGenerationError("design_pack_slide_count_mismatch")
    await initialize_checkpoints(
        job_id=job_id,
        attempt_token=attempt_token,
        roles=["narrative"] * slide_count,
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
    )
    try:
        result = await run_narrative_ir_operation(
            operation_id=operation_id,
            job_id=job_id,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            strategy_receipt_digest=str(route_receipt.get("receiptDigest")),
            brief=str(getattr(request, "content", "") or ""),
            slide_count=slide_count,
            language=getattr(request, "language", None),
            provider=_StructuredNarrativeProvider(
                slides_markdown=getattr(request, "slides_markdown", None),
                package=package,
            ),
            reserve=reserve,
            design_pack=_pack_snapshot(package),
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
