"""The process database is ready without another test or harness running first."""

import asyncio

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from services import database
from services.database import async_session_maker, sql_engine
from services.database import sql_engine as captured_process_engine


async def _table_names(maker=async_session_maker):
    async with maker() as session:
        connection = await session.connection()
        return session.bind, await connection.run_sync(
            lambda connection: inspect(connection).get_table_names()
        )


def test_process_schema_covers_both_import_styles_and_captured_maker(
    process_database_schema,
):
    assert database.sql_engine is sql_engine is process_database_schema
    assert captured_process_engine is process_database_schema
    bind, tables = asyncio.run(_table_names())
    assert bind is process_database_schema
    assert {"presentation_generation_jobs", "provider_usage_receipts"} <= set(
        tables
    )
    assert "process-database" in sql_engine.url.database


def test_process_schema_does_not_initialize_independent_engines(tmp_path):
    async def independent_tables():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{(tmp_path / 'empty.db').as_posix()}"
        )
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda connection: inspect(connection).get_table_names()
                )
        finally:
            await engine.dispose()

    assert asyncio.run(independent_tables()) == []
