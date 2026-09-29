# ruff: noqa: F401
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import io
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
from models.sql.public_admission import PublicOperationAdmission
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceActionReceipt, WorkspaceOperation
from services.database import async_session_maker
from services.artifact_store import configured_public_artifact_service
from services.workspace_service import canonical_payload_hash
from utils.datetime_utils import get_current_utc_datetime
from utils.get_env import get_app_data_directory_env
from services.authored_checkpoint_html import checkpoint_stage_html
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
from services.authored_slide_checkpoint_bundle import (
    SlideCheckpointBundle,
    SlideCheckpointCorrupt,
)


logger = logging.getLogger(__name__)


# ---- Folded back from authored_checkpoint_sweep_impl.py (bind_runtime split) ----

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



def _deck_ir_checkpoint_path(job_id: uuid.UUID, attempt_token: str) -> Path:
    """Return an attempt-scoped detached Deck IR checkpoint path."""

    if not attempt_token:
        raise ValueError("deck_ir_checkpoint_attempt_missing")
    attempt_id = hashlib.sha256(attempt_token.encode("utf-8")).hexdigest()[:12]
    return _job_directory(job_id) / f"deck-ir-{attempt_id}.checkpoint.json"


def write_detached_deck_ir_checkpoint(
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    fencing_token: int,
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Atomically persist a content-addressed, attempt/fence-bound IR record."""

    if fencing_token < 1:
        raise ValueError("deck_ir_checkpoint_fence_invalid")
    canonical = json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    document = {
        "schemaVersion": "deck-ir-detached-checkpoint/v1",
        "attemptId": hashlib.sha256(attempt_token.encode("utf-8")).hexdigest(),
        "fencingToken": fencing_token,
        "payload": json.loads(canonical.decode("utf-8")),
        "payloadDigest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
    }
    unsigned = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    document["checkpointDigest"] = "sha256:" + hashlib.sha256(unsigned).hexdigest()
    atomic_write(
        _deck_ir_checkpoint_path(job_id, attempt_token),
        json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
    )
    # Local paths must not enter operation receipts or public event streams.
    return {
        "schemaVersion": document["schemaVersion"],
        "payloadDigest": document["payloadDigest"],
        "checkpointDigest": document["checkpointDigest"],
        "fencingToken": fencing_token,
    }


def write_deck_ir_execution_evidence(*, job_id: uuid.UUID, attempt_token: str, fencing_token: int,
                                     payload: Mapping[str, object]) -> dict[str, object]:
    """Retain success/exhaustion lineage separately from the original IR checkpoint."""
    if fencing_token < 1:
        raise ValueError("deck_ir_checkpoint_fence_invalid")
    document = {"schemaVersion": "deck-ir-execution-evidence/v1",
                "attemptId": hashlib.sha256(attempt_token.encode("utf-8")).hexdigest(),
                "fencingToken": fencing_token, "payload": dict(payload)}
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    fingerprint = hashlib.sha256(encoded).hexdigest()
    document["evidenceDigest"] = "sha256:" + fingerprint
    checkpoint = _deck_ir_checkpoint_path(job_id, attempt_token)
    path = checkpoint.with_name(f"{checkpoint.stem}.execution-{fingerprint}.json")
    atomic_write(path, json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return {"evidenceDigest": document["evidenceDigest"], "fencingToken": fencing_token}


def read_detached_deck_ir_checkpoint(
    *, job_id: uuid.UUID, attempt_token: str, fencing_token: int
) -> dict[str, object] | None:
    """Read only a matching fenced checkpoint; corruption is fail-closed."""

    path = _deck_ir_checkpoint_path(job_id, attempt_token)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SlideCheckpointCorrupt("deck_ir_checkpoint_unreadable") from exc
    if not isinstance(raw, Mapping):
        raise SlideCheckpointCorrupt("deck_ir_checkpoint_invalid")
    supplied = raw.get("checkpointDigest")
    unsigned = {key: value for key, value in raw.items() if key != "checkpointDigest"}
    encoded = json.dumps(
        unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if (
        raw.get("schemaVersion") != "deck-ir-detached-checkpoint/v1"
        or raw.get("fencingToken") != fencing_token
        or not isinstance(supplied, str)
        or supplied != "sha256:" + hashlib.sha256(encoded).hexdigest()
        or not isinstance(raw.get("payload"), Mapping)
    ):
        raise SlideCheckpointCorrupt("deck_ir_checkpoint_binding_invalid")
    return dict(raw)


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
    if refs is None:
        return None
    if not isinstance(refs, dict):
        raise SlideCheckpointCorrupt("checkpoint_artifact_ref_invalid")
    if name not in refs:
        return None
    value = refs.get(name)
    if not isinstance(value, dict):
        raise SlideCheckpointCorrupt("checkpoint_artifact_ref_invalid")
    try:
        return ArtifactRef.model_validate(value)
    except Exception as exc:
        raise SlideCheckpointCorrupt("checkpoint_artifact_ref_invalid") from exc


_MAX_RETRY_ANCESTRY_DEPTH = 32
_JOB_ID_PAYLOAD_KEYS = frozenset(
    {"jobId", "job_id", "generationJobId", "generation_job_id"}
)


def _declared_job_ids(value: object, *, depth: int = 0) -> set[str]:
    if depth > 4:
        return set()
    if isinstance(value, Mapping):
        declared = {
            str(item)
            for key, item in value.items()
            if key in _JOB_ID_PAYLOAD_KEYS and item is not None
        }
        for item in value.values():
            declared.update(_declared_job_ids(item, depth=depth + 1))
        return declared
    if isinstance(value, (list, tuple)):
        declared: set[str] = set()
        for item in value:
            declared.update(_declared_job_ids(item, depth=depth + 1))
        return declared
    return set()


async def _verified_retry_artifact_origin(
    session,
    *,
    current_operation_id: uuid.UUID,
    artifact_operation_id: uuid.UUID,
    presentation_id: uuid.UUID,
    job_id: uuid.UUID,
    task_id: str,
    job_request_sha256: str,
    job_request_payload: Mapping[str, object],
) -> tuple[uuid.UUID, str] | None:
    """Resolve authority through applied workspace retries to the admitted root."""

    current = await session.get(WorkspaceOperation, current_operation_id)
    if current is None:
        return None
    authority = (
        current.owner_id,
        current.owner_scope,
        current.conversation_id,
        current.presentation_id,
        current.kind,
    )
    immutable_payload = {
        key: value
        for key, value in current.request_payload.items()
        if key not in {
            "retry_of_operation_id",
            "account_deletion_generation",
            "recovery_revision",
        }
    }
    seen: set[uuid.UUID] = set()
    artifact_origin_seen = False
    cursor = current
    for _depth in range(_MAX_RETRY_ANCESTRY_DEPTH):
        if cursor.id in seen:
            return None
        seen.add(cursor.id)
        if (
            cursor.owner_id,
            cursor.owner_scope,
            cursor.conversation_id,
            cursor.presentation_id,
            cursor.kind,
        ) != authority:
            return None
        if cursor.owner_id is None or cursor.presentation_id != presentation_id:
            return None
        declared_job_ids = set()
        for payload in (cursor.request_payload, cursor.progress, cursor.result):
            declared_job_ids.update(_declared_job_ids(payload))
        if declared_job_ids != {str(job_id)}:
            return None
        if (
            cursor.request_payload.get("generation_request") != job_request_payload
            or cursor.request_payload.get("task_id") != task_id
            or str(cursor.request_payload.get("job_id")) != str(job_id)
            or str(cursor.request_payload.get("presentation_id")) != str(presentation_id)
        ):
            return None
        cursor_immutable_payload = {
            key: value
            for key, value in cursor.request_payload.items()
            if key not in {
                "retry_of_operation_id",
                "account_deletion_generation",
                "recovery_revision",
            }
        }
        if cursor_immutable_payload != immutable_payload:
            return None
        if cursor.id == artifact_operation_id:
            artifact_origin_seen = True
        parent_id = cursor.retry_of_operation_id
        if parent_id is None:
            if (
                not artifact_origin_seen
                or cursor.request_payload.get("retry_of_operation_id") is not None
                or cursor.request_hash != job_request_sha256
            ):
                return None
            admission = (await session.execute(
                select(PublicOperationAdmission).where(
                    PublicOperationAdmission.operation_id == cursor.id,
                    PublicOperationAdmission.operation_kind == "generation",
                )
            )).scalar_one_or_none()
            if admission is None or admission.owner_id != cursor.owner_id:
                return None
            return admission.owner_id, admission.tenant_id
        if cursor.request_payload.get("retry_of_operation_id") != str(parent_id):
            return None
        expected_retry_hash = canonical_payload_hash({
            "retry_of_operation_id": str(parent_id),
            "presentation_revision": cursor.expected_revision,
            "request_payload": cursor.request_payload,
        })
        if cursor.request_hash != expected_retry_hash:
            return None
        receipt = (await session.execute(
            select(WorkspaceActionReceipt).where(
                WorkspaceActionReceipt.operation_id == parent_id,
                WorkspaceActionReceipt.result_operation_id == cursor.id,
                WorkspaceActionReceipt.action_type == "retry",
                WorkspaceActionReceipt.status == "applied",
                WorkspaceActionReceipt.owner_scope == cursor.owner_scope,
            )
        )).scalar_one_or_none()
        if receipt is None or receipt.owner_id != cursor.owner_id:
            return None
        cursor = await session.get(WorkspaceOperation, parent_id)
        if cursor is None:
            return None
    return None


async def _read_bound_checkpoint_artifact(
    checkpoint: AuthoredSlideCheckpoint, reference: ArtifactRef,
) -> bytes:
    """Resolve checkpoint bytes against durable admission and committed metadata."""

    service = configured_public_artifact_service()
    async with async_session_maker() as session:
        binding = (await session.execute(
            select(
                PublicOperationAdmission.owner_id,
                PublicOperationAdmission.tenant_id,
                PublicOperationAdmission.operation_id,
                PresentationGenerationJob.presentation_id,
            ).join(
                PresentationGenerationJob,
                PresentationGenerationJob.workspace_operation_id
                == PublicOperationAdmission.operation_id,
            ).where(
                PresentationGenerationJob.id == checkpoint.job_id,
                PublicOperationAdmission.operation_kind == "generation",
            )
        )).one_or_none()
        owner_id = binding.owner_id if binding is not None else None
        tenant_id = binding.tenant_id if binding is not None else None
        current_operation_id = binding.operation_id if binding is not None else None
        presentation_id = binding.presentation_id if binding is not None else None
        origin_allowed = (
            binding is not None
            and reference.created_by_operation_id == binding.operation_id
        )
        if binding is None or reference.created_by_operation_id != binding.operation_id:
            job = await session.get(PresentationGenerationJob, checkpoint.job_id)
            retry_authority = (
                await _verified_retry_artifact_origin(
                    session,
                    current_operation_id=job.workspace_operation_id,
                    artifact_operation_id=reference.created_by_operation_id,
                    presentation_id=job.presentation_id,
                    job_id=checkpoint.job_id,
                    task_id=job.task_id,
                    job_request_sha256=job.request_sha256,
                    job_request_payload=job.request_payload,
                )
                if job is not None and job.workspace_operation_id is not None
                else None
            )
            if retry_authority is not None:
                owner_id, tenant_id = retry_authority
                current_operation_id = job.workspace_operation_id
                presentation_id = job.presentation_id
                origin_allowed = True
        if (
            owner_id is None
            or tenant_id is None
            or current_operation_id is None
            or presentation_id is None
            or not origin_allowed
            or reference.owner_id != owner_id
            or reference.tenant_id != tenant_id
            or reference.retention_class != "checkpoint"
        ):
            raise SlideCheckpointCorrupt("checkpoint_artifact_owner_binding_invalid")
        resolved = await service.resolve_ref(
            session,
            artifact_id=reference.artifact_id,
            owner_id=owner_id,
            tenant_id=tenant_id,
            expected_digest=reference.sha256,
        )
        if resolved != reference:
            raise SlideCheckpointCorrupt("checkpoint_artifact_ref_binding_invalid")
        return await service.read_ref_bytes(resolved)


def _require_checkpoint_canvas(
    png: bytes,
    *,
    expected_width_px: int | None,
    expected_height_px: int | None,
) -> None:
    if expected_width_px is None and expected_height_px is None:
        return
    if expected_width_px is None or expected_height_px is None:
        raise SlideCheckpointCorrupt("checkpoint_canvas_contract_incomplete")
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as image:
            actual = image.size
    except Exception as exc:
        raise SlideCheckpointCorrupt("checkpoint_render_image_invalid") from exc
    if actual != (int(expected_width_px), int(expected_height_px)):
        raise SlideCheckpointCorrupt("checkpoint_render_canvas_mismatch")


def _require_evidence_canvas(
    bundle: SlideCheckpointBundle,
    *,
    expected_width_px: int | None,
    expected_height_px: int | None,
) -> None:
    _require_checkpoint_canvas(
        bundle.png,
        expected_width_px=expected_width_px,
        expected_height_px=expected_height_px,
    )
    if expected_width_px is None and expected_height_px is None:
        return
    if (
        bundle.metrics.get("width"), bundle.metrics.get("height")
    ) != (int(expected_width_px), int(expected_height_px)):
        raise SlideCheckpointCorrupt("checkpoint_evidence_canvas_mismatch")


async def read_valid_render_artifact(
    checkpoint: AuthoredSlideCheckpoint,
    *,
    expected_width_px: int | None = None,
    expected_height_px: int | None = None,
) -> bytes | None:
    """Read the authoritative PNG ArtifactRef, with legacy cache fallback."""

    reference = _checkpoint_artifact_ref(checkpoint, "render")
    if reference is None:
        if checkpoint.artifact_refs is not None:
            raise SlideCheckpointCorrupt("checkpoint_render_artifact_missing")
        render = read_valid_render(checkpoint)
        if render is not None:
            _require_checkpoint_canvas(
                render,
                expected_width_px=expected_width_px,
                expected_height_px=expected_height_px,
            )
        return render
    if (
        reference.kind != "slide-png"
        or reference.media_type != "image/png"
        or checkpoint.render_sha256 != reference.sha256
    ):
        raise SlideCheckpointCorrupt("checkpoint_render_artifact_binding_invalid")
    render = await _read_bound_checkpoint_artifact(checkpoint, reference)
    _require_checkpoint_canvas(
        render,
        expected_width_px=expected_width_px,
        expected_height_px=expected_height_px,
    )
    return render


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
    *,
    require_current_html_match: bool = True,
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
    except FileNotFoundError as missing:
        raise SlideCheckpointCorrupt(
            "checkpoint_evidence_revision_missing"
        ) from missing
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
            require_current_html_match
            and
            checkpoint_stage_html(checkpoint) is not None
            and checkpoint_stage_html(checkpoint) != bundle.html
        )
    ):
        raise SlideCheckpointCorrupt("checkpoint_evidence_pointer_mismatch")
    return bundle


async def read_valid_evidence_artifact(
    checkpoint: AuthoredSlideCheckpoint,
    *,
    require_current_html_match: bool = True,
    expected_width_px: int | None = None,
    expected_height_px: int | None = None,
) -> SlideCheckpointBundle | None:
    """Read an immutable checkpoint bundle from object storage when present."""

    reference = _checkpoint_artifact_ref(checkpoint, "evidence")
    if reference is None:
        if checkpoint.artifact_refs is not None:
            return None
        bundle = read_valid_evidence(
            checkpoint,
            require_current_html_match=require_current_html_match,
        )
        if bundle is not None:
            _require_evidence_canvas(
                bundle,
                expected_width_px=expected_width_px,
                expected_height_px=expected_height_px,
            )
        return bundle
    if (
        reference.kind != "checkpoint"
        or reference.media_type != "application/json"
    ):
        raise SlideCheckpointCorrupt("checkpoint_evidence_artifact_binding_invalid")
    try:
        payload = json.loads(
            (await _read_bound_checkpoint_artifact(checkpoint, reference)).decode(
                "utf-8"
            )
        )
        bundle = SlideCheckpointBundle.from_payload(payload)
    except SlideCheckpointCorrupt:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SlideCheckpointCorrupt("checkpoint_evidence_unreadable") from exc
    render = await read_valid_render_artifact(
        checkpoint,
        expected_width_px=expected_width_px,
        expected_height_px=expected_height_px,
    )
    if (
        render is None
        or bundle.slide_index != checkpoint.slide_index
        or bundle.png != render
        or (
            require_current_html_match
            and
            checkpoint_stage_html(checkpoint) is not None
            and checkpoint_stage_html(checkpoint) != bundle.html
        )
    ):
        raise SlideCheckpointCorrupt("checkpoint_evidence_pointer_mismatch")
    _require_evidence_canvas(
        bundle,
        expected_width_px=expected_width_px,
        expected_height_px=expected_height_px,
    )
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

