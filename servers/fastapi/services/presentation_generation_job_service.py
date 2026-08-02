from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import os
import secrets
from typing import Any
import uuid

from sqlalchemy import exists, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from services.authored_generation_observability import (
    log_authored_generation_observation,
)
from services.database import async_session_maker
from utils.owner_context import get_current_owner_id

logger = logging.getLogger(__name__)

GENERATION_JOB_LEASE_SECONDS = 300
GENERATION_JOB_HEARTBEAT_SECONDS = 30
GENERATION_JOB_DISPATCH_SECONDS = 1


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


# The in-process authored semaphore defaults to one job. Apply the same default
# to the database-backed admission gate so adding FastAPI workers does not
# silently multiply provider concurrency and spend. Operators can raise this
# independently after validating provider quotas.
AUTHORED_CLUSTER_JOB_CONCURRENCY = _positive_int_env(
    "AUTHORED_CLUSTER_JOB_CONCURRENCY",
    _positive_int_env("AUTHORED_JOB_CONCURRENCY", 1),
)
GENERATION_JOB_CAPACITY_POLL_SECONDS = 1.0
# Stable, application-specific PostgreSQL advisory-lock namespace. The lock is
# transaction-scoped, so it is always released on commit, rollback or disconnect.
_AUTHORED_CLAIM_LOCK_ID = 0x505054415554484F
NONTERMINAL_STATES = frozenset({"queued", "running", "staging", "awaiting_resume"})
TERMINAL_STATES = frozenset({"published", "failed"})

_dispatcher_task: asyncio.Task[None] | None = None
# Keyed by job id so a user-initiated cancel can interrupt the in-process
# attempt this worker is running (best-effort; other workers keep no handle).
_active_jobs: dict[uuid.UUID, asyncio.Task[None]] = {}


class GenerationCapacityUnavailable(RuntimeError):
    """The job remains queued until cluster-wide authored capacity is free."""


def _is_authored_payload(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return payload.get("strategy") == "authored" or str(
        payload.get("template", "")
    ).lower() == "authored"


async def _serialize_cluster_claims(session) -> bool:
    """Serialize quota check + claim on supported databases.

    PostgreSQL advisory transaction locks avoid a new table/migration. SQLite's
    BEGIN IMMEDIATE provides the equivalent writer serialization for local and
    multi-process desktop deployments. Unknown dialects retain the existing CAS
    claim but deliberately skip a quota count that could otherwise race.
    """

    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _AUTHORED_CLAIM_LOCK_ID},
        )
        return True
    if dialect == "sqlite":
        await session.execute(text("BEGIN IMMEDIATE"))
        return True
    logger.warning(
        "Authored cluster quota is unsupported for database dialect %s; "
        "retaining atomic per-job claims and process-local limits",
        dialect,
    )
    return False


def _job_is_claimable(
    job: PresentationGenerationJob,
    *,
    now: datetime,
    allow_auth_resume: bool,
) -> bool:
    # Workspace-linked jobs are owned exclusively by the generic operation
    # runner.  The legacy dispatcher must never create a second attempt for
    # the same durable command.
    if job.workspace_operation_id is not None:
        return False
    if job.state == "queued":
        return True
    if job.state == "awaiting_resume":
        return allow_auth_resume
    if job.state not in {"running", "staging"} or job.lease_expires_at is None:
        return False
    expires_at = job.lease_expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at < now


