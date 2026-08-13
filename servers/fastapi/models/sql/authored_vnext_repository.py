"""Durable authored-vNext revisions, publications, and export receipts.

Revision, decision, candidate, and receipt rows are append-only.  Small head and
publication rows are the only mutable records and are advanced with SQL CAS.
"""

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
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


def _utc_column(*, update: bool = False) -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        default=get_current_utc_datetime,
        onupdate=get_current_utc_datetime if update else None,
        server_default=func.now(),
    )


def _owner_column() -> Column:
    return Column(
        Uuid,
        ForeignKey(
            "user.id",
            name=None,
            ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )


class AuthoredVNextMasterRevision(SQLModel, table=True):
    __tablename__ = "authored_vnext_master_revisions"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "master_id", "revision", name="uq_avnext_master_revision"),
        CheckConstraint("revision >= 1", name="ck_avnext_master_revision_positive"),
        CheckConstraint("status IN ('draft', 'published')", name="ck_avnext_master_status"),
        Index("ix_avnext_master_scope_id_created", "owner_scope", "tenant_id", "master_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    master_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    status: str = Field(sa_column=Column(String(16), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextSlideRevision(SQLModel, table=True):
    __tablename__ = "authored_vnext_slide_revisions"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope", "tenant_id", "deck_id", "slide_id", "revision", name="uq_avnext_slide_revision"
        ),
        CheckConstraint("revision >= 1", name="ck_avnext_slide_revision_positive"),
        Index("ix_avnext_slide_scope_deck", "owner_scope", "tenant_id", "deck_id", "slide_id"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    slide_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    spec_digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextDeckRevision(SQLModel, table=True):
    __tablename__ = "authored_vnext_deck_revisions"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "deck_id", "revision", name="uq_avnext_deck_revision"),
        CheckConstraint("revision >= 1", name="ck_avnext_deck_revision_positive"),
        Index("ix_avnext_deck_scope_id_created", "owner_scope", "tenant_id", "deck_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_id: str = Field(sa_column=Column(String(256), nullable=False))
    master_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    master_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextEntityHead(SQLModel, table=True):
    __tablename__ = "authored_vnext_entity_heads"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "entity_kind", "entity_id", name="uq_avnext_entity_head"),
        CheckConstraint("entity_kind IN ('master', 'deck')", name="ck_avnext_head_kind"),
        CheckConstraint("revision >= 1 AND generation >= 1", name="ck_avnext_head_versions"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    entity_kind: str = Field(sa_column=Column(String(16), nullable=False))
    entity_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    generation: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default="1"))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column(update=True))


class AuthoredVNextCandidate(SQLModel, table=True):
    __tablename__ = "authored_vnext_candidates"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "candidate_digest", name="uq_avnext_candidate_digest"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    candidate_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextQualityDecision(SQLModel, table=True):
    __tablename__ = "authored_vnext_quality_decisions"
    __table_args__ = (
        UniqueConstraint("owner_scope", "tenant_id", "decision_key_hash", name="uq_avnext_quality_decision_key"),
        CheckConstraint("decision IN ('approved', 'rejected')", name="ck_avnext_quality_decision"),
        Index("ix_avnext_quality_deck_revision", "owner_scope", "tenant_id", "deck_id", "deck_revision"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    deck_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    deck_digest: str = Field(sa_column=Column(String(71), nullable=False))
    decision_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    decision: str = Field(sa_column=Column(String(16), nullable=False))
    policy_version: str = Field(sa_column=Column(String(128), nullable=False))
    evidence_digest: str = Field(sa_column=Column(String(71), nullable=False))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextExportJob(SQLModel, table=True):
    __tablename__ = "authored_vnext_export_jobs"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope", "tenant_id", "deck_id", "idempotency_key_hash", name="uq_avnext_export_idempotency"
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'cancel_requested', 'succeeded', 'failed', 'cancelled')",
            name="ck_avnext_export_state",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND fence_token >= 0 AND operation_fence >= 1 "
            "AND account_deletion_generation >= 0",
            name="ck_avnext_export_counters",
        ),
        CheckConstraint("deck_revision >= 1 AND master_revision >= 1", name="ck_avnext_export_source_revisions"),
        CheckConstraint("timeout_ms > 0 AND max_attempts > 0", name="ck_avnext_export_limits"),
        CheckConstraint(
            "(expected_publication_revision IS NULL AND expected_publication_digest IS NULL) OR "
            "(expected_publication_revision >= 1 AND expected_publication_digest IS NOT NULL)",
            name="ck_avnext_export_expected_publication",
        ),
        Index("ix_avnext_export_claim", "state", "lease_expires_at", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    deck_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    deck_digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_id: str = Field(sa_column=Column(String(256), nullable=False))
    master_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    master_digest: str = Field(sa_column=Column(String(71), nullable=False))
    spec_digest: str = Field(sa_column=Column(String(71), nullable=False))
    asset_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    font_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    expected_package_digest: str | None = Field(default=None, sa_column=Column(String(71), nullable=True))
    expected_publication_revision: int | None = Field(default=None, sa_column=Column(BigInteger, nullable=True))
    expected_publication_digest: str | None = Field(default=None, sa_column=Column(String(71), nullable=True))
    timeout_ms: int = Field(default=120000, sa_column=Column(BigInteger, nullable=False, server_default="120000"))
    max_attempts: int = Field(default=3, sa_column=Column(Integer, nullable=False, server_default="3"))
    request_hash: str = Field(sa_column=Column(String(64), nullable=False))
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    state: str = Field(default="queued", sa_column=Column(String(24), nullable=False, server_default="queued"))
    attempt_count: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default="0"))
    operation_fence: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default="1"))
    account_deletion_generation: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default="0")
    )
    fence_token: int = Field(default=0, sa_column=Column(BigInteger, nullable=False, server_default="0"))
    worker_id: str | None = Field(default=None, sa_column=Column(String(256), nullable=True))
    lease_expires_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))
    cancel_requested: bool = Field(
        default=False, sa_column=Column(Boolean, nullable=False, server_default=text("false"))
    )
    failure_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    failure_detail: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column(update=True))
    completed_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=True))


