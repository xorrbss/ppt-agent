from datetime import datetime
from typing import Optional
from sqlalchemy import Column, DateTime
from sqlmodel import SQLModel, Field


class TemplateModel(SQLModel, table=True):
    __tablename__ = "templates"

    id: str = Field(primary_key=True, description="UUID for the template (matches presentation_id)")
    name: str = Field(description="Human friendly template name")
    description: Optional[str] = Field(default=None, description="Optional template description")
    created_at: datetime = Field(sa_column=Column(DateTime, default=datetime.now)) 