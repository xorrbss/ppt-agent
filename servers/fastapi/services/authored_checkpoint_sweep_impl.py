# ruff: noqa: F821
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
import shutil
from datetime import timedelta
import uuid

from sqlalchemy import and_, case, func, or_, select, update

from models.sql.presentation_generation_job import PresentationGenerationJob
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime
from services.authored_checkpoint_sweep_budget import (
    _artifact_sweep_batch_size,
    _artifact_sweep_max_bytes,
    _artifact_sweep_max_files,
    _artifact_ttl_seconds,
    _directory_usage_within_budget,
    _failed_artifact_ttl_seconds,
    _published_artifact_ttl_seconds,
    _DEFAULT_SWEEP_BATCH_SIZE,
    _MAX_SWEEP_BATCH_SIZE,
    _MAX_SWEEP_SCAN_SIZE,
)


logger = logging.getLogger(__name__)


def _has_unsealed_evidence(directory: Path) -> bool:
    """Return true when an orphan directory contains evidence without a seal.

    A missing generation row is not proof that a worker stopped before its
    receipt was sealed.  Keep evidence sidecars until an explicit seal marker
    is present; this closes the crash window without changing terminal-row
    retention semantics.
    """

    sealed = any(
        (directory / marker).is_file()
        for marker in (
            ".sealed",
            ".receipt-sealed",
            "receipt.sealed.json",
        )
    )
    if sealed:
        return False
    try:
        return any(
            not entry.is_symlink()
            and entry.is_file()
            and (
                ".evidence" in entry.name
                or entry.name.endswith(".html")
                # Render revisions have an ``-rN-<nonce>`` suffix.  A bare
                # legacy ``slide-0.png`` is not enough evidence to infer an
                # in-flight receipt and remains eligible for orphan cleanup.
                or (entry.name.endswith(".png") and re.search(r"-r\d+-", entry.name) is not None)
            )
            for entry in directory.rglob("*")
        )
    except OSError:
        return True


def _job_directory(job_id: uuid.UUID) -> Path:
    root = checkpoint_root()
    directory = (root / str(job_id)).resolve()
    if root not in directory.parents:
        raise ValueError("invalid_checkpoint_path")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


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
    failed_ttl = failed_ttl_seconds if failed_ttl_seconds is not None else _failed_artifact_ttl_seconds(ttl)
    published_ttl = published_ttl_seconds if published_ttl_seconds is not None else _published_artifact_ttl_seconds(ttl)
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
        "skipped_unsealed": 0,
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
                    if _has_unsealed_evidence(directory):
                        result["skipped_unsealed"] += 1
                        await session.rollback()
                        continue
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
                if entry.stat(follow_symlinks=False).st_mtime >= orphan_cutoff.timestamp():
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
                select(PresentationGenerationJob.id).where(PresentationGenerationJob.id.in_(candidate_ids))
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
            if _has_unsealed_evidence(resolved):
                # The database row may have disappeared during a crash while
                # the receipt seal was still pending.  Never delete evidence
                # merely because its job UUID is currently orphaned.
                result["skipped_unsealed"] += 1
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

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if not name.startswith("__") and name not in {
            "bind_runtime",
            "_has_unsealed_evidence",
            "_job_directory", "render_artifact_path",
            "sweep_expired_checkpoint_artifacts",
        }:
            globals()[name] = value
