from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from datetime import timedelta
from typing import Any
import uuid

from sqlalchemy import and_, case, func, or_, select, update

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_generation_observability import (
    log_authored_generation_observation,
    log_authored_slide_failure_observation,
)
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime
from utils.get_env import get_app_data_directory_env


STAGES = ("author", "illustrate", "render", "persist")
_STATUS_FIELD = {stage: f"{stage}_status" for stage in STAGES}
_SECRET_PATTERNS = (
    (re.compile(r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+"), r"\1 [redacted]"),
    (re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}"), "[redacted]"),
    (
        re.compile(
            r"(?i)\b([a-z0-9_-]*(?:api[_-]?key|token|secret|password)[a-z0-9_-]*)\s*[:=]\s*[^\s,;]+"
        ),
        r"\1=[redacted]",
    ),
    (re.compile(r"(?i)(https?://[^\s?]+)\?[^\s]+"), r"\1?[redacted]"),
    (
        re.compile(
            r"(?i)(?:[a-z]:\\|/)(?:[^\s/:*?\"<>|]+[/\\])+[^\s/:*?\"<>|]+"
        ),
        "[redacted]",
    ),
)
_PUBLISH_FENCE_SECONDS = 300
_DEFAULT_ARTIFACT_TTL_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_SWEEP_BATCH_SIZE = 50
_DEFAULT_SWEEP_INTERVAL_SECONDS = 60 * 60
_MAX_SWEEP_BATCH_SIZE = 500
_MAX_SWEEP_SCAN_SIZE = 5000
_DEFAULT_SWEEP_MAX_BYTES = 1024 * 1024 * 1024
_DEFAULT_SWEEP_MAX_FILES = 10_000
_MAX_SWEEP_MAX_FILES = 100_000
_last_artifact_sweep_monotonic: float | None = None
logger = logging.getLogger(__name__)


class AuthoredStageError(RuntimeError):
    def __init__(
        self,
        *,
        slide_index: int,
        stage: str,
        code: str,
        cause: BaseException | None = None,
        retryable: bool = True,
        message: str | None = None,
    ) -> None:
        self.slide_index = slide_index
        self.stage = stage
        self.code = code
        self.cause = cause
        self.retryable = retryable
        self.safe_message = message or _default_message(code, stage)
        super().__init__(self.safe_message)

    def as_safe_dict(
        self, *, request_id: str | None = None, attempt_number: int | None = None
    ) -> dict[str, Any]:
        cause = self.cause or self
        exception_type = type(cause).__name__
        safe_cause = sanitize_exception_message(cause)
        fingerprint = hashlib.sha256(
            f"{exception_type}:{safe_cause}".encode("utf-8")
        ).hexdigest()[:16]
        safe_message = sanitize_exception_message(RuntimeError(self.safe_message))
        value: dict[str, Any] = {
            "status_code": 500,
            "code": self.code,
            "detail": safe_message,
            "message": safe_message,
            "stage": self.stage,
            "slide_index": self.slide_index,
            "slide_number": self.slide_index + 1,
            "exception_type": exception_type,
            "cause": safe_cause,
            "fingerprint": fingerprint,
            "retryable": self.retryable,
        }
        if request_id:
            value["request_id"] = request_id
        if attempt_number is not None:
            value["attempt_number"] = attempt_number
        return value


def _default_message(code: str, stage: str) -> str:
    messages = {
        "authored_slide_author_failed": "Slide authoring failed after retries",
        "authored_slide_illustrate_failed": "Slide illustration failed after retries",
        "authored_slide_render_failed": "Slide rendering failed after retries",
        "authored_slide_persist_failed": "Slide checkpoint persistence failed",
        "authored_injected_failure": "Development failure injection triggered",
        "authored_attempt_lost": "Generation attempt lease was lost",
    }
    return messages.get(code, f"Authored slide {stage} failed")


def sanitize_exception_message(error: BaseException) -> str:
    """Return a short diagnostic while excluding payloads, credentials and paths."""

    value = " ".join(str(error).split())[:1024]
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    # Provider bodies and authored HTML are not useful or safe in task metadata.
    if "<!doctype" in value.lower() or "<html" in value.lower():
        value = "[authored content redacted]"
    return value[:512] or type(error).__name__


async def begin_publish(
    session,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
) -> None:
    """Lock final publication to the live attempt inside the caller transaction.

    The conditional UPDATE is deliberately left uncommitted. On databases with
    row locking it prevents a lease takeover or cancellation from crossing the
    filesystem/Presentation-row publication boundary until that transaction
    commits or rolls back.
    """

    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.attempt_token == attempt_token,
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
    session,
    *,
    job_id: uuid.UUID,
    attempt_token: str,
) -> None:
    """Make the fenced job terminal in the same transaction as deck rows."""

    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.attempt_token == attempt_token,
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


def is_retryable_exception(error: BaseException) -> bool:
    """Classify provider/client failures without persisting their response body."""

    status_code = getattr(error, "status_code", None)
    if status_code is None:
        response = getattr(error, "response", None)
        status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        if status_code == 429 or status_code >= 500:
            return True
        if 400 <= status_code < 500:
            return False
    return True


def checkpoint_root() -> Path:
    configured = os.getenv("AUTHORED_CHECKPOINT_DIRECTORY", "").strip()
    if configured:
        root = Path(configured)
    else:
        # System temp is wiped by container/OS cleaning; resumable artifacts
        # must survive under app data (same fallback chain as db_utils).
        root = Path(
            get_app_data_directory_env()
            or os.path.join(tempfile.gettempdir(), "presenton")
        ) / "authored-checkpoints"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _job_directory(job_id: uuid.UUID) -> Path:
    root = checkpoint_root()
    directory = (root / str(job_id)).resolve()
    if root not in directory.parents:
        raise ValueError("invalid_checkpoint_path")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def render_artifact_path(
    job_id: uuid.UUID, slide_index: int, attempt_token: str | None = None
) -> Path:
    # A revoked worker must never overwrite an artifact selected by a newer
    # attempt. The checkpoint row is the publication pointer; attempt-scoped
    # filenames make any late write from an old worker an unreferenced orphan.
    attempt_suffix = (
        hashlib.sha256(attempt_token.encode("utf-8")).hexdigest()[:12]
        if attempt_token
        else "legacy"
    )
    return _job_directory(job_id) / f"slide-{slide_index}-{attempt_suffix}.png"


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def cleanup_checkpoint_artifacts(job_id: uuid.UUID) -> None:
    """Remove private render files after the final deck is safely published."""

    directory = _safe_job_directory(job_id)
    if directory.is_dir():
        shutil.rmtree(directory)


async def clear_checkpoint_html(job_id: uuid.UUID) -> None:
    """Null multi-MB slide HTML once the deck is published.

    Statuses and timestamps stay for history; resume never reads HTML after
    publication (reconciliation uses the published row and the export file).
    """

    async with async_session_maker() as session:
        await session.execute(
            update(AuthoredSlideCheckpoint)
            .where(AuthoredSlideCheckpoint.job_id == job_id)
            .values(author_html=None, illustrated_html=None)
        )
        await session.commit()


def _safe_job_directory(job_id: uuid.UUID) -> Path:
    """Resolve exactly one non-symlink UUID child below the checkpoint root."""

    root = checkpoint_root()
    unresolved = root / str(job_id)
    if unresolved.is_symlink():
        raise ValueError("invalid_checkpoint_path")
    directory = unresolved.resolve()
    if directory.parent != root:
        raise ValueError("invalid_checkpoint_path")
    return directory


def _configured_positive_int(
    name: str,
    default: int,
    *,
    maximum: int | None = None,
) -> int:
    try:
        value = int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        value = default
    return min(value, maximum) if maximum is not None else value


def _artifact_ttl_seconds() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_ARTIFACT_TTL_SECONDS",
        _DEFAULT_ARTIFACT_TTL_SECONDS,
    )