async def _authored_capacity_available(
    session,
    *,
    target_job_id: uuid.UUID,
    now: datetime,
) -> bool:
    active_payloads = await session.scalars(
        select(PresentationGenerationJob.request_payload)
        .where(PresentationGenerationJob.id != target_job_id)
        .where(PresentationGenerationJob.state.in_(("running", "staging")))
        .where(PresentationGenerationJob.attempt_token.is_not(None))
        .where(PresentationGenerationJob.lease_expires_at >= now)
    )
    active_authored = sum(
        1 for payload in active_payloads if _is_authored_payload(payload)
    )
    return active_authored < AUTHORED_CLUSTER_JOB_CONCURRENCY


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def idempotency_key_hash(key: str) -> str:
    """Hash a caller-supplied key inside the current owner's namespace.

    Idempotency keys are client-chosen values, so treating them as global lets
    one user accidentally (or deliberately) collide with another user's
    request.  The hash is persisted behind a globally unique index; including
    the owner in its input keeps that index while making retries owner-local.
    """

    normalized = key.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("invalid_idempotency_key")
    owner_id = get_current_owner_id()
    owner_scope = str(owner_id) if owner_id is not None else "anonymous"
    return sha256(
        (
            "presenton.presentation-generation/v1:"
            f"{owner_scope}:{normalized}"
        ).encode("utf-8")
    ).hexdigest()


