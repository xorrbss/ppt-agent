from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Protocol
import uuid

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.template_v2_pptx_import import TemplateV2PptxImport


class IngestionRuntimeDependencies(Protocol):
    """Late-bound ingestion hooks kept compatible with legacy monkeypatches."""

    async_session_maker: Any
    logger: Any
    AttemptOwnershipLost: type[RuntimeError]
    IMPORT_DISPATCH_BATCH_SIZE: int
    IMPORT_DISPATCH_INTERVAL_SECONDS: float
    _dispatcher_task: asyncio.Task | None
    _dispatcher_stop: asyncio.Event | None
    _dispatcher_wake: asyncio.Event | None
    _inflight_tasks: set[asyncio.Task]

    _now: Any
    _task_data: Any
    _task_timestamp: Any
    claim_template_v2_pptx_import: Any
    _heartbeat_loop: Any
    get_structured_template_policy: Any
    _analyze_import_source_via_runtime: Any
    _analyze_import_source: Any
    _persist_analysis: Any
    release_template_v2_pptx_import: Any
    fail_template_v2_pptx_import: Any
    _failure_code: Any
    recover_stalled_template_v2_pptx_imports: Any
    run_template_v2_pptx_import: Any
    _track_import_task: Any
    maybe_cleanup_expired_private_sources: Any
    dispatch_template_v2_pptx_imports_once: Any
    _dispatcher_loop: Any
    log_pptx_queue_observation: Any


