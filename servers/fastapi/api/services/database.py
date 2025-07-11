from contextlib import contextmanager
import os
from sqlalchemy import create_engine
from sqlmodel import Session


sql_url = os.getenv("SQL_URL") or "sqlite:///" + os.path.join(
    os.getenv("APP_DATA_DIRECTORY"), "fastapi.db"
)
connect_args = {}
if "sqlite" in sql_url:
    connect_args["check_same_thread"] = False

sql_engine = create_engine(sql_url, connect_args=connect_args)


@contextmanager
def get_sql_session():
    session = Session(sql_engine)
    try:
        yield session
    finally:
        session.close()
