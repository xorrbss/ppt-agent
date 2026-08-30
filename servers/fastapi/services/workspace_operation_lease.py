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
from hashlib import sha256
from inspect import Parameter, signature
import logging
import uuid

from sqlalchemy import case, exists, or_, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_admission import PublicOperationAdmission, PublicResourceLease
from models.sql.workspace import WorkspaceOperation
from services.database import async_session_maker
from services.public_operation_admission import (
    PublicAdmissionRejected,
    _resolved_phase2_authority,
)
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
    {"succeeded", "failed", "cancelled", "dead_letter", "deadline_exceeded"}
)


def _supports_fencing_token(callback: Callable[..., object]) -> bool:
    """Preserve pre-fence callback seams while fencing the product functions."""

    try:
        parameters = signature(callback).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == "fencing_token"
        or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


async def heartbeat_workspace_operation(
    operation_id: uuid.UUID,
    attempt_token: str,
    *,
    fencing_token: int | None = None,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    lease_seconds: int = WORKSPACE_OPERATION_LEASE_SECONDS,
) -> bool:
    now = get_current_utc_datetime()
    requested_expiry = now + timedelta(seconds=max(1, lease_seconds))
    async with session_factory() as session:
        # Public lifecycle transitions lock admission before operation. Keep
        # heartbeat in that same global order to avoid a cross-worker deadlock.
        public_admission = await session.scalar(
            select(PublicOperationAdmission)
            .where(PublicOperationAdmission.operation_id == operation_id)
            .with_for_update()
        )
        phase2_admission = bool(
            public_admission is not None
            and getattr(public_admission, "policy_digest", None)
            and getattr(public_admission, "deadline_at", None) is not None
            and int(getattr(public_admission, "fence", 0)) >= 1
        )
        if phase2_admission:
            if fencing_token is None:
                await session.rollback()
                return False
            await _resolved_phase2_authority(session)
            if (
                public_admission.state != "active"
                or public_admission.fence != fencing_token
                or public_admission.deadline_at <= now
            ):
                await session.rollback()
                return False
        operation = await session.scalar(
            select(WorkspaceOperation)
            .where(WorkspaceOperation.id == operation_id)
            .with_for_update()
        )
        if phase2_admission:
            if (
                operation is None
                or operation.contract_version != "public-operation/v2"
                or operation.execution_profile_version
                != "public-generation-export/v2"
                or operation.phase != "active"
                or operation.deadline_at is None
                or operation.deadline_at != public_admission.deadline_at
                or operation.fencing_token != public_admission.fence
            ):
                await session.rollback()
                return False
        elif operation is not None and operation.contract_version == "public-operation/v2":
            # A v2 operation without its matching v2 admission is never allowed
            # to fall back to workspace-only liveness.
            await session.rollback()
            return False
        else:
            # Phase-1/local operations preserve the workspace-only lease path.
            public_admission = None
        statement = (
            update(WorkspaceOperation)
            .where(WorkspaceOperation.id == operation_id)
            .where(WorkspaceOperation.state == "running")
            .where(WorkspaceOperation.cancel_requested_at.is_(None))
            .where(WorkspaceOperation.attempt_token == attempt_token)
            .where(
                or_(
                    WorkspaceOperation.deadline_at.is_(None),
                    WorkspaceOperation.deadline_at > now,
                )
            )
            .values(
                heartbeat_at=now,
                lease_expires_at=case(
                    (
                        WorkspaceOperation.deadline_at.is_not(None)
                        & (WorkspaceOperation.deadline_at < requested_expiry),
                        WorkspaceOperation.deadline_at,
                    ),
                    else_=requested_expiry,
                ),
                updated_at=now,
            )
        )
        if fencing_token is not None:
            statement = statement.where(
                WorkspaceOperation.fencing_token == fencing_token
            )
        result = await session.execute(statement)
        if result.rowcount != 1:
            await session.rollback()
            return False
        if public_admission is not None:
            lease_result = await session.execute(
                update(PublicResourceLease)
                .where(
                    PublicResourceLease.admission_id == public_admission.id,
                    PublicResourceLease.operation_id == operation_id,
                    PublicResourceLease.state == "leased",
                    PublicResourceLease.attempt == operation.attempt_number,
                    PublicResourceLease.fence == fencing_token,
                    PublicResourceLease.lease_owner_hash
                    == sha256(attempt_token.encode("utf-8")).hexdigest(),
                    PublicResourceLease.deadline_at == public_admission.deadline_at,
                    exists(
                        select(PublicOperationAdmission.id).where(
                            PublicOperationAdmission.id == public_admission.id,
                            PublicOperationAdmission.state == "active",
                            PublicOperationAdmission.fence == fencing_token,
                            PublicOperationAdmission.deadline_at
                            == public_admission.deadline_at,
                        )
                    ),
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=case(
                        (
                            PublicResourceLease.deadline_at < requested_expiry,
                            PublicResourceLease.deadline_at,
                        ),
                        else_=requested_expiry,
                    ),
                    version=PublicResourceLease.version + 1,
                )
            )
            if lease_result.rowcount != 3:
                await session.rollback()
                return False
        await session.commit()
        return True


