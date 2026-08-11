from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, Uuid, text
from sqlmodel import Field, SQLModel

from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import get_current_owner_id


class ImageAsset(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: Optional[uuid.UUID] = Field(
        default_factory=get_current_owner_id,
        exclude=True,
        sa_column=Column(
            ForeignKey("user.id", ondelete="CASCADE"), nullable=True, index=True
        ),
    )
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        ),
    )
    is_uploaded: bool = Field(default=False)
    path: str
    extras: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    # Additive bridge for rows created before AssetGateway. ``path`` remains a
    # private rollback locator until the compatibility readiness gate proves it
    # can be removed; API and generation consumers use this canonical id.
    canonical_asset_id: Optional[uuid.UUID] = Field(
        default=None,
        sa_column=Column(
            Uuid,
            # The production FK is Alembic-managed. Omitting it from ORM
            # metadata keeps partial-schema publication fixtures independent.
            nullable=True,
            unique=True,
            index=True,
        ),
    )
    canonicalization_attempts: int = Field(
        default=0,
        sa_column=Column(
            Integer,
            nullable=False,
            default=0,
            server_default=text("0"),
        ),
    )
    canonicalization_error_code: Optional[str] = Field(default=None)
    canonicalized_at: Optional[datetime] = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