async def claim_generation_job(
    job_id: uuid.UUID,
    *,
    allow_auth_resume: bool,
) -> tuple[str, str] | None:
    """Atomically acquire one queued/recoverable job and return task/token."""

    now = utc_now()
    token = secrets.token_urlsafe(32)
    claimable = [PresentationGenerationJob.state == "queued"]
    if allow_auth_resume:
        claimable.append(PresentationGenerationJob.state == "awaiting_resume")
    claimable.append(
        (
            PresentationGenerationJob.state.in_(("running", "staging"))
            & (PresentationGenerationJob.lease_expires_at < now)
        )
    )
    async with async_session_maker() as session:
        target = await session.get(PresentationGenerationJob, job_id)
        authored_target = target is not None and _is_authored_payload(
            target.request_payload
        )
        quota_is_serialized = False
        if authored_target:
            # The lookup above opens an implicit read transaction. End it before
            # SQLite's BEGIN IMMEDIATE (and before taking the PostgreSQL advisory
            # xact lock), then re-read all claimability state under the lock.
            await session.rollback()
            quota_is_serialized = await _serialize_cluster_claims(session)
            target = await session.get(PresentationGenerationJob, job_id)
        if target is None or not _job_is_claimable(
            target,
            now=now,
            allow_auth_resume=allow_auth_resume,
        ):
            await session.rollback()
            return None
        if (
            quota_is_serialized
            and authored_target
            and not await _authored_capacity_available(
                session,
                target_job_id=job_id,
                now=now,
            )
        ):
            await session.rollback()
            raise GenerationCapacityUnavailable(
                "authored_cluster_capacity_unavailable"
            )
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == job_id)
            .where(PresentationGenerationJob.workspace_operation_id.is_(None))
            .where(or_(*claimable))
            .values(
                state="running",
                attempt_number=PresentationGenerationJob.attempt_number + 1,
                attempt_token=token,
                lease_expires_at=now
                + timedelta(seconds=GENERATION_JOB_LEASE_SECONDS),
                heartbeat_at=now,
                last_started_at=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await session.rollback()
            return None
        job = await session.get(PresentationGenerationJob, job_id)
        if job is None:
            await session.rollback()
            return None
        task = await session.get(
            AsyncPresentationGenerationTaskModel, job.task_id
        )
        if task is None:
            job.state = "failed"
            job.failed_at = now
            job.attempt_token = None
            job.lease_expires_at = None
            await session.commit()
            return None
        task.status = "running"
        task.message = "Generating presentation"
        task.updated_at = now
        session.add(task)
        resume_counts = (
            (job.attempt_number, job.completed_slides, job.total_slides)
            if authored_target and job.attempt_number > 1
            else None
        )
        await session.commit()
        if resume_counts is not None:
            log_authored_generation_observation(
                name="resume",
                attempt_number=resume_counts[0],
                completed_slides=resume_counts[1],
                total_slides=resume_counts[2],
            )
        return job.task_id, token


async def claim_workspace_linked_generation_job(
    workspace_operation_id: uuid.UUID,
    attempt_token: str,
    attempt_number: int,
) -> tuple[uuid.UUID, str] | None:
    """Project a generic operation claim onto its legacy generation row.

    Authored checkpoints still fence on ``PresentationGenerationJob``.  The
    workspace operation remains the canonical lease; this compatibility claim
    mirrors its token so the existing checkpoint/publish path can be reused
    without allowing the legacy dispatcher to run a parallel attempt.
    """

    now = utc_now()
    async with async_session_maker() as session:
        target = (
            await session.execute(
                select(PresentationGenerationJob).where(
                    PresentationGenerationJob.workspace_operation_id
                    == workspace_operation_id
                )
            )
        ).scalar_one_or_none()
        authored_target = target is not None and _is_authored_payload(
            target.request_payload
        )
        quota_is_serialized = False
        if authored_target:
            await session.rollback()
            quota_is_serialized = await _serialize_cluster_claims(session)
            target = (
                await session.execute(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.workspace_operation_id
                        == workspace_operation_id
                    )
                )
            ).scalar_one_or_none()
        if target is None or target.state == "published":
            await session.rollback()
            return None

        live_operation = await session.scalar(
            select(WorkspaceOperation.id)
            .where(WorkspaceOperation.id == workspace_operation_id)
            .where(WorkspaceOperation.state == "running")
            .where(WorkspaceOperation.attempt_token == attempt_token)
        )
        if live_operation is None:
            await session.rollback()
            return None
        if (
            quota_is_serialized
            and authored_target
            and not await _authored_capacity_available(
                session,
                target_job_id=target.id,
                now=now,
            )
        ):
            await session.rollback()
            raise GenerationCapacityUnavailable(
                "authored_cluster_capacity_unavailable"
            )

        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == target.id)
            .where(
                PresentationGenerationJob.workspace_operation_id
                == workspace_operation_id
            )
            .where(PresentationGenerationJob.state != "published")
            .values(
                state="running",
                attempt_number=attempt_number,
                attempt_token=attempt_token,
                lease_expires_at=now
                + timedelta(seconds=GENERATION_JOB_LEASE_SECONDS),
                heartbeat_at=now,
                last_started_at=now,
                failed_at=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await session.rollback()
            return None
        task = await session.get(
            AsyncPresentationGenerationTaskModel, target.task_id
        )
        if task is None:
            await session.rollback()
            return None
        task.status = "running"
        task.message = "Generating presentation"
        task.error = None
        task.updated_at = now
        session.add(task)
        await session.commit()
        return target.id, target.task_id


async def heartbeat_workspace_linked_generation_job(
    job_id: uuid.UUID,
    workspace_operation_id: uuid.UUID,
    attempt_token: str,
) -> bool:
    now = utc_now()
    async with async_session_maker() as session:
        live_operation = exists(
            select(WorkspaceOperation.id)
            .where(WorkspaceOperation.id == workspace_operation_id)
            .where(WorkspaceOperation.state == "running")
            .where(WorkspaceOperation.attempt_token == attempt_token)
        )
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == job_id)
            .where(
                PresentationGenerationJob.workspace_operation_id
                == workspace_operation_id
            )
            .where(PresentationGenerationJob.attempt_token == attempt_token)
            .where(PresentationGenerationJob.state.in_(("running", "staging")))
            .where(live_operation)
            .values(
                heartbeat_at=now,
                lease_expires_at=now
                + timedelta(seconds=GENERATION_JOB_LEASE_SECONDS),
                updated_at=now,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def release_workspace_linked_generation_job(
    job_id: uuid.UUID,
    workspace_operation_id: uuid.UUID,
    attempt_token: str,
) -> bool:
    """Release only the compatibility projection after interruption."""

    now = utc_now()
    async with async_session_maker() as session:
        job = (
            await session.execute(
                select(PresentationGenerationJob)
                .where(PresentationGenerationJob.id == job_id)
                .where(
                    PresentationGenerationJob.workspace_operation_id
                    == workspace_operation_id
                )
                .where(PresentationGenerationJob.attempt_token == attempt_token)
                .where(
                    PresentationGenerationJob.state.in_(("running", "staging"))
                )
            )
        ).scalar_one_or_none()
        if job is None:
            await session.rollback()
            return False
        job.state = "awaiting_resume" if job.export_cookie_required else "queued"
        job.attempt_token = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        task = await session.get(
            AsyncPresentationGenerationTaskModel, job.task_id
        )
        if task is not None and task.status not in {"completed", "error"}:
            task.status = "pending"
            task.message = (
                "Authentication required to resume generation"
                if job.export_cookie_required
                else "Interrupted; queued for workspace recovery"
            )
            task.updated_at = now
            session.add(task)
        session.add(job)
        await session.commit()
        return True


async def project_workspace_linked_generation_cancelled(
    session: AsyncSession,
    workspace_operation_id: uuid.UUID,
    *,
    expected_job_id: uuid.UUID | None = None,
) -> bool:
    """Atomically terminalize the legacy projection for a user cancellation.

    The workspace operation is the canonical state machine, but the legacy
    task/status endpoints remain readable during rollout.  Callers own the
    surrounding transaction so the operation event, job row and task row can
    never disagree after a committed cancellation.
    """

    now = utc_now()
    statement = (
        select(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.workspace_operation_id
            == workspace_operation_id
        )
        .where(PresentationGenerationJob.state != "published")
        .with_for_update()
    )
    if expected_job_id is not None:
        statement = statement.where(PresentationGenerationJob.id == expected_job_id)
    job = (await session.execute(statement)).scalar_one_or_none()
    if job is None:
        return False
    task = await session.get(
        AsyncPresentationGenerationTaskModel,
        job.task_id,
        with_for_update=True,
    )
    if task is None:
        return False

    job.state = "failed"
    job.resume_allowed = False
    job.attempt_token = None
    job.lease_expires_at = None
    job.heartbeat_at = now
    job.failed_at = now
    job.last_error = {"code": "generation_cancelled"}
    job.updated_at = now
    task.status = "error"
    task.message = "Generation cancelled by user"
    task.error = {
        "status_code": 499,
        "code": "generation_cancelled",
        "detail": "Generation cancelled by user",
    }
    task.updated_at = now
    session.add_all((job, task))
    return True


async def project_workspace_linked_generation_failed(
    session: AsyncSession,
    workspace_operation_id: uuid.UUID,
    *,
    error_code: str,
    expected_job_id: uuid.UUID | None = None,
) -> bool:
    """Atomically expose a safe terminal workspace failure to legacy readers."""

    now = utc_now()
    statement = (
        select(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.workspace_operation_id
            == workspace_operation_id
        )
        .where(PresentationGenerationJob.state != "published")
        .with_for_update()
    )
    if expected_job_id is not None:
        statement = statement.where(PresentationGenerationJob.id == expected_job_id)
    job = (await session.execute(statement)).scalar_one_or_none()
    if job is None:
        return False
    task = await session.get(
        AsyncPresentationGenerationTaskModel,
        job.task_id,
        with_for_update=True,
    )
    if task is None:
        return False

    safe_code = error_code or "presentation_generation_failed"
    job.state = "failed"
    job.resume_allowed = True
    job.attempt_token = None
    job.lease_expires_at = None
    job.heartbeat_at = now
    job.failed_at = now
    job.last_error = {"code": safe_code}
    job.updated_at = now
    task.status = "error"
    task.message = "Presentation generation failed"
    task.error = {
        "status_code": 500,
        "code": safe_code,
        "detail": "Presentation generation failed",
    }
    task.updated_at = now
    session.add_all((job, task))
    return True


async def project_workspace_linked_generation_recovered(
    session: AsyncSession,
    workspace_operation_id: uuid.UUID,
    *,
    expected_job_id: uuid.UUID | None = None,
    expected_attempt_token: str | None = None,
) -> bool:
    """Atomically release a stale compatibility claim during operation recovery.

    The workspace operation owns the durable lease.  Recovery must therefore
    clear the mirrored legacy claim in the same transaction; otherwise legacy
    status readers can report ``running`` throughout the operation backoff
    window.  A different non-null token is fenced rather than overwritten.
    """

    now = utc_now()
    statement = (
        select(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.workspace_operation_id
            == workspace_operation_id
        )
        .where(PresentationGenerationJob.state != "published")
        .with_for_update()
    )
    if expected_job_id is not None:
        statement = statement.where(PresentationGenerationJob.id == expected_job_id)
    job = (await session.execute(statement)).scalar_one_or_none()
    if job is None:
        return False
    if job.attempt_token not in {None, expected_attempt_token}:
        return False

    task = await session.get(
        AsyncPresentationGenerationTaskModel,
        job.task_id,
        with_for_update=True,
    )
    if task is None:
        return False

    job.state = "awaiting_resume" if job.export_cookie_required else "queued"
    job.attempt_token = None
    job.lease_expires_at = None
    job.heartbeat_at = now
    job.updated_at = now
    if task.status not in {"completed", "error"}:
        task.status = "pending"
        task.message = (
            "Authentication required to resume generation"
            if job.export_cookie_required
            else "Interrupted; queued for workspace recovery"
        )
        task.updated_at = now
    session.add_all((job, task))
    return True


async def rebind_workspace_linked_generation_job_for_retry(
    session: AsyncSession,
    source_operation_id: uuid.UUID,
    retry_operation_id: uuid.UUID,
    *,
    expected_job_id: uuid.UUID | None = None,
) -> bool:
    """Move the single compatibility job to a terminal operation's retry.

    ``presentation_id`` is unique on the generation job, so a retry must
    rebind the existing row rather than creating a parallel job system.  The
    caller commits this together with the retry operation and action receipt.
    """

    now = utc_now()
    statement = (
        select(PresentationGenerationJob)
        .where(
            PresentationGenerationJob.workspace_operation_id
            == source_operation_id
        )
        .where(PresentationGenerationJob.state != "published")
        .with_for_update()
    )
    if expected_job_id is not None:
        statement = statement.where(PresentationGenerationJob.id == expected_job_id)
    job = (await session.execute(statement)).scalar_one_or_none()
    if job is None:
        return False
    task = await session.get(
        AsyncPresentationGenerationTaskModel,
        job.task_id,
        with_for_update=True,
    )
    if task is None:
        return False

    job.workspace_operation_id = retry_operation_id
    job.state = "queued"
    job.attempt_number = 0
    job.attempt_token = None
    job.lease_expires_at = None
    job.heartbeat_at = now
    job.current_stage = None
    job.resume_allowed = False
    job.resume_count += 1
    job.failed_at = None
    job.last_error = None
    job.updated_at = now
    task.status = "pending"
    task.message = "Queued for workspace retry"
    task.error = None
    task.updated_at = now
    session.add_all((job, task))
    return True


async def fail_workspace_linked_generation_job(
    job_id: uuid.UUID,
    workspace_operation_id: uuid.UUID,
    attempt_token: str,
    *,
    error_code: str,
) -> bool:
    """Record a safe terminal compatibility projection for a failed attempt."""

    now = utc_now()
    safe_error = {"code": error_code}
    async with async_session_maker() as session:
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == job_id)
            .where(
                PresentationGenerationJob.workspace_operation_id
                == workspace_operation_id
            )
            .where(PresentationGenerationJob.attempt_token == attempt_token)
            .where(PresentationGenerationJob.state.in_(("running", "staging")))
            .values(
                state="failed",
                attempt_token=None,
                lease_expires_at=None,
                heartbeat_at=now,
                failed_at=now,
                last_error=safe_error,
                updated_at=now,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def complete_workspace_linked_generation_job(
    job_id: uuid.UUID,
    workspace_operation_id: uuid.UUID,
    attempt_token: str,
) -> bool:
    """Finish the legacy projection, accepting authored atomic publication."""

    now = utc_now()
    async with async_session_maker() as session:
        job = await session.get(PresentationGenerationJob, job_id)
        if (
            job is not None
            and job.workspace_operation_id == workspace_operation_id
            and job.state == "published"
        ):
            await session.rollback()
            return True
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == job_id)
            .where(
                PresentationGenerationJob.workspace_operation_id
                == workspace_operation_id
            )
            .where(PresentationGenerationJob.attempt_token == attempt_token)
            .where(PresentationGenerationJob.state.in_(("running", "staging")))
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
        await session.commit()
        return result.rowcount == 1


