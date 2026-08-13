"""Privacy-safe provider usage and cost receipts.

This table deliberately has no JSON payload, request identifier, URL, prompt, or
exception fields.  It is an accounting record, not a provider request log.
"""

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


class ProviderUsageReceipt(SQLModel, table=True):
    """One idempotent, scalar-only provider or local-stage usage receipt."""

    __tablename__ = "provider_usage_receipts"
    __table_args__ = (
        UniqueConstraint(
            "receipt_key_hash", name="uq_provider_usage_receipts_key_hash"
        ),
        CheckConstraint(
            "usage_state IN ('reported', 'estimated', 'unavailable')",
            name="ck_provider_usage_receipts_usage_state",
        ),
        CheckConstraint(
            "retry_class IN ('initial', 'retry', 'resume', 'recovery')",
            name="ck_provider_usage_receipts_retry_class",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'cancelled')",
            name="ck_provider_usage_receipts_outcome",
        ),
        CheckConstraint(
            "request_count > 0", name="ck_provider_usage_receipts_request_count"
        ),
        CheckConstraint(
            "latency_ms >= 0", name="ck_provider_usage_receipts_latency"
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_provider_usage_receipts_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_provider_usage_receipts_output_tokens",
        ),
        CheckConstraint(
            "reported_cost_microunits IS NULL OR reported_cost_microunits >= 0",
            name="ck_provider_usage_receipts_reported_cost",
        ),
        CheckConstraint(
            "estimated_cost_microunits IS NULL OR estimated_cost_microunits >= 0",
            name="ck_provider_usage_receipts_estimated_cost",
        ),
        CheckConstraint(
            "usage_state != 'unavailable' OR (input_tokens IS NULL AND "
            "output_tokens IS NULL AND reported_cost_microunits IS NULL AND "
            "estimated_cost_microunits IS NULL)",
            name="ck_provider_usage_receipts_unavailable_is_null",
        ),
        CheckConstraint(
            "usage_state != 'reported' OR estimated_cost_microunits IS NULL",
            name="ck_provider_usage_receipts_reported_source",
        ),
        CheckConstraint(
            "usage_state != 'estimated' OR reported_cost_microunits IS NULL",
            name="ck_provider_usage_receipts_estimated_source",
        ),
        CheckConstraint(
            "(reported_cost_microunits IS NULL AND estimated_cost_microunits IS NULL) "
            "OR pricing_version IS NOT NULL",
            name="ck_provider_usage_receipts_priced_versioned",
        ),
        Index(
            "ix_provider_usage_receipts_job_stage",
            "job_id",
            "operation",
            "stage",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    job_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentation_generation_jobs.id",
                name="fk_provider_usage_receipts_job_id",
                ondelete="CASCADE",
            ),
            nullable=False,
            index=True,
        )
    )
    operation: str = Field(sa_column=Column(String(64), nullable=False))
    stage: str = Field(sa_column=Column(String(64), nullable=False))
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    model: str = Field(sa_column=Column(String(128), nullable=False))
    # Only a SHA-256 digest of an internally derived idempotency identity.
    receipt_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    retry_class: str = Field(sa_column=Column(String(16), nullable=False))
    outcome: str = Field(
        default="succeeded",
        sa_column=Column(
            String(16), nullable=False, default="succeeded", server_default="succeeded"
        ),
    )
    latency_ms: int = Field(sa_column=Column(BigInteger, nullable=False))
    usage_state: str = Field(sa_column=Column(String(16), nullable=False))
    input_tokens: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    output_tokens: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    request_count: int = Field(
        default=1,
        sa_column=Column(
            Integer, nullable=False, default=1, server_default=text("1")
        ),
    )
    # Integer micro-currency units avoid float rounding and make source explicit.
    reported_cost_microunits: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    estimated_cost_microunits: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    pricing_version: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
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
