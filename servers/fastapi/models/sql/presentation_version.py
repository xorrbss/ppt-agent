from datetime import datetime
from typing import List, Optional
import uuid

from sqlalchemy import JSON, Column, DateTime, ForeignKey, String
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime


class PresentationVersionModel(SQLModel, table=True):
    """A durable snapshot of a presentation's slides at a point in time.

    The editor autosave appends one of these (throttled + capped), turning the
    previously ephemeral client-side undo buffer into server-side history that
    survives reloads. A restore replaces the live slides with a snapshot.
    """

    __tablename__ = "presentation_versions"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    presentation_id: uuid.UUID = Field(
        sa_column=Column(
            ForeignKey("presentations.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        )
    )
    label: Optional[str] = Field(sa_column=Column(String), default=None)
    # Full snapshot of the deck's slides (a list of SlideModel dumps) so a restore
    # can recreate them verbatim.
    slides: List[dict] = Field(sa_column=Column(JSON), default_factory=list)