async def heartbeat_generation_job(job_id: uuid.UUID, token: str) -> bool:
    now = utc_now()
    async with async_session_maker() as session:
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == job_id)
            .where(PresentationGenerationJob.workspace_operation_id.is_(None))
            .where(PresentationGenerationJob.attempt_token == token)
            .where(PresentationGenerationJob.state.in_(("running", "staging")))
            .values(
                heartbeat_at=now,
                lease_expires_at=now
                + timedelta(seconds=GENERATION_JOB_LEASE_SECONDS),
                updated_at=now,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def heartbeat_generation_job_loop(job_id: uuid.UUID, token: str) -> None:
    try:
        while True:
            await asyncio.sleep(GENERATION_JOB_HEARTBEAT_SECONDS)
            if not await heartbeat_generation_job(job_id, token):
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Presentation generation heartbeat failed for %s", job_id)


async def finalize_generation_job(
    job_id: uuid.UUID,
    token: str,
    *,
    succeeded: bool,
) -> bool:
    now = utc_now()
    state = "published" if succeeded else "failed"
    values: dict[str, Any] = {
        "state": state,
        "attempt_token": None,
        "lease_expires_at": None,
        "heartbeat_at": now,
        "updated_at": now,
    }
    if succeeded:
        values.update(
            current_stage="completed",
            last_error=None,
            resume_allowed=False,
        )
    values["published_at" if succeeded else "failed_at"] = now
    async with async_session_maker() as session:
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == job_id)
            .where(PresentationGenerationJob.workspace_operation_id.is_(None))
            .where(PresentationGenerationJob.attempt_token == token)
            .where(PresentationGenerationJob.state.in_(("running", "staging")))
            .values(**values)
        )
        await session.commit()
        return result.rowcount == 1


