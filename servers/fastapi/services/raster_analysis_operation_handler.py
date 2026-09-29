"""Durable workspace worker for bounded, recoverable raster analysis."""
from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path

from sqlalchemy import select

from constants.user_messages import UserMessageKey, user_message
from models.sql.public_artifact import ArtifactRef
from models.sql.workspace import WorkspaceOperation
from services.artifact_store import configured_public_artifact_service
from services.database import async_session_maker
from services.source_content.raster_analysis import (
    analyze_auto_preserve_pptx, analyze_raster_pptx, analyze_redesign_pptx,
)
from services.source_content.raster_contracts import RasterRegion, raster_digest, seal_analysis
from services.source_content.raster_reconstruction_store import (
    approve_raster_analysis, load_raster_analysis, persist_raster_analysis,
    raster_review_blockers, reserve_raster_operation_position,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError, WorkspaceOperationExecutionContext,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
)


RASTER_ANALYSIS_OPERATION_KIND = "raster.analysis"

# "auto_redesign" only auto-approves once analysis lands with no
# low-confidence regions left to review; unlike "auto_preserve" it is not
# unconditionally approved. "reconstruct" has no automatic approval method.
_AUTOMATIC_APPROVAL_METHOD_BY_MODE = {
    "auto_preserve": "automatic_preserve",
    "auto_redesign": "automatic_redesign",
}
_ANALYZER_BY_MODE = {
    "auto_preserve": analyze_auto_preserve_pptx,
    "auto_redesign": analyze_redesign_pptx,
}


def automatic_approval_method_for_mode(analysis_mode: str) -> str | None:
    return _AUTOMATIC_APPROVAL_METHOD_BY_MODE.get(analysis_mode)


def raster_analyzer_for_mode(analysis_mode: str):
    """Pick the analysis function; "reconstruct" keeps the manual-review analyzer."""
    return _ANALYZER_BY_MODE.get(analysis_mode, analyze_raster_pptx)


# "auto_redesign" analysis runs OCR plus one vision-LLM call per page and has
# been measured at several minutes for a large deck. The runner's own
# heartbeat loop already renews the lease generically every
# WORKSPACE_OPERATION_HEARTBEAT_SECONDS (~30s) for as long as this handler's
# blocking work stays off the event loop -- which asyncio.to_thread below
# guarantees. The ticker here is a defensive second lease-extension signal
# (through the same fencing-checked extend_lease API, never a bypass) plus
# the only source of incremental, elapsed-time progress text: the analyzer
# itself reports no progress mid-call.
_ANALYSIS_TICK_SECONDS = 20
_ANALYSIS_LEASE_WINDOW_SECONDS = 600


