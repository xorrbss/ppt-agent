"""Load a durable generation timing receipt for the workspace result path."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from services.workspace_generation_timing_payload import (
    GENERATION_TIMING_RESULT_KEYS,
    generation_timing_result_projection,
    sanitize_generation_timing_receipt,
)


LOGGER = logging.getLogger(__name__)


class _GenerationOperationClaim(Protocol):
    kind: str
    operation_id: object
    request_payload: Mapping[str, Any]
    attempt_token: str
    attempt_number: int
    fencing_token: object


async def attach_durable_generation_timing(
    claim: _GenerationOperationClaim,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Add the immutable receipt projection only for generation operations."""

    result = dict(payload)
    if claim.kind != "presentation.generate":
        return result
    # A handled failure is CAS-projected into PostgreSQL before the handler
    # unwinds. Prefer that durable authority and use the local ledger only as a
    # recovery fallback.
    projected = sanitize_generation_timing_receipt(
        result.get("generation_timing_receipt")
    )
    if projected:
        result.update(generation_timing_result_projection(projected))
        return result
    job_id = result.get("job_id") or claim.request_payload.get("job_id")
    if not job_id:
        return result
    try:
        # Runtime import keeps the generic runner outside the receipt store's
        # initialization graph.
        from services.authored_generation_timing_receipt import (
            load_generation_attempt_timing_receipts,
            load_generation_timing_receipt,
        )
        from services.authored_quality_receipt_v2 import public_attempt_id

        receipt = await asyncio.to_thread(load_generation_timing_receipt, job_id)
        attempt_token = getattr(claim, "attempt_token", None)
        attempt_number = getattr(claim, "attempt_number", None)
        expected_attempt_id = (
            public_attempt_id(attempt_token)
            if isinstance(attempt_token, str) and attempt_token
            else None
        )
        if receipt is not None and (
            (expected_attempt_id is not None and receipt.attempt_id != expected_attempt_id)
            or (
                isinstance(attempt_number, int)
                and receipt.attempt_number != attempt_number
            )
        ):
            receipt = None
        if receipt is None:
            attempt_receipts = await asyncio.to_thread(
                load_generation_attempt_timing_receipts, job_id
            )
            matching = tuple(
                candidate
                for candidate in attempt_receipts
                if (
                    expected_attempt_id is None
                    or candidate.attempt_id == expected_attempt_id
                )
                and (
                    not isinstance(attempt_number, int)
                    or candidate.attempt_number == attempt_number
                )
            )
            receipt = matching[-1] if matching else None
    except (OSError, ValueError):
        LOGGER.warning(
            "Unable to load generation timing receipt for operation %s",
            claim.operation_id,
            exc_info=True,
        )
        return result
    if receipt is not None:
        result.update(generation_timing_result_projection(receipt.to_dict()))
    return result


async def amend_generation_timing_projection(
    session: AsyncSession,
    *,
    operation: Any,
    safe_result: Mapping[str, Any],
    now: datetime,
) -> bool | None:
    """CAS-amend an older terminal projection with the durable timing receipt.

    ``None`` means there was nothing to amend.  The caller owns transaction
    commit/rollback so its legacy terminal-event fallback stays atomic.
    """

    timing_projection = {
        key: safe_result[key]
        for key in GENERATION_TIMING_RESULT_KEYS
        if key in safe_result
    }
    current_result = (
        dict(operation.result) if isinstance(operation.result, Mapping) else {}
    )
    merged_result = {**current_result, **timing_projection}
    if not timing_projection or merged_result == current_result:
        return None
    # Runtime import avoids entangling the module with runner initialization.
    from models.sql.workspace import WorkspaceOperation

    amended = await session.execute(
        update(WorkspaceOperation)
        .where(
            WorkspaceOperation.id == operation.id,
            WorkspaceOperation.state == "succeeded",
            WorkspaceOperation.version == operation.version,
        )
        .values(
            result=merged_result,
            version=WorkspaceOperation.version + 1,
            updated_at=now,
        )
    )
    return amended.rowcount == 1


