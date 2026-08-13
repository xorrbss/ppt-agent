"""Durable operation state for the authored-vNext public write path."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class AuthoredVNextGenerationOperation(SQLModel, table=True):
    """Idempotent command receipt with a lease and monotonically increasing fence."""

    __tablename__ = "authored_vnext_generation_operations"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "operation_id",
            name="uq_avnext_generation_operation_id",
        ),
        UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "kind",
            "idempotency_key_hash",
            name="uq_avnext_generation_operation_idempotency",
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_avnext_generation_operation_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND fencing_token >= 0 AND version >= 0 "
            "AND account_deletion_generation >= 0",
            name="ck_avnext_generation_operation_counters",
        ),
        Index(
            "ix_avnext_generation_operation_dispatch",
            "state",
            "lease_expires_at",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    operation_id: str = Field(sa_column=Column(String(256), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    kind: str = Field(sa_column=Column(String(64), nullable=False))
    state: str = Field(
        default="queued",
        sa_column=Column(String(24), nullable=False, server_default="queued"),
    )
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    request_payload: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    checkpoint_payload: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    result_payload: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    expected_revision: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    expected_digest: str | None = Field(
        default=None,
        sa_column=Column(String(71), nullable=True),
    )
    result_revision: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
    )
    result_digest: str | None = Field(
        default=None,
        sa_column=Column(String(71), nullable=True),
    )
    account_deletion_generation: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    worker_id: str | None = Field(default=None, sa_column=Column(String(256), nullable=True))
    attempt_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    fencing_token: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    version: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    lease_expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    heartbeat_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    error_detail: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
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
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
