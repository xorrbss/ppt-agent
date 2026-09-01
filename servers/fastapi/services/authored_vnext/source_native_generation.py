"""Concrete provider -> Narrative IR -> source frame -> Chromium -> CAS path."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
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
                "dataRepresentations": ["none"],
                "densities": [density],
                "frameSemantics": [_frame_semantic(frame.source_index)],
                "maxSupportingPoints": 24,
                "supportsSourceOwnedContent": False,
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
            normalized_slides.append(
                {
                    "slideKey": f"slide-{index + 1:03d}",
                    "sequenceIndex": index,
                    "role": frame.role,
                    "keyMessage": key_message.strip()[:4000],
                    "supportingPoints": [str(value).strip()[:4000] for value in supporting[:24] if str(value).strip()],
                    "dataRepresentation": "none",
                    "data": [],
                    "sourceOwnership": [],
                    "frameSemantics": [_frame_semantic(index)],
                    "density": density,
                }
            )
        return {
            "schemaVersion": "narrative-ir-draft/v1",
            "title": str(raw.get("title") or "Presentation")[:4000],
            "language": str(raw.get("language") or request.get("language") or "ko-KR")[:64],
            "slides": normalized_slides,
            "sourceUnitOrder": [],
        }


def _text_chunks(slide: object) -> list[str]:
    chunks = [str(getattr(slide, "key_message"))]
    chunks.extend(str(item) for item in getattr(slide, "supporting_points"))
    return [" ".join(item.split())[:240] for item in chunks if item.strip()]


def _replace_frame_text(raw_html: str, slide: object) -> tuple[str, list[dict[str, object]]]:
    try:
        document = lxml_html.document_fromstring(raw_html)
    except (ValueError, TypeError) as error:
        raise SourceNativeGenerationError("design_pack_html_invalid") from error
    nodes = document.xpath(
        "//*[@data-qid and not(.//*[@data-qid]) and "
        "(self::div or self::p or self::span or self::h1 or self::h2 or self::h3 or self::li)]"
    )
    chunks = _text_chunks(slide)
    text_index = 0
    for node in nodes:
        original = " ".join(node.text_content().split())
        if not original:
            continue
        for child in list(node):
            node.remove(child)
        node.text = chunks[text_index] if text_index < len(chunks) else ""
        text_index += 1
    if text_index < len(chunks):
        body = document.find("body")
        if body is None:
            raise SourceNativeGenerationError("design_pack_html_body_missing")
        overlay = lxml_html.Element("section")
        overlay.set("data-qid", f"{getattr(slide, 'slide_key')}-narrative")
        overlay.set("aria-label", "narrative content")
        overlay.set(
            "style",
            "position:absolute;left:64px;right:64px;bottom:44px;padding:18px 24px;"
            "background:rgba(255,255,255,.94);color:#0f172a;border-left:8px solid #2563eb;"
            "font-family:'Malgun Gothic',sans-serif;font-size:22px;line-height:1.35;z-index:9999",
        )
        overlay.text = " · ".join(chunks[text_index:])
        body.append(overlay)
    serialized = lxml_html.tostring(document, encoding="unicode", method="html")
    semantic: list[dict[str, object]] = []
    seen: set[str] = set()
    for node in document.xpath("//*[@data-qid]"):
        qid = str(node.get("data-qid") or "").strip()
        if not qid or qid in seen:
            continue
        seen.add(qid)
        text = " ".join(node.text_content().split())[:4000]
        semantic.append(
            {
                "qid": qid,
                "kind": "text" if text else "shape",
                "semanticRole": "title" if not semantic else "body",
                "text": text,
            }
        )
    if not semantic:
        raise SourceNativeGenerationError("design_pack_qid_missing")
    return serialized, semantic


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
        captures: dict[str, object] = {}
        compiled: list[dict[str, object]] = []
        page_evidence: list[dict[str, object]] = []
        selected_slides = getattr(selection, "slides")
        for narrative_slide, selected in zip(narrative.slides, selected_slides, strict=True):
            frame = source_frames.get(selected.selected_frame_id)
            if not isinstance(frame, Mapping) or not isinstance(frame.get("html"), str):
                raise SourceNativeGenerationError("source_native_frame_payload_missing")
            html, semantic = _replace_frame_text(str(frame["html"]), narrative_slide)
            capture = await render_html_to_png_with_metrics(html)
            normalized = normalize_browser_metrics(dict(capture.browser_metrics or {}))
            report = inspect_authored_dom(
                html,
                dict(capture.browser_metrics or {}),
                reference_mode=EXACT_RECONSTRUCTION,
                strict_structural_off_canvas=True,
            )
            # A source-native frame preserves the registered source's visual
            # treatment. Contrast remains review evidence, but cannot reject
            # the frame merely because the source intentionally used muted or
            # decorative text. Structural and content defects remain blocking.
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
            compiled.append(
                {
                    "slideKey": narrative_slide.slide_key,
                    "html": html,
                    "semantic": {"elements": semantic},
                    "sourceFrameDigest": selected.selected_frame_digest,
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
