from sqlmodel import Field, Column, JSON
from services.database import CONTAINER_DB_BASE


class OllamaPullStatus(CONTAINER_DB_BASE, table=True):
    id: str = Field(primary_key=True)
    status: dict = Field(sa_column=Column(JSON))
