from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
import shutil
import tempfile
from datetime import timedelta
import uuid

from sqlalchemy import and_, case, func, or_, select, update

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.public_artifact import ArtifactRef
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.database import async_session_maker
from services.artifact_store import configured_public_artifact_service
from utils.datetime_utils import get_current_utc_datetime
from utils.get_env import get_app_data_directory_env
from services.authored_slide_checkpoint_bundle import (
    SlideCheckpointBundle,
    SlideCheckpointCorrupt,
)


_DEFAULT_ARTIFACT_TTL_SECONDS = 7 * 24 * 60 * 60
_DEFAULT_SWEEP_BATCH_SIZE = 50
_MAX_SWEEP_BATCH_SIZE = 500
_MAX_SWEEP_SCAN_SIZE = 5000
_DEFAULT_SWEEP_MAX_BYTES = 1024 * 1024 * 1024
_DEFAULT_SWEEP_MAX_FILES = 10_000
_MAX_SWEEP_MAX_FILES = 100_000
logger = logging.getLogger(__name__)


def _has_unsealed_evidence(directory: Path) -> bool:
    """Return true when an orphan directory contains evidence without a seal.

    A missing generation row is not proof that a worker stopped before its
    receipt was sealed.  Keep evidence sidecars until an explicit seal marker
    is present; this closes the crash window without changing terminal-row
    retention semantics.
    """

    sealed = any((directory / marker).is_file() for marker in (
        ".sealed", ".receipt-sealed", "receipt.sealed.json",
    ))
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


def mark_checkpoint_artifacts_sealed(job_id: uuid.UUID) -> None:
    """Durably mark an attempt safe for post-receipt TTL cleanup.

    The marker is intentionally tiny and written with the same atomic fsync
    path as render bytes.  Sweeping an orphan directory without this marker is
    unsafe because a worker may have crashed before sealing its receipt.
    """

    directory = _safe_job_directory(job_id)
    if not directory.is_dir():
        return
    atomic_write(directory / ".sealed", b"authored-checkpoint-sealed/v1\n")


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

def read_valid_render(checkpoint: AuthoredSlideCheckpoint) -> bytes | None:
    if not checkpoint.render_path or not checkpoint.render_sha256:
        return None
    root = checkpoint_root()
    unresolved = root / checkpoint.render_path
    if unresolved.is_symlink():
        return None
    path = unresolved.resolve()
    if root not in path.parents or not path.is_file():
        return None
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != checkpoint.render_sha256:
        return None
    return content


def _checkpoint_artifact_ref(
    checkpoint: AuthoredSlideCheckpoint,
    name: str,
) -> ArtifactRef | None:
    refs = checkpoint.artifact_refs
    if not isinstance(refs, dict):
        return None
    value = refs.get(name)
    if not isinstance(value, dict):
        return None
    try:
        return ArtifactRef.model_validate(value)
    except Exception as exc:
        raise SlideCheckpointCorrupt("checkpoint_artifact_ref_invalid") from exc


async def read_valid_render_artifact(
    checkpoint: AuthoredSlideCheckpoint,
) -> bytes | None:
    """Read the authoritative PNG ArtifactRef, with legacy cache fallback."""

    reference = _checkpoint_artifact_ref(checkpoint, "render")
    if reference is None:
        return read_valid_render(checkpoint)
    if (
        reference.kind != "slide-png"
        or reference.media_type != "image/png"
        or checkpoint.render_sha256 != reference.sha256
    ):
        raise SlideCheckpointCorrupt("checkpoint_render_artifact_binding_invalid")
    return await configured_public_artifact_service().read_ref_bytes(reference)


def _evidence_sidecar_path(render_path: Path) -> Path:
    """Return deterministic metadata path adjacent to an existing render."""

    return render_path.with_name(f".{render_path.name}.evidence.json")


def evidence_attempt_id(attempt_token: str) -> str:
    """Derive a stable opaque ID without persisting the worker lease token."""

    return hashlib.sha256(attempt_token.encode("utf-8")).hexdigest()[:32]


