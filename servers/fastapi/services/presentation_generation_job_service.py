from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import logging
import secrets
from typing import Any
import uuid

from sqlalchemy import or_, select, update

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.database import async_session_maker

logger = logging.getLogger(__name__)

GENERATION_JOB_LEASE_SECONDS = 300
GENERATION_JOB_HEARTBEAT_SECONDS = 30
GENERATION_JOB_DISPATCH_SECONDS = 1
NONTERMINAL_STATES = frozenset({"queued", "running", "staging", "awaiting_resume"})
TERMINAL_STATES = frozenset({"published", "failed"})

_dispatcher_task: asyncio.Task[None] | None = None
_active_jobs: set[asyncio.Task[None]] = set()


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
    normalized = key.strip()
    if not normalized or len(normalized) > 255:
        raise ValueError("invalid_idempotency_key")
    return sha256(
        f"presenton.presentation-generation/v1:{normalized}".encode("utf-8")
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
        await session.commit()
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


async def recover_generation_jobs() -> int:
    """Recover abandoned attempts without persisting reusable auth credentials."""

    now = utc_now()
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(PresentationGenerationJob).where(
                    or_(
                        PresentationGenerationJob.state == "queued",
                        (
                            PresentationGenerationJob.state.in_(("running", "staging"))
                            & (
                                or_(
                                    PresentationGenerationJob.lease_expires_at.is_(None),
                                    PresentationGenerationJob.lease_expires_at < now,
                                )
                            )
                        ),
                    )
                )
            )
        ).scalars().all()
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
        return len(rows)


async def _dispatch_ready_jobs() -> None:
    from api.v1.ppt.endpoints.presentation_generate import run_generation_job

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
        task = asyncio.create_task(run_generation_job(job_id, None))
        _active_jobs.add(task)
        task.add_done_callback(_active_jobs.discard)


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
    active = tuple(_active_jobs)
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)
    _active_jobs.clear()
