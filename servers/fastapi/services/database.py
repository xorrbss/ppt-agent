from contextlib import contextmanager
import os
from sqlalchemy import create_engine
from sqlmodel import Session

from utils.get_env import get_app_data_directory_env, get_database_url_env


database_url = get_database_url_env() or "sqlite:///" + os.path.join(
    get_app_data_directory_env() or "/tmp/presenton", "fastapi.db"
)
connect_args = {}
if "sqlite" in database_url:
    connect_args["check_same_thread"] = False

sql_engine = create_engine(database_url, connect_args=connect_args)


@contextmanager
def get_sql_session():
    session = Session(sql_engine)
    try:
        yield session
    finally:
        session.close()
