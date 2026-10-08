"""Fenced advisory review of saved SVG renders, with no slide replacement."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from time import monotonic
import uuid

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from constants.user_messages import UserMessageKey, user_message
from models.svg_quality_review import SvgQualityReviewRequest
from models.sql.async_presentation_generation_status import AsyncPresentationGenerationTaskModel
from models.sql.presentation import PresentationModel, PRESENTATION_LIFECYCLE_DELETED
from models.sql.slide import SlideModel
from models.sql.workspace import WorkspaceOperation
from services.generation_core.quality.advisory_record import advisory_record, advisory_ran, content_digest
from services.generation_core.quality.vision_advisory import run_vision_advisory
from services.presentation_access_service import require_live_presentation
from services.presentation_publication_service import PresentationPublicationConflict, publish_presentation_revision
from services.provider_usage_instrumentation import public_operation_scope, provider_usage_scope, provider_operation_usage_scope
from services.svg_contract import source_digest
from services.svg_export_visual_review import (
    assemble_page_advisory, current_page_advisories,
    slide_page_digests, split_page_advisories, svg_quality_status, page_measurement_current,
)
from services.svg_presentation_checkpoint import _read_png
from services.workspace_authored_async_admission import admit_workspace_authored_task
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError, WorkspaceOperationLeaseLost, WorkspaceOperationResult,
)
from services.workspace_operation_runner import sanitize_workspace_event_payload
from utils.datetime_utils import get_current_utc_datetime


def _positions(request, count):
    indices = list(range(count)) if request.scope == "all" else request.slide_indices
    if not indices or any(index >= count for index in indices):
        raise ValueError("SVG review slide index is out of range")
    return indices


async def _slides(session, presentation_id):
    return list((await session.execute(select(SlideModel).where(
        SlideModel.presentation == presentation_id).order_by(SlideModel.index, SlideModel.id))).scalars().all())


async def _prepare_quality_recovery_review(session, presentation, slides, request, operation_id, owner_scope):
    from services.svg_authoring_profile import read_quality_recovery_policy
    if request.target != "source_svg" or not read_quality_recovery_policy(
            ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot") or {}):
        return None
    from services.svg_quality_recovery_review import prepare_saved_review
    from services.svg_export_operation import require_quality_recovery_source_parent
    prepared = prepare_saved_review(presentation, slides,
        _positions(request, len(slides)) if request.scope != "all" else [],
        [_review_png(slide) for slide in slides], operation_id=operation_id, owner_scope=owner_scope)
    await require_quality_recovery_source_parent(session, presentation, prepared["publication"])
    from services.authored_canvas import presentation_canvas_contract
    from services.svg_export_source_preservation import source_preservation_receipts, sealed_whole_page_manifest
    from services.public_export_errors import PublicExportOperationError
    try:
        receipts = source_preservation_receipts(presentation, slides, presentation_canvas_contract(presentation.theme))
        await sealed_whole_page_manifest(session, claim=SimpleNamespace(owner_id=presentation.owner_id,
            owner_scope=owner_scope), presentation=presentation, slides=slides, receipts=receipts)
    except PublicExportOperationError as error:
        raise ValueError(str(error)) from error
    return prepared


async def queue_svg_quality_review(presentation_id: uuid.UUID, request: SvgQualityReviewRequest, session,
                                   *, allow_local_development=False):
    presentation = await require_live_presentation(session, presentation_id)
    slides = await _slides(session, presentation_id)
    if not presentation.is_svg() or not slides or not all(slide.is_svg() for slide in slides):
        raise HTTPException(status_code=400, detail={"code": "svg_quality_review_svg_required"})
    try:
        _positions(request, len(slides))
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": "svg_quality_review_selection_invalid"}) from error
    return await admit_workspace_authored_task(session,
        presentation_id=presentation_id, operation_kind="quality.review",
        expected_revision=int(presentation.revision), command=user_message(UserMessageKey.QUALITY_REVIEW_COMMAND),
        context=request.model_dump(mode="json"), task_id_prefix="workspace-quality",
        pending_message=user_message(UserMessageKey.QUALITY_REVIEW_QUEUED),
        allow_local_development=allow_local_development)


async def admit_svg_quality_review_operation(session, *, operation, presentation):
    """Reserve one review call on a new fenced operation, never on an export."""
    from services.public_operation_admission import (ProviderRequestPlan,
        admit_public_operation_compatible_v2, current_principal_context, plan_weighted_resources)
    from services.authored_canvas import presentation_canvas_contract
    request = SvgQualityReviewRequest.model_validate(operation.request_payload.get("context") or {})
    rows = await _slides(session, presentation.id)
    indices = _positions(request, len(rows))
    for index in indices:
        _review_png(rows[index])
    try:
        recovery_review = await _prepare_quality_recovery_review(session, presentation, rows, request,
            operation.id, operation.owner_scope)
    except ValueError as error:
        raise HTTPException(status_code=400, detail={"code": str(error)}) from error
    if request.target == "final_pptx":
        from services.svg_final_pptx_review import load_final_review_inputs
        await load_final_review_inputs(session, request=request, presentation=presentation, slides=rows)
    class ReviewPlan(ProviderRequestPlan):
        @property
        def initial_request_limit(self):
            return 1
        @property
        def retry_request_limit(self):
            return 0
    canvas = presentation_canvas_contract(presentation.theme)
    principal = current_principal_context()
    if principal.owner_id != operation.owner_id or principal.owner_id != presentation.owner_id:
        raise ValueError("svg_quality_review_owner_invalid")
    operation.max_attempts = 1
    admission = await admit_public_operation_compatible_v2(session, principal=principal,
        operation_id=operation.id, operation_kind="generation",
        request_plan=ReviewPlan(slide_count=1, image_budget=0, repair_budget=0, vqa_budget=1),
        resource_plan=plan_weighted_resources(pixel_count=canvas.width_px * canvas.height_px * (
            len(rows) if recovery_review is not None else len(indices)),
            device_pixel_ratio_milli=1000, dom_node_count=0, font_count=0,
            svg_complexity=0, estimated_memory_mib=128, fidelity_mode="standard"))
    admission.retry_request_limit = 0
    await session.flush()


async def _assert_fence(session, claim, *, lock=False):
    statement = select(WorkspaceOperation.id).where(
        WorkspaceOperation.id == claim.operation_id,
        WorkspaceOperation.presentation_id == claim.presentation_id,
        WorkspaceOperation.owner_id == claim.owner_id,
        WorkspaceOperation.owner_scope == claim.owner_scope,
        WorkspaceOperation.state == "running",
        WorkspaceOperation.attempt_token == claim.attempt_token,
        WorkspaceOperation.fencing_token == claim.fencing_token,
        WorkspaceOperation.expected_revision == claim.expected_revision,
        WorkspaceOperation.cancel_requested_at.is_(None),
        WorkspaceOperation.lease_expires_at > get_current_utc_datetime())
    if lock:
        statement = statement.with_for_update()
    if await session.scalar(statement) is None:
        raise WorkspaceOperationLeaseLost("operation_lease_lost")


def _review_png(slide):
    receipt = (slide.content or {}).get("svgReceipt") or {}
    quality = receipt.get("quality") or {}
    if not page_measurement_current(slide):
        raise NonRetryableWorkspaceOperationError("svg_quality_review_measurement_stale")
    try:
        png = _read_png(receipt)
    except (OSError, TypeError, ValueError) as error:
        raise NonRetryableWorkspaceOperationError("svg_quality_review_render_unavailable") from error
    if not png:
        raise NonRetryableWorkspaceOperationError("svg_quality_review_render_unavailable")
    return png


async def execute_svg_quality_review(context, session_factory) -> WorkspaceOperationResult:
    claim = context.claim
    started_at = monotonic()
    if claim.expected_revision is None:
        raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")
    try:
        request = SvgQualityReviewRequest.model_validate(claim.request_payload.get("context") or {})
    except (ValueError, ValidationError) as error:
        raise NonRetryableWorkspaceOperationError("quality_review_request_invalid") from error
    await context.raise_if_cancelled()
    if await context.progress(stage="quality_review_preparing", percent=5) is False:
        raise WorkspaceOperationLeaseLost("operation_lease_lost")
    # End this read transaction before provider I/O. Publication re-reads and
    # locks the current deck rather than committing an earlier ORM snapshot.
    async with session_factory() as session:
        await _assert_fence(session, claim)
        presentation = await session.get(PresentationModel, claim.presentation_id)
        if (presentation is None or presentation.owner_id != claim.owner_id
                or presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED):
            raise NonRetryableWorkspaceOperationError("presentation_not_found")
        if int(presentation.revision) != claim.expected_revision:
            raise NonRetryableWorkspaceOperationError("stale_presentation_revision")
        rows = await _slides(session, claim.presentation_id)
        if any(getattr(slide, "owner_id", claim.owner_id) != claim.owner_id for slide in rows):
            raise NonRetryableWorkspaceOperationError("presentation_not_found")
        if not presentation.is_svg() or not rows or not all(slide.is_svg() for slide in rows):
            raise NonRetryableWorkspaceOperationError("svg_quality_review_svg_required")
        slides = [SimpleNamespace(svg_content=slide.svg_content, content=deepcopy(slide.content)) for slide in rows]
        try:
            indices = _positions(request, len(slides))
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError("svg_quality_review_selection_invalid") from error
        final_inputs = None
        if request.target == "final_pptx":
            from services.svg_final_pptx_review import load_final_review_inputs
            final_inputs = await load_final_review_inputs(session, request=request, presentation=presentation, slides=rows)
            pngs = final_inputs["pngs"]
        else:
            pngs = [_review_png(slides[index]) for index in indices]
        recovery_review = None
        try:
            recovery_review = await _prepare_quality_recovery_review(session, presentation, rows, request,
                claim.operation_id, claim.owner_scope)
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(str(error)) from error
        if recovery_review is not None:
            pngs = recovery_review["pngs"]
            indices = recovery_review["changed"]
        digests = slide_page_digests(slides)
        png_digests = [(slide.content or {}).get("svgReceipt", {}).get("png_sha256") for slide in slides]
    await context.raise_if_cancelled()
    if await context.progress(stage="quality_review_analyzing", percent=30, total=len(indices)) is False:
        raise WorkspaceOperationLeaseLost("operation_lease_lost")
    vqa_started_at = monotonic()
    with provider_operation_usage_scope() as usage, \
            public_operation_scope(claim.operation_id, attempt_token=claim.attempt_token, fencing_token=claim.fencing_token), \
            provider_usage_scope(job_id=claim.operation_id, operation="quality_review", stage="vqa",
                                 attempt_number=claim.attempt_number, public_only=True):
        from utils.provider_idempotency import ProviderOutcomeError
        try:
            vision = await run_vision_advisory(pngs, slide_count=len(pngs), required=True,
                **({"design_context": recovery_review["context"]} if recovery_review is not None else {}))
        except ProviderOutcomeError as error:
            raise NonRetryableWorkspaceOperationError(error.code) from error
    vqa_finished_at = monotonic()
    await context.raise_if_cancelled()
    subset = advisory_record(vision, digests if recovery_review is not None else [digests[index] for index in indices])
    if not advisory_ran(subset):
        code = ("public_provider_result_unknown"
                if vision.get("skipped") == "provider_outcome:public_provider_result_unknown"
                else "svg_quality_review_input_capacity_unavailable"
                if vision.get("skipped") == "review_input_capacity_unavailable"
                else "svg_quality_review_advisory_unavailable")
        raise NonRetryableWorkspaceOperationError(code, detail={"stage_timings_ms": {
            "preflight": (vqa_started_at - started_at) * 1000,
            "vqa": (vqa_finished_at - vqa_started_at) * 1000,
        }})
    new_records = (split_page_advisories(vision, slides, indices)
                   if request.target == "source_svg" and recovery_review is None else [])
    async with session_factory() as session:
        await _assert_fence(session, claim, lock=True)
        presentation = await session.scalar(select(PresentationModel).where(
            PresentationModel.id == claim.presentation_id, PresentationModel.owner_id == claim.owner_id,
            PresentationModel.revision == claim.expected_revision,
            PresentationModel.lifecycle_status != PRESENTATION_LIFECYCLE_DELETED).with_for_update())
        if presentation is None:
            raise NonRetryableWorkspaceOperationError("stale_presentation_revision")
        current_slides = await _slides(session, claim.presentation_id)
        if any(getattr(slide, "owner_id", claim.owner_id) != claim.owner_id for slide in current_slides):
            raise NonRetryableWorkspaceOperationError("presentation_not_found")
        if slide_page_digests(current_slides) != digests:
            raise NonRetryableWorkspaceOperationError("svg_quality_review_source_changed")
        for index in range(len(current_slides)) if recovery_review is not None else indices:
            _review_png(current_slides[index])
            receipt = (current_slides[index].content or {}).get("svgReceipt") or {}
            if (receipt.get("png_sha256") != png_digests[index]
                    or (receipt.get("quality") or {}).get("measurement_complete") is not True
                    or (receipt.get("quality") or {}).get("source_digest") != source_digest(current_slides[index].svg_content or "")):
                raise NonRetryableWorkspaceOperationError("svg_quality_review_measurement_stale")
        theme = deepcopy(presentation.theme or {})
        svg_theme = dict(theme.get("svg") or {})
        if recovery_review is not None:
            try:
                current_prepared = await _prepare_quality_recovery_review(session, presentation, current_slides,
                    request, claim.operation_id, claim.owner_scope)
            except ValueError as error:
                raise NonRetryableWorkspaceOperationError(str(error)) from error
            if (current_prepared["context"] != recovery_review["context"]
                    or current_prepared["currentInputDigests"] != recovery_review["currentInputDigests"]
                    or current_prepared["runtime"] != recovery_review["runtime"]
                    or current_prepared["reusable"] != recovery_review["reusable"]
                    or current_prepared["pages"] != recovery_review["pages"]):
                raise NonRetryableWorkspaceOperationError("svg_quality_review_source_changed")
            from services.svg_quality_recovery_review import seal_saved_review
            recovery_advisory, publication = seal_saved_review(recovery_review, current_slides, vision, claim)
            vision = recovery_advisory["vision"]
            new_records = split_page_advisories(vision, current_slides, range(len(current_slides)))
            svg_theme["qualityRecoveryPublication"] = publication
        if final_inputs is not None:
            from services.svg_final_pptx_review import load_final_review_inputs, seal_final_review
            current_inputs = await load_final_review_inputs(session, request=request,
                presentation=presentation, slides=current_slides)
            if (current_inputs["artifactDigest"] != final_inputs["artifactDigest"]
                    or current_inputs["proof"]["evidenceDigest"] != final_inputs["proof"]["evidenceDigest"]
                    or current_inputs["parentExportOperationId"] != final_inputs["parentExportOperationId"]):
                raise NonRetryableWorkspaceOperationError("svg_final_pptx_review_source_changed")
            final_reviews = dict(svg_theme.get("finalPptxReviews") or {})
            final_reviews[final_inputs["artifactDigest"][7:]] = seal_final_review(claim, final_inputs, vision)
            svg_theme["finalPptxReviews"] = final_reviews
        prior_advisory = svg_theme.get("advisory")
        prior = current_page_advisories(current_slides, svg_theme.get("pageAdvisories")
            or (prior_advisory or {}).get("pageAdvisories") or [])
        # SVG-only legacy answers lack the original PNG binding. A subset
        # review therefore leaves every unreviewed legacy page uncovered.
        for record in new_records:
            prior[record["index"]] = record
        records = [prior[index] for index in sorted(prior)]
        svg_theme["pageAdvisories"] = records
        assembled = assemble_page_advisory(current_slides, records)
        if recovery_review is not None:
            assembled = recovery_advisory
            assembled["pageAdvisories"] = records
        if assembled is not None and request.target == "source_svg":
            binding = dict(schemaVersion="svg-visual-review-operation-binding/v1",
                presentationId=str(claim.presentation_id), ownerId=str(claim.owner_id),
                ownerScope=str(getattr(claim, "owner_scope", "")),
                sourceRevision=claim.expected_revision, operationId=str(claim.operation_id),
                reviewedPageIndices=list(range(len(current_slides))) if recovery_review is not None else indices,
                sourceDigests=digests, pngDigests=png_digests,
                pageRecordDigests=[record["digest"] for record in records])
            assembled["operationBinding"] = {**binding, "digest": content_digest(binding)}
            svg_theme["advisory"] = assembled
        svg_theme["lastReview"] = dict(mode="analyze_only", scope=request.scope, slideIndices=indices,
            target=request.target, artifactId=str(request.artifact_id) if request.artifact_id else None,
            sourceRevision=claim.expected_revision, operationId=str(claim.operation_id), advisory=subset,
            reviewedAt=get_current_utc_datetime().isoformat())
        svg_theme["qualityStatus"] = svg_quality_status(current_slides, svg_theme.get("advisory"), page_advisories=records)
        if recovery_review is not None:
            gate = recovery_advisory["vision"]["designGate"]
            svg_theme["qualityStatus"]["qualityRecovery"] = dict(status=gate["status"],
                blockingPages=gate["blockingPages"], unreviewedPages=gate["unreviewedPages"])
            svg_theme["reviewPages"] = sorted(set(gate["blockingPages"]) | set(gate["unreviewedPages"]))
        theme["svg"] = svg_theme
        presentation.theme = theme
        session.add(presentation)
        findings = ([entry for record in new_records for entry in record["vision"].get("slides", [])]
                    if request.target == "source_svg" else vision.get("slides", []))
        issue_indices = sorted({entry["index"] for entry in findings if entry.get("issues")})
        detailed = sum(len(entry.get("issues") or []) for entry in findings)
        if recovery_review is not None:
            issue_indices = sorted(set(issue_indices) | set(gate["blockingPages"]) | set(gate["unreviewedPages"]))
            detailed += sum(len(page["failedAxes"]) + (page["status"] == "unreviewed") for page in gate["pages"])
        result = dict(kind="quality.review", mode="analyze_only", source_format="svg", scope=request.scope,
            slide_indices=indices, inspected_revision=claim.expected_revision,
            reviewed_count=len(current_slides) if recovery_review is not None else len(indices),
            issue_slide_count=len(issue_indices), detailed_issue_count=detailed,
            fixed_count=0, fixed_slide_indices=[], remaining_slide_count=len(issue_indices),
            remaining_slide_indices=issue_indices, remaining_issue_count=detailed, version_saved=False,
            slide_results=[dict(slide_index=index, attempted=False, applied=False,
                remaining_issues=[issue for entry in findings if entry["index"] == index for issue in entry.get("issues", [])])
                for index in indices],
            advisory_only=True, qualityStatus=svg_theme["qualityStatus"], advisory_digest=subset["digest"])
        result["stage_timings_ms"] = {
            "preflight": (vqa_started_at - started_at) * 1000,
            "vqa": (vqa_finished_at - vqa_started_at) * 1000,
            "revalidation": (monotonic() - vqa_finished_at) * 1000,
        }
        # Attempt counters describe instrumented wrapper calls only. Rate-card
        # arithmetic is not an actual bill and omitted SDK retries stay unknown.
        totals = usage.snapshot()["totals"]
        result["svgTelemetry"] = dict(schemaVersion="svg-operation-telemetry/v1",
            scope="operation_attempt", logicalRequests=totals["logicalCalls"],
            wrapperPhysicalRequests=totals["physicalCalls"], sdkPhysicalRequests=None,
            sdkPhysicalRequestsStatus="unmeasured", reportedUsageRequests=totals["reportedUsageCalls"],
            unavailableUsageRequests=totals["unavailableUsageCalls"],
            cachedInputTokens=usage.cached_input_snapshot(),
            terminalProviderResults=usage.terminal_snapshot(),
            settledMoney=None, settledMoneyStatus="unmeasured", billingSource=None)
        safe_result = sanitize_workspace_event_payload(result, operation_kind="quality.review")
        task_id = f"workspace-quality-{claim.operation_id}"
        task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
        if task is None:
            task = AsyncPresentationGenerationTaskModel(id=task_id, owner_id=claim.owner_id, status="completed", data=result)
        else:
            task.status, task.data = "completed", result
        task.updated_at = get_current_utc_datetime()
        session.add(task)
        try:
            revision = await publish_presentation_revision(session, presentation_id=claim.presentation_id,
                expected_revision=claim.expected_revision, operation_id=claim.operation_id,
                attempt_token=claim.attempt_token, result=safe_result, operation_kind="quality.review", advance_revision=False)
            await session.commit()
        except PresentationPublicationConflict as error:
            await session.rollback()
            if str(error).startswith("stale_operation"):
                raise WorkspaceOperationLeaseLost("operation_lease_lost") from error
            raise NonRetryableWorkspaceOperationError("stale_presentation_revision") from error
    return WorkspaceOperationResult(payload=safe_result, result_revision=revision)