def write_valid_evidence(
    render_path: Path,
    *,
    attempt_id: str,
    slide_index: int,
    revision: int,
    html: str,
    png: bytes,
    metrics: dict[str, object],
) -> SlideCheckpointBundle:
    """Atomically persist HTML/PNG/metrics evidence beside its PNG pointer.

    Revision snapshots are retained as ``.<png>.evidence.rN.json``; the
    adjacent ``.evidence.json`` file is the sole current pointer. This keeps
    the existing SQL model/API unchanged while making repair recovery durable.
    """

    # Build through the shared contract so sidecars and the standalone store
    # use exactly the same digest and JSON rules. The render path is immutable,
    # so DB fencing (rather than a shared mutable pointer) selects the winner.
    digest = SlideCheckpointBundle._digest_for_payload(html, png, metrics)
    bundle = SlideCheckpointBundle(
        attempt_id, slide_index, revision, html, bytes(png), dict(metrics), digest
    )
    payload = json.dumps(
        bundle.to_payload(), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    sidecar = _evidence_sidecar_path(render_path)
    atomic_write(sidecar, payload)
    atomic_write(
        sidecar.with_name(f"{sidecar.stem}.r{revision}{sidecar.suffix}"),
        payload,
    )
    return bundle


def read_valid_evidence(
    checkpoint: AuthoredSlideCheckpoint,
) -> SlideCheckpointBundle | None:
    """Recover browser evidence using the checkpoint's render pointer.

    Missing sidecars return ``None`` for legacy rows. A present but malformed
    sidecar raises a corruption error instead of silently falling back to the
    defect source.
    """

    if not checkpoint.render_path:
        return None
    root = checkpoint_root()
    unresolved = root / checkpoint.render_path
    if unresolved.is_symlink():
        raise SlideCheckpointCorrupt("checkpoint_evidence_invalid_path")
    path = unresolved.resolve()
    if root not in path.parents or not path.is_file():
        return None
    sidecar = _evidence_sidecar_path(path)
    if sidecar.is_symlink():
        raise SlideCheckpointCorrupt("checkpoint_evidence_invalid_path")
    try:
        payload = json.loads(sidecar.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SlideCheckpointCorrupt("checkpoint_evidence_unreadable") from exc
    try:
        bundle = SlideCheckpointBundle.from_payload(payload)
    except SlideCheckpointCorrupt:
        raise
    render_bytes = path.read_bytes()
    # The revision snapshot is immutable and must agree with the mutable
    # current sidecar. This gives a bidirectional DB pointer/filesystem check:
    # DB -> PNG digest and sidecar -> revision bundle digest.
    revision_sidecar = sidecar.with_name(
        f"{sidecar.stem}.r{bundle.revision}{sidecar.suffix}"
    )
    if revision_sidecar.is_symlink():
        raise SlideCheckpointCorrupt("checkpoint_evidence_invalid_path")
    try:
        revision_payload = json.loads(revision_sidecar.read_text("utf-8"))
        revision_bundle = SlideCheckpointBundle.from_payload(revision_payload)
    except FileNotFoundError:
        raise SlideCheckpointCorrupt("checkpoint_evidence_revision_missing")
    except (OSError, ValueError, json.JSONDecodeError, SlideCheckpointCorrupt) as exc:
        if isinstance(exc, SlideCheckpointCorrupt):
            raise
        raise SlideCheckpointCorrupt("checkpoint_evidence_revision_unreadable") from exc
    path_binding = (
        f"-{bundle.attempt_id[:12]}-r{bundle.revision}-" in path.name
        or path.name.endswith(f"-{bundle.attempt_id[:12]}.png")
    )
    if (
        revision_bundle != bundle
        or bundle.slide_index != checkpoint.slide_index
        or not path_binding
        or bundle.png != render_bytes
        or (
            checkpoint.render_sha256 is not None
            and hashlib.sha256(render_bytes).hexdigest() != checkpoint.render_sha256
        )
        or (
            checkpoint.illustrated_html is not None
            and checkpoint.illustrated_html != bundle.html
        )
    ):
        raise SlideCheckpointCorrupt("checkpoint_evidence_pointer_mismatch")
    return bundle


async def read_valid_evidence_artifact(
    checkpoint: AuthoredSlideCheckpoint,
) -> SlideCheckpointBundle | None:
    """Read an immutable checkpoint bundle from object storage when present."""

    reference = _checkpoint_artifact_ref(checkpoint, "evidence")
    if reference is None:
        return read_valid_evidence(checkpoint)
    if (
        reference.kind != "checkpoint"
        or reference.media_type != "application/json"
    ):
        raise SlideCheckpointCorrupt("checkpoint_evidence_artifact_binding_invalid")
    try:
        payload = json.loads(
            (await configured_public_artifact_service().read_ref_bytes(reference)).decode(
                "utf-8"
            )
        )
        bundle = SlideCheckpointBundle.from_payload(payload)
    except SlideCheckpointCorrupt:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SlideCheckpointCorrupt("checkpoint_evidence_unreadable") from exc
    render = await read_valid_render_artifact(checkpoint)
    if (
        render is None
        or bundle.slide_index != checkpoint.slide_index
        or bundle.png != render
        or (
            checkpoint.illustrated_html is not None
            and checkpoint.illustrated_html != bundle.html
        )
    ):
        raise SlideCheckpointCorrupt("checkpoint_evidence_pointer_mismatch")
    return bundle


def recover_unreferenced_evidence(
    checkpoint: AuthoredSlideCheckpoint,
) -> tuple[str, str, SlideCheckpointBundle] | None:
    """Find the newest complete filesystem bundle not yet in the DB pointer.

    This closes the crash window where PNG/HTML/metrics were fsynced but the
    SQL pointer update did not commit. Candidates are accepted only when their
    sidecar, immutable revision sidecar, attempt digest, and PNG digest agree.
    """
    if checkpoint.artifact_refs is not None or not checkpoint.attempt_token:
        return None
    root = checkpoint_root()
    prefix = evidence_attempt_id(checkpoint.attempt_token)[:12]
    directory = root / str(checkpoint.job_id)
    if not directory.is_dir() or directory.is_symlink():
        return None
    current_revision = -1
    if checkpoint.render_path:
        try:
            current = read_valid_evidence(checkpoint)
            if current is not None:
                current_revision = current.revision
        except SlideCheckpointCorrupt:
            return None
    best: tuple[str, str, SlideCheckpointBundle] | None = None
    for path in directory.glob(f"slide-{checkpoint.slide_index}-{prefix}-r*-*.png"):
        if path.is_symlink() or not path.is_file():
            continue
        sidecar = _evidence_sidecar_path(path)
        try:
            bundle = SlideCheckpointBundle.from_payload(
                json.loads(sidecar.read_text("utf-8"))
            )
            revision_sidecar = sidecar.with_name(
                f"{sidecar.stem}.r{bundle.revision}{sidecar.suffix}"
            )
            immutable = SlideCheckpointBundle.from_payload(
                json.loads(revision_sidecar.read_text("utf-8"))
            )
            content = path.read_bytes()
            if (
                immutable != bundle
                or bundle.attempt_id != evidence_attempt_id(checkpoint.attempt_token)
                or bundle.slide_index != checkpoint.slide_index
                or bundle.png != content
                or bundle.revision <= current_revision
                or hashlib.sha256(content).hexdigest()
                != hashlib.sha256(bundle.png).hexdigest()
            ):
                continue
            candidate = (
                str(path.relative_to(root)).replace(os.sep, "/"),
                hashlib.sha256(content).hexdigest(),
                bundle,
            )
            if best is None or bundle.revision > best[2].revision:
                best = candidate
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, SlideCheckpointCorrupt):
            continue
    return best

