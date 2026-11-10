from __future__ import annotations

import shutil as _shutil
from typing import Any, Mapping
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint

from services import authored_checkpoint_artifacts as _artifacts
from services import authored_checkpoint_fence as _fence
from services import authored_checkpoint_state as _state
from services import generation_checkpoint_rows as _rows
from services.authored_admission_receipt import AdmissionReceipt
from services.authored_checkpoint_artifacts import (
    _artifact_sweep_batch_size,
    _artifact_sweep_max_bytes,
    _artifact_sweep_max_files,
    _artifact_ttl_seconds,
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
    AuthoredStageError,
    is_retryable_exception,
    sanitize_exception_message,
)
from services.authored_checkpoint_publish_fence import (
    begin_publish,
    complete_publish,
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
from services.checkpoint_artifact_sweep import (
    maybe_sweep_expired_checkpoint_artifacts as maybe_sweep_expired_checkpoint_artifacts,
)
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime


shutil = _shutil


def _sync_session_maker() -> None:
    _artifacts.async_session_maker = async_session_maker
    _state.async_session_maker = async_session_maker
    _rows.async_session_maker = async_session_maker
    # The shared fencing primitives moved out of ``_state``; they open their own
    # sessions on the paths that do not receive one, so they follow the same
    # redirection (tests point this facade at a scratch database).
    _fence.async_session_maker = async_session_maker


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


async def begin_author_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    attempt_token: str,
    ordinal: int,
    fencing_token: int | None = None,
) -> AuthoredSlideCheckpoint:
    """``start_stage("author")`` and ``reserve_provider_attempt`` in one commit."""

    _sync_session_maker()
    return await _checkpoint_mutation(
        _state.begin_author_stage,
        job_id=job_id,
        attempt_token=attempt_token,
        fencing_token=fencing_token,
        slide_index=slide_index,
        ordinal=ordinal,
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
    admission_receipt: AdmissionReceipt | None = None,
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
        admission_receipt=admission_receipt,
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
    "begin_author_stage",
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
