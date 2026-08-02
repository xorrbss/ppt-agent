from datetime import datetime
import secrets
from typing import Optional
import uuid

from sqlalchemy import JSON, Column, DateTime, Uuid
from sqlmodel import Field, SQLModel
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


class AsyncPresentationGenerationTaskModel(SQLModel, table=True):

    __tablename__ = "async_presentation_generation_tasks"

    id: str = Field(
        default_factory=lambda: f"task-{secrets.token_hex(32)}", primary_key=True
    )
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        # Alembic owns the production FK. Keeping it out of ORM metadata lets
        # focused tests create this legacy task table without the user table.
        sa_column=Column(Uuid, nullable=True, index=True),
    )
    status: str
    message: Optional[str] = None
    error: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    created_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=get_current_utc_datetime,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    data: Optional[dict] = Field(sa_column=Column(JSON), default=None)
