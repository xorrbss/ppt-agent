"""Durable ArtifactRef and export single-flight persistence contracts.

The rows in this module are metadata only.  Artifact bytes are authoritative in
the configured content-addressed object store; local paths are never persisted
or exposed by these contracts.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field as PydanticField, field_validator, model_validator
from pydantic.alias_generators import to_camel
from sqlalchemy import (
    BigInteger,
    Boolean,
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


ARTIFACT_REF_SCHEMA_VERSION = "artifact-ref/v1"
PUBLIC_ARTIFACT_KINDS = (
    "checkpoint",
    "slide-html",
    "slide-png",
    "export-bundle",
    "presentation-pptx",
    "presentation-pdf",
)
PUBLIC_ARTIFACT_RETENTION_CLASSES = ("checkpoint", "ephemeral", "published")
PUBLIC_ARTIFACT_LIFECYCLE_STATES = (
    "provisional",
    "committed",
    "tombstoned",
    "deleted",
)
EXPORT_IDENTITY_STATES = ("running", "succeeded", "failed")
EXPORT_MODES = ("pptx", "pdf")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MEDIA_TYPE_BY_KIND = {
    "checkpoint": "application/json",
    "slide-html": "text/html",
    "slide-png": "image/png",
    "export-bundle": "application/vnd.presenton.export-bundle+json",
    "presentation-pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "presentation-pdf": "application/pdf",
}


def normalize_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("artifact digest must be a lowercase SHA-256 value")
    return normalized


def artifact_storage_key(*, owner_id: uuid.UUID, tenant_id: str, digest: str) -> str:
    """Build an opaque, owner-bound object key without exposing a local path."""

    from hashlib import sha256

    normalized_digest = normalize_sha256(digest)
    normalized_tenant = tenant_id.strip()
    if not normalized_tenant:
        raise ValueError("artifact tenant binding is required")
    tenant_digest = sha256(normalized_tenant.encode("utf-8")).hexdigest()
    return (
        f"v1/tenant/{tenant_digest}/owner/{owner_id}/"
        f"sha256/{normalized_digest[:2]}/{normalized_digest}"
    )


class ArtifactRef(BaseModel):
    """Public internal reference to immutable bytes; never a URL or file path."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        frozen=True,
    )

    schema_version: Literal["artifact-ref/v1"] = ARTIFACT_REF_SCHEMA_VERSION
    artifact_id: uuid.UUID
    owner_id: uuid.UUID
    tenant_id: str = PydanticField(min_length=1, max_length=128)
    kind: Literal[
        "checkpoint",
        "slide-html",
        "slide-png",
        "export-bundle",
        "presentation-pptx",
        "presentation-pdf",
    ]
    sha256: str
    size_bytes: int = PydanticField(ge=0)
    media_type: str = PydanticField(min_length=1, max_length=160)
    storage_key: str = PydanticField(min_length=1, max_length=512)
    created_by_operation_id: uuid.UUID
    retention_class: Literal["checkpoint", "ephemeral", "published"]

    @field_validator("sha256")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        return normalize_sha256(value)

    @field_validator("tenant_id", "media_type", "storage_key")
    @classmethod
    def strip_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("artifact text fields must not be blank")
        return normalized

    @model_validator(mode="after")
    def validate_binding(self) -> "ArtifactRef":
        expected_key = artifact_storage_key(
            owner_id=self.owner_id,
            tenant_id=self.tenant_id,
            digest=self.sha256,
        )
        if self.storage_key != expected_key:
            raise ValueError("artifact storage key is not bound to owner, tenant, and digest")
        if self.media_type != _MEDIA_TYPE_BY_KIND[self.kind]:
            raise ValueError("artifact media type does not match its kind")
        if "\\" in self.storage_key or ".." in self.storage_key or self.storage_key.startswith("/"):
            raise ValueError("artifact storage key must not be a local path")
        return self


