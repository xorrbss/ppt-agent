from contextlib import contextmanager
import os
from sqlalchemy import create_engine
from sqlmodel import Session


sql_url = os.getenv("SQL_URL") or "sqlite:///" + os.path.join(
    os.getenv("APP_DATA_DIRECTORY"), "fastapi.db"
)
sql_engine = create_engine(sql_url)


@contextmanager
def get_sql_session():
    session = Session(sql_engine)
    try:
        yield session
    finally:
        session.close()
