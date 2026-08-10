"""Immutable, owner-scoped inputs admitted for presentation generation."""

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
    Integer,
    String,
    Uuid,
    func,
    text,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


class ReferenceSnapshot(SQLModel, table=True):
    """Append-only normalized reference material.

    Asset bindings are immutable identity/hash snapshots, not live filesystem
    locations. Generation admission revalidates the live asset before use.
    """

    __tablename__ = "reference_snapshots"
    __table_args__ = (
        CheckConstraint(
            "provider_id IN ('community', 'uploaded_document', 'template')",
            name="ck_reference_snapshots_provider",
        ),
        CheckConstraint(
            "schema_version = 1",
            name="ck_reference_snapshots_schema_version",
        ),
        Index(
            "ix_reference_snapshots_owner_conversation_created",
            "owner_id",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_reference_snapshots_expiry",
            "expires_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "user.id",
                name="fk_reference_snapshots_owner_id",
                ondelete="CASCADE",
            ),
            nullable=True,
            default=get_current_owner_id,
        ),
    )
    conversation_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            ForeignKey(
                "workspace_conversations.id",
                name="fk_reference_snapshots_conversation_id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    provider_id: str = Field(sa_column=Column(String(32), nullable=False))
    source_revision: str = Field(sa_column=Column(String(256), nullable=False))
    source_fingerprint: str = Field(sa_column=Column(String(64), nullable=False))
    normalized_payload: dict = Field(sa_column=Column(JSON, nullable=False))
    asset_bindings: list[dict] = Field(
        default_factory=list,
        sa_column=Column(JSON, nullable=False, default=list),
    )
    content_sha256: str = Field(sa_column=Column(String(64), nullable=False))
    schema_version: int = Field(
        default=1,
        sa_column=Column(
            Integer,
            nullable=False,
            default=1,
            server_default=text("1"),
        ),
    )
    expires_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
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
