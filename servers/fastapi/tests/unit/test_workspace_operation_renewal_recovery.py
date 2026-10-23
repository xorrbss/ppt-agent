"""A delayed handler can renew only while its original fence still owns the row."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql, sqlite

from models.sql.workspace import WorkspaceOperation
from services import workspace_operation_heartbeat_thread as threaded
from services import workspace_operation_lease as lease
from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled,
    WorkspaceOperationLeaseLost,
)
from tests.unit.test_workspace_operation_lease import _claim
from tests.unit.test_workspace_operation_liveness import Clock, running_operations
from tests.unit.test_workspace_operation_runner import _database, _operation
from utils.datetime_utils import as_utc


@pytest.mark.parametrize("resumed_alive", [True, False, None])
def test_missing_ticks_keep_cancel_polling_and_resume_uses_renewal_cas(monkeypatch, resumed_alive):
    clock = Clock()
    monkeypatch.setattr(threaded, "monotonic", lambda: clock.tick)
    watchdog = threaded.HandlerLoopWatchdog()
    clock.advance(threaded.HANDLER_LIVENESS_SECONDS)
    polls = []
    renewals = []

    async def sleep(seconds):
        assert seconds > 0, "paused renewal must not busy-loop"
        clock.advance(seconds)

    async def cancel(*args, **kwargs):
        polls.append(clock.tick)
        if len(polls) == 3:
            if resumed_alive is None:
                return True
            watchdog.tick()
        return len(polls) > 3

    async def renew(*args, **kwargs):
        renewals.append(clock.tick)
        return resumed_alive

    async def scenario():
        monkeypatch.setattr(lease.asyncio, "get_running_loop", lambda: SimpleNamespace(time=lambda: clock.tick))
        monkeypatch.setattr(lease.asyncio, "sleep", sleep)
        monkeypatch.setattr(lease, "operation_cancel_requested", cancel)
        monkeypatch.setattr(lease, "heartbeat_workspace_operation", renew)
        error = WorkspaceOperationLeaseLost if resumed_alive is False else WorkspaceOperationCancelled
        with pytest.raises(error):
            await lease.run_attempt_heartbeat_loop(
                _claim(), session_factory=object(), heartbeat_seconds=1,
                poll_seconds=1, lease_seconds=lambda: 300,
                renewal_allowed=lambda: watchdog.remaining() > 0,
            )

    asyncio.run(scenario())
    assert len(polls) == (4 if resumed_alive else 3)
    assert len(renewals) == (0 if resumed_alive is None else 1)
    if renewals:
        assert renewals[0] == polls[2]


@pytest.mark.parametrize("deadline_seconds", [None, 1200, 2400])
def test_stale_heartbeat_window_cannot_shorten_a_committed_extension(
    tmp_path, monkeypatch, deadline_seconds,
):
    clock = Clock()
    clock.install(monkeypatch)

    async def scenario():
        engine, maker = await _database(tmp_path / "extension.db")
        try:
            ids, _ = await running_operations(maker, clock)
            deadline = clock.wall + timedelta(seconds=deadline_seconds) if deadline_seconds else None
            contract = ({
                "contract_version": "public-operation/v1",
                "execution_profile_version": "public-generation-export/v1",
                "phase": "active",
            } if deadline is not None else {})
            async with maker() as session:
                await session.execute(update(WorkspaceOperation).where(
                    WorkspaceOperation.id == ids[0],
                ).values(deadline_at=deadline, created_at=clock.wall, **contract))
                await session.commit()
            stale_window = 300
            assert await lease.extend_workspace_operation_lease(
                ids[0], "attempt", fencing_token=1, session_factory=maker, lease_seconds=1800,
            ) == 1800
            expected = clock.wall + timedelta(seconds=min(1800, deadline_seconds or 1800))
            clock.advance(1)
            assert await lease.heartbeat_workspace_operation(
                ids[0], "attempt", fencing_token=1, session_factory=maker, lease_seconds=stale_window,
            )
            operation = await _operation(maker, ids[0])
            assert as_utc(operation.lease_expires_at) == expected
            assert as_utc(operation.heartbeat_at) == clock.wall
            assert not await lease.heartbeat_workspace_operation(
                ids[0], "attempt", fencing_token=2, session_factory=maker, lease_seconds=2400,
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("dialect", [sqlite.dialect(), postgresql.dialect()])
def test_lease_maximum_uses_portable_sql(dialect):
    expression = lease._renewed_lease_expiry(
        WorkspaceOperation.lease_expires_at, WorkspaceOperation.deadline_at, Clock().wall,
    )
    statement = str(select(expression).compile(dialect=dialect)).lower()
    assert "case when" in statement
    assert "greatest(" not in statement
    assert "max(" not in statement
