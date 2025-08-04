from sqlmodel import Field, Column, JSON

from services.database import MAIN_DB_BASE
from utils.randomizers import get_random_uuid


class KeyValueSqlModel(MAIN_DB_BASE, table=True):
    id: str = Field(default_factory=get_random_uuid, primary_key=True)
    key: str = Field(index=True)
    value: dict = Field(sa_column=Column(JSON))
