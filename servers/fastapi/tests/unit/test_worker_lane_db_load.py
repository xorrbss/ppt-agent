"""The worker lane's database load: batching, backoff and the local stage sink.

Diagnosis (2026-09-05): an authored slide spent ~115-125 database round trips
on the workspace lane, each in its own session with its own commit, while the
four poll loops added ~10 000 more per 30-slide deck -- all of them contending
for the single SQLite writer lock.  These tests hold the reductions in place
and, more importantly, hold the guarantees that must survive them.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_checkpoint_html import (
    checkpoint_stage_html,
    collapses_to_author_html,
)
from services.workspace_poll_backoff import next_poll_delay
from utils.runtime_settings import replace_process_runtime_settings


def _set_knob(monkeypatch, key: str, value: str) -> None:
    monkeypatch.setenv(key, value)
    replace_process_runtime_settings(None)


# --------------------------------------------------------------------------
# Poll backoff
# --------------------------------------------------------------------------


def test_next_poll_delay_grows_from_base_to_ceiling_and_resets_on_activity():
    assert next_poll_delay(0, base=1.0, ceiling=5.0) == 1.0
    assert next_poll_delay(1, base=1.0, ceiling=5.0) == 2.0
    assert next_poll_delay(2, base=1.0, ceiling=5.0) == 4.0
    assert next_poll_delay(3, base=1.0, ceiling=5.0) == 5.0
    # A long idle period never exceeds the ceiling, and never overflows.
    assert next_poll_delay(10_000, base=3.0, ceiling=10.0) == 10.0
    # A ceiling below the base cannot shorten the loop below its own interval.
    assert next_poll_delay(4, base=3.0, ceiling=1.0) == 3.0


# --------------------------------------------------------------------------
# Cancellation latency under the heartbeat loop
# --------------------------------------------------------------------------


async def _cancellation_latency():
    """Deduping the cancel read against the heartbeat must not slow cancels."""

    from services import workspace_operation_lease as lease
    from services.workspace_operation_handler_registry import (
        WorkspaceOperationCancelled,
        WorkspaceOperationClaim,
    )

    claim = WorkspaceOperationClaim(
        operation_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        owner_scope="owner",
        conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        kind="presentation.generate",
        request_payload={},
        expected_revision=None,
        attempt_number=1,
        attempt_token="attempt",
        fencing_token=1,
    )
    cancelled_at_call = 3
    calls = {"cancel": 0, "heartbeat": 0}


    async def fake_cancel_requested(*_args, **_kwargs) -> bool:
        calls["cancel"] += 1
        return calls["cancel"] >= cancelled_at_call

    async def fake_heartbeat(*_args, **_kwargs) -> bool:
        calls["heartbeat"] += 1
        return True

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(lease, "operation_cancel_requested", fake_cancel_requested)
    monkeypatch.setattr(lease, "heartbeat_workspace_operation", fake_heartbeat)
    try:
        started = asyncio.get_running_loop().time()
        with pytest.raises(WorkspaceOperationCancelled):
            await lease.run_attempt_heartbeat_loop(
                claim,
                session_factory=None,
                # A production-shaped gap between the two cadences: the lease
                # is renewed every 30s, cancellation is polled every tick.
                heartbeat_seconds=30.0,
                poll_seconds=0.02,
                lease_seconds=lambda: 300,
            )
        elapsed = asyncio.get_running_loop().time() - started
    finally:
        monkeypatch.undo()
    return calls, elapsed, cancelled_at_call


def test_cancellation_is_observed_within_one_poll_while_running():
    calls, elapsed, expected = asyncio.run(_cancellation_latency())
    # Cancellation is seen on the third cancellation poll, before the lease
    # renewal is even due: skipping the cancel read for one interval after a
    # heartbeat must never make the loop wait for the next heartbeat.
    assert calls["cancel"] == expected
    assert calls["heartbeat"] == 0
    assert elapsed < 1.0


# --------------------------------------------------------------------------
# Local stage receipts
# --------------------------------------------------------------------------


async def _batched_local_stage_writes(monkeypatch):
    from services import local_stage_receipt_queue as queue

    batches: list[int] = []

    async def fake_write_batch(batch):
        batches.append(len(batch))

    monkeypatch.setattr(queue, "_write_batch", fake_write_batch)
    for index in range(5):
        assert queue.enqueue_local_stage_receipt({"index": index}) is True
    await queue.flush_local_stage_queue()
    return batches


def test_local_stage_receipts_are_written_in_batches_not_one_by_one(monkeypatch):
    batches = asyncio.run(_batched_local_stage_writes(monkeypatch))
    # One transaction for the five receipts, not five.
    assert sum(batches) == 5
    assert len(batches) == 1


async def _overflow_drops(monkeypatch):
    from services import local_stage_receipt_queue as queue

    async def never_drain(_state):
        await asyncio.Event().wait()

    async def fake_write_batch(_batch):
        return None

    monkeypatch.setattr(queue, "_drain_forever", never_drain)
    monkeypatch.setattr(queue, "_write_batch", fake_write_batch)
    before = queue.local_stage_queue_observability_snapshot()["dropped"]
    accepted = sum(
        1 for index in range(queue._MAX_QUEUE + 8)
        if queue.enqueue_local_stage_receipt({"index": index})
    )
    after = queue.local_stage_queue_observability_snapshot()["dropped"]
    await queue.flush_local_stage_queue()
    return accepted, after - before, queue._MAX_QUEUE


def test_local_stage_queue_drops_on_overflow_instead_of_blocking(monkeypatch):
    accepted, dropped, capacity = asyncio.run(_overflow_drops(monkeypatch))
    assert accepted == capacity
    assert dropped == 8


# --------------------------------------------------------------------------
# Checkpoint HTML accessor
# --------------------------------------------------------------------------


class _FakeCheckpoint:
    def __init__(self, **fields):
        self.author_html = None
        self.illustrated_html = None
        self.illustrate_status = "pending"
        self.render_status = "pending"
        for key, value in fields.items():
            setattr(self, key, value)


def test_checkpoint_html_accessor_restores_the_collapsed_copy(monkeypatch):
    stored = _FakeCheckpoint(
        author_html="<section>a</section>",
        illustrated_html="<section>b</section>",
        render_status="completed",
    )
    assert checkpoint_stage_html(stored) == "<section>b</section>"

    collapsed = _FakeCheckpoint(
        author_html="<section>a</section>",
        illustrated_html=None,
        render_status="completed",
        illustrate_status="skipped",
    )
    assert checkpoint_stage_html(collapsed) == "<section>a</section>"
    assert checkpoint_stage_html(collapsed, stage="illustrate") == "<section>a</section>"

    # A stage that has not completed still means "nothing written yet", which
    # is what keeps a re-author from being read back as a published render.
    unwritten = _FakeCheckpoint(author_html="<section>a</section>")
    assert checkpoint_stage_html(unwritten) is None


def test_html_collapse_requires_byte_equality_and_its_knob(monkeypatch):
    checkpoint = _FakeCheckpoint(author_html="<section>a</section>")
    _set_knob(monkeypatch, "AUTHORED_CHECKPOINT_HTML_DEDUPE_ENABLED", "false")
    assert collapses_to_author_html(checkpoint, "<section>a</section>") is False
    _set_knob(monkeypatch, "AUTHORED_CHECKPOINT_HTML_DEDUPE_ENABLED", "true")
    assert collapses_to_author_html(checkpoint, "<section>a</section>") is True
    assert collapses_to_author_html(checkpoint, "<section>a </section>") is False
    assert collapses_to_author_html(checkpoint, None) is False


# --------------------------------------------------------------------------
# Checkpoint batching: the measurement
# --------------------------------------------------------------------------


class _CountingSession(AsyncSession):
    stats: dict[str, int] = {}

    async def commit(self) -> None:
        _CountingSession.stats["commits"] = (
            _CountingSession.stats.get("commits", 0) + 1
        )
        await super().commit()


async def _running_job(maker):
    status = AsyncPresentationGenerationTaskModel(
        status="running", message="generating"
    )
    job = PresentationGenerationJob(
        task_id=status.id,
        presentation_id=uuid.uuid4(),
        request_sha256="a" * 64,
        state="running",
        attempt_token="attempt",
        request_payload={"template": "authored"},
    )
    async with maker() as session:
        session.add(status)
        await session.flush()
        job.task_id = status.id
        session.add(job)
        await session.commit()
    return job


# --------------------------------------------------------------------------
# The runner's idle probe
# --------------------------------------------------------------------------


async def _claim_cycle_decisions(pending: bool):
    from services.workspace_operation_runner import WorkspaceOperationRunner
    from services.workspace_poll_backoff import IdleCycle

    runner = WorkspaceOperationRunner(poll_seconds=1.0)
    probes = {"count": 0}

    async def fake_probe(**_kwargs) -> bool:
        probes["count"] += 1
        return pending

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "services.workspace_operation_runner.workspace_operation_work_pending",
        fake_probe,
    )
    try:
        # A fresh cycle is due immediately, and pays for the claim without
        # spending a probe.
        cycle = IdleCycle(1.0)
        due = await runner._claim_cycle_due(cycle)
        # After an empty cycle the sweep is pushed out and the read-only probe
        # decides every tick until it comes round again.
        cycle.record(0)
        between = await runner._claim_cycle_due(cycle)
    finally:
        monkeypatch.undo()
    return due, between, probes["count"]


def test_idle_runner_skips_the_write_locking_claim_until_a_read_finds_work():
    due, between, probes = asyncio.run(_claim_cycle_decisions(pending=False))
    assert due is True
    assert between is False
    # The due sweep must not spend a probe; only the in-between tick does.
    assert probes == 1


def test_runner_claims_on_the_next_tick_once_a_row_is_waiting():
    due, between, probes = asyncio.run(_claim_cycle_decisions(pending=True))
    assert due is True
    assert between is True
    assert probes == 1


# --------------------------------------------------------------------------
# The merged author stage still fences its provider ordinals
# --------------------------------------------------------------------------


