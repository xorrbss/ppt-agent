"""Workspace operation adapter for the existing presentation generator.

The generic workspace operation owns scheduling, retries and cancellation.
The linked ``PresentationGenerationJob`` is retained as a compatibility
projection because authored checkpoints fence their writes through that row.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from api.v1.auth.internal import authenticated_internal_request_headers
from services.authored_generation_progress_poller import (
    start_authored_generation_progress_polling,
)
from services.database import async_session_maker
from services.presentation_generation_job_service import (
    GENERATION_JOB_HEARTBEAT_SECONDS,
    GenerationCapacityUnavailable,
    claim_workspace_linked_generation_job,
    complete_workspace_linked_generation_job,
    fail_workspace_linked_generation_job,
    heartbeat_workspace_linked_generation_job,
    release_workspace_linked_generation_job,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationDeferred,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationExecutionError,
    WorkspaceOperationCancelled,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
    workspace_operation_handler_registry,
)


logger = logging.getLogger(__name__)

PRESENTATION_GENERATION_OPERATION_KIND = "presentation.generate"

GenerationPipeline = Callable[
    [uuid.UUID, str, str | None, WorkspaceOperationExecutionContext],
    Awaitable[WorkspaceOperationResult],
]
InternalHeaderProvider = Callable[[], Awaitable[Mapping[str, str]]]


def _safe_failure(status: AsyncPresentationGenerationTaskModel) -> tuple[str, bool]:
    payload = status.error if isinstance(status.error, Mapping) else {}
    detail = payload.get("detail")
    nested = detail if isinstance(detail, Mapping) else {}
    code = payload.get("code") or nested.get("code")
    if not code and isinstance(detail, str):
        code = detail
    code = str(code or "presentation_generation_failed")[:128]

    explicit_retryable = payload.get("retryable", nested.get("retryable"))
    if isinstance(explicit_retryable, bool):
        return code, explicit_retryable
    status_code = payload.get("status_code")
    try:
        numeric_status = int(status_code)
    except (TypeError, ValueError):
        numeric_status = 500
    return code, numeric_status == 429 or numeric_status >= 500


class PresentationGenerationOperationHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        pipeline: GenerationPipeline | None = None,
        internal_header_provider: InternalHeaderProvider = (
            authenticated_internal_request_headers
        ),
        heartbeat_seconds: float = GENERATION_JOB_HEARTBEAT_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._pipeline = pipeline or self._run_existing_pipeline
        self._internal_header_provider = internal_header_provider
        self._heartbeat_seconds = max(0.01, heartbeat_seconds)

    async def _linked_job(self, operation_id: uuid.UUID) -> PresentationGenerationJob:
        async with self._session_factory() as session:
            job = (
                await session.execute(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.workspace_operation_id
                        == operation_id
                    )
                )
            ).scalar_one_or_none()
        if job is None:
            # Bootstrap commits the operation and linked job atomically.  A
            # short retry is still safer for rows created by rolling upgrades.
            raise RetryableWorkspaceOperationError(
                "presentation_generation_job_not_linked"
            )
        return job

    async def _legacy_heartbeat_loop(
        self,
        *,
        job_id: uuid.UUID,
        operation_id: uuid.UUID,
        attempt_token: str,
    ) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            alive = await heartbeat_workspace_linked_generation_job(
                job_id, operation_id, attempt_token
            )
            if not alive:
                # Authored publication atomically marks this projection
                # published and clears its token before the handler returns.
                # The generic operation heartbeat remains authoritative.
                return

    async def _run_existing_pipeline(
        self,
        job_id: uuid.UUID,
        task_id: str,
        export_cookie_header: str | None,
        context: WorkspaceOperationExecutionContext,
    ) -> WorkspaceOperationResult:
        # Lazy imports avoid coupling application startup to the large endpoint
        # module and prevent an import cycle through lifespan/runner services.
        from api.v1.ppt.endpoints.presentation_generate import (
            _request_from_durable_payload,
            _validate_authored_exact_admission,
            _validate_authored_master_pin_admission,
            generate_presentation_handler,
        )

        await context.raise_if_cancelled()
        async with self._session_factory() as session:
            job = await session.get(PresentationGenerationJob, job_id)
            status = await session.get(
                AsyncPresentationGenerationTaskModel, task_id
            )
            if job is None:
                raise RetryableWorkspaceOperationError(
                    "presentation_generation_job_missing"
                )
            if status is None:
                raise NonRetryableWorkspaceOperationError(
                    "presentation_generation_task_missing"
                )

            request = _request_from_durable_payload(job.request_payload)
            request._request_id = job.request_id
            _validate_authored_exact_admission(request)
            await _validate_authored_master_pin_admission(request)
            if job.template_v2_target is not None:
                from services.template_v2_generation_service import (
                    TemplateV2GenerationTarget,
                )

                request._template_v2_generation_target = (
                    TemplateV2GenerationTarget.from_durable_payload(
                        job.template_v2_target
                    )
                )

            await generate_presentation_handler(
                request,
                job.presentation_id,
                async_status=status,
                export_cookie_header=export_cookie_header,
                generation_job=job,
                progress_reporter=context.progress,
                sql_session=session,
            )
            await session.refresh(status)
            if status.status != "completed":
                code, retryable = _safe_failure(status)
                error_type = (
                    RetryableWorkspaceOperationError
                    if retryable
                    else NonRetryableWorkspaceOperationError
                )
                raise error_type(code)

            operation_projection = (
                await session.execute(
                    select(
                        WorkspaceOperation.result_revision,
                        WorkspaceOperation.result,
                    ).where(
                        WorkspaceOperation.id == context.claim.operation_id
                    )
                )
            ).one_or_none()
            result_revision = (
                operation_projection.result_revision
                if operation_projection is not None
                else None
            )
            if result_revision is None:
                result_revision = await session.scalar(
                    select(PresentationModel.revision).where(
                        PresentationModel.id == context.claim.presentation_id
                    )
                )
            publication_result = (
                operation_projection.result
                if operation_projection is not None
                and isinstance(operation_projection.result, Mapping)
                else {}
            )
            return WorkspaceOperationResult(
                payload={
                    "status": "completed",
                    "task_id": task_id,
                    "job_id": str(job_id),
                    **publication_result,
                },
                result_revision=(
                    int(result_revision) if result_revision is not None else None
                ),
            )

    async def execute(
        self, context: WorkspaceOperationExecutionContext
    ) -> WorkspaceOperationResult:
        claim = context.claim
        await context.raise_if_cancelled()
        await self._linked_job(claim.operation_id)

        # Mint the credential before claiming the compatibility projection.
        # If token issuance itself fails, the durable job remains queued and
        # the generic runner can retry without waiting for a legacy lease.
        # The short-lived Cookie header exists only in this stack frame.
        internal_headers = await self._internal_header_provider()
        export_cookie_header = internal_headers.get("Cookie") or None
        try:
            linked_claim = await claim_workspace_linked_generation_job(
                claim.operation_id,
                claim.attempt_token,
                claim.attempt_number,
            )
        except GenerationCapacityUnavailable as error:
            # Capacity is a durable scheduling condition, not a failed
            # generation attempt.  The runner returns its generic lease and
            # requeues this operation without consuming its error budget.
            raise WorkspaceOperationDeferred(
                error.code,
                retry_after_seconds=error.retry_after_seconds,
                detail=(
                    {"queue_position": error.queue_position}
                    if error.queue_position is not None
                    else None
                ),
            ) from error
        if linked_claim is None:
            raise WorkspaceOperationLeaseLost(
                "presentation_generation_compatibility_claim_lost"
            )

        job_id, task_id = linked_claim
        heartbeat = asyncio.create_task(
            self._legacy_heartbeat_loop(
                job_id=job_id,
                operation_id=claim.operation_id,
                attempt_token=claim.attempt_token,
            )
        )
        # Authored generation has no reporter seam of its own; this turns the
        # per-slide checkpoints it already writes into timeline progress.
        stop_authored_progress = start_authored_generation_progress_polling(
            job_id=job_id,
            report=context.progress,
            session_factory=self._session_factory,
        )
        try:
            await context.progress(stage="generation_started")
            result = await self._pipeline(
                job_id, task_id, export_cookie_header, context
            )
            if not await complete_workspace_linked_generation_job(
                job_id, claim.operation_id, claim.attempt_token
            ):
                raise WorkspaceOperationLeaseLost(
                    "presentation_generation_compatibility_finalize_lost"
                )
            await context.progress(stage="generation_completed", percent=100)
            return result
        except asyncio.CancelledError:
            await asyncio.shield(
                release_workspace_linked_generation_job(
                    job_id, claim.operation_id, claim.attempt_token
                )
            )
            raise
        except WorkspaceOperationCancelled:
            await release_workspace_linked_generation_job(
                job_id, claim.operation_id, claim.attempt_token
            )
            raise
        except WorkspaceOperationExecutionError as error:
            await fail_workspace_linked_generation_job(
                job_id,
                claim.operation_id,
                claim.attempt_token,
                error_code=error.code,
            )
            raise
        except WorkspaceOperationLeaseLost:
            raise
        except Exception as error:
            code = str(getattr(error, "code", "presentation_generation_failed"))
            retryable = bool(getattr(error, "retryable", True))
            await fail_workspace_linked_generation_job(
                job_id,
                claim.operation_id,
                claim.attempt_token,
                error_code=code,
            )
            error_type = (
                RetryableWorkspaceOperationError
                if retryable
                else NonRetryableWorkspaceOperationError
            )
            raise error_type(code) from error
        finally:
            await stop_authored_progress()
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)


def register_presentation_generation_operation_handler(
    registry: WorkspaceOperationHandlerRegistry = workspace_operation_handler_registry,
    *,
    replace: bool = False,
) -> PresentationGenerationOperationHandler:
    """Register the canonical generation kind and return its handler."""

    handler = PresentationGenerationOperationHandler()
    registry.register(
        PRESENTATION_GENERATION_OPERATION_KIND,
        handler,
        replace=replace,
    )
    return handler