async def release_generation_job(job_id: uuid.UUID, token: str) -> bool:
    """Release an interrupted attempt so shutdown never turns it into a failure."""

    now = utc_now()
    async with async_session_maker() as session:
        job = (
            await session.execute(
                select(PresentationGenerationJob)
                .where(PresentationGenerationJob.id == job_id)
                .where(PresentationGenerationJob.workspace_operation_id.is_(None))
                .where(PresentationGenerationJob.attempt_token == token)
                .where(
                    PresentationGenerationJob.state.in_(("running", "staging"))
                )
            )
        ).scalar_one_or_none()
        if job is None:
            return False
        job.state = "awaiting_resume" if job.export_cookie_required else "queued"
        job.attempt_token = None
        job.lease_expires_at = None
        job.heartbeat_at = now
        job.updated_at = now
        task = await session.get(
            AsyncPresentationGenerationTaskModel, job.task_id
        )
        if task is not None:
            task.status = "pending"
            task.message = (
                "Awaiting an authenticated retry with the same Idempotency-Key"
                if job.export_cookie_required
                else "Interrupted during shutdown; queued for recovery"
            )
            task.updated_at = now
            session.add(task)
        session.add(job)
        await session.commit()
        return True


async def recover_generation_jobs(*, include_queued: bool = True) -> int:
    """Recover abandoned attempts without persisting reusable auth credentials."""

    now = utc_now()
    async with async_session_maker() as session:
        reclaimable = (
            PresentationGenerationJob.state.in_(("running", "staging"))
            & or_(
                PresentationGenerationJob.lease_expires_at.is_(None),
                PresentationGenerationJob.lease_expires_at < now,
            )
        )
        if include_queued:
            reclaimable = or_(
                PresentationGenerationJob.state == "queued", reclaimable
            )
        rows = (
            await session.execute(
                select(PresentationGenerationJob)
                .where(PresentationGenerationJob.workspace_operation_id.is_(None))
                .where(reclaimable)
            )
        ).scalars().all()
        stalled_counts = {
            state: sum(
                1
                for job in rows
                if job.state == state and _is_authored_payload(job.request_payload)
            )
            for state in ("running", "staging")
        }
        for job in rows:
            job.state = (
                "awaiting_resume" if job.export_cookie_required else "queued"
            )
            job.attempt_token = None
            job.lease_expires_at = None
            job.heartbeat_at = now
            job.updated_at = now
            task = await session.get(
                AsyncPresentationGenerationTaskModel, job.task_id
            )
            if task is not None:
                task.status = "pending"
                task.message = (
                    "Awaiting an authenticated retry with the same Idempotency-Key"
                    if job.export_cookie_required
                    else "Recovered after worker restart"
                )
                task.updated_at = now
                session.add(task)
            session.add(job)
        await session.commit()
        for prior_state, count in stalled_counts.items():
            if count:
                log_authored_generation_observation(
                    name="stalled_recovery",
                    prior_state=prior_state,
                    recovered_jobs=count,
                )
        return len(rows)


