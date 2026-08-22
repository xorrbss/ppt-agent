"""Append-only durable authority for Authored capability receipts v2."""

from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Index, JSON, String, UniqueConstraint, Uuid, func
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class AuthoredCapabilityReceiptRecordV2(SQLModel, table=True):
    __tablename__ = "authored_capability_receipts_v2"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "receipt_digest", name="uq_authored_capability_receipt_v2_digest"
        ),
        UniqueConstraint(
            "tenant_id",
            "transition_target_digest",
            name="uq_authored_capability_receipt_v2_transition_target",
        ),
        CheckConstraint(
            "state IN ('disabled', 'shadow', 'ready', 'blocked')",
            name="ck_authored_capability_receipt_v2_state",
        ),
        CheckConstraint(
            "transition_kind IN ('issue', 'supersede', 'revoke')",
            name="ck_authored_capability_receipt_v2_transition_kind",
        ),
        CheckConstraint(
            "expires_at > evaluated_at",
            name="ck_authored_capability_receipt_v2_expiry",
        ),
        Index(
            "ix_authored_capability_receipt_v2_active_lookup",
            "tenant_id",
            "capability_id",
            "scope_digest",
            "environment_digest",
            "expires_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    capability_id: str = Field(sa_column=Column(String(128), nullable=False))
    state: str = Field(sa_column=Column(String(16), nullable=False))
    scope_digest: str = Field(sa_column=Column(String(71), nullable=False))
    environment_digest: str = Field(sa_column=Column(String(71), nullable=False))
    serializer_id: str = Field(sa_column=Column(String(128), nullable=False))
    serializer_version: str = Field(sa_column=Column(String(128), nullable=False))
    issuer_principal_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    issuer_authority: str = Field(sa_column=Column(String(32), nullable=False))
    evaluated_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    transition_kind: str = Field(sa_column=Column(String(16), nullable=False))
    transition_target_digest: str | None = Field(
        default=None, sa_column=Column(String(71), nullable=True)
    )
    receipt_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )


__all__ = ["AuthoredCapabilityReceiptRecordV2"]
