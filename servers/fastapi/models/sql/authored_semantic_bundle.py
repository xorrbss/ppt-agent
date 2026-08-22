"""Immutable Authored semantic bundle graph and the single mutable CAS pointer."""

from __future__ import annotations

from datetime import datetime
from typing import Any
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


def _created_at() -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        default=get_current_utc_datetime,
        server_default=func.now(),
    )


def _updated_at() -> Column:
    return Column(
        DateTime(timezone=True),
        nullable=False,
        default=get_current_utc_datetime,
        onupdate=get_current_utc_datetime,
        server_default=func.now(),
    )


class AuthoredSemanticArtifact(SQLModel, table=True):
    __tablename__ = "authored_semantic_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id", "tenant_id", "content_digest", name="uq_authored_semantic_artifact_digest"
        ),
        CheckConstraint("byte_length >= 0", name="ck_authored_semantic_artifact_length"),
        Index("ix_authored_semantic_artifact_scope_created", "owner_id", "tenant_id", "created_at"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    artifact_ref: str = Field(sa_column=Column(String(96), nullable=False))
    content_digest: str = Field(sa_column=Column(String(71), nullable=False))
    byte_length: int = Field(sa_column=Column(BigInteger, nullable=False))
    media_type: str = Field(sa_column=Column(String(128), nullable=False))
    original_locator_digest: str | None = Field(default=None, sa_column=Column(String(71), nullable=True))
    resolver_version: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
    retention_class: str = Field(sa_column=Column(String(32), nullable=False))
    content_bytes: bytes = Field(sa_column=Column(LargeBinary, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_created_at())


class AuthoredEmissionBundleRecord(SQLModel, table=True):
    __tablename__ = "authored_emission_bundles"
    __table_args__ = (
        UniqueConstraint("owner_id", "tenant_id", "bundle_digest", name="uq_authored_emission_bundle_digest"),
        UniqueConstraint(
            "owner_id",
            "tenant_id",
            "presentation_id",
            "slide_id",
            "slide_revision",
            "operation_id",
            name="uq_authored_emission_bundle_lineage",
        ),
        CheckConstraint("bundle_kind IN ('authored', 'legacyHtml')", name="ck_authored_emission_bundle_kind"),
        CheckConstraint("slide_revision >= 1", name="ck_authored_emission_bundle_revision"),
        Index(
            "ix_authored_emission_bundle_slide",
            "owner_id",
            "tenant_id",
            "presentation_id",
            "slide_id",
            "slide_revision",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    slide_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    slide_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    bundle_kind: str = Field(sa_column=Column(String(16), nullable=False))
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    origin_generation_attempt_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    html_digest: str = Field(sa_column=Column(String(71), nullable=False))
    semantic_component_digest: str = Field(sa_column=Column(String(71), nullable=False))
    asset_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    font_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_contract_digest: str = Field(sa_column=Column(String(71), nullable=False))
    composition_digest: str = Field(sa_column=Column(String(71), nullable=False))
    identity_registry_digest: str = Field(sa_column=Column(String(71), nullable=False))
    quality_policy_version: str = Field(sa_column=Column(String(128), nullable=False))
    bundle_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_created_at())


class AuthoredDeckEmissionManifestRecord(SQLModel, table=True):
    __tablename__ = "authored_deck_emission_manifests"
    __table_args__ = (
        UniqueConstraint("owner_id", "tenant_id", "canonical_digest", name="uq_authored_deck_manifest_digest"),
        UniqueConstraint(
            "owner_id", "tenant_id", "presentation_id", "presentation_revision", "operation_id",
            name="uq_authored_deck_manifest_lineage",
        ),
        CheckConstraint("presentation_revision >= 1", name="ck_authored_deck_manifest_revision"),
        Index(
            "ix_authored_deck_manifest_presentation",
            "owner_id",
            "tenant_id",
            "presentation_id",
            "presentation_revision",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    presentation_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    generation_attempt_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, nullable=True))
    canonical_digest: str = Field(sa_column=Column(String(71), nullable=False))
    master_contract_digest: str = Field(sa_column=Column(String(71), nullable=False))
    composition_digest: str = Field(sa_column=Column(String(71), nullable=False))
    asset_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    font_manifest_digest: str = Field(sa_column=Column(String(71), nullable=False))
    quality_policy_version: str = Field(sa_column=Column(String(128), nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_created_at())


class AuthoredDeckEmissionActivePointer(SQLModel, table=True):
    __tablename__ = "authored_deck_emission_active"
    __table_args__ = (
        UniqueConstraint("owner_id", "tenant_id", "presentation_id", name="uq_authored_deck_active_pointer"),
        CheckConstraint("canonical_mode IN ('legacyHtml', 'bundleGraph')", name="ck_authored_deck_active_mode"),
        CheckConstraint("presentation_revision >= 0 AND generation >= 1", name="ck_authored_deck_active_versions"),
        CheckConstraint(
            "(canonical_mode = 'legacyHtml' AND manifest_id IS NULL) OR "
            "(canonical_mode = 'bundleGraph' AND manifest_id IS NOT NULL)",
            name="ck_authored_deck_active_manifest_mode",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    canonical_mode: str = Field(sa_column=Column(String(16), nullable=False))
    manifest_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey("authored_deck_emission_manifests.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    presentation_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    canonical_digest: str = Field(sa_column=Column(String(71), nullable=False))
    generation: int = Field(default=1, sa_column=Column(BigInteger, nullable=False, server_default="1"))
    operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    updated_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_updated_at())


class AuthoredSemanticExportBinding(SQLModel, table=True):
    __tablename__ = "authored_semantic_export_bindings"
    __table_args__ = (
        UniqueConstraint("export_attempt_id", name="uq_authored_semantic_export_attempt"),
        UniqueConstraint("owner_id", "tenant_id", "binding_digest", name="uq_authored_semantic_export_binding"),
        CheckConstraint("mode IN ('fidelity', 'hybrid')", name="ck_authored_semantic_export_mode"),
        CheckConstraint("presentation_revision >= 1", name="ck_authored_semantic_export_revision"),
        Index(
            "ix_authored_semantic_export_presentation",
            "owner_id",
            "tenant_id",
            "presentation_id",
            "presentation_revision",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    export_attempt_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("presentation_export_attempts.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    presentation_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    canonical_digest: str = Field(sa_column=Column(String(71), nullable=False))
    renderer_source_digest: str = Field(sa_column=Column(String(71), nullable=False))
    mode: str = Field(sa_column=Column(String(16), nullable=False))
    serializer_id: str = Field(sa_column=Column(String(128), nullable=False))
    serializer_version: str = Field(sa_column=Column(String(128), nullable=False))
    pptx_digest: str | None = Field(default=None, sa_column=Column(String(71), nullable=True))
    binding_digest: str = Field(sa_column=Column(String(71), nullable=False))
    payload: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=_created_at())


__all__ = [
    "AuthoredDeckEmissionActivePointer",
    "AuthoredDeckEmissionManifestRecord",
    "AuthoredEmissionBundleRecord",
    "AuthoredSemanticArtifact",
    "AuthoredSemanticExportBinding",
]