class AuthoredVNextExportArtifactReceipt(SQLModel, table=True):
    __tablename__ = "authored_vnext_export_artifact_receipts"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_avnext_artifact_job"),
        UniqueConstraint("owner_scope", "tenant_id", "storage_key", name="uq_avnext_artifact_storage"),
        CheckConstraint("size_bytes >= 0", name="ck_avnext_artifact_size"),
        CheckConstraint("readback_verified = true", name="ck_avnext_artifact_readback"),
        Index("ix_avnext_artifact_deck_created", "owner_scope", "tenant_id", "deck_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    job_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    deck_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    storage_key: str = Field(sa_column=Column(String(1024), nullable=False))
    artifact_digest: str = Field(sa_column=Column(String(71), nullable=False))
    readback_digest: str = Field(sa_column=Column(String(71), nullable=False))
    package_digest: str = Field(sa_column=Column(String(71), nullable=False))
    size_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    master_digest: str = Field(sa_column=Column(String(71), nullable=False))
    spec_digest: str = Field(sa_column=Column(String(71), nullable=False))
    asset_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    font_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    readback_verified: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true"))
    )
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextRenderReceipt(SQLModel, table=True):
    __tablename__ = "authored_vnext_render_receipts"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope",
            "tenant_id",
            "idempotency_key_hash",
            name="uq_avnext_render_receipt_key",
        ),
        CheckConstraint(
            "deck_revision >= 1 AND slide_revision >= 1",
            name="ck_avnext_render_revisions",
        ),
        CheckConstraint(
            "width_px > 0 AND height_px > 0",
            name="ck_avnext_render_dimensions",
        ),
        Index(
            "ix_avnext_render_deck_slide",
            "owner_scope",
            "tenant_id",
            "deck_id",
            "slide_id",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    deck_id: str = Field(sa_column=Column(String(256), nullable=False))
    deck_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    slide_id: str = Field(sa_column=Column(String(256), nullable=False))
    slide_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    renderer: str = Field(sa_column=Column(String(128), nullable=False))
    idempotency_key_hash: str = Field(sa_column=Column(String(64), nullable=False))
    input_digest: str = Field(sa_column=Column(String(71), nullable=False))
    output_digest: str = Field(sa_column=Column(String(71), nullable=False))
    storage_key: str = Field(sa_column=Column(String(1024), nullable=False))
    width_px: int = Field(sa_column=Column(Integer, nullable=False))
    height_px: int = Field(sa_column=Column(Integer, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class AuthoredVNextPublicationPointer(SQLModel, table=True):
    __tablename__ = "authored_vnext_publication_pointers"
    __table_args__ = (
        UniqueConstraint(
            "owner_scope", "tenant_id", "publication_kind", "entity_id", name="uq_avnext_publication_pointer"
        ),
        CheckConstraint("publication_kind IN ('master', 'deck_artifact')", name="ck_avnext_publication_kind"),
        CheckConstraint("revision >= 1 AND generation >= 1", name="ck_avnext_publication_versions"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=_owner_column())
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    publication_kind: str = Field(sa_column=Column(String(32), nullable=False))
    entity_id: str = Field(sa_column=Column(String(256), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    digest: str = Field(sa_column=Column(String(71), nullable=False))
    artifact_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    generation: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default="1"))
    operation_id: uuid.UUID = Field(default_factory=uuid.uuid4, sa_column=Column(Uuid, nullable=False))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column(update=True))


_IMMUTABLE_MODELS = (
    AuthoredVNextMasterRevision,
    AuthoredVNextSlideRevision,
    AuthoredVNextDeckRevision,
    AuthoredVNextCandidate,
    AuthoredVNextQualityDecision,
    AuthoredVNextExportArtifactReceipt,
    AuthoredVNextRenderReceipt,
)


def _reject_immutable_change(*_args: object) -> None:
    raise ValueError("authored_vnext_record_is_append_only")


for _model in _IMMUTABLE_MODELS:
    event.listen(_model, "before_update", _reject_immutable_change)
    event.listen(_model, "before_delete", _reject_immutable_change)


AUTHORED_VNEXT_SQL_TABLES = tuple(
    model.__table__
    for model in (
        AuthoredVNextMasterRevision,
        AuthoredVNextSlideRevision,
        AuthoredVNextDeckRevision,
        AuthoredVNextEntityHead,
        AuthoredVNextCandidate,
        AuthoredVNextQualityDecision,
        AuthoredVNextExportJob,
        AuthoredVNextExportArtifactReceipt,
        AuthoredVNextRenderReceipt,
        AuthoredVNextPublicationPointer,
    )
)
