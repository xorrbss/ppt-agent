"""Durable, scalar-only provider budget/RPM/circuit reservations."""

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
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class ProviderRuntimeBudget(SQLModel, table=True):
    """One durable accounting row for a user-scoped provider job."""

    __tablename__ = "provider_runtime_budgets"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "owner_id", "job_id", name="uq_provider_runtime_budget_job"
        ),
        CheckConstraint(
            "state IN ('active', 'cancelled', 'completed')",
            name="ck_provider_runtime_budget_state",
        ),
        CheckConstraint(
            "estimated_limit IS NULL OR estimated_limit >= 0",
            name="ck_provider_runtime_budget_estimated_limit",
        ),
        CheckConstraint(
            "actual_limit IS NULL OR actual_limit >= 0",
            name="ck_provider_runtime_budget_actual_limit",
        ),
        CheckConstraint(
            "estimated_consumed >= 0 AND estimated_reserved >= 0 "
            "AND actual_consumed >= 0 AND request_count >= 0 AND version >= 0",
            name="ck_provider_runtime_budget_counters",
        ),
        Index("ix_provider_runtime_budget_owner", "tenant_id", "owner_id", "state"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    owner_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    job_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("presentation_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    state: str = Field(
        default="active",
        sa_column=Column(String(16), nullable=False, server_default="active"),
    )
    cancellation_reason: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    estimated_limit: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    actual_limit: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    estimated_consumed: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    estimated_reserved: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    actual_consumed: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    request_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    version: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class ProviderRuntimeReservation(SQLModel, table=True):
    """Idempotent estimated-cost reservation fenced by a unique digest."""

    __tablename__ = "provider_runtime_reservations"
    __table_args__ = (
        UniqueConstraint(
            "budget_id", "reservation_key_hash", name="uq_provider_runtime_reservation"
        ),
        CheckConstraint(
            "state IN ('reserved', 'reconciled', 'cancelled', 'uncertain')",
            name="ck_provider_runtime_reservation_state",
        ),
        CheckConstraint(
            "estimated_cost >= 0 AND (actual_cost IS NULL OR actual_cost >= 0)",
            name="ck_provider_runtime_reservation_cost",
        ),
        Index("ix_provider_runtime_reservation_state", "state", "lease_expires_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    budget_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("provider_runtime_budgets.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    reservation_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    state: str = Field(
        default="reserved",
        sa_column=Column(String(16), nullable=False, server_default="reserved"),
    )
    estimated_cost: int = Field(sa_column=Column(BigInteger, nullable=False))
    actual_cost: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    outcome: str | None = Field(default=None, sa_column=Column(String(24), nullable=True))
    lease_token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    lease_expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class ProviderRuntimeCircuit(SQLModel, table=True):
    """Provider-wide durable RPM window and circuit-breaker CAS row."""

    __tablename__ = "provider_runtime_circuits"
    __table_args__ = (
        UniqueConstraint("service_key", name="uq_provider_runtime_circuit_service"),
        CheckConstraint(
            "state IN ('closed', 'open', 'half_open')",
            name="ck_provider_runtime_circuit_state",
        ),
        CheckConstraint(
            "rpm_count >= 0 AND consecutive_failures >= 0 AND version >= 0",
            name="ck_provider_runtime_circuit_counters",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    service_key: str = Field(sa_column=Column(String(128), nullable=False))
    state: str = Field(
        default="closed",
        sa_column=Column(String(16), nullable=False, server_default="closed"),
    )
    rpm_window_started_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    rpm_count: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    consecutive_failures: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default=text("0")),
    )
    open_until: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    half_open_lease_hash: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    version: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