def _failed_artifact_ttl_seconds(fallback: int) -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_FAILED_ARTIFACT_TTL_SECONDS",
        fallback,
    )


def _published_artifact_ttl_seconds(fallback: int) -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_PUBLISHED_ARTIFACT_TTL_SECONDS",
        fallback,
    )


def _artifact_sweep_batch_size() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_BATCH_SIZE",
        _DEFAULT_SWEEP_BATCH_SIZE,
        maximum=_MAX_SWEEP_BATCH_SIZE,
    )


def _artifact_sweep_max_bytes() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_MAX_BYTES",
        _DEFAULT_SWEEP_MAX_BYTES,
    )


def _artifact_sweep_max_files() -> int:
    return _configured_positive_int(
        "AUTHORED_CHECKPOINT_SWEEP_MAX_FILES",
        _DEFAULT_SWEEP_MAX_FILES,
        maximum=_MAX_SWEEP_MAX_FILES,
    )


def _directory_usage_within_budget(
    directory: Path,
    *,
    max_bytes: int,
    max_files: int,
) -> tuple[int, int] | None:
    """Measure a private directory without following links, stopping at a budget."""

    total_bytes = 0
    total_files = 0
    pending = [directory]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                total_files += 1
                if total_files > max_files:
                    return None
                stat_result = entry.stat(follow_symlinks=False)
                total_bytes += stat_result.st_size
                if total_bytes > max_bytes:
                    return None
                if entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                    pending.append(Path(entry.path))
    return total_bytes, total_files


