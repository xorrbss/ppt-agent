"""Workspace operation adapter for the existing presentation generator.

The generic workspace operation owns scheduling, retries and cancellation.
The linked ``PresentationGenerationJob`` is retained as a compatibility
projection because authored checkpoints fence their writes through that row.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
import time
from typing import Any
import uuid

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.workspace import WorkspaceOperation
from api.v1.auth.internal import authenticated_internal_request_headers
from services.authored_generation_progress_poller import (
    mark_authored_generation_stage,
    start_authored_generation_progress_polling,
)
from services.authored_generation_timing_receipt import (
    GENERATION_ATTEMPT_TIMING_STAGES,
    GenerationTimingRecorder,
    create_generation_attempt_timing_receipt,
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
from services.public_quality_receipt_v3_store import (
    PostgresOperationQualityReceiptV3Port,
)
from services.provider_usage_instrumentation import provider_operation_usage_scope
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
from services.workspace_generation_timing_payload import (
    generation_timing_result_projection,
)


logger = logging.getLogger(__name__)

PRESENTATION_GENERATION_OPERATION_KIND = "presentation.generate"


def _begin_upstream_outline_timing(request: object) -> None:
    """Pin the authored outline clock before any upstream provider work."""

    if str(getattr(request, "template", "")).strip().lower() == "authored":
        object.__setattr__(request, "_authored_outline_started_at", time.monotonic())

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


def _attempt_failure_stage(
    error: BaseException,
    status: AsyncPresentationGenerationTaskModel,
    current_stage: object = None,
) -> str:
    payload = status.error if isinstance(status.error, Mapping) else {}
    detail = payload.get("detail")
    nested = detail if isinstance(detail, Mapping) else {}
    stage = str(
        getattr(error, "stage", "")
        or payload.get("stage")
        or nested.get("stage")
        or current_stage
        or ""
    ).strip().lower()
    if stage in GENERATION_ATTEMPT_TIMING_STAGES:
        return stage
    if stage == "persist":
        return "checkpoint"
    return "author"


async def _persist_failed_attempt_timing(
    *,
    request: object,
    job: PresentationGenerationJob,
    status: AsyncPresentationGenerationTaskModel,
    context: WorkspaceOperationExecutionContext,
    error: BaseException,
    recorder: GenerationTimingRecorder,
    provider_usage: object,
) -> dict[str, object] | None:
    if str(getattr(request, "template", "")).lower() != "authored":
        return None
    failure_stage = _attempt_failure_stage(
        error,
        status,
        getattr(request, "_authored_timing_stage", None),
    )
    active_stage = str(
        getattr(request, "_authored_timing_stage", "") or ""
    ).strip().lower()
    measurement_stage = (
        active_stage
        if active_stage in GENERATION_ATTEMPT_TIMING_STAGES
        else failure_stage
    )
    stage_started_at = getattr(request, "_authored_timing_stage_started_at", None)
    if isinstance(stage_started_at, (int, float)):
        recorder.record_stage_ms(
            measurement_stage,
            max(
                recorder.snapshot_stage_timings_ms().get(measurement_stage, 0),
                round((time.monotonic() - stage_started_at) * 1000),
            ),
        )
    usage_snapshot = provider_usage.snapshot()
    totals = usage_snapshot.get("totals", {})
    recorder.record_stage_ms(
        "provider",
        totals.get("latencyMs", 0) if isinstance(totals, Mapping) else 0,
    )
    try:
        receipt = await asyncio.to_thread(
            create_generation_attempt_timing_receipt,
            job_id=job.id,
            attempt_id=context.claim.attempt_token,
            attempt_number=context.claim.attempt_number,
            revision=max(0, int(context.claim.expected_revision or 0)),
            outcome=(
                "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
            ),
            failure_stage=failure_stage,
            stage_timings_ms=recorder.snapshot_stage_timings_ms(),
            total_ms=recorder.elapsed_ms,
            provider_usage=usage_snapshot,
            checkpoint_signals=recorder.checkpoint_signals(),
        )
        return receipt.to_dict()
    except (OSError, TypeError, ValueError):
        logger.warning(
            "Failed to persist generation attempt timing for job %s",
            job.id,
            exc_info=True,
        )
        return None


async def _project_failed_attempt_timing(
    *,
    session: AsyncSession,
    context: WorkspaceOperationExecutionContext,
    receipt_payload: Mapping[str, object] | None,
) -> bool:
    """CAS-project one verified receipt into PostgreSQL before failure unwind.

    The local immutable ledger remains crash-recovery evidence, but the
    operation row is the public authority once a handled exception reaches this
    boundary.  Losing the fence leaves the generation outcome unchanged and
    prevents stale timing from amending a newer attempt.
    """

    if receipt_payload is None:
        return False
    projection = generation_timing_result_projection(receipt_payload)
    if not projection:
        return False
    operation = await session.get(
        WorkspaceOperation, context.claim.operation_id
    )
    if (
        operation is None
        or operation.state != "running"
        or operation.attempt_token != context.claim.attempt_token
        or operation.fencing_token != context.claim.fencing_token
    ):
        return False
    current_result = (
        dict(operation.result) if isinstance(operation.result, Mapping) else {}
    )
    projected = await session.execute(
        update(WorkspaceOperation)
        .where(
            WorkspaceOperation.id == context.claim.operation_id,
            WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_token == context.claim.attempt_token,
            WorkspaceOperation.fencing_token == context.claim.fencing_token,
            WorkspaceOperation.version == operation.version,
        )
        .values(
            result={**current_result, **projection},
            version=WorkspaceOperation.version + 1,
            updated_at=func.now(),
        )
    )
    if projected.rowcount != 1:
        await session.rollback()
        return False
    await session.commit()
    return True


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
            _begin_upstream_outline_timing(request)
            request._request_id = job.request_id
            timing_recorder = GenerationTimingRecorder()
            for stage in GENERATION_ATTEMPT_TIMING_STAGES:
                timing_recorder.record_stage_ms(stage, 0)
            object.__setattr__(
                request,
                "_authored_generation_timing_recorder",
                timing_recorder,
            )
            object.__setattr__(request, "_authored_timing_stage", "outline")
            object.__setattr__(
                request,
                "_authored_timing_stage_started_at",
                getattr(request, "_authored_outline_started_at", time.monotonic()),
            )
            with provider_operation_usage_scope() as provider_usage:
                try:
                    request._quality_receipt_v3_persistence_port = (
                        PostgresOperationQualityReceiptV3Port(
                            session,
                            operation_id=context.claim.operation_id,
                            attempt_token=context.claim.attempt_token,
                            fencing_token=context.claim.fencing_token,
                        )
                    )
                    request._quality_receipt_v3_durability_profile = "public"
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
                except BaseException as error:
                    receipt_payload = await _persist_failed_attempt_timing(
                        request=request,
                        job=job,
                        status=status,
                        context=context,
                        error=error,
                        recorder=timing_recorder,
                        provider_usage=provider_usage,
                    )
                    if receipt_payload is not None:
                        try:
                            # Discard any partial generation transaction before
                            # making the content-free timing projection durable.
                            await session.rollback()
                            await _project_failed_attempt_timing(
                                session=session,
                                context=context,
                                receipt_payload=receipt_payload,
                            )
                        except Exception:
                            await session.rollback()
                            logger.warning(
                                "Failed to project generation attempt timing for job %s",
                                job.id,
                                exc_info=True,
                            )
                    raise
            await session.refresh(status)
            if status.status != "completed":
                code, retryable = _safe_failure(status)
                projected_error = RuntimeError(code)
                receipt_payload = await _persist_failed_attempt_timing(
                    request=request,
                    job=job,
                    status=status,
                    context=context,
                    error=projected_error,
                    recorder=timing_recorder,
                    provider_usage=provider_usage,
                )
                if receipt_payload is not None:
                    await _project_failed_attempt_timing(
                        session=session,
                        context=context,
                        receipt_payload=receipt_payload,
                    )
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

        # Public generation workers are sessionless.  Export is submitted as a
        # principal-bound durable operation and must never inherit a browser or
        # service Cookie through the generation stack.  Keep the compatibility
        # pipeline argument during the staged rollout, but pin it to ``None``.
        export_cookie_header = None
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
        if claim.attempt_number > 1:
            async with self._session_factory() as session:
                checkpoint_reuse_count = int(
                    await session.scalar(
                        select(func.count())
                        .select_from(AuthoredSlideCheckpoint)
                        .where(AuthoredSlideCheckpoint.job_id == job_id)
                        .where(
                            or_(
                                AuthoredSlideCheckpoint.persist_status == "completed",
                                AuthoredSlideCheckpoint.render_status == "completed",
                            )
                        )
                    )
                    or 0
                )
            if checkpoint_reuse_count:
                recorded = await context.emit_durable_event(
                    event_type="run.status",
                    payload={
                        "attempt_number": claim.attempt_number,
                        "checkpoint_reuse_count": checkpoint_reuse_count,
                    },
                )
                if not recorded:
                    raise WorkspaceOperationLeaseLost(
                        "presentation_generation_checkpoint_reuse_event_fenced"
                    )
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
            if not await context.progress(stage="generation_started"):
                raise WorkspaceOperationLeaseLost(
                    "presentation_generation_lifecycle_fenced"
                )
            await mark_authored_generation_stage(
                job_id=job_id,
                attempt_token=claim.attempt_token,
                stage="outline",
                session_factory=self._session_factory,
            )
            if not await context.progress(
                stage="authored_outline",
                percent=8,
                message="발표 구조와 작성 기준을 준비하고 있습니다.",
            ):
                raise WorkspaceOperationLeaseLost(
                    "presentation_generation_lifecycle_fenced"
                )
            result = await self._pipeline(
                job_id, task_id, export_cookie_header, context
            )
            if not await complete_workspace_linked_generation_job(
                job_id, claim.operation_id, claim.attempt_token
            ):
                raise WorkspaceOperationLeaseLost(
                    "presentation_generation_compatibility_finalize_lost"
                )
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
