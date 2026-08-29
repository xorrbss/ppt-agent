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

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.presentation_generation_job import PresentationGenerationJob
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime

LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL_SECONDS = 3.0
# The band this poller owns. `generation_started` has already been reported at
# the floor, and publication reports completion at 100.
_PROGRESS_FLOOR = 5
_PROGRESS_CEILING = 95

# Each durable stage owns a monotonic band.  The poller may advance inside the
# active band as a heartbeat, but it never crosses into a stage that has not
# actually been persisted by the generation owner.
_STAGE_PROGRESS_BANDS: dict[str, tuple[int, int, str, str]] = {
    "outline": (8, 19, "authored_outline", "발표 구조와 작성 기준을 준비하고 있습니다."),
    "author": (20, 44, "authored_author", "슬라이드 내용을 작성하고 있습니다."),
    "illustrate": (45, 59, "authored_illustrate", "시각 요소를 구성하고 있습니다."),
    "render": (60, 74, "authored_render", "슬라이드를 렌더링하고 있습니다."),
    "persist": (75, 79, "authored_checkpoint", "슬라이드 체크포인트를 저장하고 있습니다."),
    "repair": (80, 84, "authored_repair", "문제가 있는 슬라이드만 복구하고 있습니다."),
    "checkpoint": (85, 88, "authored_checkpoint", "최종 품질 증거를 확인하고 있습니다."),
    "export": (89, 92, "authored_export", "PowerPoint 파일을 만들고 있습니다."),
    "publication": (93, 94, "authored_publication", "생성 결과를 게시하고 있습니다."),
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
        message: str | None = None,
    ) -> bool | None: ...


def describe_authored_progress(
    completed: int,
    total: int,
    current_stage: str | None,
) -> tuple[str, int, str]:
    """Map job counters to (stage, percent, message)."""

    normalized_stage = str(current_stage or "").strip().lower()
    band = _STAGE_PROGRESS_BANDS.get(normalized_stage)
    if band is not None:
        floor, ceiling, public_stage, message = band
        bounded_total = max(0, total)
        bounded_completed = max(0, min(bounded_total, completed))
        if bounded_total and normalized_stage in {
            "author",
            "illustrate",
            "render",
            "persist",
        }:
            percent = floor + int(
                round((ceiling - floor) * (bounded_completed / bounded_total))
            )
            message = f"{message} · {bounded_completed}/{bounded_total}장"
        else:
            percent = floor
        return public_stage, percent, message

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


def advance_authored_progress_heartbeat(
    update: tuple[str, int, str],
    previous: tuple[str, int, str] | None,
    current_stage: str | None,
) -> tuple[str, int, str]:
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

    normalized = str(stage).strip().lower()
    if normalized not in _STAGE_PROGRESS_BANDS:
        raise ValueError("invalid_authored_generation_stage")
    try:
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
            normalized,
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
            update = advance_authored_progress_heartbeat(
                update, last_reported, counters[2]
            )
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
