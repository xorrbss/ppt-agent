"""Progress narration for authored generation, which otherwise reports nothing.

The poller narrates the durable ``current_stage`` of the generation job: each
stage owns a monotonic percent band (``_STAGE_PROGRESS_BANDS``) and the poller
heartbeats inside the active band without crossing into a stage that has not
been persisted.  The pre-band ``authored_slide_generation`` /
``authored_generation_finalizing`` narration survives only for jobs that carry
no durable stage.
"""

import asyncio
import uuid

import pytest

from services.authored_generation_progress_poller import (
    describe_authored_progress,
    stream_authored_generation_progress,
)


def test_authored_progress_walks_the_persisted_stage_bands():
    author_stage, zero_percent, zero_message = describe_authored_progress(
        0, 8, "author"
    )
    _, mid_percent, mid_message = describe_authored_progress(4, 8, "author")
    persist_stage, persist_percent, persist_message = describe_authored_progress(
        8, 8, "persist"
    )
    checkpoint_stage, checkpoint_percent, _ = describe_authored_progress(
        8, 8, "checkpoint"
    )
    export_stage, export_percent, _ = describe_authored_progress(8, 8, "export")
    publication_stage, publication_percent, _ = describe_authored_progress(
        8, 8, "publication"
    )

    assert author_stage == "authored_author"
    assert "0/8장" in zero_message and "4/8장" in mid_message
    assert zero_percent == 20 and mid_percent == 32
    # A stage that finished every slide sits at its own band ceiling, never in
    # a band the generation owner has not persisted yet.
    assert persist_stage == "authored_checkpoint"
    assert persist_percent == 79
    assert "저장" in persist_message
    assert checkpoint_stage == "authored_checkpoint"
    assert export_stage == "authored_export"
    assert publication_stage == "authored_publication"
    assert (
        zero_percent
        < mid_percent
        < persist_percent
        < checkpoint_percent
        < export_percent
        < publication_percent
        < 95
    )


def test_authored_progress_keeps_the_stageless_narration_for_legacy_jobs():
    # A job that never persisted a durable stage still narrates slide counts
    # and the finalizing hand-off the lifecycle map continues to accept.
    stage, percent, message = describe_authored_progress(4, 8, None)
    assert stage == "authored_slide_generation"
    assert 5 < percent < 95
    assert "4/8장" in message

    final_stage, final_percent, final_message = describe_authored_progress(
        8, 8, None
    )
    assert final_stage == "authored_generation_finalizing"
    assert final_percent == 95
    assert "저장" in final_message


def test_authored_progress_tolerates_an_unknown_total():
    stage, percent, message = describe_authored_progress(0, 0, None)
    assert stage == "authored_generation"
    assert percent == 5
    assert message
    # A stage with no per-slide counter yet still narrates from its band floor.
    outline_stage, outline_percent, _ = describe_authored_progress(0, 0, "outline")
    assert (outline_stage, outline_percent) == ("authored_outline", 8)
    # A count beyond the total must never exceed the active stage's band.
    _, overshoot, overshoot_message = describe_authored_progress(99, 8, "author")
    assert overshoot == 44
    assert "8/8장" in overshoot_message
    _, legacy_overshoot, _ = describe_authored_progress(99, 8, None)
    assert legacy_overshoot == 95


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


def test_authored_poller_heartbeats_inside_the_active_band():
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

    assert reported, "an unchanged deck still heartbeats inside its band"
    percents = [update["percent"] for update in reported]
    # Every heartbeat is a distinct, strictly increasing step ...
    assert percents == sorted(set(percents))
    # ... that never leaves the author band or invents a later stage.
    assert {update["stage"] for update in reported} == {"authored_author"}
    assert 20 <= percents[0] and percents[-1] <= 44
    currents = [update["current"] for update in reported]
    assert currents == sorted(currents)
    assert currents[0] == 1 and currents[-1] == 2
    assert reported[-1]["percent"] >= 34


def test_authored_poller_reports_each_distinct_change_once():
    reported: list[dict] = []

    async def report(**update):
        reported.append(update)
        return True

    async def scenario():
        # Every slide authored: the band ceiling leaves the heartbeat nothing
        # to advance, so repeated polls must not re-report the same snapshot.
        job = _StubJob("authored", 4, 4)
        task = asyncio.create_task(
            stream_authored_generation_progress(
                job_id=uuid.uuid4(),
                report=report,
                session_factory=lambda: _StubSession(job),
                interval_seconds=0.01,
            )
        )
        await asyncio.sleep(0.1)
        job.current_stage = "illustrate"
        await asyncio.sleep(0.3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    # One report per distinct snapshot: the author ceiling, then the
    # illustrate ceiling once the owner persisted that stage.
    assert [(update["stage"], update["percent"]) for update in reported] == [
        ("authored_author", 44),
        ("authored_illustrate", 59),
    ]
    assert [update["current"] for update in reported] == [4, 4]


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
