from __future__ import annotations

from datetime import datetime
from typing import Optional
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
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


AUTHORED_CHECKPOINT_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
)


class AuthoredSlideCheckpoint(SQLModel, table=True):
    """Durable per-slide outputs for resumable authored generation."""

    __tablename__ = "authored_slide_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "slide_index",
            name="uq_authored_slide_checkpoints_job_slide",
        ),
        CheckConstraint(
            "slide_index >= 0",
            name="ck_authored_slide_checkpoints_slide_index_nonnegative",
        ),
        *(
            CheckConstraint(
                f"{stage}_status IN "
                "('pending', 'running', 'completed', 'failed', 'skipped')",
                name=f"ck_authored_slide_checkpoints_{stage}_status",
            )
            for stage in ("author", "illustrate", "render", "persist")
        ),
        Index(
            "ix_authored_slide_checkpoints_job_persist",
            "job_id",
            "persist_status",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    job_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentation_generation_jobs.id",
                name="fk_authored_slide_checkpoints_job_id",
                ondelete="CASCADE",
            ),
            nullable=False,
        )
    )
    slide_index: int = Field(nullable=False)
    role: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )

    author_status: str = Field(
        default="pending",
        sa_column=Column(
            String(16), nullable=False, default="pending", server_default="pending"
        ),
    )
    illustrate_status: str = Field(
        default="pending",
        sa_column=Column(
            String(16), nullable=False, default="pending", server_default="pending"
        ),
    )
    render_status: str = Field(
        default="pending",
        sa_column=Column(
            String(16), nullable=False, default="pending", server_default="pending"
        ),
    )
    persist_status: str = Field(
        default="pending",
        sa_column=Column(
            String(16), nullable=False, default="pending", server_default="pending"
        ),
    )

    author_html: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    illustrated_html: Optional[str] = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    render_path: Optional[str] = Field(
        default=None,
        sa_column=Column(String(1024), nullable=True),
    )
    render_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    artifact_refs: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )

    failed_stage: Optional[str] = Field(
        default=None,
        sa_column=Column(String(32), nullable=True),
    )
    error: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    attempt_token: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )

    author_started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    author_completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    illustrate_started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    illustrate_completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    render_started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    render_completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    persist_started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    persist_completed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    failed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
        default_factory=get_current_utc_datetime,
    )
    updated_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            onupdate=get_current_utc_datetime,
            server_default=func.now(),
        ),
        default_factory=get_current_utc_datetime,
    )
