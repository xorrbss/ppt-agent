from collections.abc import AsyncGenerator
import os
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    create_async_engine,
    async_sessionmaker,
    AsyncSession,
)
from sqlmodel import SQLModel

from utils.get_env import get_app_data_directory_env, get_database_url_env


raw_database_url = get_database_url_env() or "sqlite:///" + os.path.join(
    get_app_data_directory_env() or "/tmp/presenton", "fastapi.db"
)

if raw_database_url.startswith("sqlite://"):
    database_url = raw_database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
elif raw_database_url.startswith("postgresql://"):
    database_url = raw_database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
elif raw_database_url.startswith("mysql://"):
    database_url = raw_database_url.replace("mysql://", "mysql+aiomysql://", 1)
else:
    database_url = raw_database_url

connect_args = {}
if "sqlite" in database_url:
    connect_args["check_same_thread"] = False

sql_engine: AsyncEngine = create_async_engine(database_url, connect_args=connect_args)
async_session_maker = async_sessionmaker(sql_engine, expire_on_commit=False)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        yield session


# Container DB (Lives inside the container)
container_db_url = "sqlite+aiosqlite:////app/container.db"
container_db_engine: AsyncEngine = create_async_engine(
    container_db_url, connect_args={"check_same_thread": False}
)
container_db_async_session_maker = async_sessionmaker(
    container_db_engine, expire_on_commit=False
)


async def get_container_db_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with container_db_async_session_maker() as session:
        yield session


# Create Database and Tables
async def create_db_and_tables():
    async with sql_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

    async with container_db_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
