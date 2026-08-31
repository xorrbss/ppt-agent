from __future__ import annotations

import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class AccountDeletionRequest(SQLModel, table=True):
    """Durable controller outbox for one account-deletion generation."""

    __tablename__ = "account_deletion_requests"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'processing', 'retry')",
            name="ck_account_deletion_request_state",
        ),
        CheckConstraint(
            "generation >= 1 AND claim_fence >= 0 AND attempt_count >= 0",
            name="ck_account_deletion_request_counters",
        ),
        CheckConstraint(
            "(state = 'processing' AND claimed_by IS NOT NULL "
            "AND claim_expires_at IS NOT NULL) OR "
            "(state <> 'processing' AND claimed_by IS NULL "
            "AND claim_expires_at IS NULL)",
            name="ck_account_deletion_request_claim",
        ),
        Index(
            "ix_account_deletion_request_claim",
            "state",
            "available_at",
            "claim_expires_at",
            "requested_at",
        ),
    )

    owner_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("user.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    generation: int = Field(sa_column=Column(BigInteger, nullable=False))
    state: str = Field(
        default="pending",
        sa_column=Column(String(16), nullable=False, server_default="pending"),
    )
    claim_fence: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    claimed_by: str | None = Field(
        default=None,
        sa_column=Column(String(160), nullable=True),
    )
    claim_expires_at: datetime.datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    available_at: datetime.datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    requested_at: datetime.datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    updated_at: datetime.datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
    last_error_code: str | None = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )


__all__ = ["AccountDeletionRequest"]
