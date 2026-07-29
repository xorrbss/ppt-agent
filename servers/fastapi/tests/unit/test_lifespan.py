import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from api import lifespan


def test_managed_database_guard_precedes_schema_mutation(monkeypatch):
    migrate = AsyncMock()
    create_tables = AsyncMock()
    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "production")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///production.db")
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=True),
    )
    monkeypatch.setattr(lifespan, "migrate_database_on_startup", migrate)
    monkeypatch.setattr(lifespan, "create_db_and_tables", create_tables)

    async def enter_lifespan() -> None:
        async with lifespan.app_lifespan(FastAPI()):
            raise AssertionError("unsafe managed database must fail before startup")

    with pytest.raises(
        RuntimeError,
        match="template_v2_managed_canary_requires_postgresql",
    ):
        asyncio.run(enter_lifespan())

    migrate.assert_not_awaited()
    create_tables.assert_not_awaited()
