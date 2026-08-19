"""Attempt liveness for workspace operations: heartbeat, lease, cancel poll.

A worker proves it is still executing an attempt by renewing a lease from its
own session.  On a single-writer backend that proof is not always available:
SQLite grants one write transaction at a time, so a handler that opens a long
transaction (a deck-wide batch publish, a quality review) holds the write lock
until it commits, and every other session -- including this heartbeat -- blocks
until ``busy_timeout`` elapses and then fails with ``database is locked``.

Two rules follow, and this module owns both:

* A heartbeat write that could not acquire the lock proves nothing about the
  attempt.  It must not be reported as a lost lease, or a healthy attempt is
  reclaimed mid-transaction and restarted from the beginning, forever.
* Because no heartbeat can commit while that lock is held, a handler that is
  about to take it widens the lease *first*, to a window that covers the whole
  transaction.  Fencing still protects correctness: a reclaimed attempt loses
  its token and can no longer publish.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.workspace import WorkspaceOperation
from services.database import async_session_maker
from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled,
    WorkspaceOperationClaim,
    WorkspaceOperationLeaseLost,
)
from utils.datetime_utils import get_current_utc_datetime

LOGGER = logging.getLogger(__name__)

WORKSPACE_OPERATION_LEASE_SECONDS = 300
WORKSPACE_OPERATION_HEARTBEAT_SECONDS = 30
# A handler may widen its own lease, but never past this bound: an attempt that
# dies with a wide lease stays unrecoverable for exactly that long.
WORKSPACE_OPERATION_MAX_LEASE_SECONDS = 7200

TERMINAL_OPERATION_STATES = frozenset(
    {"succeeded", "failed", "cancelled", "dead_letter"}
)


async def heartbeat_workspace_operation(
    operation_id: uuid.UUID,
    attempt_token: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    lease_seconds: int = WORKSPACE_OPERATION_LEASE_SECONDS,
) -> bool:
    now = get_current_utc_datetime()
    async with session_factory() as session:
        result = await session.execute(
            update(WorkspaceOperation)
            .where(WorkspaceOperation.id == operation_id)
            .where(WorkspaceOperation.state == "running")
            .where(WorkspaceOperation.cancel_requested_at.is_(None))
            .where(WorkspaceOperation.attempt_token == attempt_token)
            .values(
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
                updated_at=now,
            )
        )
        await session.commit()
        return result.rowcount == 1


async def extend_workspace_operation_lease(
    operation_id: uuid.UUID,
    attempt_token: str,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    lease_seconds: int,
    minimum_seconds: int = WORKSPACE_OPERATION_LEASE_SECONDS,
) -> int | None:
    """Widen this attempt's lease and return the window actually granted.

    Callers must invoke this *before* taking a long write lock; afterwards the
    write cannot commit.  ``None`` means the lease was not widened -- either the
    attempt no longer owns the operation, or the write itself was locked out.
    """

    granted = max(
        int(minimum_seconds),
        min(int(lease_seconds), WORKSPACE_OPERATION_MAX_LEASE_SECONDS),
    )
    try:
        alive = await heartbeat_workspace_operation(
            operation_id,
            attempt_token,
            session_factory=session_factory,
            lease_seconds=granted,
        )
    except OperationalError:
        LOGGER.warning(
            "Lease extension not recorded for operation %s", operation_id, exc_info=True
        )
        return None
    return granted if alive else None


async def operation_cancel_requested(
    operation_id: uuid.UUID,
    attempt_token: str,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(
                    WorkspaceOperation.state,
                    WorkspaceOperation.cancel_requested_at,
                    WorkspaceOperation.attempt_token,
                ).where(WorkspaceOperation.id == operation_id)
            )
        ).one_or_none()
    if row is None:
        return True
    if row.state in TERMINAL_OPERATION_STATES:
        return row.state == "cancelled"
    return (
        row.attempt_token != attempt_token
        or row.state == "cancelling"
        or row.cancel_requested_at is not None
    )


async def run_attempt_heartbeat_loop(
    claim: WorkspaceOperationClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    heartbeat_seconds: float,
    poll_seconds: float,
    lease_seconds: Callable[[], int],
) -> None:
    """Renew the lease until the attempt is cancelled or demonstrably lost.

    ``lease_seconds`` is read per renewal so a mid-flight extension by the
    handler survives the next heartbeat instead of being narrowed back.
    """

    loop = asyncio.get_running_loop()
    heartbeat_interval = max(0.05, float(heartbeat_seconds))
    # Cancellation is a durable cross-process signal.  Poll it separately from
    # the lease write so a five-minute lease (or a long heartbeat interval)
    # cannot strand an operation in `cancelling`.
    cancellation_poll_interval = max(
        0.05,
        min(float(poll_seconds), heartbeat_interval, 1.0),
    )
    next_heartbeat_at = loop.time() + heartbeat_interval
    while True:
        await asyncio.sleep(
            min(
                cancellation_poll_interval,
                max(0.0, next_heartbeat_at - loop.time()),
            )
        )
        try:
            if await operation_cancel_requested(
                claim.operation_id,
                claim.attempt_token,
                session_factory=session_factory,
            ):
                raise WorkspaceOperationCancelled("workspace_operation_cancelled")
            if loop.time() < next_heartbeat_at:
                continue
            alive = await heartbeat_workspace_operation(
                claim.operation_id,
                claim.attempt_token,
                session_factory=session_factory,
                lease_seconds=lease_seconds(),
            )
        except OperationalError:
            # The write lock is held by this attempt's own handler.  Losing the
            # renewal says nothing about liveness, so keep the attempt and try
            # again; the widened lease taken before the lock covers the gap.
            LOGGER.warning(
                "Heartbeat not recorded for operation %s",
                claim.operation_id,
                exc_info=True,
            )
            next_heartbeat_at = loop.time() + heartbeat_interval
            continue
        if not alive:
            raise WorkspaceOperationLeaseLost("workspace_operation_lease_lost")
        next_heartbeat_at = loop.time() + heartbeat_interval