async def launch_generation_job(
    job_id: uuid.UUID,
    export_cookie_header: str | None = None,
) -> None:
    """Run one job as a tracked asyncio task so a cancel can interrupt it."""

    async with async_session_maker() as session:
        linked_operation_id = await session.scalar(
            select(PresentationGenerationJob.workspace_operation_id).where(
                PresentationGenerationJob.id == job_id
            )
        )
    if linked_operation_id is not None:
        logger.debug(
            "Skipping legacy launch for workspace-linked generation job %s",
            job_id,
        )
        return

    from api.v1.ppt.endpoints.presentation_generate import run_generation_job

    existing = _active_jobs.get(job_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(run_generation_job(job_id, export_cookie_header))
    _active_jobs[job_id] = task

    def _discard(done: asyncio.Task[None]) -> None:
        if _active_jobs.get(job_id) is done:
            _active_jobs.pop(job_id, None)

    task.add_done_callback(_discard)


async def cancel_generation_job(
    task_id: str,
) -> AsyncPresentationGenerationTaskModel | None:
    """Durably cancel an owner-scoped generation task.

    Marks the job terminal so the dispatcher and recovery never (re)start it,
    then cancels the in-process attempt when this worker holds its handle.
    Returns the task row (unchanged if already terminal), or None when no
    owner-scoped task exists.
    """

    owner_id = get_current_owner_id()
    now = utc_now()
    job_id: uuid.UUID | None = None
    async with async_session_maker() as session:
        owner_filter = (
            AsyncPresentationGenerationTaskModel.owner_id.is_(None)
            if owner_id is None
            else AsyncPresentationGenerationTaskModel.owner_id == owner_id
        )
        task = (
            await session.execute(
                select(AsyncPresentationGenerationTaskModel)
                .where(AsyncPresentationGenerationTaskModel.id == task_id)
                .where(owner_filter)
            )
        ).scalar_one_or_none()
        if task is None:
            return None
        if task.status in ("completed", "error"):
            return task
        job = (
            await session.execute(
                select(PresentationGenerationJob).where(
                    PresentationGenerationJob.task_id == task_id
                )
            )
        ).scalar_one_or_none()
        if job is not None:
            if job.workspace_operation_id is not None:
                # Preserve the generic operation as the canonical state machine.
                # It performs the fenced terminal projection and interrupts its
                # local handler attempt when possible.
                operation_id = job.workspace_operation_id
                await session.rollback()
                from services.workspace_operation_runner import (
                    request_workspace_operation_cancellation,
                )

                await request_workspace_operation_cancellation(operation_id)
                return task
            if job.state not in NONTERMINAL_STATES:
                return task
            result = await session.execute(
                update(PresentationGenerationJob)
                .where(PresentationGenerationJob.id == job.id)
                .where(PresentationGenerationJob.state.in_(NONTERMINAL_STATES))
                .values(
                    state="failed",
                    resume_allowed=False,
                    failed_at=now,
                    attempt_token=None,
                    lease_expires_at=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                await session.refresh(task)
                return task
            job_id = job.id
        task.status = "error"
        task.message = "Generation cancelled by user"
        task.error = {
            "status_code": 499,
            "code": "generation_cancelled",
            "detail": "Generation cancelled by user",
        }
        task.updated_at = now
        session.add(task)
        await session.commit()

    if job_id is not None:
        handle = _active_jobs.get(job_id)
        if handle is not None:
            handle.cancel()
    return task


async def _dispatch_ready_jobs() -> None:
    # Startup reconciliation cannot reclaim a still-valid lease because another
    # process may genuinely own it. Re-running reconciliation here ensures a
    # crashed worker is picked up as soon as that lease later expires.
    await recover_generation_jobs(include_queued=False)
    try:
        # Lazy import avoids coupling generic durable-job model initialization
        # to authored checkpoint storage. The sweeper is interval-gated and
        # deletes only old terminal/orphan UUID directories in bounded batches.
        from services.authored_checkpoint_service import (
            maybe_sweep_expired_checkpoint_artifacts,
        )

        await maybe_sweep_expired_checkpoint_artifacts()
    except Exception:
        # Artifact maintenance must never stop durable job dispatch.
        log_authored_generation_observation(
            name="artifact_sweep",
            outcome="failure",
        )
        logger.exception("Authored checkpoint artifact sweep failed")
    async with async_session_maker() as session:
        job_ids = list(
            await session.scalars(
                select(PresentationGenerationJob.id)
                .where(PresentationGenerationJob.workspace_operation_id.is_(None))
                .where(PresentationGenerationJob.state == "queued")
                .where(PresentationGenerationJob.export_cookie_required.is_(False))
                .order_by(PresentationGenerationJob.created_at)
                .limit(10)
            )
        )
    for job_id in job_ids:
        await launch_generation_job(job_id, None)


async def _dispatcher_loop() -> None:
    while True:
        try:
            await _dispatch_ready_jobs()
        except asyncio.CancelledError:
            raise
        except Exception:
            # A single malformed row or transient database failure must not
            # permanently kill legacy dispatch for unrelated unlinked jobs.
            logger.exception("Presentation generation dispatcher iteration failed")
        await asyncio.sleep(GENERATION_JOB_DISPATCH_SECONDS)


async def start_presentation_generation_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task is not None and not _dispatcher_task.done():
        return
    recovered = await recover_generation_jobs()
    if recovered:
        logger.info("Recovered %d durable presentation generation job(s)", recovered)
    _dispatcher_task = asyncio.create_task(_dispatcher_loop())


async def stop_presentation_generation_dispatcher() -> None:
    global _dispatcher_task
    if _dispatcher_task is not None:
        _dispatcher_task.cancel()
        await asyncio.gather(_dispatcher_task, return_exceptions=True)
        _dispatcher_task = None
    active = tuple(_active_jobs.values())
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    _active_jobs.clear()
