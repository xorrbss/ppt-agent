from __future__ import annotations

import shutil as _shutil
import time
from typing import Any, Mapping
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint

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
    render_artifact_path,
)
from services.authored_checkpoint_errors import (
    AuthoredStageError,
    begin_publish,
    complete_publish,
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
    *, job_id: uuid.UUID, attempt_token: str, roles: list[str]
) -> None:
    _sync_session_maker()
    return await _state.initialize_checkpoints(
        job_id=job_id, attempt_token=attempt_token, roles=roles
    )


async def get_checkpoint(
    job_id: uuid.UUID, slide_index: int
) -> AuthoredSlideCheckpoint:
    _sync_session_maker()
    return await _state.get_checkpoint(job_id, slide_index)


async def start_stage(
    *, job_id: uuid.UUID, slide_index: int, stage: str, attempt_token: str
) -> AuthoredSlideCheckpoint:
    _sync_session_maker()
    return await _state.start_stage(
        job_id=job_id,
        slide_index=slide_index,
        stage=stage,
        attempt_token=attempt_token,
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
) -> AuthoredSlideCheckpoint:
    _sync_session_maker()
    return await _state.complete_stage(
        job_id=job_id,
        slide_index=slide_index,
        stage=stage,
        attempt_token=attempt_token,
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
) -> dict[str, Any]:
    _sync_session_maker()
    return await _state.fail_stage(
        job_id=job_id,
        slide_index=slide_index,
        stage=stage,
        attempt_token=attempt_token,
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
    "read_valid_render",
    "read_valid_evidence",
    "recover_unreferenced_evidence",
    "write_valid_evidence",
    "evidence_attempt_id",
    "render_artifact_path",
    "sanitize_exception_message",
    "should_inject_failure",
    "start_stage",
    "sweep_expired_checkpoint_artifacts",
]
