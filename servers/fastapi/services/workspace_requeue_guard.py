"""A failed or released attempt either requeues durably or terminalizes.

Requeueing a public v2 admission moves its reservation from the active to the
queued quota bucket (``requeue_public_operation_v2``).  That is a move, not a
second charge, but the database capacity trigger
(``public_guard_quota_counter_capacity``) checks each bucket on its own: when
the owner's queue is already full, taking the slot back raises
``public_quota_capacity_exceeded``.  The runner used to let that escape from
the failure transition, so the operation stayed ``running`` holding its active
slot, every later requeue attempt (lease recovery) failed the same way, and
the owner's queued operations could never start.

Every requeue site goes through ``try_requeue``: a rejection rolls the
transaction back and the caller terminalizes the attempt instead, which
releases the admission's reservation in the same transaction as the terminal
event.  ``run_guarded`` is the last line: nothing a runner task raises is left
unretrieved, and the operation records why.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
import re
from typing import TypeVar

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.workspace import WorkspaceOperation
from services.public_operation_admission_models import PublicAdmissionRejected
from services.workspace_operation_handler_registry import WorkspaceOperationClaim

LOGGER = logging.getLogger(__name__)
REQUEUE_REJECTED_DETAIL_KEY = "requeue_rejected_code"
TRANSITION_FAILED_CODE = "workspace_operation_transition_failed"
_DATABASE_CODE = re.compile(r"\b(public_[a-z0-9_]+)\b")
T = TypeVar("T")


def requeue_rejection_code(error: BaseException) -> str | None:
    """The admission code a rejected requeue carries, or ``None`` if not one."""

    if isinstance(error, PublicAdmissionRejected):
        return error.code
    if isinstance(error, DBAPIError):
        # A capacity trigger raises its code as the database error message.
        match = _DATABASE_CODE.search(str(getattr(error, "orig", None) or error))
        return match.group(1) if match else None
    return None


async def try_requeue(
    session: AsyncSession, requeue: Awaitable[T]
) -> tuple[T | None, str | None]:
    """Run one requeue; on rejection roll back and return its code instead."""

    try:
        return await requeue, None
    except (PublicAdmissionRejected, DBAPIError) as error:
        code = requeue_rejection_code(error)
        if code is None:
            raise
        await session.rollback()
        return None, code


def claim_from_operation(operation: WorkspaceOperation) -> WorkspaceOperationClaim:
    """The live attempt of an operation row, for a fenced terminal transition."""

    return WorkspaceOperationClaim(
        operation_id=operation.id,
        owner_id=operation.owner_id,
        owner_scope=operation.owner_scope,
        conversation_id=operation.conversation_id,
        presentation_id=operation.presentation_id,
        kind=operation.kind,
        request_payload=dict(operation.request_payload or {}),
        expected_revision=operation.expected_revision,
        attempt_number=operation.attempt_number,
        attempt_token=operation.attempt_token or "",
        fencing_token=operation.fencing_token,
    )


async def run_guarded(
    claim: WorkspaceOperationClaim,
    run: Callable[[], Awaitable[None]],
    *,
    record_failure: Callable[[str], Awaitable[object]],
) -> None:
    """Run one claim's execution; log and record anything it lets escape."""

    try:
        await run()
    except asyncio.CancelledError:
        raise
    except Exception:
        LOGGER.exception(
            "%s kind=%s operation_id=%s", TRANSITION_FAILED_CODE, claim.kind, claim.operation_id
        )
        try:
            await record_failure(TRANSITION_FAILED_CODE)
        except Exception:
            LOGGER.exception(
                "Could not record %s for operation %s", TRANSITION_FAILED_CODE, claim.operation_id
            )


__all__ = [
    "REQUEUE_REJECTED_DETAIL_KEY",
    "TRANSITION_FAILED_CODE",
    "claim_from_operation",
    "requeue_rejection_code",
    "run_guarded",
    "try_requeue",
]
