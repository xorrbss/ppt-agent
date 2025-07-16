from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field

from utils.randomizers import get_random_uuid


class ImageAsset(SQLModel, table=True):
    id: str = Field(default=get_random_uuid, primary_key=True)
    prompt: Optional[str] = Field(default=None)
    path: str
    created_at: datetime = Field(default=datetime.now())
