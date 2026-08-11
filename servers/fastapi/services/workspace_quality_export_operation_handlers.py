"""Quality review and immutable export workspace operations."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.v1.auth.internal import authenticated_internal_request_headers
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
from services.presentation_export_attempt_service import (
    terminalize_export_attempt_failed,
)
from services.presentation_export_capabilities import (
    PresentationExportContractError,
)
from services.presentation_export_router import execute_presentation_export
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    compensate_workspace_artifact,
    prepare_workspace_generation_artifact,
    publish_workspace_export_artifact,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)
from services.workspace_operation_runner import sanitize_workspace_event_payload

QUALITY_REVIEW_OPERATION_KIND = "quality.review"
EXPORT_OPERATION_KINDS = ("export.pptx", "export.pdf")
InternalHeaderProvider = Callable[[], Awaitable[dict[str, str]]]
LOGGER = logging.getLogger(__name__)
QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS = 30.0


def _context(claim_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = claim_payload.get("context")
    return value if isinstance(value, Mapping) else {}


class WorkspaceQualityReviewOperationHandler:
    """Run authored review and publish a new revision only when fixes are saved."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    ) -> None:
        self._session_factory = session_factory

    async def _published_result_after_commit_error(
        self, operation_id: uuid.UUID
    ) -> WorkspaceOperationResult | None:
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

        review = asyncio.create_task(
            run_authored_quality_review(presentation_id, request, task, session)
        )
        percent = 20
        try:
            while True:
                done, _pending = await asyncio.wait(
                    {review}, timeout=QUALITY_REVIEW_PROGRESS_INTERVAL_SECONDS
                )
                if review in done:
                    await review
                    return
                await context.raise_if_cancelled()
                # Approach 95% without claiming completion before results have
                # passed the revision and task-status checks below.
                percent = min(95, percent + max(1, (95 - percent + 7) // 8))
                progressed = await context.progress(
                    stage="quality_review_analyzing", percent=percent
                )
                if progressed is False:
                    raise WorkspaceOperationLeaseLost("operation_lease_lost")
        finally:
            if not review.done():
                review.cancel()
                await asyncio.gather(review, return_exceptions=True)

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )
        command_context = _context(claim.request_payload)
        mode = command_context.get("mode", "analyze_and_fix")
        if mode != "analyze_and_fix":
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        scope = command_context.get("scope", "all")
        slide_indices = command_context.get("slide_indices", [])
        slidespec_opt_in = command_context.get("slidespec_opt_in", True)
        if scope not in {"all", "current", "subset"} or not isinstance(
            slide_indices, list
        ) or not isinstance(slidespec_opt_in, bool):
            raise NonRetryableWorkspaceOperationError("quality_review_request_invalid")
        try:
            request = AuthoredQualityReviewRequest(
                scope=scope,
                slide_indices=slide_indices,
                mode="analyze_and_fix",
                slidespec_opt_in=slidespec_opt_in,
            )
        except ValueError as error:
            raise NonRetryableWorkspaceOperationError(
                "quality_review_request_invalid"
            ) from error

        await context.raise_if_cancelled()
        if (
            await context.progress(stage="quality_review_preparing", percent=5)
            is False
        ):
            raise WorkspaceOperationLeaseLost("operation_lease_lost")
        async with self._session_factory() as session:
            inspected_revision = await session.scalar(
                select(PresentationModel.revision).where(
                    PresentationModel.id == claim.presentation_id
                )
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
            review_completed = (
                task is not None
                and task.status == "completed"
                and isinstance(task.data, dict)
            )
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
                    if (
                        await context.progress(
                            stage="quality_review_analyzing", percent=20
                        )
                        is False
                    ):
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
                    raise NonRetryableWorkspaceOperationError(
                        "quality_review_unsupported_presentation"
                    ) from error
                except Exception as error:
                    raise RetryableWorkspaceOperationError(
                        "quality_review_failed"
                    ) from error

            if (
                await context.progress(
                    stage="quality_review_saving_results", percent=96
                )
                is False
            ):
                raise WorkspaceOperationLeaseLost("operation_lease_lost")
            current_revision = await session.scalar(
                select(PresentationModel.revision).where(
                    PresentationModel.id == claim.presentation_id
                ).execution_options(populate_existing=True)
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
                raise RetryableWorkspaceOperationError(
                    "quality_review_failed"
                ) from error
            fixed_slide_indices = task.data.get("fixed_slide_indices")
            if not isinstance(fixed_slide_indices, list):
                fixed_slide_indices = []
            normalized_fixed_slide_indices = [
                value
                for value in fixed_slide_indices
                if isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            ]
            result_revision = (
                int(inspected_revision) + 1
                if fixed_count > 0
                else int(inspected_revision)
            )
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
                    normalized_fixed_slide_indices[0]
                    if normalized_fixed_slide_indices
                    else None
                ),
                "canvas_refresh_status": (
                    "requested" if fixed_count > 0 else "not_required"
                ),
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
                        raise WorkspaceOperationLeaseLost(
                            "operation_lease_lost"
                        ) from error
                    raise NonRetryableWorkspaceOperationError(
                        "stale_presentation_revision"
                    ) from error
                except Exception as error:
                    await session.rollback()
                    published = await self._published_result_after_commit_error(
                        claim.operation_id
                    )
                    if published is not None:
                        return published
                    raise RetryableWorkspaceOperationError(
                        "quality_review_result_commit_failed"
                    ) from error
                if published_revision != result_revision:
                    raise RetryableWorkspaceOperationError(
                        "quality_review_result_commit_failed"
                    )
        return WorkspaceOperationResult(
            payload=safe_result,
            result_revision=result_revision,
        )


class WorkspaceExportOperationHandler:
    """Render then immutably publish an owner-scoped export artifact."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        internal_header_provider: InternalHeaderProvider = authenticated_internal_request_headers,
    ) -> None:
        self._session_factory = session_factory
        self._internal_header_provider = internal_header_provider

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        export_format = claim.kind.removeprefix("export.")
        if export_format not in {"pptx", "pdf"}:
            raise NonRetryableWorkspaceOperationError(
                "workspace_export_format_invalid"
            )
        if claim.expected_revision is None:
            raise NonRetryableWorkspaceOperationError(
                "workspace_expected_revision_required"
            )

        await context.raise_if_cancelled()
        async with self._session_factory() as session:
            presentation = await session.get(PresentationModel, claim.presentation_id)
            if presentation is None:
                raise NonRetryableWorkspaceOperationError("presentation_not_found")
            if presentation.revision != claim.expected_revision:
                raise NonRetryableWorkspaceOperationError(
                    "stale_presentation_revision"
                )
            title = presentation.title or "presentation"
            operation = await session.get(WorkspaceOperation, claim.operation_id)
            if (
                operation is None
                or operation.presentation_id != claim.presentation_id
                or operation.owner_scope != claim.owner_scope
            ):
                raise NonRetryableWorkspaceOperationError(
                    "workspace_export_operation_invalid"
                )

        await context.progress(stage="export_rendering", percent=10)
        # Mint at execution time only. This credential is never copied into a
        # request payload, event, artifact row or result projection.
        headers = await self._internal_header_provider()
        cookie_header = headers.get("Cookie") or None
        exported_source: Path | None = None
        execution = None
        try:
            async with self._session_factory() as session:
                execution = await execute_presentation_export(
                    session,
                    render_presentation_id=claim.presentation_id,
                    attempt_presentation_id=claim.presentation_id,
                    export_format=cast(Literal["pptx", "pdf"], export_format),
                    expected_render_revision=claim.expected_revision,
                    source_revision=claim.expected_revision,
                    title=title,
                    cookie_header=cookie_header,
                    owner_id=claim.owner_id,
                    owner_scope=claim.owner_scope,
                    workspace_operation_id=claim.operation_id,
                    request_hash=operation.request_hash,
                    idempotency_key_hash=operation.idempotency_key_hash,
                )
            exported = execution.presentation
            exported_source = Path(exported.path)
            if execution.route.result.artifact_sha256 != (
                hashlib.sha256(exported_source.read_bytes()).hexdigest()
            ):
                raise PresentationExportContractError(
                    "presentation_export_artifact_digest_mismatch"
                )
            await context.raise_if_cancelled()
            await context.progress(stage="export_publishing", percent=80)
            async with self._session_factory() as session:
                artifact = await prepare_workspace_generation_artifact(
                    session,
                    source_path=exported.path,
                    operation_id=claim.operation_id,
                    presentation_id=claim.presentation_id,
                    source_revision=claim.expected_revision,
                    kind=claim.kind,
                    export_attempt_id=execution.attempt_id,
                    expected_source_sha256=(
                        execution.route.result.artifact_sha256
                    ),
                    expected_source_size_bytes=(
                        execution.route.result.artifact_size_bytes
                    ),
                )
                try:
                    await context.raise_if_cancelled()
                except WorkspaceOperationCancelled:
                    await compensate_workspace_artifact(session, artifact)
                    raise
                revision = await publish_workspace_export_artifact(
                    session,
                    artifact=artifact,
                    attempt_token=claim.attempt_token,
                    fencing_token=claim.fencing_token,
                    export_format=export_format,
                    export_source_snapshot_sha256=(
                        execution.source_snapshot_sha256
                    ),
                    export_selected_adapter=(
                        execution.route.result.selected_adapter
                    ),
                    export_provenance=execution.provenance,
                )
        except WorkspaceOperationCancelled:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(
                            execution.workspace_operation_id
                        ),
                        failure_code="presentation_export_cancelled",
                        failure_path=None,
                    )
            raise
        except WorkspaceArtifactPublicationError as error:
            error_type = (
                NonRetryableWorkspaceOperationError
                if error.code
                in {
                    "stale_presentation_revision",
                    "operation_lease_lost",
                    "workspace_export_format_invalid",
                }
                else RetryableWorkspaceOperationError
            )
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(
                            execution.workspace_operation_id
                        ),
                        failure_code=error.code,
                        failure_path=None,
                    )
            if error.code == "operation_lease_lost":
                raise WorkspaceOperationLeaseLost(error.code) from error
            raise error_type(error.code) from error
        except WorkspaceOperationLeaseLost:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(
                            execution.workspace_operation_id
                        ),
                        failure_code="operation_lease_lost",
                        failure_path=None,
                    )
            raise
        except PresentationExportContractError as error:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(
                            execution.workspace_operation_id
                        ),
                        failure_code=error.code,
                        failure_path=error.path,
                    )
            raise NonRetryableWorkspaceOperationError(error.code) from error
        except Exception as error:
            if execution is not None:
                async with self._session_factory() as session:
                    await terminalize_export_attempt_failed(
                        session,
                        attempt_id=execution.attempt_id,
                        expected_owner_id=execution.owner_id,
                        expected_owner_scope=execution.owner_scope,
                        expected_workspace_operation_id=(
                            execution.workspace_operation_id
                        ),
                        failure_code=getattr(
                            error,
                            "code",
                            "presentation_export_unexpected_failure",
                        ),
                        failure_path=getattr(error, "path", None),
                    )
            raise RetryableWorkspaceOperationError("presentation_export_failed") from error
        finally:
            # The renderer output is only a staging source. The immutable,
            # hashed workspace artifact is the durable copy and must be the
            # sole downloadable file after prepare succeeds or fails.
            if exported_source is not None:
                try:
                    exported_source.unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Unable to remove workspace export staging source",
                        extra={
                            "operation_id": str(claim.operation_id),
                            "presentation_id": str(claim.presentation_id),
                            "format": export_format,
                        },
                        exc_info=True,
                    )

        await context.progress(stage="export_completed", percent=100)
        # Publication has already terminalized the operation. The runner will
        # reconcile this canonical durable projection rather than append twice.
        return WorkspaceOperationResult(
            payload={"status": "completed", "format": export_format},
            result_revision=revision,
        )


def register_workspace_quality_export_operation_handlers(
    *,
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    replace: bool = False,
) -> tuple[WorkspaceQualityReviewOperationHandler, WorkspaceExportOperationHandler]:
    quality = WorkspaceQualityReviewOperationHandler()
    export = WorkspaceExportOperationHandler()
    registry.register(
        QUALITY_REVIEW_OPERATION_KIND,
        quality,
        replace=replace,
        recovery_policy="automatic",
    )
    for kind in EXPORT_OPERATION_KINDS:
        registry.register(kind, export, replace=replace)
    return quality, export
