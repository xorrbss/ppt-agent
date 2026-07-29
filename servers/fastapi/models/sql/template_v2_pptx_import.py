from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import (
    JSON,
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


class TemplateV2PptxImport(SQLModel, table=True):
    """Local lifecycle sidecar and review evidence for one private PPTX import.

    Import state is deliberately separate from the upstream-compatible
    ``template_v2`` definition. ``draft_template_id`` remains null until the
    owner explicitly confirms the analyzed candidate.
    """

    __tablename__ = "template_v2_pptx_imports"
    __table_args__ = (
        Index(
            "ix_template_v2_pptx_imports_dispatch",
            "state",
            "lease_expires_at",
        ),
        Index(
            "ix_template_v2_pptx_imports_source_cleanup",
            "state",
            "source_deleted_at",
            "source_retention_expires_at",
        ),
        Index(
            "uq_template_v2_pptx_imports_owner_request_key",
            "owner_scope",
            "request_key_hash",
            unique=True,
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_scope: str = Field(
        sa_column=Column(
            String(64),
            nullable=False,
            default="local-disabled-auth-scope-v1",
            server_default="local-disabled-auth-scope-v1",
        )
    )
    request_key_hash: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    request_fingerprint: Optional[str] = Field(
        default=None,
        sa_column=Column(String(64), nullable=True),
    )
    revision: int = Field(
        sa_column=Column(
            Integer,
            nullable=False,
            default=1,
            server_default=text("1"),
        ),
        default=1,
    )
    task_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey(
                "async_presentation_generation_tasks.id",
                name="fk_template_v2_pptx_imports_task_id",
                ondelete="CASCADE",
            ),
            nullable=False,
            unique=True,
            index=True,
        )
    )
    requested_template_id: str = Field(nullable=False, index=True)
    draft_template_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            String,
            ForeignKey(
                "template_v2.id",
                name="fk_template_v2_pptx_imports_draft_template_id",
                ondelete="SET NULL",
            ),
            nullable=True,
            index=True,
        ),
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
    source_retention_expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    source_cleanup_token: Optional[str] = Field(default=None, nullable=True)
    source_cleanup_lease_expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    source_cleanup_attempted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    source_deleted_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    source_filename: str = Field(nullable=False)
    source_media_type: str = Field(nullable=False)
    source_size_bytes: int = Field(nullable=False)
    source_sha256: str = Field(nullable=False)
    source_storage_key: str = Field(nullable=False, unique=True)
    pipeline_version: str = Field(
        sa_column=Column(
            String,
            nullable=False,
            default="template-v2-pptx-ooxml-v1",
            server_default="template-v2-pptx-ooxml-v1",
        ),
        default="template-v2-pptx-ooxml-v1",
    )
    manifest: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON, nullable=False, default=dict),
    )
    analysis_result: Optional[dict] = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    repeat_suggestions: Optional[list[dict]] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=True, default=list),
    )
    confirmed_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    cancelled_at: Optional[datetime] = Field(
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