async def recover_stalled_template_v2_pptx_imports(
    session: AsyncSession,
    dependencies: IngestionRuntimeDependencies,
    *,
    now: datetime | None = None,
) -> int:
    """CAS stale processing rows back to queued without trusting local memory."""

    recovered_at = now or dependencies._now()
    rows = (
        await session.execute(
            select(
                TemplateV2PptxImport.id,
                TemplateV2PptxImport.task_id,
                TemplateV2PptxImport.attempt_token,
                TemplateV2PptxImport.attempt_number,
            ).where(
                TemplateV2PptxImport.state.in_(("processing", "finalizing")),
                or_(
                    TemplateV2PptxImport.lease_expires_at.is_(None),
                    TemplateV2PptxImport.lease_expires_at <= recovered_at,
                ),
            )
        )
    ).all()
    recovered = 0
    for import_id, task_id, attempt_token, attempt_number in rows:
        predicates = [
            TemplateV2PptxImport.id == import_id,
            TemplateV2PptxImport.task_id == task_id,
            TemplateV2PptxImport.state.in_(("processing", "finalizing")),
        ]
        if attempt_token is None:
            predicates.append(TemplateV2PptxImport.attempt_token.is_(None))
        else:
            predicates.append(TemplateV2PptxImport.attempt_token == attempt_token)
        result = await session.execute(
            update(TemplateV2PptxImport)
            .where(
                *predicates,
                or_(
                    TemplateV2PptxImport.lease_expires_at.is_(None),
                    TemplateV2PptxImport.lease_expires_at <= recovered_at,
                ),
            )
            .values(
                state="queued",
                attempt_token=None,
                lease_expires_at=None,
                updated_at=recovered_at,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            continue
        await session.execute(
            update(AsyncPresentationGenerationTaskModel)
            .where(AsyncPresentationGenerationTaskModel.id == task_id)
            .values(
                status="pending",
                message="Recovered stalled Template V2 PPTX import",
                error=None,
                data=dependencies._task_data(
                    import_id,
                    state="queued",
                    attempt_number=attempt_number,
                ),
                updated_at=dependencies._task_timestamp(recovered_at),
            )
            .execution_options(synchronize_session=False)
        )
        recovered += 1
    await session.commit()
    return recovered


async def run_template_v2_pptx_import(
    import_id: uuid.UUID,
    task_id: str,
    dependencies: IngestionRuntimeDependencies,
) -> None:
    """Claim, heartbeat, and complete a durable import attempt."""

    async with dependencies.async_session_maker() as session:
        attempt_token = await dependencies.claim_template_v2_pptx_import(
            session,
            import_id,
            task_id,
        )
    if attempt_token is None:
        return
    stop_heartbeat = asyncio.Event()
    ownership_lost = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        dependencies._heartbeat_loop(
            import_id,
            task_id,
            attempt_token,
            stop_heartbeat,
            ownership_lost,
        )
    )
    try:
        async with dependencies.async_session_maker() as session:
            import_job = await session.get(TemplateV2PptxImport, import_id)
            if (
                import_job is None
                or import_job.task_id != task_id
                or import_job.attempt_token != attempt_token
            ):
                raise dependencies.AttemptOwnershipLost()
            storage_key = import_job.source_storage_key
            source_sha256 = import_job.source_sha256
            source_filename = import_job.source_filename
            source_media_type = import_job.source_media_type
            source_size_bytes = import_job.source_size_bytes
        if dependencies.get_structured_template_policy().pptx_analyzer == "runtime":
            analysis_source = dependencies._analyze_import_source_via_runtime(
                storage_key,
                source_sha256,
                import_id=import_id,
                source_filename=source_filename,
                source_media_type=source_media_type,
                source_size_bytes=source_size_bytes,
            )
        else:
            analysis_source = asyncio.to_thread(
                dependencies._analyze_import_source,
                storage_key,
                source_sha256,
                import_id=import_id,
                source_filename=source_filename,
                source_media_type=source_media_type,
                source_size_bytes=source_size_bytes,
            )
        analysis_result, repeat_suggestions, source_inventory = await analysis_source
        if ownership_lost.is_set():
            raise dependencies.AttemptOwnershipLost()
        if not await dependencies._persist_analysis(
            import_id,
            task_id,
            attempt_token,
            analysis_result,
            repeat_suggestions,
            source_inventory,
        ):
            raise dependencies.AttemptOwnershipLost()
    except asyncio.CancelledError:
        await dependencies.release_template_v2_pptx_import(
            import_id,
            task_id,
            attempt_token,
        )
        raise
    except dependencies.AttemptOwnershipLost:
        dependencies.logger.warning(
            "Template V2 PPTX import attempt lost ownership for %s",
            import_id,
        )
    except Exception as error:
        recorded = await dependencies.fail_template_v2_pptx_import(
            import_id,
            task_id,
            attempt_token,
            error,
        )
        if recorded:
            dependencies.logger.exception(
                "Template V2 PPTX import failed: %s",
                dependencies._failure_code(error),
            )
    finally:
        stop_heartbeat.set()
        await heartbeat_task


def notify_template_v2_pptx_dispatcher(
    dependencies: IngestionRuntimeDependencies,
) -> None:
    if dependencies._dispatcher_wake is not None:
        dependencies._dispatcher_wake.set()


def track_import_task(
    task: asyncio.Task,
    dependencies: IngestionRuntimeDependencies,
) -> None:
    dependencies._inflight_tasks.add(task)
    task.add_done_callback(dependencies._inflight_tasks.discard)


async def dispatch_template_v2_pptx_imports_once(
    dependencies: IngestionRuntimeDependencies,
) -> int:
    async with dependencies.async_session_maker() as session:
        recovered = await dependencies.recover_stalled_template_v2_pptx_imports(
            session
        )
        queued = (
            await session.execute(
                select(
                    TemplateV2PptxImport.id,
                    TemplateV2PptxImport.task_id,
                )
                .where(TemplateV2PptxImport.state == "queued")
                .order_by(TemplateV2PptxImport.created_at)
                .limit(dependencies.IMPORT_DISPATCH_BATCH_SIZE)
            )
        ).all()
    for import_id, task_id in queued:
        dependencies._track_import_task(
            asyncio.create_task(
                dependencies.run_template_v2_pptx_import(import_id, task_id)
            )
        )
    dependencies.log_pptx_queue_observation(
        operation="recover",
        outcome="completed",
        count=recovered,
    )
    dependencies.log_pptx_queue_observation(
        operation="dispatch",
        outcome="completed",
        count=len(queued),
    )
    if recovered:
        dependencies.logger.warning(
            "Recovered %s stalled Template V2 PPTX imports",
            recovered,
        )
    return len(queued)


async def dispatcher_loop(dependencies: IngestionRuntimeDependencies) -> None:
    assert dependencies._dispatcher_stop is not None
    assert dependencies._dispatcher_wake is not None
    while not dependencies._dispatcher_stop.is_set():
        dependencies._dispatcher_wake.clear()
        try:
            await dependencies.maybe_cleanup_expired_private_sources()
            await dependencies.dispatch_template_v2_pptx_imports_once()
        except Exception:
            dependencies.logger.exception(
                "Template V2 PPTX durable dispatcher iteration failed"
            )
        try:
            await asyncio.wait_for(
                dependencies._dispatcher_wake.wait(),
                timeout=dependencies.IMPORT_DISPATCH_INTERVAL_SECONDS,
            )
        except TimeoutError:
            pass


async def start_template_v2_pptx_dispatcher(
    dependencies: IngestionRuntimeDependencies,
) -> None:
    policy = dependencies.get_structured_template_policy()
    if not policy.creation_enabled or not policy.allowed_template_ids:
        dependencies.logger.info(
            "Template V2 PPTX dispatcher remains disabled by rollout policy"
        )
        return
    if (
        dependencies._dispatcher_task is not None
        and not dependencies._dispatcher_task.done()
    ):
        return
    dependencies._dispatcher_stop = asyncio.Event()
    dependencies._dispatcher_wake = asyncio.Event()
    await dependencies.maybe_cleanup_expired_private_sources()
    await dependencies.dispatch_template_v2_pptx_imports_once()
    dependencies._dispatcher_task = asyncio.create_task(
        dependencies._dispatcher_loop()
    )


async def stop_template_v2_pptx_dispatcher(
    dependencies: IngestionRuntimeDependencies,
) -> None:
    if dependencies._dispatcher_stop is not None:
        dependencies._dispatcher_stop.set()
    if dependencies._dispatcher_wake is not None:
        dependencies._dispatcher_wake.set()
    if dependencies._dispatcher_task is not None:
        await dependencies._dispatcher_task
    active = list(dependencies._inflight_tasks)
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    dependencies._inflight_tasks.clear()
    dependencies._dispatcher_task = None
    dependencies._dispatcher_stop = None
    dependencies._dispatcher_wake = None
