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

from sqlalchemy import or_, select, text, update

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
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


async def heartbeat_generation_job(job_id: uuid.UUID, token: str) -> bool:
    now = utc_now()
    async with async_session_maker() as session:
        result = await session.execute(
            update(PresentationGenerationJob)
            .where(PresentationGenerationJob.id == job_id)
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
                select(PresentationGenerationJob).where(reclaimable)
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
                .where(PresentationGenerationJob.state == "queued")
                .where(PresentationGenerationJob.export_cookie_required.is_(False))
                .order_by(PresentationGenerationJob.created_at)
                .limit(10)
            )
        )
    for job_id in job_ids:
        await launch_generation_job(job_id, None)


async def _dispatcher_loop() -> None:
    try:
        while True:
            await _dispatch_ready_jobs()
            await asyncio.sleep(GENERATION_JOB_DISPATCH_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("Presentation generation dispatcher stopped unexpectedly")


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