_TIMING_AMEND_MAX_ATTEMPTS = 3


async def _mark_generation_timing_receipt_error(
    session: AsyncSession,
    *,
    operation: Any,
    error_code: str,
    now: datetime,
) -> bool:
    """Best-effort CAS write of a public ``timingReceiptError`` marker.

    Used only after every retry of the durable amend above has lost the
    optimistic-concurrency race.  Leaves a visible marker on the operation
    result instead of completing with a receipt silently missing.
    """

    current_result = (
        dict(operation.result) if isinstance(operation.result, Mapping) else {}
    )
    if current_result.get("timingReceiptError") == error_code:
        return True
    merged_result = {**current_result, "timingReceiptError": error_code}
    # Runtime import avoids entangling the module with runner initialization.
    from models.sql.workspace import WorkspaceOperation

    amended = await session.execute(
        update(WorkspaceOperation)
        .where(
            WorkspaceOperation.id == operation.id,
            WorkspaceOperation.state == "succeeded",
            WorkspaceOperation.version == operation.version,
        )
        .values(
            result=merged_result,
            version=WorkspaceOperation.version + 1,
            updated_at=now,
        )
    )
    return amended.rowcount == 1


async def amend_generation_timing_projection_with_error_marker(
    session: AsyncSession,
    *,
    claim: _GenerationOperationClaim,
    operation: Any,
    safe_result: Mapping[str, Any],
    now: datetime,
    max_attempts: int = _TIMING_AMEND_MAX_ATTEMPTS,
) -> tuple[bool | None, Any]:
    """Amend the durable timing receipt, retrying the CAS under contention.

    The single-shot ``amend_generation_timing_projection`` above loses its
    optimistic-concurrency race whenever something else bumps the
    operation's ``version`` between the caller's read and this write (seen in
    practice under heavier SQLite lock contention).  A lost race previously
    made the caller return ``False`` with no logging at all, so a
    fully-successful generation could publish without its timing receipt and
    nothing on disk showed why.  This retries against a fresh read a bounded
    number of times and, only if every attempt still loses the race, logs an
    ERROR and writes a best-effort ``timingReceiptError`` marker so the drop
    is visible to an operator instead of silent.

    Returns ``(outcome, operation)`` where ``operation`` is the latest row
    read.  ``outcome`` is ``None`` (nothing to amend), ``True`` (amended, or
    the error marker landed), or ``False`` (amend and marker both failed).
    """

    from models.sql.workspace import WorkspaceOperation

    current = operation
    outcome: bool | None = None
    for _attempt in range(1, max_attempts + 1):
        outcome = await amend_generation_timing_projection(
            session, operation=current, safe_result=safe_result, now=now
        )
        if outcome is not False:
            return outcome, current
        # The CAS lost the race: reread the row so both a further retry and
        # the eventual error-marker write below use the freshest version
        # instead of retrying/marking against the stale one that just lost.
        await session.rollback()
        refreshed = await session.get(
            WorkspaceOperation, claim.operation_id, populate_existing=True
        )
        if (
            refreshed is None
            or refreshed.state != "succeeded"
            or refreshed.fencing_token != claim.fencing_token
        ):
            current = refreshed if refreshed is not None else current
            break
        current = refreshed
    LOGGER.error(
        "Dropped generation timing receipt for workspace operation %s after "
        "%d attempts (optimistic-concurrency contention on the durable "
        "amend); writing a timingReceiptError marker instead of completing "
        "silently without one.",
        claim.operation_id,
        max_attempts,
    )
    marked = await _mark_generation_timing_receipt_error(
        session,
        operation=current,
        error_code="generation_timing_receipt_amend_conflict",
        now=now,
    )
    if not marked:
        LOGGER.error(
            "Failed to write the timingReceiptError marker for workspace "
            "operation %s; its terminal result has no generation timing "
            "receipt and no visible marker of why.",
            claim.operation_id,
        )
        return False, current
    return True, current


__all__ = [
    "amend_generation_timing_projection",
    "amend_generation_timing_projection_with_error_marker",
    "attach_durable_generation_timing",
]
