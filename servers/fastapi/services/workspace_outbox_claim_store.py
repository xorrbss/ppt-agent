"""Durable lease operations over the workspace transactional outbox table.

This module owns the row-level contract of the outbox: the lease constants, the
:class:`WorkspaceOutboxClaim` snapshot handed to a delivering worker, the
fencing-checked claim/heartbeat/complete/fail statements, and the best-effort
metric recorders those statements fire outside their transactions. It was split
out of ``services/workspace_operation_outbox_dispatcher.py`` so the dispatcher
module is left with scheduling and topic dispatch alone; the storage contract
here is exercised directly by tests and has no dependency on the worker loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from collections.abc import Mapping
from typing import Any
import uuid

from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.workspace import WorkspaceOutbox
from services.database import async_session_maker
from services.workspace_metrics import WORKSPACE_METRICS
from services.workspace_operation_runner import sanitize_workspace_event_payload
from utils.datetime_utils import get_current_utc_datetime


logger = logging.getLogger(__name__)

WORKSPACE_OUTBOX_LEASE_SECONDS = 60
WORKSPACE_OUTBOX_MAX_ATTEMPTS = 8


def record_workspace_outbox_outcome(outcome: str) -> None:
    """Keep metrics best-effort and outside durable outbox transactions."""

    try:
        WORKSPACE_METRICS.record_outbox_outcome(outcome)
    except Exception:
        logger.exception(
            "Workspace outbox metric recording failed outcome=%s", outcome
        )


def record_workspace_event_lag(claim: "WorkspaceOutboxClaim", now: datetime) -> None:
    """Measure only durable operation-event delivery, never payload contents."""

    if claim.topic != "workspace.operation.event":
        return
    try:
        created_at = claim.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        delivered_at = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        lag_ms = max(0, int((delivered_at - created_at).total_seconds() * 1_000))
        WORKSPACE_METRICS.record_event_lag_ms(lag_ms)
    except Exception:
        try:
            WORKSPACE_METRICS.record_signal_failure("event_lag")
        except Exception:
            pass
        logger.exception("Workspace event lag metric recording failed")


@dataclass(slots=True)
class WorkspaceOutboxClaim:
    outbox_id: uuid.UUID
    operation_id: uuid.UUID | None
    topic: str
    payload: Mapping[str, Any]
    delivery_attempts: int
    lease_expires_at: datetime
    created_at: datetime


def outbox_backoff_seconds(outbox_id: uuid.UUID, attempt: int) -> int:
    # Stable one-second-scale jitter avoids synchronized retries without ever
    # storing or logging the delivery payload.
    jitter = (outbox_id.int + attempt) % 3
    return min(300, (2 ** min(max(0, attempt - 1), 8)) + jitter)


async def claim_next_workspace_outbox(
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    lease_seconds: int = WORKSPACE_OUTBOX_LEASE_SECONDS,
) -> WorkspaceOutboxClaim | None:
    now = get_current_utc_datetime()
    new_lease = now + timedelta(seconds=max(1, lease_seconds))
    async with session_factory() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            await session.execute(text("BEGIN IMMEDIATE"))
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
        statement = (
            select(WorkspaceOutbox)
            .where(ready)
            .order_by(WorkspaceOutbox.available_at, WorkspaceOutbox.created_at)
            .limit(1)
        )
        if dialect == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        row = (await session.execute(statement)).scalar_one_or_none()
        if row is None:
            await session.rollback()
            return None

        prior_status = row.status
        prior_lease = row.lease_expires_at
        prior_attempts = row.delivery_attempts
        ownership = [
            WorkspaceOutbox.id == row.id,
            WorkspaceOutbox.status == prior_status,
            WorkspaceOutbox.delivery_attempts == prior_attempts,
        ]
        ownership.append(
            WorkspaceOutbox.lease_expires_at.is_(None)
            if prior_lease is None
            else WorkspaceOutbox.lease_expires_at == prior_lease
        )
        result = await session.execute(
            update(WorkspaceOutbox)
            .where(*ownership)
            .values(
                status="delivering",
                delivery_attempts=WorkspaceOutbox.delivery_attempts + 1,
                lease_expires_at=new_lease,
                heartbeat_at=now,
                last_error=None,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await session.rollback()
            return None
        await session.commit()
        claim = WorkspaceOutboxClaim(
            outbox_id=row.id,
            operation_id=row.operation_id,
            topic=row.topic,
            payload=sanitize_workspace_event_payload(row.payload),
            delivery_attempts=prior_attempts + 1,
            lease_expires_at=new_lease,
            created_at=row.created_at,
        )
        record_workspace_outbox_outcome("dispatched")
        return claim


async def heartbeat_workspace_outbox_claim(
    claim: WorkspaceOutboxClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
    lease_seconds: int = WORKSPACE_OUTBOX_LEASE_SECONDS,
) -> bool:
    now = get_current_utc_datetime()
    next_lease = now + timedelta(seconds=max(1, lease_seconds))
    async with session_factory() as session:
        result = await session.execute(
            update(WorkspaceOutbox)
            .where(WorkspaceOutbox.id == claim.outbox_id)
            .where(WorkspaceOutbox.status == "delivering")
            .where(WorkspaceOutbox.delivery_attempts == claim.delivery_attempts)
            .where(WorkspaceOutbox.lease_expires_at == claim.lease_expires_at)
            .values(
                lease_expires_at=next_lease,
                heartbeat_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    if result.rowcount != 1:
        return False
    claim.lease_expires_at = next_lease
    return True


async def mark_workspace_outbox_delivered(
    claim: WorkspaceOutboxClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    now = get_current_utc_datetime()
    async with session_factory() as session:
        result = await session.execute(
            update(WorkspaceOutbox)
            .where(WorkspaceOutbox.id == claim.outbox_id)
            .where(WorkspaceOutbox.status == "delivering")
            .where(WorkspaceOutbox.delivery_attempts == claim.delivery_attempts)
            .where(WorkspaceOutbox.lease_expires_at == claim.lease_expires_at)
            .values(
                status="delivered",
                delivered_at=now,
                lease_expires_at=None,
                heartbeat_at=now,
                last_error=None,
                updated_at=now,
            )
        )
        await session.commit()
        updated = result.rowcount == 1
    if updated:
        record_workspace_outbox_outcome("delivered")
        if claim.topic == "workspace.operation.event":
            record_workspace_outbox_outcome("event_delivered")
        record_workspace_event_lag(claim, now)
    return updated


async def mark_workspace_outbox_failed(
    claim: WorkspaceOutboxClaim,
    *,
    code: str,
    retryable: bool,
    max_attempts: int = WORKSPACE_OUTBOX_MAX_ATTEMPTS,
    session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
) -> bool:
    now = get_current_utc_datetime()
    terminal = not retryable or claim.delivery_attempts >= max(1, max_attempts)
    next_status = "dead_letter" if terminal else "failed"
    available_at = now
    if not terminal:
        available_at = now + timedelta(
            seconds=outbox_backoff_seconds(
                claim.outbox_id, claim.delivery_attempts
            )
        )
    async with session_factory() as session:
        result = await session.execute(
            update(WorkspaceOutbox)
            .where(WorkspaceOutbox.id == claim.outbox_id)
            .where(WorkspaceOutbox.status == "delivering")
            .where(WorkspaceOutbox.delivery_attempts == claim.delivery_attempts)
            .where(WorkspaceOutbox.lease_expires_at == claim.lease_expires_at)
            .values(
                status=next_status,
                available_at=available_at,
                lease_expires_at=None,
                heartbeat_at=now,
                # Never persist exception messages, stack traces, or payloads.
                last_error={"error_code": code, "retryable": retryable},
                updated_at=now,
            )
        )
        await session.commit()
        updated = result.rowcount == 1
    if updated:
        record_workspace_outbox_outcome(
            "dead_letter" if terminal else "retry_wait"
        )
    return updated
