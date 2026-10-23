"""Join a live export flight instead of failing the duplicate submission.

``claim_export_identity`` makes one export identity the cluster-wide authority:
the first submission is ``claimed``, duplicates are ``in_flight``.  A duplicate
used to become an operation failure, so the second submission hard-failed while
the bytes it wanted were already being produced.

This module is the single point where a non-leading disposition is resolved.
It runs no worker and publishes no new bytes: it polls the identity row -- the
same row the lease fences on -- and resolves to ``completed`` (bound to the
leader's immutable result through a consumer-scoped reference), ``claimed``
(the leader's lease died mid-wait, so this waiter was promoted by the existing
re-claim path, fencing token incremented and the dead leader's late return
still rejected as a stale fence), or an error: the leader's own failure code,
the pinned deadline code, or the retryable in-flight code.

Two bounds apply to the wait, neither an invented sleep.  The pinned admission
deadline (``services/public_export_deadline.py``) is the outer bound; reaching
it fails with the deadline code, never a synthesized success.  The workspace
operation lease is the slot-yield bound: a waiting joiner holds a runner slot
*and* a global export quota slot (the admission is ``active`` before the
handler runs) while running no worker, so with the global export cap at 4 a few
duplicates would otherwise park most of the cluster's export capacity for the
full 30-minute deadline.  Yielding after one operation lease raises the
retryable in-flight code, which requeues the admission (``active`` ->
``queued``) and hands the slot back; the retry re-enters here and normally
finds the flight completed.

``heartbeat_export_identity`` had no caller, so a leader's 120 s identity lease
expired while a legitimate 30-minute export was still running and the first
waiter would have been promoted into a *second* worker.  The lease keeper below
renews it on the heartbeat cadence, which is what makes lease expiry mean "the
leader is gone" -- the assumption the re-claim path is built on.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, AsyncIterator, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.sql.public_artifact import ArtifactRef, PublicExportIdentity
from models.sql.workspace import WorkspaceOperation
from services.artifact_store import (
    configured_public_artifact_service,
    fail_export_identity,
    heartbeat_export_identity,
)

from services.public_export_deadline import PublicExportDeadlineGrant
from services.public_export_errors import PublicExportOperationError
from services.public_export_hybrid_evidence import bounded_hybrid_worker_evidence
from services.public_export_identity import (
    CanonicalExportExecution,
    claim_canonical_export_identity,
    reuse_completed_export_identity,
)
from services.workspace_operation_handler_registry import WorkspaceOperationClaim
from services.workspace_operation_lease import (
    WORKSPACE_OPERATION_HEARTBEAT_SECONDS,
    WORKSPACE_OPERATION_LEASE_SECONDS,
)
from utils.datetime_utils import as_utc, get_current_utc_datetime


LOGGER = logging.getLogger(__name__)

#: Lease granted to the leader of one export identity.  Renewed by the lease
#: keeper below, so it bounds crash recovery rather than legitimate runtime.
EXPORT_IDENTITY_LEASE_SECONDS = 120

#: Cadence for both the leader's lease renewal and the joiner's identity poll.
EXPORT_JOIN_POLL_SECONDS = float(WORKSPACE_OPERATION_HEARTBEAT_SECONDS)

#: A joiner never dwells past one workspace operation lease; see module docs.
EXPORT_JOIN_SLOT_YIELD_SECONDS = float(WORKSPACE_OPERATION_LEASE_SECONDS)

IN_FLIGHT_ERROR_CODE = "public_export_singleflight_in_flight"

Clock = Callable[[], datetime]
Sleeper = Callable[[float], Awaitable[None]]
#: Resolved by the caller, never re-resolved here: the export adapter is the
#: one place that decides which durable artifact service this run uses.
ArtifactServiceFactory = Callable[[], Any]

HYBRID_EDITABILITY_SCHEMA = "public-hybrid-editability/v1"
HYBRID_EDITABILITY_VERDICTS = frozenset(
    {
        "fully-editable",
        "partially-editable",
        "not-applicable",
        "unknown",
    }
)


def _cached_hybrid_worker_evidence(
    operation: WorkspaceOperation,
) -> dict[str, Any] | None:
    result = operation.result if isinstance(operation.result, dict) else {}
    worker_evidence = result.get("public_export_worker_evidence")
    worker_evidence = worker_evidence if isinstance(worker_evidence, dict) else {}
    bounded = bounded_hybrid_worker_evidence(worker_evidence)
    editability = bounded.get("editability")
    if (
        not isinstance(editability, dict)
        or editability.get("schemaVersion") != HYBRID_EDITABILITY_SCHEMA
        or editability.get("verdict") not in HYBRID_EDITABILITY_VERDICTS
    ):
        return None
    producer_timing = bounded.pop("timing", None)
    if isinstance(producer_timing, dict):
        bounded["producerTiming"] = producer_timing
    return bounded


@dataclass(frozen=True, slots=True)
class ExportJoinOutcome:
    """How this operation obtained the right to the identity's result.

    ``result`` is set exactly when no worker has to run: the artifact this
    operation may return plus the receipt evidence recording the join.
    Otherwise ``execution`` is a leading claim the caller executes itself.
    """

    execution: CanonicalExportExecution
    joined: bool
    leading_operation_id: uuid.UUID | None
    waited_seconds: float
    poll_count: int
    result: tuple[ArtifactRef, dict[str, Any]] | None = None

    def evidence(self) -> dict[str, object]:
        """Receipt fields that tell a joined export from an executed one."""
        return {
            "joined": self.joined,
            "joinedOperationId": (
                str(self.leading_operation_id)
                if self.joined and self.leading_operation_id is not None
                else None
            ),
            "joinWaitSeconds": round(self.waited_seconds, 3),
            "joinPollCount": self.poll_count,
        }


async def _read_identity(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    identity_id: uuid.UUID,
) -> PublicExportIdentity:
    async with session_factory() as session:
        row = await session.scalar(
            select(PublicExportIdentity).where(
                PublicExportIdentity.id == identity_id
            )
        )
    if row is None:
        raise PublicExportOperationError("public_export_singleflight_identity_missing")
    return row


async def _reclaim(
    operation_claim: WorkspaceOperationClaim,
    *,
    execution: CanonicalExportExecution,
    session_factory: async_sessionmaker[AsyncSession],
    now: datetime,
) -> CanonicalExportExecution:
    """Re-read the authority: completed result, promotion, or still in flight.

    ``claim_canonical_export_identity`` answers all three from one row, so the
    joiner never reimplements the reuse or the promotion rules.
    """
    async with session_factory() as session:
        rejoined = await claim_canonical_export_identity(
            session,
            identity=execution.identity,
            source_checkpoint=execution.source_checkpoint,
            operation_claim=operation_claim,
            worker_id=execution.worker_id,
            lease_seconds=EXPORT_IDENTITY_LEASE_SECONDS,
            now=now,
        )
        await session.commit()
    return rejoined


async def _resolved_outcome(
    execution: CanonicalExportExecution,
    *,
    operation_claim: WorkspaceOperationClaim,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_service_factory: ArtifactServiceFactory,
    leading_operation_id: uuid.UUID | None,
    waited_seconds: float,
    poll_count: int,
) -> ExportJoinOutcome:
    """Bind a completed identity to this operation, or hand back a leading claim."""
    outcome = ExportJoinOutcome(
        execution=execution,
        joined=(
            execution.singleflight.disposition == "completed"
            and leading_operation_id is not None
            and leading_operation_id != operation_claim.operation_id
        ),
        leading_operation_id=leading_operation_id,
        waited_seconds=waited_seconds,
        poll_count=poll_count,
    )
    if execution.singleflight.disposition != "completed":
        return outcome
    candidate = reuse_completed_export_identity(
        execution=execution, operation_claim=operation_claim
    )
    async with session_factory() as session:
        cached_worker_evidence: dict[str, Any] | None = None
        if execution.identity.export_mode == "editable-hybrid":
            producer_operation_id = leading_operation_id or execution.operation_id
            producer = await session.scalar(
                select(WorkspaceOperation).where(
                    WorkspaceOperation.id == producer_operation_id
                )
            )
            if producer is None or producer.state != "succeeded":
                raise PublicExportOperationError(
                    "public_export_editability_evidence_pending"
                )
            cached_worker_evidence = _cached_hybrid_worker_evidence(producer)
            if cached_worker_evidence is None:
                raise PublicExportOperationError(
                    "public_export_editability_evidence_unavailable"
                )
        # Keep the completed identity's producer immutable. The public
        # publisher requires a separately registered consumer operation ref
        # even when both refs share the same verified CAS bytes.
        registered = await artifact_service_factory().register_consumer_ref(
            session,
            candidate.artifact,
            created_by_operation_id=operation_claim.operation_id,
        )
        await session.commit()
    return replace(
        outcome,
        result=(
            registered,
            {
                # No worker ran, but the identity being complete is itself the
                # statement of what these bytes are: an identity only completes
                # when the worker delivered the mode it names.
                "public_export_worker_evidence": {
                    **(cached_worker_evidence or {}),
                    "requestedExportMode": execution.identity.export_mode,
                    "deliveredExportMode": execution.identity.export_mode,
                    "fallbackReason": None,
                    "reusedCompletedIdentity": True,
                    **outcome.evidence(),
                }
            },
        ),
    )


async def resolve_export_join(
    operation_claim: WorkspaceOperationClaim,
    *,
    execution: CanonicalExportExecution,
    session_factory: async_sessionmaker[AsyncSession],
    artifact_service_factory: ArtifactServiceFactory = (
        configured_public_artifact_service
    ),
    grant: PublicExportDeadlineGrant | None = None,
    poll_seconds: float = EXPORT_JOIN_POLL_SECONDS,
    slot_yield_seconds: float = EXPORT_JOIN_SLOT_YIELD_SECONDS,
    clock: Clock = get_current_utc_datetime,
    sleep: Sleeper = asyncio.sleep,
) -> ExportJoinOutcome:
    """Resolve one submission's disposition, waiting for a live flight."""
    if poll_seconds <= 0 or slot_yield_seconds <= 0:
        raise ValueError("export join bounds must be positive")
    identity_id = execution.singleflight.identity_id
    started = as_utc(clock())
    poll_count = 0

    if execution.singleflight.disposition != "in_flight":
        row = await _read_identity(session_factory, identity_id=identity_id)
        return await _resolved_outcome(
            execution,
            operation_claim=operation_claim,
            session_factory=session_factory,
            artifact_service_factory=artifact_service_factory,
            leading_operation_id=row.claimed_by_operation_id,
            waited_seconds=0.0,
            poll_count=0,
        )

    while True:
        poll_count += 1
        now = as_utc(clock())
        row = await _read_identity(session_factory, identity_id=identity_id)
        if row.state == "failed":
            # The leader's own verdict, never a synthesized success and never a
            # second publication from the waiter.
            raise PublicExportOperationError(
                row.error_code or "public_export_singleflight_failed"
            )
        if row.state == "succeeded" or (
            row.lease_expires_at is not None
            and as_utc(row.lease_expires_at) <= now
        ):
            rejoined = await _reclaim(
                operation_claim,
                execution=execution,
                session_factory=session_factory,
                now=now,
            )
            if rejoined.singleflight.disposition != "in_flight":
                current = await _read_identity(
                    session_factory, identity_id=identity_id
                )
                return await _resolved_outcome(
                    rejoined,
                    operation_claim=operation_claim,
                    session_factory=session_factory,
                    artifact_service_factory=artifact_service_factory,
                    leading_operation_id=current.claimed_by_operation_id,
                    waited_seconds=(as_utc(clock()) - started).total_seconds(),
                    poll_count=poll_count,
                )
        if grant is not None:
            # Raises ``public_export_admission_deadline_exhausted`` from the one
            # module that owns the pinned deadline.
            grant.export_worker_seconds(now=now)
        if (as_utc(clock()) - started).total_seconds() >= slot_yield_seconds:
            # Hand the runner slot and the global export quota slot back; the
            # requeued attempt re-enters here and normally finds the result.
            raise PublicExportOperationError(IN_FLIGHT_ERROR_CODE)
        await sleep(poll_seconds)


