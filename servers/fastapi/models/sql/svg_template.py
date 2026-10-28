"""Owner and tenant scoped SVG template metadata; bytes use canonical assets."""
from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class SvgTemplate(SQLModel, table=True):
    __tablename__ = "svg_templates"
    __table_args__ = (Index("ix_svg_templates_owner_tenant", "owner_id", "tenant_id"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False))
    tenant_id: str = Field(sa_column=Column(String(128), nullable=False))
    name: str = Field(max_length=160)
    kind: str = Field(default="framed", max_length=16)
    registration_state: str = Field(default="analyzing", max_length=24)
    head_revision: int = Field(default=0, ge=0)
    draft_operation_id: uuid.UUID | None = Field(default=None)
    archived: bool = Field(default=False)
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False))


class SvgTemplateVersion(SQLModel, table=True):
    __tablename__ = "svg_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "revision", name="uq_svg_template_version"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("svg_templates.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False))
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False))
    tenant_id: str = Field(max_length=128)
    revision: int = Field(ge=1)
    digest: str = Field(max_length=80)
    kind: str = Field(max_length=16)
    source_snapshot_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("reference_snapshots.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False))
    package: dict = Field(sa_column=Column(JSON, nullable=False))
    style_projection: dict = Field(sa_column=Column(JSON, nullable=False))
    review_evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=get_current_utc_datetime, sa_column=Column(DateTime(timezone=True), nullable=False))


class SvgTemplateFrameAdaptation(SQLModel, table=True):
    __tablename__ = "svg_template_frame_adaptations"
    __table_args__ = (UniqueConstraint("version_id", "frame_id", "canvas_digest", name="uq_svg_template_frame_canvas"),)
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    version_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("svg_template_versions.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False))
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False))
    tenant_id: str = Field(max_length=128)
    frame_id: str = Field(max_length=128)
    canvas_digest: str = Field(max_length=80)
    digest: str = Field(max_length=80)
    payload: dict = Field(sa_column=Column(JSON, nullable=False))


class SvgTemplateAssetBinding(SQLModel, table=True):
    """Explicit retention even while an unpublished draft is awaiting review."""
    __tablename__ = "svg_template_asset_bindings"
    __table_args__ = (UniqueConstraint("template_id", "asset_id", "version_id", name="uq_svg_template_retained_asset"),
                      UniqueConstraint("template_id", "artifact_id", "version_id", name="uq_svg_template_retained_artifact"))
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    template_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("svg_templates.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=False))
    version_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("svg_template_versions.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=True))
    owner_id: uuid.UUID = Field(sa_column=Column(Uuid, ForeignKey("user.id", ondelete="CASCADE"), nullable=False))
    tenant_id: str = Field(max_length=128)
    asset_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("workspace_assets.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=True))
    snapshot_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("reference_snapshots.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=True))
    artifact_id: uuid.UUID | None = Field(default=None, sa_column=Column(Uuid, ForeignKey("public_artifacts.id", ondelete="NO ACTION", deferrable=True, initially="DEFERRED"), nullable=True))
    binding: dict = Field(sa_column=Column(JSON, nullable=False))
