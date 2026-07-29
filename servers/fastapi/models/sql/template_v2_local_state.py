from datetime import datetime
import uuid

from sqlalchemy import (
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


class TemplateV2LocalState(SQLModel, table=True):
    """Local provenance and edit state kept outside canonical Template V2.

    Parent-first deletion is restricted. The presentation deletion service
    deletes the canonical template first, which cascades to this row, before
    deleting the presentation.
    """

    __tablename__ = "template_v2_local_state"
    __table_args__ = (
        CheckConstraint(
            "revision >= 1",
            name="ck_template_v2_local_state_revision_positive",
        ),
        Index(
            "ix_template_v2_local_state_presentation_id",
            "presentation_id",
        ),
    )

    template_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey(
                "template_v2.id",
                name="fk_template_v2_local_state_template_id_template_v2",
                ondelete="CASCADE",
            ),
            primary_key=True,
            nullable=False,
        )
    )
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            Uuid,
            ForeignKey(
                "presentations.id",
                name=(
                    "fk_template_v2_local_state_presentation_id_presentations"
                ),
                ondelete="RESTRICT",
            ),
            nullable=False,
        )
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
