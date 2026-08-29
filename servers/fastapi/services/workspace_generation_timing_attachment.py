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
)


LOGGER = logging.getLogger(__name__)


class _GenerationOperationClaim(Protocol):
    kind: str
    operation_id: object
    request_payload: Mapping[str, Any]


async def attach_durable_generation_timing(
    claim: _GenerationOperationClaim,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Add the immutable receipt projection only for generation operations."""

    result = dict(payload)
    if claim.kind != "presentation.generate":
        return result
    job_id = result.get("job_id") or claim.request_payload.get("job_id")
    if not job_id:
        return result
    try:
        # Runtime import keeps the generic runner outside the receipt store's
        # initialization graph.
        from services.authored_generation_timing_receipt import (
            load_generation_timing_receipt,
        )

        receipt = await asyncio.to_thread(load_generation_timing_receipt, job_id)
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


__all__ = [
    "amend_generation_timing_projection",
    "attach_durable_generation_timing",
]
