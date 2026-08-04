"""Immutable, reproducible evidence for one SlideSpec candidate outcome."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


SLIDESPEC_BUNDLE_STATUSES = (
    "imported",
    "candidate",
    "shadow",
    "rejected",
    "published",
)
SLIDESPEC_BUNDLE_ROUTES = ("import_shadow", "repair_shadow", "slidespec_active")


class SlideSpecArtifactBundle(SQLModel, table=True):
    """Append-only spec, plan, evidence, render manifest, and publication receipt.

    A live slide points at a published row from ``properties.slidespec_active``.
    Rows are never edited to turn a candidate into a publication: the final outcome
    is assembled first and inserted in the same transaction as the live-slide CAS.
    """

    __tablename__ = "slidespec_artifact_bundles"
    __table_args__ = (
        UniqueConstraint(
            "slide_id",
            "candidate_spec_hash",
            "status",
            "route",
            name="uq_slidespec_artifact_bundles_slide_candidate_outcome",
        ),
        CheckConstraint(
            "slide_index >= 0",
            name="ck_slidespec_artifact_bundles_slide_index_nonnegative",
        ),
        CheckConstraint(
            "spec_revision >= 1",
            name="ck_slidespec_artifact_bundles_spec_revision_positive",
        ),
        CheckConstraint(
            "status IN ('imported', 'candidate', 'shadow', 'rejected', 'published')",
            name="ck_slidespec_artifact_bundles_status",
        ),
        CheckConstraint(
            "route IN ('import_shadow', 'repair_shadow', 'slidespec_active')",
            name="ck_slidespec_artifact_bundles_route",
        ),
        Index(
            "ix_slidespec_artifact_bundles_presentation_slide_created",
            "presentation_id",
            "slide_index",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("presentations.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    slide_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("slides.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    slide_index: int = Field(nullable=False)
    spec_revision: int = Field(nullable=False)
    route: str = Field(sa_column=Column(String(32), nullable=False))
    status: str = Field(sa_column=Column(String(16), nullable=False))
    base_spec_hash: str | None = Field(
        default=None, sa_column=Column(String(71), nullable=True)
    )
    candidate_spec_hash: str = Field(sa_column=Column(String(71), nullable=False))
    render_input_hash: str = Field(sa_column=Column(String(71), nullable=False))
    html_hash: str = Field(sa_column=Column(String(71), nullable=False))
    render_hash: str = Field(sa_column=Column(String(71), nullable=False))
    image_hash: str | None = Field(
        default=None, sa_column=Column(String(71), nullable=True)
    )
    spec: dict = Field(sa_column=Column(JSON, nullable=False))
    repair_plan: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    acceptance: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    manifest: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    publication_receipt: dict | None = Field(
        default=None, sa_column=Column(JSON, nullable=True)
    )
    html: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
        )
    )


@event.listens_for(SlideSpecArtifactBundle, "before_update")
def _reject_bundle_update(*_args: object) -> None:
    raise ValueError("slidespec_artifact_bundle_is_append_only")


@event.listens_for(SlideSpecArtifactBundle, "before_delete")
def _reject_bundle_delete(*_args: object) -> None:
    raise ValueError("slidespec_artifact_bundle_is_append_only")
