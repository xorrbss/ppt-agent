"""Append-only state transitions for one SlideSpec publication attempt."""

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
    UniqueConstraint,
    Uuid,
    event,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


PUBLICATION_ATTEMPT_STATES = (
    "staged",
    "activation_committed",
    "verified",
    "failed",
    "rolled_back",
    "rollback_conflict",
)


class SlideSpecPublicationAttempt(SQLModel, table=True):
    """One immutable event in a slide-scoped publication state machine."""

    __tablename__ = "slidespec_publication_attempts"
    __table_args__ = (
        UniqueConstraint(
            "attempt_id",
            "state",
            name="uq_slidespec_publication_attempt_state",
        ),
        CheckConstraint(
            "state IN ('staged', 'activation_committed', 'verified', 'failed', "
            "'rolled_back', 'rollback_conflict')",
            name="ck_slidespec_publication_attempt_state",
        ),
        Index(
            "ix_slidespec_publication_attempts_slide_created",
            "slide_id",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    attempt_id: uuid.UUID = Field(sa_column=Column(Uuid, nullable=False))
    artifact_bundle_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("slidespec_artifact_bundles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
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
    state: str = Field(sa_column=Column(String(32), nullable=False))
    reason_code: str | None = Field(
        default=None, sa_column=Column(String(96), nullable=True)
    )
    evidence: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
        )
    )


@event.listens_for(SlideSpecPublicationAttempt, "before_update")
def _reject_attempt_update(*_args: object) -> None:
    raise ValueError("slidespec_publication_attempt_is_append_only")


@event.listens_for(SlideSpecPublicationAttempt, "before_delete")
def _reject_attempt_delete(*_args: object) -> None:
    raise ValueError("slidespec_publication_attempt_is_append_only")
