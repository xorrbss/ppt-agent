"""Narrate authored deck generation on the workspace timeline.

Template V2 generation reports slide progress directly through the reporter it
is handed (``_generate_template_v2_presentation``). The authored pipeline has no
such seam: it is a deep chain of stages, so the operation would only report
``generation_started`` and then go silent for the whole authoring run.

It does, however, already record per-slide stage completion durably —
``complete_stage`` keeps ``PresentationGenerationJob.completed_slides`` current.
This poller reads that existing record and translates it into the same
``operation.progress`` events every other operation emits, without threading a
reporter through the authoring stages.

Progress is advisory. Every failure here is swallowed: a poll that fails costs
the user narration, never the deck.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.presentation_generation_job import PresentationGenerationJob
from services.database import async_session_maker

LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3.0
# The band this poller owns. `generation_started` has already been reported at
# the floor, and publication reports completion at 100.
_PROGRESS_FLOOR = 5
_PROGRESS_CEILING = 95


class ProgressReporter(Protocol):
    """Matches ``WorkspaceOperationContext.progress``."""

    async def __call__(
        self,
        *,
        stage: str,
        percent: int | None = None,
        current: int | None = None,
        total: int | None = None,
        message: str | None = None,
    ) -> bool | None: ...


def describe_authored_progress(
    completed: int,
    total: int,
    current_stage: str | None,
) -> tuple[str, int, str]:
    """Map job counters to (stage, percent, message)."""

    if total <= 0:
        return (
            "authored_generation",
            _PROGRESS_FLOOR,
            "슬라이드를 준비하고 있습니다.",
        )
    bounded = max(0, min(total, completed))
    span = _PROGRESS_CEILING - _PROGRESS_FLOOR
    percent = _PROGRESS_FLOOR + int(round(span * (bounded / total)))
    if bounded >= total:
        return (
            "authored_generation_finalizing",
            _PROGRESS_CEILING,
            "생성한 슬라이드를 저장하고 있습니다.",
        )
    stage_suffix = f" · {current_stage}" if current_stage else ""
    return (
        "authored_slide_generation",
        percent,
        f"슬라이드를 작성하고 있습니다 · {bounded}/{total}장{stage_suffix}",
    )


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
        return (
            max(0, job.completed_slides or 0),
            max(0, job.total_slides or 0),
            job.current_stage,
        )


async def stream_authored_generation_progress(
    *,
    job_id: uuid.UUID,
    report: ProgressReporter,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    interval_seconds: float = _POLL_INTERVAL_SECONDS,
) -> None:
    """Poll job counters until cancelled, reporting each distinct change."""

    last_reported: tuple[str, int, str] | None = None
    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                counters = await _read_job_counters(session_factory, job_id)
            except Exception:
                LOGGER.debug(
                    "Authored progress read failed for job %s", job_id, exc_info=True
                )
                continue
            if counters is None:
                return
            update = describe_authored_progress(*counters)
            if update == last_reported:
                continue
            last_reported = update
            stage, percent, message = update
            completed, total, _ = counters
            try:
                await report(
                    stage=stage,
                    percent=percent,
                    current=completed if total else None,
                    total=total or None,
                    message=message,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A lost lease is the runner's to surface; progress must never
                # be the thing that fails a generation.
                LOGGER.debug(
                    "Authored progress report failed for job %s", job_id, exc_info=True
                )
                return
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
