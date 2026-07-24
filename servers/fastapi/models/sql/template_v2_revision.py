from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class TemplateV2Revision(SQLModel, table=True):
    """Immutable restore point for one committed Template V2 revision."""

    __tablename__ = "template_v2_revisions"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_template_v2_revisions_revision_positive",
        ),
        Index(
            "ix_template_v2_revisions_template_created",
            "template_id",
            "created_at",
        ),
    )

    template_id: str = Field(
        sa_column=Column(
            String(128),
            ForeignKey("template_v2.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        )
    )
    revision: int = Field(
        sa_column=Column(Integer, primary_key=True, nullable=False)
    )
    reason: str = Field(sa_column=Column(String(32), nullable=False))
    name: str = Field(sa_column=Column(String(200), nullable=False))
    description: str | None = Field(
        default=None,
        sa_column=Column(String(1000), nullable=True),
    )
    merged_components: dict | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    layouts: dict | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    assets: dict | None = Field(
        default=None,
        sa_column=Column(JSON, nullable=True),
    )
    is_default: bool = Field(sa_column=Column(Boolean, nullable=False))
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            default=get_current_utc_datetime,
            server_default=func.now(),
        ),
    )
