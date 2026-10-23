"""Narrate authored deck generation on the workspace timeline.

The authored pipeline has no seam that reports slide progress directly to the
reporter it is handed: it is a deep chain of stages, so the operation would
only report ``generation_started`` and then go silent for the whole authoring
run.

It does, however, already record per-slide stage completion durably —
``complete_stage`` keeps ``PresentationGenerationJob.completed_slides`` current.
This poller reads that existing record and translates it into the same
``operation.progress`` events every other operation emits, without threading a
reporter through the authoring stages.

Progress is advisory. Every failure here is swallowed: a poll that fails costs
the user narration, never the deck. A failed report is retried on a later tick
rather than ending the narration, because a deck whose progress silently froze
looks stalled while it is still generating; only a typed attempt-ended error
or the owner's stopper ends the poll.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Protocol

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from constants.user_messages import (
    UserMessage,
    UserMessageKey,
    UserMessageLike,
    user_message,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.authored_slide_checkpoint import AuthoredSlideCheckpoint
from services.database import async_session_maker
from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled,
    WorkspaceOperationLeaseLost,
)
from services.workspace_poll_backoff import next_poll_delay
from utils.datetime_utils import get_current_utc_datetime

LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3.0
# Nothing waits on narration, so an unchanging deck relaxes to this interval.
_IDLE_CEILING_SECONDS = 10.0
# The band this poller owns. `generation_started` has already been reported at
# the floor, and publication reports completion at 100.
_PROGRESS_FLOOR = 5
_PROGRESS_CEILING = 95

# Each durable stage owns a monotonic band.  The poller may advance inside the
# active band as a heartbeat, but it never crosses into a stage that has not
# actually been persisted by the generation owner.
_STAGE_PROGRESS_BANDS: dict[str, tuple[int, int, str, UserMessageKey]] = {
    "outline": (8, 19, "authored_outline", UserMessageKey.GENERATION_STAGE_OUTLINE),
    "author": (20, 44, "authored_author", UserMessageKey.GENERATION_STAGE_AUTHOR),
    "illustrate": (45, 59, "authored_illustrate", UserMessageKey.GENERATION_STAGE_ILLUSTRATE),
    "render": (60, 74, "authored_render", UserMessageKey.GENERATION_STAGE_RENDER),
    "persist": (75, 79, "authored_checkpoint", UserMessageKey.GENERATION_STAGE_PERSIST),
    "repair": (80, 84, "authored_repair", UserMessageKey.GENERATION_STAGE_REPAIR),
    "checkpoint": (85, 88, "authored_checkpoint", UserMessageKey.GENERATION_STAGE_CHECKPOINT),
    "export": (89, 92, "authored_export", UserMessageKey.GENERATION_STAGE_EXPORT),
    "publication": (93, 94, "authored_publication", UserMessageKey.GENERATION_STAGE_PUBLICATION),
}
# The typed signals for "this attempt no longer owns a running operation".
# ``WorkspaceOperationExecutionContext.progress`` returns ``False`` instead,
# which is ambiguous (see below); a reporter that raises uses these types.
_ATTEMPT_ENDED_ERRORS = (WorkspaceOperationLeaseLost, WorkspaceOperationCancelled)
# Stages whose message carries a slide counter (``completed``/``total``).
_COUNTED_STAGE_KEYS: dict[str, UserMessageKey] = {
    "author": UserMessageKey.GENERATION_STAGE_AUTHOR_COUNTED,
    "illustrate": UserMessageKey.GENERATION_STAGE_ILLUSTRATE_COUNTED,
    "render": UserMessageKey.GENERATION_STAGE_RENDER_COUNTED,
    "persist": UserMessageKey.GENERATION_STAGE_PERSIST_COUNTED,
}


class ProgressReporter(Protocol):
    """Matches ``WorkspaceOperationContext.progress``."""

    async def __call__(
        self,
        *,
        stage: str,
        percent: int | None = None,
        current: int | None = None,
        total: int | None = None,
        message: UserMessageLike = None,
    ) -> bool | None: ...


def describe_authored_progress(
    completed: int,
    total: int,
    current_stage: str | None,
) -> tuple[str, int, UserMessage]:
    """Map job counters to (stage, percent, message)."""

    normalized_stage = str(current_stage or "").strip().lower()
    band = _STAGE_PROGRESS_BANDS.get(normalized_stage)
    if band is not None:
        floor, ceiling, public_stage, key = band
        bounded_total = max(0, total)
        bounded_completed = max(0, min(bounded_total, completed))
        counted_key = _COUNTED_STAGE_KEYS.get(normalized_stage)
        if bounded_total and counted_key is not None:
            percent = floor + int(
                round((ceiling - floor) * (bounded_completed / bounded_total))
            )
            message = user_message(
                counted_key, completed=bounded_completed, total=bounded_total
            )
        else:
            percent = floor
            message = user_message(key)
        return public_stage, percent, message

    if total <= 0:
        return (
            "authored_generation",
            _PROGRESS_FLOOR,
            user_message(UserMessageKey.GENERATION_PREPARING_SLIDES),
        )
    bounded = max(0, min(total, completed))
    span = _PROGRESS_CEILING - _PROGRESS_FLOOR
    percent = _PROGRESS_FLOOR + int(round(span * (bounded / total)))
    if bounded >= total:
        return (
            "authored_generation_finalizing",
            _PROGRESS_CEILING,
            user_message(UserMessageKey.GENERATION_SAVING_SLIDES),
        )
    return (
        "authored_slide_generation",
        percent,
        user_message(
            UserMessageKey.GENERATION_WRITING_SLIDES_STAGED,
            completed=bounded,
            total=total,
            stage=current_stage,
        )
        if current_stage
        else user_message(
            UserMessageKey.GENERATION_WRITING_SLIDES, completed=bounded, total=total
        ),
    )


def advance_authored_progress_heartbeat(
    update: tuple[str, int, UserMessage],
    previous: tuple[str, int, UserMessage] | None,
    current_stage: str | None,
) -> tuple[str, int, UserMessage]:
    """Move a long-running stage within its own band without inventing stages."""

    normalized_stage = str(current_stage or "").strip().lower()
    band = _STAGE_PROGRESS_BANDS.get(normalized_stage)
    if band is None or previous is None or previous[0] != update[0]:
        return update
    ceiling = band[1]
    return update[0], min(ceiling, max(update[1], previous[1] + 1)), update[2]


async def mark_authored_generation_stage(
    *,
    job_id: uuid.UUID,
    attempt_token: str,
    stage: str,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    """Persist one fenced deck-wide stage for progress narration.

    This is advisory state: a lost fence or telemetry database error never
    changes the generation outcome.
    """

    try:
        normalized = str(stage).strip().lower()
        if normalized not in _STAGE_PROGRESS_BANDS:
            raise ValueError("invalid_authored_generation_stage")
        now = get_current_utc_datetime()
        async with session_factory() as session:
            result = await session.execute(
                update(PresentationGenerationJob)
                .where(
                    PresentationGenerationJob.id == job_id,
                    PresentationGenerationJob.attempt_token == attempt_token,
                    PresentationGenerationJob.state.in_(("running", "staging")),
                )
                .values(current_stage=normalized, updated_at=now)
            )
            await session.commit()
            return result.rowcount == 1
    except Exception:
        LOGGER.debug(
            "Authored stage update failed for job %s stage=%s",
            job_id,
            stage,
            exc_info=True,
        )
        return False


async def _read_job_counters(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
) -> tuple[int, int, str | None] | None:
    async with session_factory() as session:
        job = await session.get(PresentationGenerationJob, job_id)
        if job is None:
            return None
        if job.request_payload.get("template") != "authored":
            # Template V2 reports its own slide progress; a second writer here
            # would fight it and move the bar backwards.
            return None
        total = max(0, job.total_slides or 0)
        current_stage = str(job.current_stage or "").strip().lower()
        completed = max(0, job.completed_slides or 0)
        stage_status_field = {
            "author": AuthoredSlideCheckpoint.author_status,
            "illustrate": AuthoredSlideCheckpoint.illustrate_status,
            "render": AuthoredSlideCheckpoint.render_status,
            "persist": AuthoredSlideCheckpoint.persist_status,
        }.get(current_stage)
        if stage_status_field is not None:
            completed = int(
                await session.scalar(
                    select(func.count())
                    .select_from(AuthoredSlideCheckpoint)
                    .where(
                        AuthoredSlideCheckpoint.job_id == job_id,
                        stage_status_field == "completed",
                    )
                )
                or 0
            )
        return (max(0, min(total, completed)), total, job.current_stage)


async def stream_authored_generation_progress(
    *,
    job_id: uuid.UUID,
    report: ProgressReporter,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    interval_seconds: float = _POLL_INTERVAL_SECONDS,
) -> None:
    """Poll job counters until cancelled, reporting each distinct change."""

    last_reported: tuple[str, int, UserMessage] | None = None
    # Narration is advisory, so an unchanging deck must not keep paying for
    # reads that contend with the generation lane's own writes.  Any change
    # resets the interval to the base cadence.
    idle_ticks = 0
    # Consecutive failed reports; the first of a streak is logged loudly.
    report_failures = 0
    ceiling = max(interval_seconds, _IDLE_CEILING_SECONDS)
    try:
        while True:
            await asyncio.sleep(
                min(
                    ceiling,
                    next_poll_delay(
                        idle_ticks, base=interval_seconds, ceiling=ceiling
                    ),
                )
            )
            try:
                counters = await _read_job_counters(session_factory, job_id)
            except Exception:
                LOGGER.debug(
                    "Authored progress read failed for job %s", job_id, exc_info=True
                )
                idle_ticks += 1
                continue
            if counters is None:
                return
            update = describe_authored_progress(*counters)
            update = advance_authored_progress_heartbeat(
                update, last_reported, counters[2]
            )
            if update == last_reported:
                idle_ticks += 1
                continue
            stage, percent, message = update
            completed, total, _ = counters
            try:
                recorded = await report(
                    stage=stage,
                    percent=percent,
                    current=completed if total else None,
                    total=total or None,
                    message=message,
                )
            except asyncio.CancelledError:
                raise
            except _ATTEMPT_ENDED_ERRORS:
                # A lost lease is the runner's to surface; nothing further
                # this attempt reports can land.
                return
            except Exception:
                # Progress must never be the thing that fails a generation, nor
                # may one failed write freeze the narration for the rest of the
                # run.  ``last_reported`` is left unchanged so the next tick
                # retries this update, and the idle tick lets backoff apply.
                report_failures += 1
                LOGGER.log(
                    logging.WARNING if report_failures == 1 else logging.DEBUG,
                    "Authored progress report failed for job %s (consecutive=%d)",
                    job_id,
                    report_failures,
                    exc_info=True,
                )
                idle_ticks += 1
                continue
            if recorded is False:
                # ``False`` covers a lost lease but also a lost version race
                # with another writer of the same operation, so it cannot end
                # the narration: retry later.  A truly ended attempt cancels
                # this poll through its owner's stopper.
                idle_ticks += 1
                continue
            report_failures = 0
            idle_ticks = 0
            last_reported = update
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.debug(
            "Authored progress polling stopped for job %s", job_id, exc_info=True
        )


def start_authored_generation_progress_polling(
    *,
    job_id: uuid.UUID,
    report: ProgressReporter,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> Callable[[], Awaitable[None]]:
    """Start polling and return an idempotent stopper for the caller's finally."""

    task = asyncio.create_task(
        stream_authored_generation_progress(
            job_id=job_id,
            report=report,
            session_factory=session_factory,
        )
    )

    async def stop() -> None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    return stop
