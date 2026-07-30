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


def test_database_admin_bootstrap_runs_during_startup(monkeypatch, tmp_path):
    bootstrap_admin = AsyncMock()
    async_steps = (
        "migrate_database_on_startup",
        "create_db_and_tables",
        "log_template_v2_operational_health",
        "start_template_v2_private_source_cleanup",
        "start_presentation_generation_dispatcher",
        "check_llm_and_image_provider_api_or_model_availability",
        "stop_presentation_generation_dispatcher",
        "stop_template_v2_private_source_cleanup",
        "dispose_engines",
    )
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(
            creation_enabled=False,
            allowed_template_ids=(),
        ),
    )
    monkeypatch.setattr(lifespan, "get_app_data_directory_env", lambda: str(tmp_path))
    monkeypatch.setattr(lifespan, "bootstrap_database_admin", bootstrap_admin)
    for name in async_steps:
        monkeypatch.setattr(lifespan, name, AsyncMock())

    async def enter_lifespan() -> None:
        async with lifespan.app_lifespan(FastAPI()):
            pass

    asyncio.run(enter_lifespan())

    bootstrap_admin.assert_awaited_once_with()
