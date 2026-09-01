from __future__ import annotations

import shutil as _shutil
import time
from datetime import timedelta
from typing import Any, Mapping
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.public_admission import PublicOperationAdmission
from models.sql.workspace import WorkspaceOperation

from services import authored_checkpoint_artifacts as _artifacts
from services import authored_checkpoint_state as _state
from services.authored_checkpoint_artifacts import (
    _artifact_sweep_batch_size,
    _artifact_sweep_max_bytes,
    _artifact_sweep_max_files,
    _artifact_ttl_seconds,
    _configured_positive_int,
    _failed_artifact_ttl_seconds,
    _published_artifact_ttl_seconds,
    atomic_write,
    checkpoint_root,
    cleanup_checkpoint_artifacts,
    mark_checkpoint_artifacts_sealed,
    read_valid_render,
    read_valid_evidence,
    recover_unreferenced_evidence,
    write_valid_evidence,
    evidence_attempt_id,
    read_detached_deck_ir_checkpoint,
    render_artifact_path,
    write_detached_deck_ir_checkpoint,
)
from services.authored_checkpoint_errors import (
    _PUBLISH_FENCE_SECONDS,
    AuthoredStageError,
    begin_publish as _begin_publish,
    complete_publish as _complete_publish,
    is_retryable_exception,
    sanitize_exception_message,
)
from services.authored_checkpoint_state import (
    STAGES,
    should_inject_failure,
)
from services.authored_slide_checkpoint_bundle import (
    AtomicSlideCheckpointStore,
    SlideCheckpointBundle,
    SlideCheckpointConflict,
    SlideCheckpointCorrupt,
)
from services.authored_generation_observability import log_authored_generation_observation
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


_DEFAULT_SWEEP_INTERVAL_SECONDS = 60 * 60
_last_artifact_sweep_monotonic: float | None = None
shutil = _shutil


def _sync_session_maker() -> None:
    _artifacts.async_session_maker = async_session_maker
    _state.async_session_maker = async_session_maker


async def _lock_public_admission_if_available(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> PublicOperationAdmission | None:
    """Keep legacy/rolling schemas readable while public-v2 fails closed later."""

    try:
        async with session.begin_nested():
            return await session.scalar(
                select(PublicOperationAdmission)
                .where(PublicOperationAdmission.operation_id == operation_id)
                .with_for_update()
            )
    except OperationalError:
        return None


async def _require_workspace_operation_fence(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None,
) -> bool:
    """CAS-fence a linked authored write against its workspace operation.

    Legacy generation jobs are intentionally unlinked and retain the historical
    generation-job attempt fence.  Once a job is linked, its own attempt token
    is only an identifier: the running workspace operation is authoritative.
    The conditional no-op update acquires the operation row write lock and the
    database rechecks state, attempt and fence before a caller may write.
    """

    job = await session.get(PresentationGenerationJob, job_id)
    if job is None or job.workspace_operation_id is None:
        return False
    if isinstance(fencing_token, bool) or not isinstance(fencing_token, int):
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_workspace_operation_fencing_token_required",
            retryable=True,
        )
    admission = await _lock_public_admission_if_available(
        session,
        job.workspace_operation_id,
    )
    now = get_current_utc_datetime()
    result = await session.execute(
        update(WorkspaceOperation)
        .where(
            WorkspaceOperation.id == job.workspace_operation_id,
            WorkspaceOperation.state == "running",
            WorkspaceOperation.attempt_token == attempt_token,
            WorkspaceOperation.fencing_token == fencing_token,
        )
        .values(heartbeat_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_workspace_operation_fence_lost",
            retryable=True,
        )
    operation_contract = await session.scalar(
        select(WorkspaceOperation.contract_version).where(
            WorkspaceOperation.id == job.workspace_operation_id
        )
    )
    if operation_contract == "public-operation/v2" and (
        admission is None
        or admission.operation_kind != "generation"
        or admission.state != "active"
        or admission.fence != fencing_token
    ):
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_workspace_operation_fence_lost",
            retryable=True,
        )
    return True


async def _checkpoint_mutation(
    operation,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None,
    **kwargs,
):
    """Commit a state write with its WorkspaceOperation CAS lock.

    A render's provisional artifact may be written first in a separate
    transaction.  The final checkpoint pointer is still fenced here; failed
    stale pointers leave only the pre-existing orphan-cleanup input.
    """

    _sync_session_maker()
    async with async_session_maker() as session:
        result = await operation(
            job_id=job_id,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            session=session,
            **kwargs,
        )
        await session.commit()
        return result


async def clear_checkpoint_html(job_id: uuid.UUID) -> None:
    _sync_session_maker()
    return await _artifacts.clear_checkpoint_html(job_id)


async def sweep_expired_checkpoint_artifacts(
    *,
    ttl_seconds: int | None = None,
    failed_ttl_seconds: int | None = None,
    published_ttl_seconds: int | None = None,
    batch_size: int | None = None,
    max_bytes: int | None = None,
    max_files: int | None = None,
) -> dict[str, int]:
    _sync_session_maker()
    return await _artifacts.sweep_expired_checkpoint_artifacts(
        ttl_seconds=ttl_seconds,
        failed_ttl_seconds=failed_ttl_seconds,
        published_ttl_seconds=published_ttl_seconds,
        batch_size=batch_size,
        max_bytes=max_bytes,
        max_files=max_files,
    )