@asynccontextmanager
async def export_identity_lease_keeper(
    *,
    execution: CanonicalExportExecution,
    session_factory: async_sessionmaker[AsyncSession],
    lease_seconds: int = EXPORT_IDENTITY_LEASE_SECONDS,
    interval_seconds: float = EXPORT_JOIN_POLL_SECONDS,
    sleep: Sleeper = asyncio.sleep,
) -> AsyncIterator[None]:
    """Keep the leading attempt's identity lease alive while its worker runs.

    Without this the lease expires under a live leader and a waiter is promoted
    into a duplicate worker.  A lost renewal is not escalated here: the leader's
    completion is fenced on the same tokens and already fails with
    ``public_export_singleflight_fence_stale``.
    """
    attempt_token = execution.singleflight.attempt_token
    if execution.singleflight.disposition != "claimed" or not attempt_token:
        yield
        return

    async def renew() -> None:
        while True:
            await sleep(interval_seconds)
            async with session_factory() as session:
                alive = await heartbeat_export_identity(
                    session,
                    identity_id=execution.singleflight.identity_id,
                    worker_id=execution.worker_id,
                    attempt_token=attempt_token,
                    fencing_token=execution.singleflight.fencing_token,
                    lease_seconds=lease_seconds,
                )
                await session.commit()
            if not alive:
                LOGGER.warning(
                    "Export identity lease lost while the worker was running",
                    extra={"identity_id": str(execution.singleflight.identity_id)},
                )
                return

    keeper = asyncio.create_task(renew())
    try:
        yield
    except BaseException as error:
        # Release only this attempt's fence, including on cancellation. A late
        # result cannot complete a failed or subsequently reclaimed identity.
        code = getattr(error, "code", None) or str(error)
        if not isinstance(code, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,127}", code):
            code = type(error).__name__

        async def release() -> None:
            async with session_factory() as session:
                await fail_export_identity(
                    session,
                    identity_id=execution.singleflight.identity_id,
                    worker_id=execution.worker_id,
                    attempt_token=attempt_token,
                    fencing_token=execution.singleflight.fencing_token,
                    error_code=code,
                )
                await session.commit()

        cleanup = asyncio.create_task(release())
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # Repeated cancellation must not detach the release transaction.
                continue
            except Exception:
                break
        try:
            cleanup.result()
        except Exception:  # noqa: BLE001 - preserve the original export failure
            LOGGER.exception("Failed to release export identity after abnormal exit")
        raise
    finally:
        keeper.cancel()
        try:
            await keeper
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001 - teardown must not mask the export
            LOGGER.exception("Export identity lease keeper failed on teardown")


__all__ = [
    "EXPORT_IDENTITY_LEASE_SECONDS",
    "EXPORT_JOIN_POLL_SECONDS",
    "EXPORT_JOIN_SLOT_YIELD_SECONDS",
    "ExportJoinOutcome",
    "IN_FLIGHT_ERROR_CODE",
    "export_identity_lease_keeper",
    "resolve_export_join",
]
