"""Every attempt records its queue wait and its execution time separately."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import update

import test_workspace_operation_runner as runner_test
from models.sql.workspace import WorkspaceOperation
from services.workspace_attempt_timing import sanitize_attempt_timing
from services.workspace_operation_handler_registry import (
    RetryableWorkspaceOperationError,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from utils.datetime_utils import as_utc, get_current_utc_datetime


def _at(value: str) -> datetime:
    return as_utc(datetime.fromisoformat(value))


def test_failed_then_succeeded_attempts_carry_their_own_timing(tmp_path: Path) -> None:
    async def scenario() -> None:
        engine, maker = await runner_test._database(tmp_path / "timing.db")
        try:
            _, (operation_id,) = await runner_test._seed_operations(maker)
            calls: list[int] = []

            async def handler(context):
                calls.append(context.claim.attempt_number)
                await asyncio.sleep(0.05)
                if len(calls) == 1:
                    raise RetryableWorkspaceOperationError("transient_failure")
                return WorkspaceOperationResult(payload={"status": "completed"})

            registry = WorkspaceOperationHandlerRegistry()
            registry.register("test.operation", handler)
            runner = WorkspaceOperationRunner(
                session_factory=maker, registry=registry, max_concurrency=1, heartbeat_seconds=60,
            )
            assert await runner_test._drain_once(runner) == 1
            failed = await runner_test._operation(maker, operation_id)
            assert failed.state == "queued"
            first = failed.error_detail["attempt_timing"]
            created_at = as_utc(failed.created_at)

            # Skip the retry backoff so the second attempt is claimed now.
            await asyncio.sleep(0.05)
            async with maker() as session, session.begin():
                await session.execute(
                    update(WorkspaceOperation)
                    .where(WorkspaceOperation.id == operation_id)
                    .values(next_attempt_at=get_current_utc_datetime() - timedelta(seconds=1))
                    .execution_options(skip_owner_scope=True)
                )
            assert await runner_test._drain_once(runner) == 1
            succeeded = await runner_test._operation(maker, operation_id)
            assert succeeded.state == "succeeded"
            second = succeeded.result["attempt_timing"]
            assert succeeded.result["status"] == "completed"  # other keys unchanged

            # The failed attempt's timing is also durable on its error event.
            events = await runner_test._events(maker, operation_id)
            error_events = [event for event in events if event.event_type == "operation.error"]
            assert [event.payload["attempt_timing"] for event in error_events] == [first]
        finally:
            await engine.dispose()

        assert calls == [1, 2]
        for number, timing in ((1, first), (2, second)):
            assert timing["attempt_number"] == number
            assert _at(timing["queued_at"]) == created_at  # anchored on operation.created_at
            claimed, finished = _at(timing["attempt_claimed_at"]), _at(timing["attempt_finished_at"])
            assert timing["queue_wait_seconds"] == round((claimed - created_at).total_seconds(), 3)
            assert timing["execution_seconds"] == round((finished - claimed).total_seconds(), 3)
            assert timing["execution_seconds"] >= 0.05
        # Attempt 2 waited through attempt 1 and its backoff; it did not re-run it.
        assert _at(second["attempt_claimed_at"]) > _at(first["attempt_finished_at"])
        assert second["queue_wait_seconds"] > first["queue_wait_seconds"] + first["execution_seconds"]

    asyncio.run(scenario())


def test_sanitized_timing_keeps_only_known_scalar_fields() -> None:
    assert sanitize_attempt_timing({
        "attempt_number": 2,
        "queued_at": "2026-09-25T00:00:00+00:00",
        "attempt_claimed_at": {"nested": True},
        "attempt_finished_at": "x" * 200,
        "queue_wait_seconds": float("nan"),
        "execution_seconds": True,
        "raw_html": "<p>secret</p>",
    }) == {
        "attempt_number": 2,
        "queued_at": "2026-09-25T00:00:00+00:00",
        "attempt_claimed_at": None,
        "attempt_finished_at": "x" * 40,
        "queue_wait_seconds": None,
        "execution_seconds": None,
    }
