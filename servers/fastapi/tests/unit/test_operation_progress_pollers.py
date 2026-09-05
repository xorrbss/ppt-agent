"""Progress narration for the two operations that otherwise report nothing."""

import asyncio
import uuid

import pytest

from services.authored_generation_progress_poller import (
    describe_authored_progress,
    stream_authored_generation_progress,
)


def test_authored_progress_reports_slides_then_finalizing():
    _, zero_percent, zero_message = describe_authored_progress(0, 8, "author")
    _, mid_percent, mid_message = describe_authored_progress(4, 8, "author")
    final_stage, final_percent, final_message = describe_authored_progress(
        8, 8, "persist"
    )

    assert "0/8장" in zero_message and "4/8장" in mid_message
    assert zero_percent < mid_percent < final_percent
    assert final_stage == "authored_generation_finalizing"
    assert final_percent == 95
    assert "저장" in final_message


def test_authored_progress_tolerates_an_unknown_total():
    stage, percent, message = describe_authored_progress(0, 0, None)
    assert stage == "authored_generation"
    assert percent == 5
    assert message
    # A count beyond the total must never exceed the band.
    _, overshoot, _ = describe_authored_progress(99, 8, "author")
    assert overshoot == 95


class _StubJob:
    def __init__(
        self,
        template: str,
        completed: int,
        total: int,
        *,
        stage_completed: int | None = None,
    ):
        self.request_payload = {"template": template}
        self.completed_slides = completed
        self.total_slides = total
        self.current_stage = "author"
        self.stage_completed = stage_completed


class _StubSession:
    def __init__(self, job):
        self._job = job

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def get(self, _model, _job_id):
        return self._job

    async def scalar(self, _statement):
        if self._job.stage_completed is not None:
            return self._job.stage_completed
        return self._job.completed_slides


def test_authored_poller_defers_to_template_v2_self_reporting():
    reported: list[dict] = []

    async def report(**update):
        reported.append(update)
        return True

    async def scenario():
        job = _StubJob("template_v2", 3, 9)
        await asyncio.wait_for(
            stream_authored_generation_progress(
                job_id=uuid.uuid4(),
                report=report,
                session_factory=lambda: _StubSession(job),
                interval_seconds=0.01,
            ),
            timeout=2,
        )

    asyncio.run(scenario())

    # Template V2 reports its own slide progress; a second writer would fight it.
    assert reported == []


def test_authored_poller_reports_each_distinct_change_once():
    reported: list[dict] = []

    async def report(**update):
        reported.append(update)
        return True

    async def scenario():
        job = _StubJob("authored", 1, 4)
        task = asyncio.create_task(
            stream_authored_generation_progress(
                job_id=uuid.uuid4(),
                report=report,
                session_factory=lambda: _StubSession(job),
                interval_seconds=0.01,
            )
        )
        await asyncio.sleep(0.08)
        job.completed_slides = 2
        await asyncio.sleep(0.08)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    percents = [update["percent"] for update in reported]
    assert percents == sorted(percents)
    # An unchanged snapshot is polled repeatedly but reported only once.
    assert len(reported) == 2
    assert [update["current"] for update in reported] == [1, 2]


def test_authored_poller_counts_completed_author_checkpoints_before_persist():
    reported: list[dict] = []

    async def report(**update):
        reported.append(update)
        return True

    async def scenario():
        job = _StubJob("authored", 0, 35, stage_completed=9)
        task = asyncio.create_task(
            stream_authored_generation_progress(
                job_id=uuid.uuid4(),
                report=report,
                session_factory=lambda: _StubSession(job),
                interval_seconds=0.01,
            )
        )
        await asyncio.sleep(0.04)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert reported[0]["current"] == 9
    assert reported[0]["total"] == 35
    assert "9/35장" in reported[0]["message"]
