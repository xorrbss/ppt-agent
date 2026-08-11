"""Durable owner-scoped idempotency receipts for Asset Gateway writes."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


class AssetGatewayIdempotencyReceipt(SQLModel, table=True):
    """A durable reservation and path-free response for one gateway request."""

    __tablename__ = "asset_gateway_idempotency_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope",
            "operation",
            "key_hash",
            name="uq_asset_gateway_receipts_owner_operation_key",
        ),
        CheckConstraint(
            "state IN ('pending', 'complete')",
            name="ck_asset_gateway_receipts_state",
        ),
        Index(
            "ix_asset_gateway_receipts_expiry",
            "expires_at",
            "created_at",
        ),
        Index(
            "ix_asset_gateway_receipts_asset",
            "owner_scope",
            "asset_id",
        ),
        Index(
            "ix_asset_gateway_receipts_snapshot",
            "owner_scope",
            "snapshot_id",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "user.id",
                name="fk_asset_gateway_receipts_owner_id",
                ondelete="CASCADE",
            ),
            nullable=True,
            default=get_current_owner_id,
        ),
    )
    owner_scope: str = Field(sa_column=Column(String(36), nullable=False))
    operation: str = Field(sa_column=Column(String(64), nullable=False))
    key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_sha256: str = Field(sa_column=Column(String(64), nullable=False))
    state: str = Field(
        default="pending",
        sa_column=Column(
            String(16),
            nullable=False,
            default="pending",
            server_default="pending",
        ),
    )
    response_payload: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    asset_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workspace_assets.id",
                name="fk_asset_gateway_receipts_asset_id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    snapshot_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "reference_snapshots.id",
                name="fk_asset_gateway_receipts_snapshot_id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            onupdate=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )
