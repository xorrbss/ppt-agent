from datetime import datetime
import uuid
from sqlmodel import Field, Column, JSON, SQLModel, DateTime


class OllamaPullStatus(SQLModel, table=True):
    id: uuid.UUID = Field(primary_key=True, default_factory=uuid.uuid4)
    last_updated: datetime = Field(sa_column=Column(DateTime, default=datetime.now))
    status: dict = Field(sa_column=Column(JSON))