async def _run_analyzer_with_progress(
    context: WorkspaceOperationExecutionContext,
    analyzer,
    source_path: Path,
    *,
    orientation: str,
    analysis_mode: str,
):
    loop = asyncio.get_running_loop()
    started = loop.time()
    stop = asyncio.Event()

    async def tick() -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=_ANALYSIS_TICK_SECONDS)
                return
            except asyncio.TimeoutError:
                elapsed = int(loop.time() - started)
                await context.extend_lease(seconds=_ANALYSIS_LEASE_WINDOW_SECONDS)
                await context.progress(
                    stage="raster_analysis",
                    percent=min(75, 5 + elapsed // 5),
                    message=user_message(
                        UserMessageKey.RASTER_ANALYSIS_REDESIGN_ELAPSED
                        if analysis_mode == "auto_redesign"
                        else UserMessageKey.RASTER_ANALYSIS_REGIONS_ELAPSED,
                        elapsed=elapsed,
                    ),
                )

    ticker = asyncio.create_task(tick())
    try:
        # Runs entirely in a worker thread (analyze_redesign_pptx issues its
        # own vision-LLM/OCR calls synchronously), so the event loop -- and
        # both this ticker and the runner's heartbeat task -- stay responsive.
        return await asyncio.to_thread(analyzer, source_path, orientation=orientation)
    finally:
        stop.set()
        await ticker


async def _persist_one_crop(
    region: RasterRegion, crop: bytes | None, *,
    service, session, owner_id, tenant_id, review_id, missing_code: str,
) -> RasterRegion:
    if crop is None:
        raise NonRetryableWorkspaceOperationError(missing_code)
    crop_ref = await service.persist_bytes(
        session, owner_id=owner_id, tenant_id=tenant_id,
        kind="image-derivative", payload=crop, media_type="image/png",
        created_by_operation_id=review_id, retention_class="published",
    )
    return RasterRegion.model_validate(
        region.model_dump(mode="json") | {"crop_artifact_ref": crop_ref.model_dump(mode="json")}
    )


async def _persist_page_crops(page, draft, *, service, session, owner_id, tenant_id, review_id):
    """Attach a persisted crop_artifact_ref to every region this page carries.

    Every region role that draft.crop_png_by_region can hold a crop for gets
    one: ``page.regions`` (crop optional -- text-only regions have none),
    ``page.full_page_region`` and ``page.reference_region`` (crop required --
    both are the whole-page image raster_redesign.py/raster_analysis.py
    always render). Missing ``full_page_region``/``reference_region`` reduces
    to raster_review_blockers' ``*_crop_missing`` blocker on every page,
    which looks like every page needs review when it is actually a persist
    bug -- see the "reference_crop_missing on 28/28 pages" regression this
    guards against.
    """
    regions = []
    for region in page.regions:
        crop = draft.crop_png_by_region.get(region.region_id)
        if crop is not None:
            region = await _persist_one_crop(
                region, crop, service=service, session=session, owner_id=owner_id,
                tenant_id=tenant_id, review_id=review_id, missing_code="raster_region_crop_missing",
            )
        regions.append(region)
    page_changes = {"regions": tuple(regions)}
    if page.full_page_region is not None:
        page_changes["full_page_region"] = await _persist_one_crop(
            page.full_page_region, draft.crop_png_by_region.get(page.full_page_region.region_id),
            service=service, session=session, owner_id=owner_id, tenant_id=tenant_id,
            review_id=review_id, missing_code="raster_full_page_crop_missing",
        )
    if page.reference_region is not None:
        page_changes["reference_region"] = await _persist_one_crop(
            page.reference_region, draft.crop_png_by_region.get(page.reference_region.region_id),
            service=service, session=session, owner_id=owner_id, tenant_id=tenant_id,
            review_id=review_id, missing_code="raster_reference_crop_missing",
        )
    return page.model_copy(update=page_changes)


class RasterAnalysisOperationHandler:
    async def execute(
        self, context: WorkspaceOperationExecutionContext,
    ) -> WorkspaceOperationResult:
        claim = context.claim
        tenant = claim.request_payload.get("tenant_id")
        if claim.owner_id is None or not isinstance(tenant, str) or not tenant:
            raise NonRetryableWorkspaceOperationError("raster_authenticated_scope_required")
        reference = ArtifactRef.model_validate(claim.request_payload.get("source_artifact_ref"))
        analysis_mode = claim.request_payload.get("analysis_mode", "reconstruct")
        if analysis_mode not in {"auto_preserve", "reconstruct", "auto_redesign"}:
            raise NonRetryableWorkspaceOperationError("raster_analysis_mode_invalid")
        automatic_approval_method = automatic_approval_method_for_mode(analysis_mode)
        if (
            reference.owner_id != claim.owner_id
            or reference.tenant_id != tenant
            or reference.kind != "presentation-pptx"
            or reference.sha256 != claim.request_payload.get("source_sha256")
        ):
            raise NonRetryableWorkspaceOperationError("raster_source_binding_invalid")
        review_id = uuid.uuid5(claim.operation_id, "raster-review/v1")
        service = configured_public_artifact_service()
        async with async_session_maker() as session:
            existing = await session.scalar(select(WorkspaceOperation).where(
                WorkspaceOperation.id == review_id,
                WorkspaceOperation.owner_id == claim.owner_id,
                WorkspaceOperation.owner_scope == claim.owner_scope,
            ))
            if existing is not None and existing.request_payload.get("tenant_id") != tenant:
                raise NonRetryableWorkspaceOperationError("raster_review_scope_mismatch")
            existing_result = (existing.result or {}) if existing is not None else {}
            if existing is not None and existing_result.get("raster_reconstruction"):
                existing_reconstruction = existing_result.get("raster_reconstruction", {})
                if automatic_approval_method is not None and not existing_reconstruction.get("approval_pin"):
                    if existing.request_payload.get("analysis_mode") != analysis_mode:
                        raise NonRetryableWorkspaceOperationError("raster_auto_review_binding_invalid")
                    previous = await load_raster_analysis(
                        session, operation_id=review_id, owner_id=claim.owner_id,
                        tenant_id=tenant, artifact_service=service,
                    )
                    if previous.revision != 1 or any(
                        page.approval_method != automatic_approval_method for page in previous.pages
                    ):
                        raise NonRetryableWorkspaceOperationError("raster_auto_review_modified")
                    # "auto_preserve" pages are always approvable; "auto_redesign"
                    # pages only clear once no low-confidence regions remain.
                    if analysis_mode == "auto_preserve" or not raster_review_blockers(previous):
                        await approve_raster_analysis(
                            session, operation_id=review_id, owner_id=claim.owner_id,
                            tenant_id=tenant, analysis_digest=previous.digest,
                            expected_revision=1, expected_operation_version=existing.version,
                            artifact_service=service,
                        )
                    await session.commit()
                return WorkspaceOperationResult(payload={"review_operation_id": str(review_id)})
            source_bytes = await service.read_bytes(
                session, artifact_id=reference.artifact_id, owner_id=claim.owner_id,
                tenant_id=tenant, expected_digest=reference.sha256,
            )
        await context.raise_if_cancelled()
        await context.progress(stage="raster_analysis", percent=5, message=user_message(
            UserMessageKey.RASTER_ANALYSIS_PRESERVE if analysis_mode == "auto_preserve"
            else UserMessageKey.RASTER_ANALYSIS_REDESIGN if analysis_mode == "auto_redesign"
            else UserMessageKey.RASTER_ANALYSIS_REGIONS
        ))
        await context.extend_lease(seconds=_ANALYSIS_LEASE_WINDOW_SECONDS)
        with tempfile.TemporaryDirectory(prefix="raster-analysis-") as directory:
            source_path = Path(directory) / "source.pptx"
            await asyncio.to_thread(source_path.write_bytes, source_bytes)
            analyzer = raster_analyzer_for_mode(analysis_mode)
            draft = await _run_analyzer_with_progress(
                context, analyzer, source_path,
                orientation=claim.request_payload.get("orientation", "portrait"),
                analysis_mode=analysis_mode,
            )
        await context.raise_if_cancelled()
        if draft.analysis.source.source_sha256 != reference.sha256:
            raise NonRetryableWorkspaceOperationError("raster_source_digest_mismatch")
        await context.progress(
            stage="raster_persisting", percent=85,
            message=user_message(UserMessageKey.RASTER_ANALYSIS_PERSISTING),
        )
        await context.extend_lease(seconds=900)
        async with async_session_maker() as session:
            operation = await session.scalar(select(WorkspaceOperation).where(
                WorkspaceOperation.id == claim.operation_id,
                WorkspaceOperation.owner_id == claim.owner_id,
                WorkspaceOperation.owner_scope == claim.owner_scope,
                WorkspaceOperation.state == "running",
                WorkspaceOperation.attempt_token == claim.attempt_token,
                WorkspaceOperation.fencing_token == claim.fencing_token,
                WorkspaceOperation.cancel_requested_at.is_(None),
            ).with_for_update())
            if operation is None:
                raise WorkspaceOperationLeaseLost()
            review_payload = {
                "tenant_id": tenant,
                "analysis_operation_id": str(claim.operation_id),
                "reference_snapshot_id": claim.request_payload.get("reference_snapshot_id"),
                "analysis_mode": analysis_mode,
            }
            anchor_position = await reserve_raster_operation_position(
                session, conversation_id=claim.conversation_id, owner_id=claim.owner_id,
            )
            review = WorkspaceOperation(
                id=review_id, owner_id=claim.owner_id, owner_scope=claim.owner_scope,
                conversation_id=claim.conversation_id, presentation_id=claim.presentation_id,
                anchor_position=anchor_position, kind="raster.review", state="awaiting_approval",
                idempotency_key_hash=raster_digest({"review_id": str(review_id)}),
                request_hash=raster_digest(review_payload), request_payload=review_payload,
                version=1,
            )
            session.add(review)
            await session.flush()
            pages = [
                await _persist_page_crops(
                    page, draft, service=service, session=session,
                    owner_id=claim.owner_id, tenant_id=tenant, review_id=review_id,
                )
                for page in draft.analysis.pages
            ]
            analysis = seal_analysis(draft.analysis.model_copy(update={
                "source": draft.analysis.source.model_copy(update={"source_artifact_ref": reference}),
                "pages": tuple(pages),
            }))
            await persist_raster_analysis(
                session, operation_id=review_id, owner_id=claim.owner_id, tenant_id=tenant,
                analysis=analysis, expected_operation_version=1, artifact_service=service,
            )
            auto_approved = analysis_mode == "auto_preserve" or (
                analysis_mode == "auto_redesign" and not raster_review_blockers(analysis)
            )
            if auto_approved:
                await approve_raster_analysis(
                    session, operation_id=review_id, owner_id=claim.owner_id, tenant_id=tenant,
                    analysis_digest=analysis.digest, expected_revision=analysis.revision,
                    expected_operation_version=2, artifact_service=service,
                )
            await session.commit()
        return WorkspaceOperationResult(payload={
            "review_operation_id": str(review_id),
            "analysis_id": str(analysis.analysis_id),
            "analysis_digest": analysis.digest,
            "page_count": analysis.source.page_count,
            "auto_approved": auto_approved,
        })