async def extend_workspace_operation_lease(
    operation_id: uuid.UUID,
    attempt_token: str,
    *,
    fencing_token: int | None = None,
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
        heartbeat_kwargs = {
            "session_factory": session_factory,
            "lease_seconds": granted,
        }
        if _supports_fencing_token(heartbeat_workspace_operation):
            heartbeat_kwargs["fencing_token"] = fencing_token
        alive = await heartbeat_workspace_operation(
            operation_id,
            attempt_token,
            **heartbeat_kwargs,
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
    fencing_token: int | None = None,
    session_factory: async_sessionmaker[AsyncSession],
) -> bool:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(
                    WorkspaceOperation.state,
                    WorkspaceOperation.cancel_requested_at,
                    WorkspaceOperation.attempt_token,
                    WorkspaceOperation.fencing_token,
                ).where(WorkspaceOperation.id == operation_id)
            )
        ).one_or_none()
    if row is None:
        return True
    if row.state in TERMINAL_OPERATION_STATES:
        return row.state == "cancelled"
    return (
        row.attempt_token != attempt_token
        or (fencing_token is not None and row.fencing_token != fencing_token)
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
            cancel_kwargs = {"session_factory": session_factory}
            if _supports_fencing_token(operation_cancel_requested):
                cancel_kwargs["fencing_token"] = claim.fencing_token
            if await operation_cancel_requested(
                claim.operation_id,
                claim.attempt_token,
                **cancel_kwargs,
            ):
                raise WorkspaceOperationCancelled("workspace_operation_cancelled")
            if loop.time() < next_heartbeat_at:
                continue
            heartbeat_kwargs = {
                "session_factory": session_factory,
                "lease_seconds": lease_seconds(),
            }
            if _supports_fencing_token(heartbeat_workspace_operation):
                heartbeat_kwargs["fencing_token"] = claim.fencing_token
            alive = await heartbeat_workspace_operation(
                claim.operation_id,
                claim.attempt_token,
                **heartbeat_kwargs,
            )
        except OperationalError as error:
            # The write lock is held by this attempt's own handler.  Losing the
            # renewal says nothing about liveness, so keep the attempt and try
            # again; the widened lease taken before the lock covers the gap.
            if "database is locked" not in str(error).lower():
                raise WorkspaceOperationLeaseLost(
                    "workspace_operation_authority_unavailable"
                ) from error
            LOGGER.warning(
                "Heartbeat not recorded for operation %s",
                claim.operation_id,
                exc_info=True,
            )
            next_heartbeat_at = loop.time() + heartbeat_interval
            continue
        except PublicAdmissionRejected as error:
            raise WorkspaceOperationLeaseLost(error.code) from error
        if not alive:
            raise WorkspaceOperationLeaseLost("workspace_operation_lease_lost")
        next_heartbeat_at = loop.time() + heartbeat_interval
