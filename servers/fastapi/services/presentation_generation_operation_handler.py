"""Workspace operation adapter for the existing presentation generator.

The generic workspace operation owns scheduling, retries and cancellation.
The linked ``PresentationGenerationJob`` is retained as a compatibility
projection because authored checkpoints fence their writes through that row.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
import logging
import time
from typing import Any
import uuid

from sqlalchemy import func, inspect, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from constants.user_messages import UserMessageKey, user_message
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
from services.authored_provider_rate_profile import resolve_authored_rate_profile
from services.authored_generation_timing_receipt import (
    GENERATION_ATTEMPT_TIMING_STAGES,
    GenerationTimingRecorder,
    create_generation_attempt_timing_receipt_v2,
)
from services.database import async_session_maker
from services.presentation_generation_job_service import (
    GENERATION_JOB_HEARTBEAT_SECONDS,
    claim_workspace_linked_generation_job,
)
from services.public_quality_receipt_v3_store import (
    PostgresOperationQualityReceiptV3Port,
)
from services.public_generation_worker_lifecycle import (
    claim_linked_generation_job,
    durable_generation_failure,
    public_generation_deadline,
    require_public_generation_publication,
)
from services.presentation_generation_attempt_timing import (
    _persist_failed_attempt_timing,
    _persist_terminal_attempt_timing,
    _project_failed_attempt_timing,
    # Re-exported for the timing tests that reach for it on this module.
    _provider_call_timing_snapshot,
    _queue_wait_ms,
    _safe_failure,
)
from services.public_operation_deadline_scope import public_operation_deadline_scope
from services.authored_local_fit_deadline import (
    authored_local_fit_deadline_scope,
)
from services.provider_usage_instrumentation import provider_operation_usage_scope
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    RetryableWorkspaceOperationError,
    WorkspaceOperationExecutionContext,
    WorkspaceOperationExecutionError,
    WorkspaceOperationCancelled,
    WorkspaceOperationLeaseLost,
    WorkspaceOperationResult,
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


def _snapshot_status_error(
    status: AsyncPresentationGenerationTaskModel,
    fallback: Mapping[str, object] | None = None,
) -> dict[str, object]:
    # A nested generation transaction may already have rolled back.  Reading
    # loaded state avoids implicit async IO from an expired ORM attribute.
    payload = inspect(status).dict.get("error", fallback)
    return deepcopy(dict(payload)) if isinstance(payload, Mapping) else {}


async def _generation_failure_decision(
    session: AsyncSession,
    job_id: uuid.UUID,
    status_error: Mapping[str, object],
) -> tuple[str, bool]:
    """Durable retry decision for a failed generation status.

    The failure keeps its own code. It is retryable only when no slide row of
    the job carries a quality-exhausted ``fail_stage`` record: the job's
    ``last_error``/``resume_allowed`` hold only the last write, so a later
    retryable global error would otherwise buy the exhausted page again.
    Unreadable evidence never reopens an automatic retry.
    """

    code, retryable = _safe_failure(status_error)
    if not retryable:
        return code, retryable
    from services.generation_core.quality.page_failure_kind import PAGE_QUALITY, page_error_class

    try:
        rows = (
            await session.execute(
                select(AuthoredSlideCheckpoint.slide_index, AuthoredSlideCheckpoint.error)
                .where(AuthoredSlideCheckpoint.job_id == job_id)
                .where(AuthoredSlideCheckpoint.failed_stage.is_not(None))
            )
        ).all()
    except Exception:
        await session.rollback()
        logger.warning(
            "Failed to read slide failure evidence for job %s", job_id, exc_info=True
        )
        return code, False
    for slide_index, error in rows:
        if (
            not isinstance(error, Mapping)
            or not isinstance(error.get("code"), str)
            or type(error.get("retryable")) is not bool
            or type(error.get("slide_index")) is not int
            or error.get("slide_index") != slide_index
        ):
            # A failed row whose record cannot be read is not proof that no
            # page is exhausted.
            return code, False
        # The page ended on its quality (``terminal.is_exhausted_page``'s rule).
        if page_error_class(error["code"]) == PAGE_QUALITY and error["retryable"] is False:
            return code, False
    return code, retryable


class PresentationGenerationOperationHandler:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        pipeline: GenerationPipeline | None = None,
        internal_header_provider: InternalHeaderProvider = (authenticated_internal_request_headers),
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
                        PresentationGenerationJob.workspace_operation_id == operation_id
                    )
                )
            ).scalar_one_or_none()
        if job is None:
            # Bootstrap commits the operation and linked job atomically.  A
            # short retry is still safer for rows created by rolling upgrades.
            raise RetryableWorkspaceOperationError("presentation_generation_job_not_linked")
        return job

    async def _run_existing_pipeline(
        self,
        job_id: uuid.UUID,
        task_id: str,
        export_cookie_header: str | None,
        context: WorkspaceOperationExecutionContext,
    ) -> WorkspaceOperationResult:
        # Lazy imports avoid coupling application startup to the large endpoint
        # module and prevent an import cycle through lifespan/runner services.
        from services.presentation_generation_pipeline import (
            _request_from_durable_payload,
            _validate_authored_exact_admission,
            _validate_authored_master_pin_admission,
            generate_presentation_handler,
        )

        await context.raise_if_cancelled()
        async with self._session_factory() as session:
            job = await session.get(PresentationGenerationJob, job_id)
            status = await session.get(AsyncPresentationGenerationTaskModel, task_id)
            operation_timing = await session.get(WorkspaceOperation, context.claim.operation_id)
            operation_queue_wait_ms = _queue_wait_ms(operation_timing)
            if job is None:
                raise RetryableWorkspaceOperationError("presentation_generation_job_missing")
            if status is None:
                raise NonRetryableWorkspaceOperationError("presentation_generation_task_missing")

            # The generic operation claim is authoritative.  Keep its
            # provenance available to checkpoint/publication callers without
            # creating a second job lease or heartbeat lifecycle.
            job.attempt_token = context.claim.attempt_token
            object.__setattr__(
                job,
                "_workspace_fencing_token",
                context.claim.fencing_token,
            )
            request = _request_from_durable_payload(job.request_payload)
            if getattr(request, "source_mode", None) == "raster_reconstruction":
                from services.svg_generation_assets import publication_context

                raster_publication, _ = await publication_context(session, job)
                request._raster_tenant_id = raster_publication.tenant_id
            object.__setattr__(
                request,
                "_workspace_operation_attempt_token",
                context.claim.attempt_token,
            )
            object.__setattr__(
                request,
                "_workspace_operation_fencing_token",
                context.claim.fencing_token,
            )
            _begin_upstream_outline_timing(request)
            request._request_id = job.request_id
            timing_recorder = GenerationTimingRecorder()
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
            operation_payload = (
                operation_timing.request_payload
                if isinstance(operation_timing, WorkspaceOperation)
                and isinstance(operation_timing.request_payload, Mapping)
                else {}
            )
            generation_route_receipt = operation_payload.get("generation_route_receipt")
            if (
                isinstance(generation_route_receipt, Mapping)
                and generation_route_receipt.get("selectedRoute") == "source-native"
            ):
                from services.authored_vnext.source_native_generation import (
                    SourceNativeGenerationError,
                    run_source_native_generation,
                )
                from services.generation_provider_admission import (
                    generation_provider_capabilities,
                    server_generation_profile_identity,
                )
                from models.presentation_profiles import resolve_presentation_profile
                from services.provider_usage_instrumentation import (
                    public_operation_scope,
                )
                from services.source_content.pipeline import (
                    RASTER_SOURCE_MODE,
                    SOURCE_MODE as SOURCE_CONTENT_RESTYLE_MODE,
                    admit_source_content_request,
                )
                from utils.llm_provider import generation_provider_policy

                if request.source_mode in {SOURCE_CONTENT_RESTYLE_MODE, RASTER_SOURCE_MODE}:
                    from utils.owner_context import get_current_tenant_id

                    await admit_source_content_request(
                        request,
                        sql_session=session,
                        owner_id=str(context.claim.owner_id),
                        tenant_id=(
                            request._raster_tenant_id
                            if request.source_mode == RASTER_SOURCE_MODE
                            else get_current_tenant_id()
                        ),
                    )

                status_error = _snapshot_status_error(status)
                with provider_operation_usage_scope() as provider_usage:
                    try:
                        persisted = await session.get(PresentationModel, context.claim.presentation_id)
                        identity = server_generation_profile_identity(request, persisted)
                        profile = resolve_presentation_profile(identity)
                        required_capabilities = generation_provider_capabilities(request, profile.driver_id)
                        with public_operation_scope(
                            context.claim.operation_id,
                            attempt_token=context.claim.attempt_token,
                            fencing_token=context.claim.fencing_token,
                        ):
                            with generation_provider_policy(
                                identity,
                                required_capabilities=required_capabilities,
                            ):
                                source_native_result = await run_source_native_generation(
                                    request=request,
                                    generation_job=job,
                                    context=context,
                                    route_receipt=generation_route_receipt,
                                )
                    except BaseException as error:
                        logger.exception(
                            "Source-native generation attempt failed for operation %s",
                            context.claim.operation_id,
                        )
                        status_error = _snapshot_status_error(status, status_error)
                        try:
                            receipt_payload = await _persist_failed_attempt_timing(
                                request=request,
                                job_id=job_id,
                                status_error=status_error,
                                context=context,
                                error=error,
                                recorder=timing_recorder,
                                provider_usage=provider_usage,
                                queue_wait_ms=operation_queue_wait_ms,
                            )
                            if receipt_payload is not None:
                                await session.rollback()
                                await _project_failed_attempt_timing(
                                    session=session,
                                    context=context,
                                    receipt_payload=receipt_payload,
                                )
                        except Exception:
                            await session.rollback()
                            logger.warning(
                                "Failed to record source-native attempt timing for job %s",
                                job_id,
                                exc_info=True,
                            )
                        if isinstance(error, SourceNativeGenerationError):
                            raise NonRetryableWorkspaceOperationError(error.code) from error
                        raise
                publication = source_native_result.get("publication")
                if not isinstance(publication, Mapping):
                    raise NonRetryableWorkspaceOperationError("authored_manifest_publication_failed")
                try:
                    from services.authored_vnext.narrative_ir.content_graph import (
                        validate_content_integrity_receipt,
                    )

                    validate_content_integrity_receipt(source_native_result.get("contentIntegrityReceipt"))
                except Exception as error:
                    raise NonRetryableWorkspaceOperationError("content_integrity_receipt_invalid") from error
                success_receipt_payload = await _persist_terminal_attempt_timing(
                    job_id=job_id,
                    context=context,
                    recorder=timing_recorder,
                    provider_usage=provider_usage,
                    queue_wait_ms=operation_queue_wait_ms,
                    outcome="succeeded",
                    retry_reason=("workspace_retry" if context.claim.attempt_number > 1 else None),
                    terminal_error=None,
                    request=request,
                )
                if success_receipt_payload is None:
                    raise NonRetryableWorkspaceOperationError("generation_timing_receipt_persistence_failed")
                success_timing_projection = generation_timing_result_projection(success_receipt_payload)
                if not success_timing_projection:
                    raise NonRetryableWorkspaceOperationError("generation_timing_receipt_projection_invalid")
                status.status = "completed"
                status.message = "Source-native Design Pack generation completed"
                status.data = {
                    **dict(source_native_result),
                    **success_timing_projection,
                }
                session.add(status)
                await session.commit()
                raw_revision = publication.get("revision")
                return WorkspaceOperationResult(
                    payload={
                        "status": "completed",
                        "task_id": task_id,
                        "job_id": str(job_id),
                        **dict(source_native_result),
                        **success_timing_projection,
                    },
                    result_revision=(
                        raw_revision if isinstance(raw_revision, int) and not isinstance(raw_revision, bool) else None
                    ),
                )
            strategy_receipt = operation_payload.get("generation_strategy")
            if isinstance(strategy_receipt, Mapping):
                from services.authored_vnext.deck_ir.integration import (
                    DeckIrIntegrationError,
                    ensure_product_deck_ir_execution_adapter,
                    run_deck_ir_generation,
                )
                from services.authored_vnext.deck_ir.strategy import (
                    GenerationStrategyCapabilityError,
                    validate_sealed_generation_strategy,
                )
                from services.authored_vnext.deck_ir.grammar import DeckIrGrammarError

                try:
                    selected_strategy = validate_sealed_generation_strategy(
                        strategy_receipt,
                        operation_id=str(context.claim.operation_id),
                    )
                except GenerationStrategyCapabilityError as error:
                    raise NonRetryableWorkspaceOperationError(error.code) from error
                if selected_strategy == "reject":
                    raise NonRetryableWorkspaceOperationError("generation_strategy_capability_missing")
                if selected_strategy == "deck-ir":
                    status_error = _snapshot_status_error(status)
                    with provider_operation_usage_scope() as provider_usage:
                        try:
                            from services.generation_provider_admission import (
                                generation_provider_capabilities,
                                server_generation_profile_identity,
                            )
                            from models.presentation_profiles import (
                                resolve_presentation_profile,
                            )
                            from services.provider_usage_instrumentation import (
                                public_operation_scope,
                            )
                            from utils.llm_provider import generation_provider_policy

                            # Workers can run in a different process than public
                            # admission; install the same no-I/O composition root
                            # before resolving the sealed operation strategy.
                            ensure_product_deck_ir_execution_adapter(explicit=True)
                            persisted = await session.get(PresentationModel, context.claim.presentation_id)
                            identity = server_generation_profile_identity(request, persisted)
                            profile = resolve_presentation_profile(identity)
                            required_capabilities = generation_provider_capabilities(request, profile.driver_id)
                            with public_operation_scope(
                                context.claim.operation_id,
                                attempt_token=context.claim.attempt_token,
                                fencing_token=context.claim.fencing_token,
                            ):
                                with generation_provider_policy(
                                    identity,
                                    required_capabilities=required_capabilities,
                                ):
                                    deck_result = await run_deck_ir_generation(
                                        request=request,
                                        generation_job=job,
                                        context=context,
                                        strategy_receipt=strategy_receipt,
                                    )
                        except BaseException as error:
                            status_error = _snapshot_status_error(status, status_error)
                            try:
                                receipt_payload = await _persist_failed_attempt_timing(
                                    request=request,
                                    job_id=job_id,
                                    status_error=status_error,
                                    context=context,
                                    error=error,
                                    recorder=timing_recorder,
                                    provider_usage=provider_usage,
                                    queue_wait_ms=operation_queue_wait_ms,
                                )
                                if receipt_payload is not None:
                                    await session.rollback()
                                    await _project_failed_attempt_timing(
                                        session=session,
                                        context=context,
                                        receipt_payload=receipt_payload,
                                    )
                            except Exception:
                                await session.rollback()
                                logger.warning(
                                    "Failed to record Deck IR attempt timing for job %s",
                                    job_id,
                                    exc_info=True,
                                )
                            if isinstance(
                                error,
                                (
                                    DeckIrIntegrationError,
                                    GenerationStrategyCapabilityError,
                                    DeckIrGrammarError,
                                ),
                            ):
                                raise NonRetryableWorkspaceOperationError(error.code) from error
                            raise
                    publication = deck_result.get("publication")
                    if not isinstance(publication, Mapping):
                        raise NonRetryableWorkspaceOperationError("authored_manifest_publication_failed")
                    await require_public_generation_publication(
                        session=session,
                        context=context,
                    )
                    success_receipt_payload = await _persist_terminal_attempt_timing(
                        job_id=job_id,
                        context=context,
                        recorder=timing_recorder,
                        provider_usage=provider_usage,
                        queue_wait_ms=operation_queue_wait_ms,
                        outcome="succeeded",
                        retry_reason=("workspace_retry" if context.claim.attempt_number > 1 else None),
                        terminal_error=None,
                        request=request,
                    )
                    if success_receipt_payload is None:
                        raise NonRetryableWorkspaceOperationError("generation_timing_receipt_persistence_failed")
                    success_timing_projection = generation_timing_result_projection(success_receipt_payload)
                    if not success_timing_projection:
                        raise NonRetryableWorkspaceOperationError("generation_timing_receipt_projection_invalid")
                    # The injected publisher has already performed the fenced
                    # immutable manifest CAS.  This compatibility task row is
                    # updated only after that success and never becomes the
                    # publication authority.
                    status.status = "completed"
                    status.message = "Deck IR generation completed"
                    status.data = {
                        **dict(deck_result),
                        **success_timing_projection,
                    }
                    session.add(status)
                    await session.commit()
                    raw_revision = publication.get("revision")
                    return WorkspaceOperationResult(
                        payload={
                            "status": "completed",
                            "task_id": task_id,
                            "job_id": str(job_id),
                            **dict(deck_result),
                            **success_timing_projection,
                        },
                        result_revision=(
                            raw_revision
                            if isinstance(raw_revision, int) and not isinstance(raw_revision, bool)
                            else None
                        ),
                    )
            status_error = _snapshot_status_error(status)
            with provider_operation_usage_scope() as provider_usage:
                try:
                    request._quality_receipt_v3_persistence_port = PostgresOperationQualityReceiptV3Port(
                        session,
                        operation_id=context.claim.operation_id,
                        attempt_token=context.claim.attempt_token,
                        fencing_token=context.claim.fencing_token,
                    )
                    request._quality_receipt_v3_durability_profile = "public"
                    _validate_authored_exact_admission(request)
                    await _validate_authored_master_pin_admission(request)
                    await generate_presentation_handler(
                        request,
                        job.presentation_id,
                        async_status=status,
                        export_cookie_header=export_cookie_header,
                        generation_job=job,
                        progress_reporter=context.progress,
                        # WorkspaceOperationExecutionContext.extend_lease is a
                        # Protocol method, not enforced at runtime; resolve it
                        # defensively so a context double that predates this
                        # capability (declared optional throughout the generation
                        # handler chain) degrades to "no lease to extend" instead
                        # of an AttributeError.
                        extend_lease=getattr(context, "extend_lease", None),
                        sql_session=session,
                    )
                except BaseException as error:
                    status_error = _snapshot_status_error(status, status_error)
                    try:
                        receipt_payload = await _persist_failed_attempt_timing(
                            request=request,
                            job_id=job_id,
                            status_error=status_error,
                            context=context,
                            error=error,
                            recorder=timing_recorder,
                            provider_usage=provider_usage,
                            queue_wait_ms=operation_queue_wait_ms,
                        )
                        if receipt_payload is not None:
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
                            "Failed to record generation attempt timing for job %s",
                            job_id,
                            exc_info=True,
                        )
                    raise
            await session.refresh(status)
            if status.status != "completed":
                status_error = _snapshot_status_error(status)
                code, retryable = await _generation_failure_decision(session, job_id, status_error)
                projected_error = RuntimeError(code)
                try:
                    receipt_payload = await _persist_failed_attempt_timing(
                        request=request,
                        job_id=job_id,
                        status_error=status_error,
                        context=context,
                        error=projected_error,
                        recorder=timing_recorder,
                        provider_usage=provider_usage,
                        queue_wait_ms=operation_queue_wait_ms,
                    )
                    if receipt_payload is not None:
                        await _project_failed_attempt_timing(
                            session=session,
                            context=context,
                            receipt_payload=receipt_payload,
                        )
                except Exception:
                    await session.rollback()
                    logger.warning(
                        "Failed to record generation attempt timing for job %s",
                        job_id,
                        exc_info=True,
                    )
                error_type = RetryableWorkspaceOperationError if retryable else NonRetryableWorkspaceOperationError
                raise error_type(code)

            operation_projection_row = await require_public_generation_publication(
                session=session,
                context=context,
            )
            success_receipt_payload = await _persist_terminal_attempt_timing(
                job_id=job_id,
                context=context,
                recorder=timing_recorder,
                provider_usage=provider_usage,
                queue_wait_ms=operation_queue_wait_ms,
                outcome="succeeded",
                retry_reason=("workspace_retry" if context.claim.attempt_number > 1 else None),
                terminal_error=None,
                request=request,
            )
            if success_receipt_payload is None:
                raise NonRetryableWorkspaceOperationError("generation_timing_receipt_persistence_failed")
            success_timing_projection = generation_timing_result_projection(success_receipt_payload)
            if not success_timing_projection:
                raise NonRetryableWorkspaceOperationError("generation_timing_receipt_projection_invalid")

            result_revision = operation_projection_row.result_revision
            if result_revision is None:
                result_revision = await session.scalar(
                    select(PresentationModel.revision).where(PresentationModel.id == context.claim.presentation_id)
                )
            publication_result = (
                operation_projection_row.result if isinstance(operation_projection_row.result, Mapping) else {}
            )
            return WorkspaceOperationResult(
                payload={
                    "status": "completed",
                    "task_id": task_id,
                    "job_id": str(job_id),
                    **publication_result,
                    **success_timing_projection,
                },
                result_revision=(int(result_revision) if result_revision is not None else None),
            )

    async def execute(self, context: WorkspaceOperationExecutionContext) -> WorkspaceOperationResult:
        """Run public work under its immutable operation deadline."""

        deadline = await public_generation_deadline(self._session_factory, context)
        if deadline is None:
            return await self._execute_without_deadline(context)
        remaining_seconds = (deadline - datetime.now(timezone.utc)).total_seconds()
        if remaining_seconds <= 0:
            raise NonRetryableWorkspaceOperationError("public_deadline_exceeded")
        try:
            # Publish the pinned instant so stages below can size their own
            # sub-budget against what is left; asyncio.timeout does not expose
            # its deadline to the code it wraps.
            with public_operation_deadline_scope(deadline), authored_local_fit_deadline_scope(remaining_seconds):
                async with asyncio.timeout(remaining_seconds):
                    return await self._execute_without_deadline(context)
        except TimeoutError:
            if datetime.now(timezone.utc) >= deadline:
                raise NonRetryableWorkspaceOperationError("public_deadline_exceeded") from None
            raise

    async def _execute_without_deadline(
        self,
        context: WorkspaceOperationExecutionContext,
    ) -> WorkspaceOperationResult:
        claim = context.claim
        await context.raise_if_cancelled()
        linked_job = await self._linked_job(claim.operation_id)

        # Public generation workers are sessionless.  Export is submitted as a
        # principal-bound durable operation and must never inherit a browser or
        # service Cookie through the generation stack.  Keep the compatibility
        # pipeline argument during the staged rollout, but pin it to ``None``.
        export_cookie_header = None
        job_id, task_id = await claim_linked_generation_job(
            session_factory=self._session_factory,
            context=context,
            claim_linked=claim_workspace_linked_generation_job,
        )
        carrier = claim.request_payload or {}
        if carrier.get("recovery_revision") == "svg-partial-recovery/v1":
            from services.svg_partial_recovery import LINEAGE_KEY, digest

            payload = linked_job.request_payload or {}
            lineage = payload.get(LINEAGE_KEY)
            manifest = carrier.get("svg_partial_recovery_manifest")
            manifest_hash = carrier.get("svg_partial_recovery_manifest_hash")
            if (not isinstance(lineage, dict) or not isinstance(manifest, dict)
                    or manifest.get("append_only_recovery") is not True
                    or linked_job.id != job_id or linked_job.workspace_operation_id != claim.operation_id
                    or linked_job.presentation_id != claim.presentation_id
                    or carrier.get("generation_request") != payload
                    or carrier.get("svg_partial_recovery_lineage") != lineage
                    or lineage.get("schema") != "svg-partial-recovery-lineage/v2"
                    or lineage.get("operation_id") != str(claim.operation_id)
                    or lineage.get("job_id") != str(job_id)
                    or lineage.get("presentation_id") != str(claim.presentation_id)
                    or lineage.get("manifest_hash") != manifest_hash
                    or digest(manifest) != manifest_hash
                    or lineage.get("archive_digest") != manifest.get("archive_digest")
                    or manifest.get("owner_id") != str(claim.owner_id)
                    or manifest.get("expected_page_count") != linked_job.total_slides):
                raise NonRetryableWorkspaceOperationError("svg_partial_recovery_episode_unbound")
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
                    raise WorkspaceOperationLeaseLost("presentation_generation_checkpoint_reuse_event_fenced")
        # Authored generation has no reporter seam of its own; this turns the
        # per-slide checkpoints it already writes into timeline progress.
        stop_authored_progress = start_authored_generation_progress_polling(
            job_id=job_id,
            report=context.progress,
            session_factory=self._session_factory,
        )
        try:
            if not await context.progress(stage="generation_started"):
                raise WorkspaceOperationLeaseLost("presentation_generation_lifecycle_fenced")
            await mark_authored_generation_stage(
                job_id=job_id,
                attempt_token=claim.attempt_token,
                stage="outline",
                session_factory=self._session_factory,
            )
            if not await context.progress(
                stage="authored_outline",
                percent=8,
                message=user_message(UserMessageKey.GENERATION_STAGE_OUTLINE),
            ):
                raise WorkspaceOperationLeaseLost("presentation_generation_lifecycle_fenced")
            from utils.generation_model import generation_model_context

            preset = (
                linked_job.request_payload.get("codex_generation_preset")
                if isinstance(linked_job.request_payload, Mapping)
                else None
            )
            with generation_model_context(preset):
                result = await self._pipeline(job_id, task_id, export_cookie_header, context)
            return result
        except asyncio.CancelledError:
            raise
        except WorkspaceOperationCancelled:
            raise
        except WorkspaceOperationExecutionError:
            raise
        except WorkspaceOperationLeaseLost:
            raise
        except Exception as error:
            code, retryable = durable_generation_failure(error)
            error_type = RetryableWorkspaceOperationError if retryable else NonRetryableWorkspaceOperationError
            raise error_type(code) from error
        finally:
            await stop_authored_progress()
