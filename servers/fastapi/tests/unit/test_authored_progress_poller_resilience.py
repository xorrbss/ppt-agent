"""A failed progress report must not freeze narration for the rest of a run.

A 100-page deck looked stalled while it was still generating because one
failed ``report(...)`` ended the poller. Failures are now retried on the next
tick; only a typed attempt-ended error or the owner's stopper ends the poll.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest

from services.authored_generation_progress_poller import (
    stream_authored_generation_progress,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled,
    WorkspaceOperationLeaseLost,
)

_LOGGER = "services.authored_generation_progress_poller"


class _Job:
    def __init__(self, completed: int, total: int):
        self.request_payload = {"template": "authored"}
        self.completed_slides = completed
        self.total_slides = total
        self.current_stage = "author"


class _Session:
    def __init__(self, job):
        self._job = job

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _model, _job_id):
        return self._job

    async def scalar(self, _statement):
        return self._job.completed_slides


async def _run(report, job, *, seconds: float) -> asyncio.Task:
    task = asyncio.create_task(
        stream_authored_generation_progress(
            job_id=uuid.uuid4(),
            report=report,
            session_factory=lambda: _Session(job),
            interval_seconds=0.01,
        )
    )
    await asyncio.sleep(seconds)
    return task


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_a_failed_report_is_retried_and_reporting_continues():
    attempts: list[dict] = []
    recorded: list[dict] = []

    async def report(**update):
        attempts.append(update)
        if len(attempts) == 1:
            raise RuntimeError("database is locked")
        recorded.append(update)
        return True

    async def scenario():
        job = _Job(1, 4)
        task = await _run(report, job, seconds=0.1)
        job.completed_slides = 3
        await asyncio.sleep(0.1)
        assert not task.done(), "a failed report must not end the poll"
        await _stop(task)

    asyncio.run(scenario())

    # The failed update is retried unchanged rather than skipped ...
    assert attempts[0] == attempts[1] == recorded[0]
    # ... and later changes keep arriving.
    assert recorded[-1]["current"] == 3
    percents = [update["percent"] for update in recorded]
    assert percents == sorted(set(percents))


def test_an_unrecorded_report_is_retried_not_final():
    attempts: list[dict] = []

    async def report(**update):
        attempts.append(update)
        # ``WorkspaceOperationExecutionContext.progress`` answers ``False`` for
        # a lost lease but also for a version race with another writer, so
        # ``False`` alone must not freeze narration.
        return len(attempts) > 1

    async def scenario():
        task = await _run(report, _Job(1, 4), seconds=0.1)
        assert not task.done(), "an unrecorded report must not end the poll"
        await _stop(task)

    asyncio.run(scenario())
    assert len(attempts) >= 2
    assert attempts[0] == attempts[1]


@pytest.mark.parametrize("error", [WorkspaceOperationLeaseLost, WorkspaceOperationCancelled])
def test_a_typed_attempt_ended_error_ends_the_poll(error):
    calls: list[dict] = []

    async def report(**update):
        calls.append(update)
        raise error("presentation_generation_lifecycle_fenced")

    async def scenario():
        task = await _run(report, _Job(1, 4), seconds=0.1)
        assert task.done()
        await task

    asyncio.run(scenario())
    assert len(calls) == 1


def test_a_failure_streak_warns_once_then_logs_quietly(caplog):
    outcomes = ["fail", "fail", "fail", "ok", "fail", "fail", "ok"]
    calls: list[str] = []

    async def report(**_update):
        outcome = outcomes[len(calls)] if len(calls) < len(outcomes) else "ok"
        calls.append(outcome)
        if outcome == "fail":
            raise RuntimeError("transient")
        return True

    async def scenario():
        job = _Job(1, 40)
        task = asyncio.create_task(
            stream_authored_generation_progress(
                job_id=uuid.uuid4(),
                report=report,
                session_factory=lambda: _Session(job),
                interval_seconds=0.005,
            )
        )
        for _ in range(400):
            await asyncio.sleep(0.01)
            if len(calls) > len(outcomes):
                break
        await _stop(task)

    with caplog.at_level(logging.DEBUG, logger=_LOGGER):
        asyncio.run(scenario())

    assert len(calls) > len(outcomes)
    failures = [
        record for record in caplog.records
        if record.name == _LOGGER and "report failed" in record.getMessage()
    ]
    assert [record.levelno for record in failures] == [
        logging.WARNING, logging.DEBUG, logging.DEBUG,
        logging.WARNING, logging.DEBUG,
    ]
