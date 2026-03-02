from datetime import datetime
from typing import Optional
import uuid

from sqlalchemy import JSON, Column, DateTime
from sqlmodel import Field, SQLModel
from pydantic import computed_field

from utils.datetime_utils import get_current_utc_datetime


class ImageAsset(SQLModel, table=True):
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    created_at: datetime = Field(
        sa_column=Column(
            DateTime(timezone=True), nullable=False, default=get_current_utc_datetime
        ),
    )
    is_uploaded: bool = Field(default=False)
    path: str
    extras: Optional[dict] = Field(sa_column=Column(JSON), default=None)
    
    @computed_field
    @property
    def file_url(self) -> str:
        """Returns the path with file:// prefix for Electron compatibility"""
        if self.path.startswith("http://") or self.path.startswith("https://") or self.path.startswith("file://"):
            return self.path
        # Add file:// prefix for local paths
        return f"file://{self.path}"