async def sweep_expired_checkpoint_artifacts(
    *,
    ttl_seconds: int | None = None,
    failed_ttl_seconds: int | None = None,
    published_ttl_seconds: int | None = None,
    batch_size: int | None = None,
    max_bytes: int | None = None,
    max_files: int | None = None,
) -> dict[str, int]:
    """Delete old terminal and orphan checkpoint directories in a bounded batch.

    Terminal rows are fenced with a conditional write lock before deletion, so
    a queued/running/staging attempt is never touched. Orphan cleanup accepts
    only canonical UUID directory names and never follows symlinks.
    """

    ttl = ttl_seconds if ttl_seconds is not None else _artifact_ttl_seconds()
    failed_ttl = (
        failed_ttl_seconds
        if failed_ttl_seconds is not None
        else _failed_artifact_ttl_seconds(ttl)
    )
    published_ttl = (
        published_ttl_seconds
        if published_ttl_seconds is not None
        else _published_artifact_ttl_seconds(ttl)
    )
    batch = batch_size if batch_size is not None else _artifact_sweep_batch_size()
    byte_budget = max_bytes if max_bytes is not None else _artifact_sweep_max_bytes()
    file_budget = max_files if max_files is not None else _artifact_sweep_max_files()
    if ttl < 0 or failed_ttl < 0 or published_ttl < 0:
        raise ValueError("artifact_ttl_must_be_nonnegative")
    if batch <= 0:
        raise ValueError("artifact_sweep_batch_must_be_positive")
    if byte_budget <= 0 or file_budget <= 0:
        raise ValueError("artifact_sweep_budget_must_be_positive")
    batch = min(batch, _MAX_SWEEP_BATCH_SIZE)
    now = get_current_utc_datetime()
    failed_cutoff = now - timedelta(seconds=failed_ttl)
    published_cutoff = now - timedelta(seconds=published_ttl)
    orphan_cutoff = now - timedelta(seconds=ttl)
    result = {
        "scanned_terminal": 0,
        "deleted_terminal": 0,
        "scanned_orphan": 0,
        "deleted_orphan": 0,
        "deleted_bytes": 0,
        "deleted_files": 0,
        "skipped_budget": 0,
        "errors": 0,
    }

    # Query only terminal rows old enough to expire.  The conditional UPDATE
    # below rechecks state/token while obtaining a write lock for each removal.
    failed_terminal_at = func.coalesce(
        PresentationGenerationJob.failed_at,
        PresentationGenerationJob.updated_at,
        PresentationGenerationJob.created_at,
    )
    published_terminal_at = func.coalesce(
        PresentationGenerationJob.published_at,
        PresentationGenerationJob.updated_at,
        PresentationGenerationJob.created_at,
    )
    terminal_at = case(
        (PresentationGenerationJob.state == "failed", failed_terminal_at),
        else_=published_terminal_at,
    )
    terminal_expired = or_(
        and_(
            PresentationGenerationJob.state == "failed",
            failed_terminal_at < failed_cutoff,
        ),
        and_(
            PresentationGenerationJob.state == "published",
            published_terminal_at < published_cutoff,
        ),
    )
    async with async_session_maker() as session:
        terminal_ids = list(
            await session.scalars(
                select(PresentationGenerationJob.id)
                .where(terminal_expired)
                .where(PresentationGenerationJob.attempt_token.is_(None))
                .order_by(terminal_at, PresentationGenerationJob.id)
                .limit(batch)
            )
        )

    for job_id in terminal_ids:
        result["scanned_terminal"] += 1
        async with async_session_maker() as session:
            try:
                locked = await session.execute(
                    update(PresentationGenerationJob)
                    .where(
                        PresentationGenerationJob.id == job_id,
                        terminal_expired,
                        PresentationGenerationJob.attempt_token.is_(None),
                    )
                    # Deliberately retain the timestamp while acquiring the row
                    # write lock. This prevents extending TTL on every sweep.
                    .values(updated_at=PresentationGenerationJob.updated_at)
                )
                if locked.rowcount != 1:
                    await session.rollback()
                    continue
                directory = _safe_job_directory(job_id)
                if directory.is_dir():
                    usage = _directory_usage_within_budget(
                        directory,
                        max_bytes=byte_budget - result["deleted_bytes"],
                        max_files=file_budget - result["deleted_files"],
                    )
                    if usage is None:
                        result["skipped_budget"] += 1
                        await session.rollback()
                        continue
                    shutil.rmtree(directory)
                    result["deleted_terminal"] += 1
                    result["deleted_bytes"] += usage[0]
                    result["deleted_files"] += usage[1]
                await session.commit()
            except (OSError, ValueError) as exc:
                result["errors"] += 1
                await session.rollback()
                logger.warning(
                    "Authored checkpoint artifact cleanup failed for terminal job %s (%s)",
                    job_id,
                    type(exc).__name__,
                )

    remaining = batch - result["deleted_terminal"]
    if remaining <= 0:
        return result

    root = checkpoint_root()
    scan_limit = min(
        _MAX_SWEEP_SCAN_SIZE,
        max(batch * 10, _DEFAULT_SWEEP_BATCH_SIZE),
    )
    orphan_candidates: list[tuple[uuid.UUID, Path]] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if result["scanned_orphan"] >= scan_limit:
                break
            result["scanned_orphan"] += 1
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
                job_id = uuid.UUID(entry.name)
                if entry.name != str(job_id):
                    continue
                if (
                    entry.stat(follow_symlinks=False).st_mtime
                    >= orphan_cutoff.timestamp()
                ):
                    continue
                orphan_candidates.append((job_id, Path(entry.path)))
            except (OSError, ValueError):
                continue

    if not orphan_candidates:
        return result
    candidate_ids = [job_id for job_id, _ in orphan_candidates]
    async with async_session_maker() as session:
        existing_ids = set(
            await session.scalars(
                select(PresentationGenerationJob.id).where(
                    PresentationGenerationJob.id.in_(candidate_ids)
                )
            )
        )
    for job_id, directory in orphan_candidates:
        if result["deleted_orphan"] >= remaining:
            break
        if job_id in existing_ids:
            continue
        try:
            # Re-resolve immediately before removal and require an exact child;
            # this rejects a directory swapped for a symlink after enumeration.
            if directory.is_symlink():
                continue
            resolved = directory.resolve()
            if resolved.parent != root or not resolved.is_dir():
                continue
            usage = _directory_usage_within_budget(
                resolved,
                max_bytes=byte_budget - result["deleted_bytes"],
                max_files=file_budget - result["deleted_files"],
            )
            if usage is None:
                result["skipped_budget"] += 1
                continue
            shutil.rmtree(resolved)
            result["deleted_orphan"] += 1
            result["deleted_bytes"] += usage[0]
            result["deleted_files"] += usage[1]
        except (OSError, ValueError) as exc:
            result["errors"] += 1
            logger.warning(
                "Authored checkpoint artifact cleanup failed for orphan job %s (%s)",
                job_id,
                type(exc).__name__,
            )
    return result


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
    # Set before awaiting so overlapping dispatcher ticks do not duplicate work.
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


