"""Owner-scoped persistence for source-content restyle contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


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
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=True,
        default=get_current_owner_id,
    )


class SourceContentManifestRecord(SQLModel, table=True):
    __tablename__ = "source_content_manifests"
    __table_args__ = (
        CheckConstraint(
            "contract_version = 'source-content-manifest/v1'",
            name="ck_source_content_manifest_contract",
        ),
        CheckConstraint("source_type = 'pptx'", name="ck_source_content_manifest_type"),
        Index(
            "ix_source_content_manifest_owner_snapshot",
            "owner_scope",
            "reference_snapshot_id",
            "created_at",
        ),
    )

    manifest_id: str = Field(sa_column=Column(String(68), primary_key=True))
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=_owner_column(),
    )
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    reference_snapshot_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("reference_snapshots.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    contract_version: str = Field(sa_column=Column(String(64), nullable=False))
    source_type: str = Field(sa_column=Column(String(16), nullable=False))
    source_digest: str = Field(sa_column=Column(String(71), nullable=False))
    manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    manifest_json: dict = Field(sa_column=Column(JSON, nullable=False))
    inventory_json: dict = Field(sa_column=Column(JSON, nullable=False))
    counts_json: dict = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class SourceContentManifestAsset(SQLModel, table=True):
    __tablename__ = "source_content_manifest_assets"
    __table_args__ = (
        UniqueConstraint(
            "manifest_id",
            "asset_id",
            name="uq_source_content_manifest_asset",
        ),
        Index("ix_source_content_manifest_asset_digest", "manifest_id", "content_digest"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    manifest_id: str = Field(
        sa_column=Column(
            String(68),
            ForeignKey("source_content_manifests.manifest_id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    asset_id: str = Field(sa_column=Column(String(80), nullable=False))
    media_type: str = Field(sa_column=Column(String(255), nullable=False))
    content_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())


class SourceContentGenerationBinding(SQLModel, table=True):
    __tablename__ = "source_content_generation_bindings"
    __table_args__ = (
        CheckConstraint(
            "state IN ('admitted', 'claimed', 'cancelled', 'failed', 'published')",
            name="ck_source_content_generation_binding_state",
        ),
        UniqueConstraint(
            "owner_scope",
            "generation_identity_digest",
            name="uq_source_content_generation_identity",
        ),
        Index(
            "ix_source_content_generation_binding_manifest",
            "owner_scope",
            "manifest_id",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=_owner_column(),
    )
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    manifest_id: str = Field(
        sa_column=Column(
            String(68),
            ForeignKey("source_content_manifests.manifest_id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    generation_job_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey("presentation_generation_jobs.id", ondelete="CASCADE"),
            nullable=True,
            unique=True,
        ),
    )
    presentation_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey("presentations.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    composition_pin: dict = Field(sa_column=Column(JSON, nullable=False))
    content_policy_digest: str = Field(sa_column=Column(String(71), nullable=False))
    generation_identity_digest: str = Field(sa_column=Column(String(71), nullable=False))
    state: str = Field(default="admitted", sa_column=Column(String(24), nullable=False))
    attempt_token: Optional[str] = Field(default=None, sa_column=Column(String(128), nullable=True))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column(update=True))


class SourceContentCoverageReceipt(SQLModel, table=True):
    __tablename__ = "source_content_coverage_receipts"
    __table_args__ = (
        CheckConstraint(
            "contract_version = 'source-content-coverage-receipt/v1'",
            name="ck_source_content_coverage_contract",
        ),
        CheckConstraint(
            "edit_state IN ('pristine', 'stale')",
            name="ck_source_content_coverage_edit_state",
        ),
        CheckConstraint(
            "status IN ('passed', 'failed')",
            name="ck_source_content_coverage_status",
        ),
        CheckConstraint(
            "presentation_revision >= 0",
            name="ck_source_content_coverage_revision",
        ),
        UniqueConstraint(
            "owner_scope",
            "presentation_id",
            "presentation_revision",
            "candidate_artifact_digest",
            name="uq_source_content_coverage_artifact_revision",
        ),
        Index(
            "ix_source_content_coverage_publication",
            "owner_scope",
            "presentation_id",
            "presentation_revision",
            "edit_state",
            "status",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=_owner_column(),
    )
    owner_scope: str = Field(sa_column=Column(String(128), nullable=False))
    contract_version: str = Field(sa_column=Column(String(64), nullable=False))
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("presentations.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    presentation_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    plan_digest: str = Field(sa_column=Column(String(71), nullable=False))
    scene_digest: str = Field(sa_column=Column(String(71), nullable=False))
    candidate_artifact_digest: str = Field(sa_column=Column(String(71), nullable=False))
    export_readback_digest: str = Field(sa_column=Column(String(71), nullable=False))
    receipt_json: dict = Field(sa_column=Column(JSON, nullable=False))
    edit_state: str = Field(default="pristine", sa_column=Column(String(16), nullable=False))
    status: str = Field(sa_column=Column(String(16), nullable=False))
    superseded_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column())
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_utc_column(update=True))
