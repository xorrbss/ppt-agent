from typing import Optional
from sqlmodel import SQLModel, Field, Column, JSON

from utils.randomizers import get_random_uuid


class SlideModel(SQLModel, table=True):
    id: str = Field(primary_key=True, default_factory=get_random_uuid)
    presentation: str
    layout_group: str
    layout: str
    index: int
    content: dict = Field(sa_column=Column(JSON))
    html_content: Optional[str]
