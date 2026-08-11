"""Retention helpers for durable Asset Gateway idempotency receipts."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.asset_gateway_idempotency_receipt import (
    AssetGatewayIdempotencyReceipt,
)
from utils.datetime_utils import get_current_utc_datetime


ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE = 128


async def cleanup_expired_asset_gateway_idempotency_receipts(
    session: AsyncSession,
    *,
    owner_scope: str | None = None,
    now: datetime | None = None,
    limit: int = ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE,
) -> int:
    """Delete one bounded batch, globally or for one opportunistic caller."""

    cutoff = now or get_current_utc_datetime()
    bounded_limit = max(
        1,
        min(limit, ASSET_GATEWAY_RECEIPT_CLEANUP_BATCH_SIZE),
    )
    statement = (
        select(AssetGatewayIdempotencyReceipt.id)
        .where(AssetGatewayIdempotencyReceipt.expires_at <= cutoff)
        .order_by(
            AssetGatewayIdempotencyReceipt.expires_at,
            AssetGatewayIdempotencyReceipt.created_at,
        )
        .limit(bounded_limit)
    )
    if owner_scope is None:
        statement = statement.execution_options(skip_owner_scope=True)
    else:
        statement = statement.where(
            AssetGatewayIdempotencyReceipt.owner_scope == owner_scope
        )
    receipt_ids = list((await session.execute(statement)).scalars())
    if not receipt_ids:
        return 0
    await session.execute(
        delete(AssetGatewayIdempotencyReceipt).where(
            AssetGatewayIdempotencyReceipt.id.in_(receipt_ids)
        )
    )
    await session.commit()
    return len(receipt_ids)