class PublicArtifact(SQLModel, table=True):
    """Owner-scoped metadata for one immutable object-store artifact."""

    __tablename__ = "public_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "tenant_id",
            "kind",
            "sha256",
            "created_by_operation_id",
            "retention_class",
            name="uq_public_artifact_operation_digest",
        ),
        CheckConstraint(
            "kind IN ('checkpoint', 'slide-html', 'slide-png', 'export-bundle', "
            "'presentation-pptx', 'presentation-pdf')",
            name="ck_public_artifact_kind",
        ),
        CheckConstraint(
            "retention_class IN ('checkpoint', 'ephemeral', 'published')",
            name="ck_public_artifact_retention",
        ),
        CheckConstraint("size_bytes >= 0", name="ck_public_artifact_size"),
        CheckConstraint("length(sha256) = 64", name="ck_public_artifact_digest_length"),
        CheckConstraint(
            "lifecycle_state IN ('provisional', 'committed', 'tombstoned', 'deleted')",
            name="ck_public_artifact_lifecycle_state",
        ),
        CheckConstraint("refcount >= 0", name="ck_public_artifact_refcount"),
        CheckConstraint(
            "lifecycle_revision >= 1",
            name="ck_public_artifact_lifecycle_revision",
        ),
        CheckConstraint(
            "lifecycle_state <> 'deleted' OR refcount = 0",
            name="ck_public_artifact_deleted_refcount",
        ),
        Index("ix_public_artifact_owner_created", "owner_id", "tenant_id", "created_at"),
        Index("ix_public_artifact_digest", "sha256"),
        Index("ix_public_artifact_gc", "lifecycle_state", "expires_at", "refcount"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    schema_version: str = Field(
        default=ARTIFACT_REF_SCHEMA_VERSION,
        sa_column=Column(String(32), nullable=False, server_default=ARTIFACT_REF_SCHEMA_VERSION),
    )
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    kind: str = Field(sa_column=Column(String(32), nullable=False))
    sha256: str = Field(sa_column=Column(String(64), nullable=False))
    size_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    media_type: str = Field(sa_column=Column(String(160), nullable=False))
    storage_key: str = Field(sa_column=Column(String(512), nullable=False))
    created_by_operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    retention_class: str = Field(sa_column=Column(String(24), nullable=False))
    lifecycle_state: str = Field(
        default="committed",
        sa_column=Column(String(16), nullable=False, server_default="committed"),
    )
    expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    tombstoned_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    deleted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    refcount: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    lifecycle_revision: int = Field(
        default=1,
        sa_column=Column(BigInteger, nullable=False, server_default=text("1")),
    )
    last_gc_error: str | None = Field(
        default=None, sa_column=Column(String(512), nullable=True)
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

    def to_ref(self) -> ArtifactRef:
        return ArtifactRef(
            schema_version=self.schema_version,
            artifact_id=self.id,
            owner_id=self.owner_id,
            tenant_id=self.tenant_id,
            kind=self.kind,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            media_type=self.media_type,
            storage_key=self.storage_key,
            created_by_operation_id=self.created_by_operation_id,
            retention_class=self.retention_class,
        )


class PublicExportIdentity(SQLModel, table=True):
    """Cluster-wide export single-flight authority for one canonical identity."""

    __tablename__ = "public_export_identities"
    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "tenant_id",
            "presentation_id",
            "revision",
            "scene_digest",
            "semantic_digest",
            "quality_digest",
            "exporter_digest",
            "export_mode",
            "output_mode",
            "font_manifest_digest",
            "render_adapter",
            "identity_contract_version",
            name="uq_public_export_identity",
        ),
        CheckConstraint("revision >= 0", name="ck_public_export_identity_revision"),
        CheckConstraint(
            "export_mode IN ('pptx', 'pdf')",
            name="ck_public_export_identity_mode",
        ),
        CheckConstraint(
            "output_mode IN ('fidelity-raster', 'editable-hybrid')",
            name="ck_public_export_identity_output_mode",
        ),
        CheckConstraint(
            "(identity_contract_version = 'digest-bound/v2' AND reuse_eligible = true) "
            "OR (identity_contract_version = 'legacy-unreusable/v1' AND reuse_eligible = false)",
            name="ck_public_export_identity_reuse_contract",
        ),
        CheckConstraint(
            "state IN ('running', 'succeeded', 'failed')",
            name="ck_public_export_identity_state",
        ),
        CheckConstraint(
            "attempt_count >= 1 AND fencing_token >= 1",
            name="ck_public_export_identity_counters",
        ),
        CheckConstraint(
            "length(canonical_digest) = 64 AND length(scene_digest) = 64 "
            "AND length(semantic_digest) = 64 AND length(quality_digest) = 64 "
            "AND length(exporter_digest) = 64 AND length(font_manifest_digest) = 64",
            name="ck_public_export_identity_digests",
        ),
        Index("ix_public_export_identity_claim", "state", "lease_expires_at", "created_at"),
        Index("ix_public_export_identity_owner", "owner_id", "tenant_id", "presentation_id"),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    presentation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    canonical_digest: str = Field(sa_column=Column(String(64), nullable=False))
    scene_digest: str = Field(sa_column=Column(String(64), nullable=False))
    semantic_digest: str = Field(sa_column=Column(String(64), nullable=False))
    quality_digest: str = Field(sa_column=Column(String(64), nullable=False))
    exporter_digest: str = Field(sa_column=Column(String(64), nullable=False))
    export_mode: str = Field(sa_column=Column(String(8), nullable=False))
    output_mode: str = Field(sa_column=Column(String(24), nullable=False))
    font_manifest_digest: str = Field(sa_column=Column(String(64), nullable=False))
    exporter_version: str = Field(sa_column=Column(String(128), nullable=False))
    render_adapter: str = Field(sa_column=Column(String(128), nullable=False))
    identity_contract_version: str = Field(
        default="digest-bound/v2",
        sa_column=Column(
            String(32), nullable=False, server_default="digest-bound/v2"
        ),
    )
    reuse_eligible: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    source_artifact_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("public_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    state: str = Field(
        default="running",
        sa_column=Column(String(16), nullable=False, server_default="running"),
    )
    claimed_by_operation_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    worker_id: str = Field(sa_column=Column(String(160), nullable=False))
    attempt_token_hash: str = Field(sa_column=Column(String(71), nullable=False))
    fencing_token: int = Field(
        default=1,
        sa_column=Column(BigInteger, nullable=False, server_default=text("1")),
    )
    attempt_count: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default=text("1")),
    )
    lease_expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    result_artifact_id: uuid.UUID | None = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey("public_artifacts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    error_code: str | None = Field(default=None, sa_column=Column(String(128), nullable=True))
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


class PublicArtifactPublication(SQLModel, table=True):
    """One fenced, owner-scoped publication pointer for an operation result."""

    __tablename__ = "public_artifact_publications"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "publication_name",
            name="uq_public_artifact_publication_operation_name",
        ),
        CheckConstraint("revision >= 0", name="ck_public_artifact_publication_revision"),
        CheckConstraint(
            "fencing_token >= 1 AND length(attempt_token_hash) = 71 "
            "AND length(quality_digest) = 64",
            name="ck_public_artifact_publication_fence",
        ),
        Index(
            "ix_public_artifact_publication_artifact",
            "artifact_id",
            "published_at",
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
    publication_name: str = Field(sa_column=Column(String(64), nullable=False))
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    artifact_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("public_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    quality_digest: str = Field(sa_column=Column(String(64), nullable=False))
    attempt_token_hash: str = Field(sa_column=Column(String(71), nullable=False))
    fencing_token: int = Field(sa_column=Column(BigInteger, nullable=False))
    published_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )


class PublicSlideCheckpoint(SQLModel, table=True):
    """Fenced immutable checkpoint pointer used for cross-node resume."""

    __tablename__ = "public_slide_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "revision",
            "slide_index",
            "input_digest",
            "stage_version",
            name="uq_public_slide_checkpoint_identity",
        ),
        CheckConstraint(
            "revision >= 0 AND slide_index >= 0",
            name="ck_public_slide_checkpoint_position",
        ),
        CheckConstraint(
            "length(input_digest) = 64 AND length(attempt_token_hash) = 71 "
            "AND fencing_token >= 1",
            name="ck_public_slide_checkpoint_fence",
        ),
        Index(
            "ix_public_slide_checkpoint_artifact",
            "artifact_id",
            "committed_at",
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
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    slide_index: int = Field(sa_column=Column(Integer, nullable=False))
    input_digest: str = Field(sa_column=Column(String(64), nullable=False))
    stage_version: str = Field(sa_column=Column(String(64), nullable=False))
    artifact_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("public_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        )
    )
    attempt_token_hash: str = Field(sa_column=Column(String(71), nullable=False))
    fencing_token: int = Field(sa_column=Column(BigInteger, nullable=False))
    committed_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=func.now(),
        ),
    )
