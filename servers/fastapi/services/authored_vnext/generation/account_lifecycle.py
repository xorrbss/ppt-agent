"""Account lifecycle hooks for authored-vNext generation operations."""

from __future__ import annotations

import uuid

from sqlalchemy import inspect, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_vnext_generation_operation import (
    AuthoredVNextGenerationOperation,
)
from utils.datetime_utils import get_current_utc_datetime


async def cancel_authored_vnext_generation_operations_for_account(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
) -> int:
    """Fence active work before the account FK cascade removes its receipts."""

    table_exists = await session.run_sync(
        lambda sync_session: inspect(sync_session.connection()).has_table(
            AuthoredVNextGenerationOperation.__tablename__
        )
    )
    if not table_exists:
        return 0
    now = get_current_utc_datetime()
    result = await session.execute(
        update(AuthoredVNextGenerationOperation)
        .where(
            AuthoredVNextGenerationOperation.owner_id == owner_id,
            AuthoredVNextGenerationOperation.state.in_(("queued", "running")),
        )
        .values(
            state="cancelled",
            worker_id=None,
            lease_expires_at=None,
            completed_at=now,
            fencing_token=AuthoredVNextGenerationOperation.fencing_token + 1,
            version=AuthoredVNextGenerationOperation.version + 1,
            error_code="authored_vnext_account_deleting",
            error_detail="Account deletion fenced this generation operation.",
        )
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


__all__ = ["cancel_authored_vnext_generation_operations_for_account"]
