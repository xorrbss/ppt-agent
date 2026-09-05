"""Read-only "is there anything to do" probes for the worker-lane loops.

``claim_next_workspace_operation`` and ``claim_next_workspace_outbox`` both open
``BEGIN IMMEDIATE`` on SQLite before they know whether a row exists, so an idle
worker takes the single writer lock once per second per loop and contends with
the checkpoint writes of the deck it is generating.

These probes answer the same question with a plain ``SELECT`` that takes no
write lock.  They are deliberately *broader* than the claim predicates they
guard: an extra claim cycle costs one wasted round trip, while a probe that
missed a claimable row would delay it.  Even then nothing is lost -- the
callers still run the full claim on their backoff ceiling, so a false negative
costs latency and never an operation.
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from collections.abc import Collection

from models.sql.workspace import WorkspaceOperation, WorkspaceOutbox
from services.database import async_session_maker
from utils.datetime_utils import get_current_utc_datetime

LOGGER = logging.getLogger(__name__)


async def workspace_operation_work_pending(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    allowed_kinds: Collection[str] | None = None,
) -> bool:
    """Whether a queued operation or an expired lease may be waiting.

    Returns ``True`` on any read failure: an unavailable probe must never be
    read as "no work", or a transient error would stall the lane until the
    ceiling sweep.
    """

    now = get_current_utc_datetime()
    normalized_kinds = (
        None
        if allowed_kinds is None
        else tuple(sorted({kind for kind in allowed_kinds if kind}))
    )
    if normalized_kinds == ():
        return False
    claimable = (
        (WorkspaceOperation.state == "queued")
        & WorkspaceOperation.cancel_requested_at.is_(None)
        & or_(
            WorkspaceOperation.next_attempt_at.is_(None),
            WorkspaceOperation.next_attempt_at <= now,
        )
    )
    recoverable = WorkspaceOperation.state.in_(("running", "cancelling")) & or_(
        WorkspaceOperation.lease_expires_at.is_(None),
        WorkspaceOperation.lease_expires_at <= now,
    )
    if normalized_kinds is not None:
        claimable = claimable & WorkspaceOperation.kind.in_(normalized_kinds)
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    select(WorkspaceOperation)
                    .where(or_(claimable, recoverable))
                    .limit(1)
                )
            ).scalars().first()
            return row is not None
    except Exception:
        LOGGER.debug("Workspace operation work probe failed", exc_info=True)
        return True


async def workspace_outbox_work_pending(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    """Whether a deliverable or lease-expired outbox row may be waiting."""

    now = get_current_utc_datetime()
    ready = or_(
        (
            WorkspaceOutbox.status.in_(("pending", "failed"))
            & (WorkspaceOutbox.available_at <= now)
        ),
        (
            (WorkspaceOutbox.status == "delivering")
            & or_(
                WorkspaceOutbox.lease_expires_at.is_(None),
                WorkspaceOutbox.lease_expires_at <= now,
            )
        ),
    )
    try:
        async with session_factory() as session:
            row = (
                await session.execute(select(WorkspaceOutbox).where(ready).limit(1))
            ).scalars().first()
            return row is not None
    except Exception:
        LOGGER.debug("Workspace outbox work probe failed", exc_info=True)
        return True


__all__ = [
    "workspace_operation_work_pending",
    "workspace_outbox_work_pending",
]
