"""Generation checkpoint rows that every generation driver writes.

``initialize_checkpoints``, ``fail_stage`` and ``progress_for_task`` touch only
the checkpoint and job rows. They moved here unchanged from the former
``authored_checkpoint_state`` (removed with authored generation) so svg
generation records page failures and reads progress without importing the
authored HTML admission chain.
"""

from __future__ import annotations

from typing import Any
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.workspace import WorkspaceOperation
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_checkpoint_fence import (
    checkpoint_mutation_session as _checkpoint_mutation_session,
    completed_count as _completed_count,
    load_checkpoint as _checkpoint,
    owned_job as _owned_job,
)
from services.authored_generation_observability import log_authored_slide_failure_observation
from services.database import async_session_maker
from services.workspace_public_generation_lifecycle import monotonic_svg_job_stage
from utils.datetime_utils import get_current_utc_datetime


STAGES = ("author", "illustrate", "render", "persist")
_STATUS_FIELD = {stage: f"{stage}_status" for stage in STAGES}


async def initialize_checkpoints(
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    roles: list[str],
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> None:
    now = get_current_utc_datetime()
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        existing = set(
            await mutation_session.scalars(
                select(AuthoredSlideCheckpoint.slide_index).where(
                    AuthoredSlideCheckpoint.job_id == job_id
                )
            )
        )
        for index, role in enumerate(roles):
            if index not in existing:
                mutation_session.add(
                    AuthoredSlideCheckpoint(
                        job_id=job_id,
                        slide_index=index,
                        role=role,
                    )
                )
        job.total_slides = len(roles)
        job.completed_slides = await _completed_count(mutation_session, job_id)
        job.current_stage = "author"
        job.updated_at = now
        mutation_session.add(job)
        if owns_session:
            await mutation_session.commit()


async def fail_stage(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    error: AuthoredStageError,
    request_id: str | None,
    fencing_token: int | None = None,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    _require_stage(stage)
    async with _checkpoint_mutation_session(session) as (mutation_session, owns_session):
        job = await _owned_job(
            mutation_session, job_id, attempt_token, fencing_token=fencing_token
        )
        safe = error.as_safe_dict(
            request_id=request_id, attempt_number=job.attempt_number
        )
        checkpoint = await _checkpoint(mutation_session, job_id, slide_index)
        setattr(checkpoint, _STATUS_FIELD[stage], "failed")
        checkpoint.failed_stage = stage
        checkpoint.error = safe
        checkpoint.failed_at = get_current_utc_datetime()
        checkpoint.attempt_token = attempt_token
        checkpoint.updated_at = get_current_utc_datetime()
        if (job.request_payload or {}).get("strategy") == "svg":
            # Siblings may still render after this page fails. Read the latest
            # deck stage under the owned_job lock before projecting the failure.
            await mutation_session.refresh(job, attribute_names=["current_stage"])
            job.current_stage = monotonic_svg_job_stage(job.current_stage, stage)
        else:
            job.current_stage = stage
        job.resume_allowed = error.retryable
        job.last_error = safe
        job.completed_slides = await _completed_count(mutation_session, job_id)
        job.updated_at = checkpoint.updated_at
        mutation_session.add_all((checkpoint, job))
        if owns_session:
            await mutation_session.commit()
    log_authored_slide_failure_observation(
        stage=stage,
        code=error.code,
        slide_number=slide_index + 1,
        retryable=error.retryable,
        provider_attempt_count=error.provider_attempt_count,
    )
    return safe


async def fail_stage_committed(
    *,
    job_id: uuid.UUID,
    slide_index: int,
    stage: str,
    attempt_token: str,
    error: AuthoredStageError,
    request_id: str | None,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    """``fail_stage`` in its own committed transaction.

    The same write ``authored_checkpoint_service.fail_stage`` commits, without
    importing that facade.
    """

    async with async_session_maker() as session:
        result = await fail_stage(
            job_id=job_id,
            attempt_token=attempt_token,
            fencing_token=fencing_token,
            session=session,
            slide_index=slide_index,
            stage=stage,
            error=error,
            request_id=request_id,
        )
        await session.commit()
        return result


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
    if job is None:
        return None, None
    if job.request_payload.get("template") != "authored":
        if job.workspace_operation_id is None:
            return None, job.last_error
        operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
        raw = operation.progress if operation is not None else None
        if not isinstance(raw, dict) or not raw:
            return None, job.last_error

        def safe_count(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        total = safe_count(raw.get("total"))
        completed = min(total, safe_count(raw.get("current"))) if total else 0
        stage = str(raw.get("stage") or "generation_started")
        try:
            percent = max(0, min(100, int(raw.get("percent") or 0)))
        except (TypeError, ValueError):
            percent = 0
        published = job.state == "published"
        if published:
            completed = total
            percent = 100
            stage = "completed"
        return (
            {
                "completed_slides": completed,
                "total_slides": total,
                "percent": percent,
                "current_slide": (
                    completed + 1
                    if total and completed < total and stage.endswith("slide_generation")
                    else None
                ),
                "current_stage": stage,
                "stage_counts": {},
                "finalizing": bool(total and completed == total and not published),
            },
            job.last_error,
        )
    if not job.total_slides:
        return None, job.last_error
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


def _require_stage(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError("invalid_authored_stage")
