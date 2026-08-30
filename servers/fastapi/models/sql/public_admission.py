"""Durable public-service admission, quota, and provider-call accounting."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
    true,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class PublicPolicyProfile(SQLModel, table=True):
    """Versioned operational policy; public jobs always pin one immutable row."""

    __tablename__ = "public_policy_profiles"
    __table_args__ = (
        CheckConstraint("max_slides > 0", name="ck_public_policy_max_slides"),
        CheckConstraint(
            "global_active_generation > 0 AND global_queued_generation >= 0 "
            "AND owner_active_generation > 0 AND owner_queued_generation >= 0 "
            "AND tenant_active_generation > 0 AND tenant_queued_generation >= 0 "
            "AND global_active_export > 0 AND global_queued_export >= 0 "
            "AND owner_active_export > 0 AND owner_queued_export >= 0 "
            "AND tenant_active_export > 0 AND tenant_queued_export >= 0",
            name="ck_public_policy_quota_limits",
        ),
        CheckConstraint(
            "global_active_resource_units > 0 AND global_queued_resource_units >= 0 "
            "AND tenant_active_resource_units > 0 AND tenant_queued_resource_units >= 0 "
            "AND owner_active_resource_units > 0 AND owner_queued_resource_units >= 0",
            name="ck_public_policy_resource_limits",
        ),
        CheckConstraint(
            "retry_request_limit >= 0 AND estimated_cost_limit_microunits > 0 "
            "AND actual_cost_limit_microunits > 0",
            name="ck_public_policy_budget_limits",
        ),
    )

    version: str = Field(sa_column=Column(String(64), primary_key=True))
    enabled: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=true()),
    )
    max_slides: int = Field(sa_column=Column(Integer, nullable=False))
    global_active_generation: int = Field(sa_column=Column(Integer, nullable=False))
    global_queued_generation: int = Field(sa_column=Column(Integer, nullable=False))
    owner_active_generation: int = Field(sa_column=Column(Integer, nullable=False))
    owner_queued_generation: int = Field(sa_column=Column(Integer, nullable=False))
    tenant_active_generation: int = Field(sa_column=Column(Integer, nullable=False))
    tenant_queued_generation: int = Field(sa_column=Column(Integer, nullable=False))
    global_active_export: int = Field(sa_column=Column(Integer, nullable=False))
    global_queued_export: int = Field(sa_column=Column(Integer, nullable=False))
    owner_active_export: int = Field(sa_column=Column(Integer, nullable=False))
    owner_queued_export: int = Field(sa_column=Column(Integer, nullable=False))
    tenant_active_export: int = Field(sa_column=Column(Integer, nullable=False))
    tenant_queued_export: int = Field(sa_column=Column(Integer, nullable=False))
    global_active_resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    global_queued_resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    tenant_active_resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    tenant_queued_resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    owner_active_resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    owner_queued_resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    retry_request_limit: int = Field(sa_column=Column(Integer, nullable=False))
    estimated_cost_limit_microunits: int = Field(
        sa_column=Column(BigInteger, nullable=False)
    )
    actual_cost_limit_microunits: int = Field(
        sa_column=Column(BigInteger, nullable=False)
    )
    generation_deadline_seconds: int = Field(sa_column=Column(Integer, nullable=False))
    export_deadline_seconds: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class PublicQuotaCounter(SQLModel, table=True):
    """Lockable cluster-wide counters for one owner or tenant and operation kind."""

    __tablename__ = "public_quota_counters"
    __table_args__ = (
        UniqueConstraint(
            "policy_version", "scope_type", "scope_key", "operation_kind",
            name="uq_public_quota_counter_scope",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'tenant', 'owner')",
            name="ck_public_quota_scope",
        ),
        CheckConstraint(
            "(scope_type = 'global' AND scope_key = 'global') OR "
            "(scope_type <> 'global' AND length(scope_key) > 0)",
            name="ck_public_quota_scope_key",
        ),
        CheckConstraint(
            "operation_kind IN ('generation', 'export')",
            name="ck_public_quota_operation_kind",
        ),
        CheckConstraint(
            "active_count >= 0 AND queued_count >= 0 "
            "AND active_resource_units >= 0 AND queued_resource_units >= 0 "
            "AND version >= 0",
            name="ck_public_quota_counts",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    policy_version: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("public_policy_profiles.version", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    scope_type: str = Field(sa_column=Column(String(16), nullable=False))
    scope_key: str = Field(sa_column=Column(String(128), nullable=False))
    operation_kind: str = Field(sa_column=Column(String(16), nullable=False))
    active_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    queued_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    active_resource_units: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    queued_resource_units: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    version: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    last_activated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class PublicOperationAdmission(SQLModel, table=True):
    """Principal-bound policy/budget snapshot for one durable operation."""

    __tablename__ = "public_operation_admissions"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_public_operation_admission_operation"),
        CheckConstraint(
            "operation_kind IN ('generation', 'export')",
            name="ck_public_operation_admission_kind",
        ),
        CheckConstraint(
            "state IN ('queued', 'active', 'succeeded', 'failed', 'cancelled', "
            "'deadline_exceeded', 'budget_exhausted')",
            name="ck_public_operation_admission_state",
        ),
        CheckConstraint(
            "resource_units > 0 AND fence >= 0 AND length(policy_digest) = 64",
            name="ck_public_operation_admission_control",
        ),
        CheckConstraint(
            "planned_request_limit >= 0 AND retry_request_limit >= 0 "
            "AND initial_request_count >= 0 AND retry_request_count >= 0",
            name="ck_public_operation_admission_requests",
        ),
        CheckConstraint(
            "estimated_cost_limit_microunits >= 0 "
            "AND actual_cost_limit_microunits >= 0 "
            "AND estimated_reserved_microunits >= 0 "
            "AND estimated_consumed_microunits >= 0 "
            "AND actual_reserved_microunits >= 0 "
            "AND actual_consumed_microunits >= 0 "
            "AND actual_cost_unavailable_count >= 0",
            name="ck_public_operation_admission_costs",
        ),
        Index(
            "ix_public_operation_admission_queue",
            "policy_version", "operation_kind", "state", "queued_at",
        ),
        Index(
            "ix_public_operation_admission_owner",
            "tenant_id", "owner_id", "operation_kind", "state",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    operation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("workspace_operations.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    policy_version: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("public_policy_profiles.version", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    owner_id: uuid.UUID = Field(
        sa_column=Column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    )
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    trace_id_hash: str = Field(sa_column=Column(String(64), nullable=False))
    operation_kind: str = Field(sa_column=Column(String(16), nullable=False))
    resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    policy_digest: str = Field(sa_column=Column(String(64), nullable=False))
    deadline_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    fence: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    state: str = Field(
        default="queued",
        sa_column=Column(String(24), nullable=False, server_default="queued"),
    )
    slide_count: int = Field(default=0, sa_column=Column(Integer, nullable=False))
    planned_request_limit: int = Field(sa_column=Column(Integer, nullable=False))
    retry_request_limit: int = Field(sa_column=Column(Integer, nullable=False))
    estimated_cost_limit_microunits: int = Field(
        sa_column=Column(BigInteger, nullable=False)
    )
    actual_cost_limit_microunits: int = Field(sa_column=Column(BigInteger, nullable=False))
    initial_request_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    retry_request_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    estimated_reserved_microunits: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    estimated_consumed_microunits: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    actual_reserved_microunits: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    actual_consumed_microunits: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    actual_cost_unavailable_count: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    queued_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    activated_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    terminal_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class PublicResourceLease(SQLModel, table=True):
    """One scope reservation in the cluster-wide weighted public resource lease."""

    __tablename__ = "public_resource_leases"
    __table_args__ = (
        UniqueConstraint(
            "admission_id", "scope_type", "scope_key",
            name="uq_public_resource_lease_admission_scope",
        ),
        CheckConstraint(
            "scope_type IN ('global', 'tenant', 'owner')",
            name="ck_public_resource_lease_scope",
        ),
        CheckConstraint(
            "(scope_type = 'global' AND scope_key = 'global') OR "
            "(scope_type <> 'global' AND length(scope_key) > 0)",
            name="ck_public_resource_lease_scope_key",
        ),
        CheckConstraint(
            "operation_kind IN ('generation', 'export')",
            name="ck_public_resource_lease_kind",
        ),
        CheckConstraint(
            "state IN ('queued', 'leased', 'released', 'cancelled', 'expired')",
            name="ck_public_resource_lease_state",
        ),
        CheckConstraint(
            "resource_units > 0 AND attempt >= 0 AND fence >= 0 AND version >= 0",
            name="ck_public_resource_lease_control",
        ),
        CheckConstraint(
            "lease_expires_at IS NULL OR lease_expires_at <= deadline_at",
            name="ck_public_resource_lease_deadline",
        ),
        Index(
            "ix_public_resource_lease_claim",
            "policy_version", "operation_kind", "state", "reserved_at",
        ),
        Index(
            "ix_public_resource_lease_operation_fence",
            "operation_id", "fence", "state",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    admission_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("public_operation_admissions.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    operation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("workspace_operations.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    policy_version: str = Field(
        sa_column=Column(
            String(64),
            ForeignKey("public_policy_profiles.version", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    scope_type: str = Field(sa_column=Column(String(16), nullable=False))
    scope_key: str = Field(sa_column=Column(String(128), nullable=False))
    operation_kind: str = Field(sa_column=Column(String(16), nullable=False))
    resource_units: int = Field(sa_column=Column(BigInteger, nullable=False))
    state: str = Field(
        default="queued",
        sa_column=Column(String(16), nullable=False, server_default="queued"),
    )
    attempt: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    fence: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    lease_owner_hash: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    deadline_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    reserved_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    leased_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    heartbeat_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    lease_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    released_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    version: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )


class PublicProviderLogicalCall(SQLModel, table=True):
    """One idempotent pre-I/O reservation and post-I/O receipt."""

    __tablename__ = "public_provider_logical_calls"
    __table_args__ = (
        UniqueConstraint(
            "admission_id", "stage", "slide_index", "logical_call_ordinal",
            name="uq_public_provider_logical_call",
        ),
        CheckConstraint(
            "state IN ('reserved', 'reported', 'unavailable', 'failed', 'cancelled')",
            name="ck_public_provider_logical_call_state",
        ),
        CheckConstraint(
            "retry_class IN ('initial', 'retry', 'resume', 'recovery')",
            name="ck_public_provider_logical_call_retry_class",
        ),
        CheckConstraint(
            "slide_index >= -1 AND logical_call_ordinal >= 1",
            name="ck_public_provider_logical_call_identity",
        ),
        CheckConstraint(
            "estimated_cost_microunits >= 0 "
            "AND (actual_cost_microunits IS NULL OR actual_cost_microunits >= 0) "
            "AND latency_ms >= 0",
            name="ck_public_provider_logical_call_costs",
        ),
        Index(
            "ix_public_provider_logical_call_stage",
            "admission_id", "stage", "slide_index", "logical_call_ordinal",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    admission_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("public_operation_admissions.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    stage: str = Field(sa_column=Column(String(64), nullable=False))
    slide_index: int = Field(default=-1, sa_column=Column(Integer, nullable=False))
    logical_call_ordinal: int = Field(sa_column=Column(Integer, nullable=False))
    retry_class: str = Field(sa_column=Column(String(16), nullable=False))
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    provider_id: str = Field(
        default="unavailable",
        sa_column=Column(String(64), nullable=False, server_default="unavailable"),
    )
    model_id: str = Field(
        default="unavailable",
        sa_column=Column(String(128), nullable=False, server_default="unavailable"),
    )
    pricing_revision: str = Field(
        default="unpriced-v1",
        sa_column=Column(String(64), nullable=False, server_default="unpriced-v1"),
    )
    provider_idempotency_capable: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("0")),
    )
    state: str = Field(
        default="reserved",
        sa_column=Column(String(16), nullable=False, server_default="reserved"),
    )
    estimated_cost_microunits: int = Field(sa_column=Column(BigInteger, nullable=False))
    actual_cost_microunits: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    input_tokens: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    output_tokens: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    latency_ms: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    outcome: str | None = Field(default=None, sa_column=Column(String(24), nullable=True))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    reconciled_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )


class AuthoredDeckPlanCheckpoint(SQLModel, table=True):
    """Digest-bound deck-wide planning result reusable across fenced attempts."""

    __tablename__ = "authored_deck_plan_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "generation_job_id", "stage", name="uq_authored_deck_plan_checkpoint_stage"
        ),
        CheckConstraint(
            "stage IN ('deck_plan', 'chapter_plan', 'header', 'illustration')",
            name="ck_authored_deck_plan_checkpoint_stage",
        ),
        CheckConstraint(
            "length(input_digest) = 64 AND length(output_digest) = 64",
            name="ck_authored_deck_plan_checkpoint_digests",
        ),
        CheckConstraint(
            "produced_attempt_number >= 1",
            name="ck_authored_deck_plan_checkpoint_attempt",
        ),
        Index(
            "ix_authored_deck_plan_checkpoint_job",
            "generation_job_id",
            "stage",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    generation_job_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("presentation_generation_jobs.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    stage: str = Field(sa_column=Column(String(24), nullable=False))
    input_digest: str = Field(sa_column=Column(String(64), nullable=False))
    output_digest: str = Field(sa_column=Column(String(64), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    produced_attempt_number: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


__all__ = [
    "AuthoredDeckPlanCheckpoint",
    "PublicOperationAdmission",
    "PublicPolicyProfile",
    "PublicProviderLogicalCall",
    "PublicQuotaCounter",
    "PublicResourceLease",
]
