"""Let a handler finish the work it does after publication.

Publication may atomically mark an operation ``succeeded`` and clear its
attempt token in the same transaction that publishes the revision
(``services/presentation_publication_service.py``).  The handler keeps running
after that commit: ``presentation.generate`` still records the async task
outcome and submits the chained ``export_as`` export
(``services/presentation_generation_export_chain.py``).

That tail does not depend on the lease -- the operation is already terminal and
no other attempt can claim it -- but the heartbeat notices the cleared attempt
token as a lost lease.  The runner used to finalize from the published row and
then cancel the handler.  A cancelled tail rolled back the chained export's
uncommitted insert, and ``CancelledError`` bypasses the chain's
``except Exception``, so the deck published without its export and nothing was
logged.  The runner now waits for the handler instead, bounded by one constant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import logging
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.workspace import WorkspaceOperation
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
    WorkspaceOperationResult,
)
from services.workspace_operation_lease import WORKSPACE_OPERATION_LEASE_SECONDS
from utils.datetime_utils import get_current_utc_datetime

LOGGER = logging.getLogger(__name__)

#: How long the tail may run after publication consumed the lease.  It is the
#: same bound a running attempt has between two renewals
#: (``WORKSPACE_OPERATION_LEASE_SECONDS``): a handler that has not finished
#: within one lease interval would have been recovered as dead had it still
#: held the lease.
POST_PUBLICATION_TAIL_TIMEOUT_SECONDS: float = WORKSPACE_OPERATION_LEASE_SECONDS
POST_PUBLICATION_TAIL_TIMEOUT_CODE = "workspace_operation_post_publication_tail_timeout"
#: Result key of the marker left on the succeeded operation when the tail was cut.
POST_PUBLICATION_TAIL_ERROR_KEY = "postPublicationTailError"
_MARKER_ATTEMPTS = 3

Finalize = Callable[..., Awaitable[bool]]


async def _await_tail(
    claim: WorkspaceOperationClaim,
    handler_task: asyncio.Task[WorkspaceOperationResult],
    published: WorkspaceOperationResult,
) -> tuple[WorkspaceOperationResult, bool]:
    """The handler's own result, or the published one; and whether it timed out."""

    done, _ = await asyncio.wait(
        {handler_task}, timeout=POST_PUBLICATION_TAIL_TIMEOUT_SECONDS
    )
    if handler_task not in done:
        handler_task.cancel()
        await asyncio.gather(handler_task, return_exceptions=True)
        LOGGER.error(
            "%s kind=%s operation_id=%s timeout_seconds=%s",
            POST_PUBLICATION_TAIL_TIMEOUT_CODE,
            claim.kind,
            claim.operation_id,
            POST_PUBLICATION_TAIL_TIMEOUT_SECONDS,
        )
        return published, True
    if handler_task.cancelled():
        return published, False
    error = handler_task.exception()
    if error is not None:
        # Publication already committed; the durable row stays authoritative.
        LOGGER.error(
            "Handler failed after publication kind=%s operation_id=%s",
            claim.kind,
            claim.operation_id,
            exc_info=error,
        )
        return published, False
    return handler_task.result(), False


async def _mark_tail_timeout(
    claim: WorkspaceOperationClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    """CAS the timeout marker onto the succeeded operation's result."""

    for _attempt in range(_MARKER_ATTEMPTS):
        async with session_factory() as session:
            operation = await session.get(WorkspaceOperation, claim.operation_id)
            if (
                operation is None
                or operation.state != "succeeded"
                or operation.fencing_token != claim.fencing_token
            ):
                return False
            current: Mapping[str, Any] = (
                operation.result if isinstance(operation.result, Mapping) else {}
            )
            marked = await session.execute(
                update(WorkspaceOperation)
                .where(
                    WorkspaceOperation.id == claim.operation_id,
                    WorkspaceOperation.state == "succeeded",
                    WorkspaceOperation.fencing_token == claim.fencing_token,
                    WorkspaceOperation.version == operation.version,
                )
                .values(
                    result={
                        **dict(current),
                        POST_PUBLICATION_TAIL_ERROR_KEY: POST_PUBLICATION_TAIL_TIMEOUT_CODE,
                    },
                    version=WorkspaceOperation.version + 1,
                    updated_at=get_current_utc_datetime(),
                )
            )
            if marked.rowcount == 1:
                await session.commit()
                return True
            await session.rollback()
    return False


async def finish_after_publication(
    claim: WorkspaceOperationClaim,
    handler_task: asyncio.Task[WorkspaceOperationResult],
    published: WorkspaceOperationResult,
    *,
    finalize: Finalize,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Wait for the post-publication tail, then finalize the succeeded attempt."""

    result, timed_out = await _await_tail(claim, handler_task, published)
    await finalize(claim, result, session_factory=session_factory)
    if timed_out and not await _mark_tail_timeout(
        claim, session_factory=session_factory
    ):
        LOGGER.error(
            "Could not record %s for operation %s",
            POST_PUBLICATION_TAIL_TIMEOUT_CODE,
            claim.operation_id,
        )


__all__ = [
    "POST_PUBLICATION_TAIL_ERROR_KEY",
    "POST_PUBLICATION_TAIL_TIMEOUT_CODE",
    "POST_PUBLICATION_TAIL_TIMEOUT_SECONDS",
    "finish_after_publication",
]
