"""Quality review and immutable export workspace operations."""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.presentation_from_template import AuthoredQualityReviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceOperation
from services.authored_quality_review_service import run_authored_quality_review
from services.database import async_session_maker
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import sanitize_workspace_event_payload

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)
QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS = 30.0


def _compatibility_review_runner():
    facade = sys.modules.get("services.workspace_quality_export_operation_handlers")
    return getattr(facade, "run_authored_quality_review", run_authored_quality_review)


def _compatibility_progress_interval() -> float:
    facade = sys.modules.get("services.workspace_quality_export_operation_handlers")
    return float(
        getattr(
            facade,
            "QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS",
            QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS,
        )
    )


def _context(claim_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = claim_payload.get("context")
    return value if isinstance(value, Mapping) else {}


def _resolve_workspace_export_options(
    claim_payload: Mapping[str, Any],
    *,
    export_format: str,
) -> tuple[Literal["fidelity", "hybrid"], bool]:
    """Resolve existing workspace wire values without implicit Hybrid opt-in."""

    command_context = _context(claim_payload)
    raw_mode = command_context.get("pptx_mode")
    if raw_mode is None:
        pptx_mode: Literal["fidelity", "hybrid"] = "fidelity"
    elif raw_mode in {"fidelity", "hybrid"}:
        pptx_mode = cast(Literal["fidelity", "hybrid"], raw_mode)
    else:
        raise NonRetryableWorkspaceOperationError("workspace_export_pptx_mode_invalid")

    font_embedding = command_context.get("font_embedding", False)
    if not isinstance(font_embedding, bool):
        raise NonRetryableWorkspaceOperationError("workspace_export_font_embedding_invalid")
    if font_embedding and (export_format != "pptx" or pptx_mode != "hybrid"):
        raise NonRetryableWorkspaceOperationError("workspace_export_font_embedding_requires_hybrid")
    return pptx_mode, font_embedding


class WorkspaceQualityReviewOperationHandler:
    """Run authored review and publish a new revision only when fixes are saved."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def _published_result_after_commit_error(self, operation_id: uuid.UUID) -> WorkspaceOperationResult | None:
        """Reconcile an ambiguous commit acknowledgement without republishing."""

        async with self._session_factory() as session:
            operation = await session.get(WorkspaceOperation, operation_id)
            if (
                operation is None
                or operation.state != "succeeded"
                or not isinstance(operation.result, dict)
                or operation.result_revision is None
            ):
                return None
            return WorkspaceOperationResult(
                payload=operation.result,
                result_revision=operation.result_revision,
            )

    async def _run_review_with_live_progress(
        self,
        *,
        context: WorkspaceOperationExecutionContext,
        presentation_id: uuid.UUID,
        request: AuthoredQualityReviewRequest,
        task: AsyncPresentationGenerationTaskModel,
        session: AsyncSession,
    ) -> None:
        """Keep long reviews visibly alive and stop work after losing the lease."""

        review = asyncio.create_task(_compatibility_review_runner()(presentation_id, request, task, session))
        percent = 20
        try:
            while True:
                done, _pending = await asyncio.wait({review}, timeout=_compatibility_progress_interval())
                if review in done:
                    await review
                    return
                await context.raise_if_cancelled()
                # Approach 95% without claiming completion before results have
                # passed the revision and task-status checks below.
                percent = min(95, percent + max(1, (95 - percent + 7) // 8))
                progressed = await context.progress(stage="quality_review_analyzing", percent=percent)
                if progressed is False:
                    raise WorkspaceOperationLeaseLost("operation_lease_lost")
        finally:
            if not review.done():
                review.cancel()
                await asyncio.gather(review, return_exceptions=True)

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError("workspace_expected_revision_required")
        command_context = _context(claim.request_payload)
        mode = command_context.get("mode", "analyze_and_fix")
        if mode != "analyze_and_fix":
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        scope = command_context.get("scope", "all")
        slide_indices = command_context.get("slide_indices", [])
        slidespec_opt_in = command_context.get("slidespec_opt_in", True)
        if (
            scope not in {"all", "current", "subset"}
            or not isinstance(slide_indices, list)
            or not isinstance(slidespec_opt_in, bool)
        ):
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        try:
            request = AuthoredQualityReviewRequest(
                scope=scope,
                slide_indices=slide_indices,
                mode="analyze_and_fix",
                slidespec_opt_in=slidespec_opt_in,
            )
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid") from error

        await context.raise_if_cancelled()
        if await context.progress(stage="quality_review_preparing", percent=5) is False:
            raise WorkspaceOperationLeaseLost("operation_lease_lost")
        async with self._session_factory() as session:
            inspected_revision = await session.scalar(
                select(PresentationModel.revision).where(PresentationModel.id == claim.presentation_id)
            )
            if inspected_revision is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            if int(inspected_revision) != claim.expected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "stale_presentation_revision",
                    detail={"current_revision": int(inspected_revision)},
                )
            # A stable task id is the durable resume marker for this operation.
            # Once slide fixes and the completed task result are committed
            # together, a worker retry can publish that result without running
            # the mutating review a second time.
            task_id = f"workspace-quality-{claim.operation_id}"
            task = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            review_completed = task is not None and task.status == "completed" and isinstance(task.data, dict)
            if task is None:
                task = AsyncPresentationGenerationTaskModel(
                    id=task_id,
                    status="pending",
                    message="Preparing quality review.",
                    data=None,
                )
                session.add(task)
                await session.commit()
            elif not review_completed:
                task.status = "pending"
                task.message = "Resuming quality review."
                task.data = None
                task.error = None
                session.add(task)
                await session.commit()

            if not review_completed:
                try:
                    if await context.progress(stage="quality_review_analyzing", percent=20) is False:
                        raise WorkspaceOperationLeaseLost("operation_lease_lost")
                    await self._run_review_with_live_progress(
                        context=context,
                        presentation_id=claim.presentation_id,
                        request=request,
                        task=task,
                        session=session,
                    )
                    await context.raise_if_cancelled()
                except WorkspaceOperationCancelled:
                    raise
                except WorkspaceOperationLeaseLost:
                    raise
                except (ValueError, HTTPException) as error:
                    raise NonRetryableWorkspaceOperationError("quality_review_unsupported_presentation") from error
                except Exception as error:
                    raise RetryableWorkspaceOperationError("quality_review_failed") from error

            if await context.progress(stage="quality_review_saving_results", percent=96) is False:
                raise WorkspaceOperationLeaseLost("operation_lease_lost")
            current_revision = await session.scalar(
                select(PresentationModel.revision)
                .where(PresentationModel.id == claim.presentation_id)
                .execution_options(populate_existing=True)
            )
            if current_revision != inspected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "quality_review_revision_changed",
                    detail={
                        "inspected_revision": int(inspected_revision),
                        "current_revision": int(current_revision or 0),
                    },
                )
            await session.refresh(task)
            if task.status != "completed" or not isinstance(task.data, dict):
                raise RetryableWorkspaceOperationError("quality_review_failed")
            try:
                fixed_count = max(0, int(task.data.get("fixed_count") or 0))
            except (TypeError, ValueError) as error:
                raise RetryableWorkspaceOperationError("quality_review_failed") from error
            fixed_slide_indices = task.data.get("fixed_slide_indices")
            if not isinstance(fixed_slide_indices, list):
                fixed_slide_indices = []
            normalized_fixed_slide_indices = [
                value
                for value in fixed_slide_indices
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
            ]
            result_revision = int(inspected_revision) + 1 if fixed_count > 0 else int(inspected_revision)
            result = {
                **task.data,
                "kind": QUALITY_REVIEW_OPERATION_KIND,
                "message": task.message,
                "mode": "analyze_and_fix",
                "scope": request.scope,
                "slide_indices": request.slide_indices,
                "inspected_revision": int(inspected_revision),
                "result_revision": result_revision,
                "fixed_count": fixed_count,
                "fixed_slide_indices": normalized_fixed_slide_indices,
                "canvas_refresh_required": fixed_count > 0,
                "canvas_refresh_slide_index": (
                    normalized_fixed_slide_indices[0] if normalized_fixed_slide_indices else None
                ),
                "canvas_refresh_status": ("requested" if fixed_count > 0 else "not_required"),
            }
            safe_result = sanitize_workspace_event_payload(
                result,
                operation_kind=QUALITY_REVIEW_OPERATION_KIND,
            )
            if fixed_count > 0:
                try:
                    published_revision = await publish_presentation_revision(
                        session,
                        presentation_id=claim.presentation_id,
                        expected_revision=int(inspected_revision),
                        operation_id=claim.operation_id,
                        attempt_token=claim.attempt_token,
                        result=safe_result,
                        operation_kind=QUALITY_REVIEW_OPERATION_KIND,
                    )
                    await session.commit()
                except PresentationPublicationConflict as error:
                    await session.rollback()
                    if str(error) in {
                        "stale_operation_fence",
                        "stale_operation_event_fence",
                    }:
                        raise WorkspaceOperationLeaseLost("operation_lease_lost") from error
                    raise NonRetryableWorkspaceOperationError("stale_presentation_revision") from error
                except Exception as error:
                    await session.rollback()
                    published = await self._published_result_after_commit_error(claim.operation_id)
                    if published is not None:
                        return published
                    raise RetryableWorkspaceOperationError("quality_review_result_commit_failed") from error
                if published_revision != result_revision:
                    raise RetryableWorkspaceOperationError("quality_review_result_commit_failed")
        return WorkspaceOperationResult(
            payload=safe_result,
            result_revision=result_revision,
        )