def read_valid_render(checkpoint: AuthoredSlideCheckpoint) -> bytes | None:
    if not checkpoint.render_path or not checkpoint.render_sha256:
        return None
    root = checkpoint_root()
    path = (root / checkpoint.render_path).resolve()
    if root not in path.parents or not path.is_file():
        return None
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != checkpoint.render_sha256:
        return None
    return content


async def initialize_checkpoints(
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    roles: list[str],
) -> None:
    now = get_current_utc_datetime()
    async with async_session_maker() as session:
        job = await _owned_job(session, job_id, attempt_token)
        existing = set(
            await session.scalars(
                select(AuthoredSlideCheckpoint.slide_index).where(
                    AuthoredSlideCheckpoint.job_id == job_id
                )
            )
        )
        for index, role in enumerate(roles):
            if index not in existing:
                session.add(
                    AuthoredSlideCheckpoint(
                        job_id=job_id,
                        slide_index=index,
                        role=role,
                    )
                )
        job.total_slides = len(roles)
        job.completed_slides = await _completed_count(session, job_id)
        job.current_stage = "author"
        job.updated_at = now
        session.add(job)
        await session.commit()


async def get_checkpoint(
    job_id: uuid.UUID, slide_index: int
) -> AuthoredSlideCheckpoint:
    async with async_session_maker() as session:
        checkpoint = (
            await session.execute(
                select(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == job_id,
                    AuthoredSlideCheckpoint.slide_index == slide_index,
                )
            )
        ).scalar_one()
        return checkpoint


