"""Append-only canonical Authored document revisions."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
import uuid

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


class AuthoredCanonicalRevision(SQLModel, table=True):
    """One immutable full-document checkpoint plus its undo/redo cursor."""

    __tablename__ = "authored_canonical_revisions"
    __table_args__ = (
        UniqueConstraint(
            "presentation_id",
            "revision",
            name="uq_authored_canonical_presentation_revision",
        ),
        CheckConstraint(
            "revision >= 0 AND parent_revision >= 0",
            name="ck_authored_canonical_revision_nonnegative",
        ),
        CheckConstraint(
            "history_position >= 0",
            name="ck_authored_canonical_history_position_nonnegative",
        ),
        CheckConstraint(
            "operation IN ('initialize', 'patch', 'undo', 'redo')",
            name="ck_authored_canonical_operation",
        ),
        Index(
            "ix_authored_canonical_presentation_created",
            "presentation_id",
            "created_at",
        ),
    )

    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=Column(Uuid, nullable=True, index=True),
    )
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey("presentations.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    parent_revision: int = Field(sa_column=Column(BigInteger, nullable=False))
    source_digest: str = Field(sa_column=Column(String(71), nullable=False))
    operation: str = Field(sa_column=Column(String(16), nullable=False))
    snapshot: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    history: list[dict[str, Any]] = Field(sa_column=Column(JSON, nullable=False))
    history_position: int = Field(sa_column=Column(BigInteger, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )


__all__ = ["AuthoredCanonicalRevision"]
