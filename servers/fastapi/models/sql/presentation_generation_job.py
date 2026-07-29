from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class PresentationGenerationJob(SQLModel, table=True):
    """Durable execution record for one asynchronous presentation generation."""

    __tablename__ = "presentation_generation_jobs"
    __table_args__ = (
        Index(
            "ix_presentation_generation_jobs_dispatch",
            "state",
            "lease_expires_at",
        ),
        Index(
            "uq_presentation_generation_jobs_idempotency_key",
            "idempotency_key_hash",
            unique=True,
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    task_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey(
                "async_presentation_generation_tasks.id",
                name="fk_presentation_generation_jobs_task_id",
                ondelete="CASCADE",
            ),
            nullable=False,
            unique=True,
            index=True,
        )
    )
    presentation_id: uuid.UUID = Field(
        sa_column=Column(Uuid, nullable=False, unique=True, index=True)
    )
    idempotency_key_hash: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    request_id: Optional[str] = Field(
        default=None,
        sa_column=Column(String(128), nullable=True),
    )
    request_sha256: str = Field(sa_column=Column(String(64), nullable=False))
    request_payload: dict = Field(sa_column=Column(JSON, nullable=False))
    template_v2_target: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    export_cookie_required: bool = Field(
        sa_column=Column(
            Boolean,
            nullable=False,
            default=False,
            server_default=text("false"),
        ),
        default=False,
    )
    state: str = Field(nullable=False, default="queued")
    attempt_number: int = Field(
        sa_column=Column(
            Integer,
            nullable=False,
            default=0,
            server_default=text("0"),
        ),
        default=0,
    )
    attempt_token: Optional[str] = Field(default=None, nullable=True)
    lease_expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    heartbeat_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    last_started_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    published_at: Optional[datetime] = Field(
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