async def start_stage(
    *, job_id: uuid.UUID, slide_index: int, stage: str, attempt_token: str
) -> AuthoredSlideCheckpoint:
    _require_stage(stage)
    async with async_session_maker() as session:
        job = await _owned_job(session, job_id, attempt_token)
        checkpoint = await _checkpoint(session, job_id, slide_index)
        stage_index = STAGES.index(stage)
        for downstream in STAGES[stage_index + 1 :]:
            setattr(checkpoint, _STATUS_FIELD[downstream], "pending")
        setattr(checkpoint, _STATUS_FIELD[stage], "running")
        setattr(checkpoint, f"{stage}_started_at", get_current_utc_datetime())
        checkpoint.failed_stage = None
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        job.current_stage = stage
        job.completed_slides = await _completed_count(session, job_id)
        job.updated_at = checkpoint.updated_at
        session.add_all((checkpoint, job))
        await session.commit()
        return checkpoint


async def complete_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    html: str | None = None,
    png: bytes | None = None,
    skipped: bool = False,
) -> AuthoredSlideCheckpoint:
    _require_stage(stage)
    render_path: str | None = None
    render_sha256: str | None = None
    if png is not None:
        path = render_artifact_path(job_id, slide_index, attempt_token)
        atomic_write(path, png)
        render_path = str(path.relative_to(checkpoint_root())).replace(os.sep, "/")
        render_sha256 = hashlib.sha256(png).hexdigest()
    async with async_session_maker() as session:
        job = await _owned_job(session, job_id, attempt_token)
        checkpoint = await _checkpoint(session, job_id, slide_index)
        setattr(checkpoint, _STATUS_FIELD[stage], "skipped" if skipped else "completed")
        setattr(checkpoint, f"{stage}_completed_at", get_current_utc_datetime())
        if stage == "author" and html is not None:
            checkpoint.author_html = html
        if stage == "illustrate" and html is not None:
            checkpoint.illustrated_html = html
        if render_path is not None:
            checkpoint.render_path = render_path
            checkpoint.render_sha256 = render_sha256
        checkpoint.failed_stage = None
        checkpoint.error = None
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        if stage == "persist":
            await session.flush()
            job.completed_slides = await _completed_count(session, job_id)
        job.current_stage = stage
        job.updated_at = checkpoint.updated_at
        session.add_all((checkpoint, job))
        await session.commit()
        return checkpoint


async def fail_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    error: AuthoredStageError,
    request_id: str | None,
) -> dict[str, Any]:
    _require_stage(stage)
    async with async_session_maker() as session:
        job = await _owned_job(session, job_id, attempt_token)
        safe = error.as_safe_dict(
            request_id=request_id, attempt_number=job.attempt_number
        )
        checkpoint = await _checkpoint(session, job_id, slide_index)
        setattr(checkpoint, _STATUS_FIELD[stage], "failed")
        checkpoint.failed_stage = stage
        checkpoint.error = safe
        checkpoint.failed_at = get_current_utc_datetime()
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        job.current_stage = stage
        job.resume_allowed = error.retryable
        job.last_error = safe
        job.completed_slides = await _completed_count(session, job_id)
        job.updated_at = checkpoint.updated_at
        session.add_all((checkpoint, job))
        await session.commit()
    log_authored_slide_failure_observation(
        stage=stage,
        code=error.code,
        slide_number=slide_index + 1,
        retryable=error.retryable,
    )
    return safe


