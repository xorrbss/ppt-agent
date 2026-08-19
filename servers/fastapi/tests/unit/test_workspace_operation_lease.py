"""Attempt liveness under a write lock held by the attempt's own handler.

A handler that opens a long write transaction locks every other session out of
a single-writer database, its own heartbeat included.  These tests pin the two
properties that keep such an attempt alive: a locked-out renewal is not
evidence of a lost lease, and a lease widened before the lock is not narrowed
back by the renewals that follow.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.exc import OperationalError

from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled,
    WorkspaceOperationClaim,
    WorkspaceOperationLeaseLost,
)
from services import workspace_operation_lease as lease_module
from services.workspace_operation_lease import (
    WORKSPACE_OPERATION_MAX_LEASE_SECONDS,
    extend_workspace_operation_lease,
    run_attempt_heartbeat_loop,
)


def _claim() -> WorkspaceOperationClaim:
    return WorkspaceOperationClaim(
        operation_id=uuid.uuid4(),
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        kind="slide.regenerate.batch",
        request_payload={},
        expected_revision=1,
        attempt_number=1,
        attempt_token="attempt",
        fencing_token=1,
    )


def _locked() -> OperationalError:
    return OperationalError("UPDATE workspace_operations", {}, Exception("database is locked"))


def _cancel_after(count: int, calls: list[int]):
    async def probe(operation_id, attempt_token, *, session_factory):
        calls.append(1)
        return len(calls) > count

    return probe


def test_a_locked_out_heartbeat_keeps_the_attempt(monkeypatch):
    """The handler holds the write lock; that is not a lost lease."""

    attempts: list[int] = []
    probes: list[int] = []

    async def heartbeat(*_args, **_kwargs):
        attempts.append(1)
        raise _locked()

    monkeypatch.setattr(lease_module, "heartbeat_workspace_operation", heartbeat)
    monkeypatch.setattr(
        lease_module, "operation_cancel_requested", _cancel_after(12, probes)
    )

    async def scenario():
        with pytest.raises(WorkspaceOperationCancelled):
            await run_attempt_heartbeat_loop(
                _claim(),
                session_factory=object(),
                heartbeat_seconds=0.01,
                poll_seconds=0.01,
                lease_seconds=lambda: 300,
            )

    asyncio.run(scenario())

    # The loop survived repeated lock timeouts and only ended on cancellation.
    assert attempts, "the heartbeat was never attempted"


def test_a_rejected_heartbeat_still_loses_the_lease(monkeypatch):
    """A committed `alive=False` is real evidence: the attempt was reclaimed."""

    probes: list[int] = []

    async def heartbeat(*_args, **_kwargs):
        return False

    monkeypatch.setattr(lease_module, "heartbeat_workspace_operation", heartbeat)
    monkeypatch.setattr(
        lease_module, "operation_cancel_requested", _cancel_after(1000, probes)
    )

    async def scenario():
        with pytest.raises(WorkspaceOperationLeaseLost):
            await run_attempt_heartbeat_loop(
                _claim(),
                session_factory=object(),
                heartbeat_seconds=0.01,
                poll_seconds=0.01,
                lease_seconds=lambda: 300,
            )

    asyncio.run(scenario())


def test_renewals_read_the_widened_lease_instead_of_narrowing_it(monkeypatch):
    """A mid-flight extension must survive the next renewal."""

    granted: list[int] = []
    probes: list[int] = []
    window = [300]

    async def heartbeat(_operation_id, _attempt_token, *, session_factory, lease_seconds):
        granted.append(lease_seconds)
        if len(granted) == 1:
            window[0] = 1500
        return True

    monkeypatch.setattr(lease_module, "heartbeat_workspace_operation", heartbeat)
    monkeypatch.setattr(
        lease_module, "operation_cancel_requested", _cancel_after(30, probes)
    )

    async def scenario():
        with pytest.raises(WorkspaceOperationCancelled):
            await run_attempt_heartbeat_loop(
                _claim(),
                session_factory=object(),
                heartbeat_seconds=0.01,
                poll_seconds=0.01,
                lease_seconds=lambda: window[0],
            )

    asyncio.run(scenario())

    assert granted[0] == 300
    assert granted[-1] == 1500


def test_the_extension_is_clamped_between_the_base_and_the_ceiling(monkeypatch):
    granted: list[int] = []

    async def heartbeat(_operation_id, _attempt_token, *, session_factory, lease_seconds):
        granted.append(lease_seconds)
        return True

    monkeypatch.setattr(lease_module, "heartbeat_workspace_operation", heartbeat)

    async def scenario():
        # A window narrower than the configured lease never shortens it.
        assert (
            await extend_workspace_operation_lease(
                uuid.uuid4(),
                "attempt",
                session_factory=object(),
                lease_seconds=10,
                minimum_seconds=300,
            )
            == 300
        )
        # No handler may make a crashed attempt unrecoverable for a day.
        assert (
            await extend_workspace_operation_lease(
                uuid.uuid4(),
                "attempt",
                session_factory=object(),
                lease_seconds=10**9,
                minimum_seconds=300,
            )
            == WORKSPACE_OPERATION_MAX_LEASE_SECONDS
        )

    asyncio.run(scenario())
    assert granted == [300, WORKSPACE_OPERATION_MAX_LEASE_SECONDS]


@pytest.mark.parametrize("outcome", ["locked", "reclaimed"])
def test_a_refused_extension_is_reported_to_the_handler(monkeypatch, outcome):
    async def heartbeat(*_args, **_kwargs):
        if outcome == "locked":
            raise _locked()
        return False

    monkeypatch.setattr(lease_module, "heartbeat_workspace_operation", heartbeat)

    async def scenario():
        assert (
            await extend_workspace_operation_lease(
                uuid.uuid4(),
                "attempt",
                session_factory=object(),
                lease_seconds=1500,
            )
            is None
        )

    asyncio.run(scenario())