async def maybe_sweep_expired_checkpoint_artifacts() -> dict[str, int] | None:
    """Run the bounded sweeper at most once per configured process interval."""

    global _last_artifact_sweep_monotonic
    interval = _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_INTERVAL_SECONDS",
        _DEFAULT_SWEEP_INTERVAL_SECONDS,
    )
    now = time.monotonic()
    if (
        _last_artifact_sweep_monotonic is not None
        and now - _last_artifact_sweep_monotonic < interval
    ):
        return None
    _last_artifact_sweep_monotonic = now
    result = await sweep_expired_checkpoint_artifacts()
    log_authored_generation_observation(
        name="artifact_sweep",
        outcome="success",
        scanned_terminal=result.get("scanned_terminal", 0),
        deleted_terminal=result.get("deleted_terminal", 0),
        scanned_orphan=result.get("scanned_orphan", 0),
        deleted_orphan=result.get("deleted_orphan", 0),
        deleted_bytes=result.get("deleted_bytes", 0),
        deleted_files=result.get("deleted_files", 0),
        skipped_budget=result.get("skipped_budget", 0),
        errors=result.get("errors", 0),
    )
    return result


async def initialize_checkpoints(
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    roles: list[str],
    fencing_token: int | None = None,
) -> None:
    return await _checkpoint_mutation(
        _state.initialize_checkpoints,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        roles=roles,
    )


async def get_checkpoint(
    job_id: uuid.UUID,
    slide_index: int,
    *,
    attempt_token: str | None = None,
    fencing_token: int | None = None,
) -> AuthoredSlideCheckpoint:
    _sync_session_maker()
    return await _state.get_checkpoint(
        job_id,
        slide_index,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )


async def start_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    fencing_token: int | None = None,
) -> AuthoredSlideCheckpoint:
    return await _checkpoint_mutation(
        _state.start_stage,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        slide_index=slide_index,
        stage=stage,
    )


async def reserve_provider_attempt(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    ordinal: int,
    fencing_token: int | None = None,
) -> AuthoredSlideCheckpoint:
    return await _checkpoint_mutation(
        _state.reserve_provider_attempt,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        slide_index=slide_index,
        ordinal=ordinal,
    )


async def complete_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    html: str | None = None,
    png: bytes | None = None,
    browser_metrics: Mapping[str, object] | None = None,
    checkpoint_revision: int | None = None,
    skipped: bool = False,
    fencing_token: int | None = None,
) -> AuthoredSlideCheckpoint:
    return await _checkpoint_mutation(
        _state.complete_stage,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        slide_index=slide_index,
        stage=stage,
        html=html,
        png=png,
        browser_metrics=browser_metrics,
        checkpoint_revision=checkpoint_revision,
        skipped=skipped,
    )


async def fail_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    error: AuthoredStageError,
    request_id: str | None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    return await _checkpoint_mutation(
        _state.fail_stage,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        slide_index=slide_index,
        stage=stage,
        error=error,
        request_id=request_id,
    )


async def begin_publish(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None = None,
) -> None:
    linked = await _require_workspace_operation_fence(
        session,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )
    if not linked:
        await _begin_publish(session, job_id=job_id, attempt_token=attempt_token)
        return
    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.state.in_(("running", "staging")),
        )
        .values(
            state="staging",
            current_stage="publish",
            lease_expires_at=now + timedelta(seconds=_PUBLISH_FENCE_SECONDS),
            heartbeat_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )


async def complete_publish(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int | None = None,
) -> None:
    linked = await _require_workspace_operation_fence(
        session,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
    )
    if not linked:
        await _complete_publish(session, job_id=job_id, attempt_token=attempt_token)
        return
    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.state == "staging",
        )
        .values(
            state="published",
            attempt_token=None,
            lease_expires_at=None,
            heartbeat_at=now,
            current_stage="completed",
            last_error=None,
            resume_allowed=False,
            published_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )


async def progress_for_task(
    task_id: str,
    session: AsyncSession | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    _sync_session_maker()
    return await _state.progress_for_task(task_id, session)


__all__ = [
    "_artifact_sweep_batch_size",
    "_artifact_sweep_max_bytes",
    "_artifact_sweep_max_files",
    "_artifact_ttl_seconds",
    "_failed_artifact_ttl_seconds",
    "_published_artifact_ttl_seconds",
    "AuthoredStageError",
    "AtomicSlideCheckpointStore",
    "SlideCheckpointBundle",
    "SlideCheckpointConflict",
    "SlideCheckpointCorrupt",
    "STAGES",
    "atomic_write",
    "begin_publish",
    "checkpoint_root",
    "cleanup_checkpoint_artifacts",
    "mark_checkpoint_artifacts_sealed",
    "clear_checkpoint_html",
    "complete_publish",
    "complete_stage",
    "fail_stage",
    "get_checkpoint",
    "get_current_utc_datetime",
    "initialize_checkpoints",
    "is_retryable_exception",
    "maybe_sweep_expired_checkpoint_artifacts",
    "progress_for_task",
    "reserve_provider_attempt",
    "read_valid_render",
    "read_valid_evidence",
    "recover_unreferenced_evidence",
    "write_valid_evidence",
    "write_detached_deck_ir_checkpoint",
    "evidence_attempt_id",
    "read_detached_deck_ir_checkpoint",
    "render_artifact_path",
    "sanitize_exception_message",
    "should_inject_failure",
    "start_stage",
    "sweep_expired_checkpoint_artifacts",
]
