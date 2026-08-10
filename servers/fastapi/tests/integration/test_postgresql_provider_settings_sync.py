from __future__ import annotations

import asyncio
import os
import re
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from models.sql.provider_settings import ProviderSettings
from services.provider_settings import (
    get_provider_settings_record,
    save_provider_settings_record,
)
from utils.db_utils import to_sync_sqlalchemy_url


def _postgres_urls() -> tuple[str, str]:
    required = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured:
        if required:
            pytest.fail("PPT_AGENT_POSTGRES_TEST_URL is required")
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run live PostgreSQL tests")
    sync_url = to_sync_sqlalchemy_url(configured)
    parsed = make_url(sync_url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    if not re.search(r"(?:^|[_-])tests?$", parsed.database or "", re.IGNORECASE):
        pytest.fail("PostgreSQL integration database name must end in test or tests")
    query = dict(parsed.query)
    if "sslmode" in query and "ssl" not in query:
        query["ssl"] = query.pop("sslmode")
    async_url = parsed.set(
        drivername="postgresql+asyncpg",
        query=query,
    ).render_as_string(hide_password=False)
    return sync_url, async_url


def test_postgresql_provider_settings_cas_preserves_concurrent_updates(monkeypatch):
    sync_url, async_url = _postgres_urls()
    schema = f"runtime_settings_{uuid.uuid4().hex}"
    sync_engine = create_engine(sync_url, poolclass=NullPool)
    monkeypatch.setattr(
        "services.provider_settings._mirror_to_legacy_file",
        lambda _config, _revision, **_kwargs: None,
    )
    with sync_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET search_path TO "{schema}"'))
        ProviderSettings.__table__.create(connection)

    async def exercise():
        engine = create_async_engine(
            async_url,
            poolclass=NullPool,
            connect_args={"server_settings": {"search_path": schema}},
        )
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session:
                initial = await save_provider_settings_record(
                    session,
                    {"LLM": "openai"},
                )

            async def write(patch):
                async with maker() as session:
                    return await save_provider_settings_record(session, patch)

            await asyncio.gather(
                write({"OPENAI_MODEL": "model-a"}),
                write({"GOOGLE_MODEL": "model-b"}),
            )
            async with maker() as session:
                final = await get_provider_settings_record(session)
            return initial, final
        finally:
            await engine.dispose()

    try:
        initial, final = asyncio.run(exercise())
        assert final.revision == initial.revision + 2
        assert final.config["OPENAI_MODEL"] == "model-a"
        assert final.config["GOOGLE_MODEL"] == "model-b"
    finally:
        with sync_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        sync_engine.dispose()