async def progress_for_task(
    task_id: str,
    session: AsyncSession | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return authored progress, using the caller's read transaction when given."""

    if session is None:
        async with async_session_maker() as owned_session:
            return await progress_for_task(task_id, owned_session)

    job = (
        await session.execute(
            select(PresentationGenerationJob).where(
                PresentationGenerationJob.task_id == task_id
            )
        )
    ).scalar_one_or_none()
    if (
        job is None
        or job.request_payload.get("template") != "authored"
        or not job.total_slides
    ):
        return None, None
    total = max(0, job.total_slides or 0)
    completed = min(total, max(0, job.completed_slides or 0))
    percent = 0 if total == 0 else int(completed * 100 / total)
    finalizing = completed == total and job.state != "published"
    if job.state != "published":
        percent = min(percent, 99)
    counts: dict[str, int] = {}
    for stage in STAGES:
        field = getattr(AuthoredSlideCheckpoint, _STATUS_FIELD[stage])
        counts[stage] = int(
            await session.scalar(
                select(func.count()).select_from(AuthoredSlideCheckpoint).where(
                    AuthoredSlideCheckpoint.job_id == job.id,
                    field.in_(("completed", "skipped")),
                )
            )
            or 0
        )
    current = (
        await session.execute(
            select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job.id,
                AuthoredSlideCheckpoint.failed_stage.is_not(None),
            ).order_by(AuthoredSlideCheckpoint.slide_index)
        )
    ).scalars().first()
    progress = {
        "completed_slides": completed,
        "total_slides": total,
        "percent": 100 if job.state == "published" else percent,
        "current_slide": current.slide_index + 1 if current else None,
        "current_stage": job.current_stage,
        "stage_counts": counts,
        "finalizing": finalizing,
    }
    return progress, job.last_error


def should_inject_failure(
    checkpoint: AuthoredSlideCheckpoint, *, slide_index: int, stage: str
) -> bool:
    if os.getenv("AUTHORED_TEST_FAILURES_ENABLED", "").lower() not in {"1", "true"}:
        return False
    if os.getenv("ENVIRONMENT", "").lower() not in {
        "development",
        "test",
    } and not os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        target_slide = int(os.getenv("AUTHORED_TEST_FAILURE_SLIDE", "0"))
    except ValueError:
        return False
    target_stage = os.getenv("AUTHORED_TEST_FAILURE_STAGE", "").strip().lower()
    already_injected = bool(
        checkpoint.error and checkpoint.error.get("code") == "authored_injected_failure"
    )
    return target_slide == slide_index + 1 and target_stage == stage and not already_injected


async def _owned_job(session, job_id: uuid.UUID, attempt_token: str) -> PresentationGenerationJob:
    # Fence every checkpoint transaction, not just final publication.  A plain
    # SELECT leaves a race where cancellation/lease takeover can happen before
    # the checkpoint UPDATE commits.  This conditional no-op UPDATE acquires the
    # job-row write lock and is rechecked by the database before any stage data
    # is made visible.
    now = get_current_utc_datetime()
    result = await session.execute(
        update(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.id == job_id,
            PresentationGenerationJob.attempt_token == attempt_token,
            PresentationGenerationJob.state.in_(("running", "staging")),
        )
        .values(heartbeat_at=now, updated_at=now)
    )
    if result.rowcount != 1:
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    job = await session.get(PresentationGenerationJob, job_id)
    if job is None:  # Defensive: the fenced row cannot normally disappear.
        await session.rollback()
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_attempt_lost",
            retryable=True,
        )
    return job


async def _checkpoint(session, job_id: uuid.UUID, slide_index: int) -> AuthoredSlideCheckpoint:
    return (
        await session.execute(
            select(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job_id,
                AuthoredSlideCheckpoint.slide_index == slide_index,
            )
        )
    ).scalar_one()


async def _completed_count(session, job_id: uuid.UUID) -> int:
    return int(
        await session.scalar(
            select(func.count()).select_from(AuthoredSlideCheckpoint).where(
                AuthoredSlideCheckpoint.job_id == job_id,
                AuthoredSlideCheckpoint.persist_status == "completed",
            )
        )
        or 0
    )


def _require_stage(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError("invalid_authored_stage")
