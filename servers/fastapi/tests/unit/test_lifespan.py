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


def test_managed_chat_first_scanner_guard_precedes_schema_mutation(monkeypatch):
    migrate = AsyncMock()
    create_tables = AsyncMock()
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")
    monkeypatch.setenv("TEMPLATE_V2_DEPLOYMENT_TIER", "production")
    monkeypatch.setenv("WORKSPACE_ASSET_MALWARE_SCAN_MODE", "disabled")
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=False),
    )
    monkeypatch.setattr(lifespan, "migrate_database_on_startup", migrate)
    monkeypatch.setattr(lifespan, "create_db_and_tables", create_tables)

    async def enter_lifespan() -> None:
        async with lifespan.app_lifespan(FastAPI()):
            raise AssertionError("unsafe asset scan policy must fail before startup")

    with pytest.raises(
        RuntimeError,
        match="^chat_first_asset_malware_scan_required$",
    ):
        asyncio.run(enter_lifespan())

    migrate.assert_not_awaited()
    create_tables.assert_not_awaited()


def test_chat_first_runtime_profile_mismatch_precedes_schema_mutation(monkeypatch):
    migrate = AsyncMock()
    create_tables = AsyncMock()
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "postgresql-cluster")
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=False),
    )
    monkeypatch.setattr(
        lifespan,
        "require_workspace_asset_malware_scan_safety",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(lifespan, "migrate_database_on_startup", migrate)
    monkeypatch.setattr(lifespan, "create_db_and_tables", create_tables)

    async def enter_lifespan() -> None:
        async with lifespan.app_lifespan(FastAPI()):
            raise AssertionError("runtime mismatch must fail before startup")

    with pytest.raises(RuntimeError, match="^chat_first_runtime_profile_mismatch$"):
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
        "start_export_retention_sweeper",
        "start_presentation_generation_dispatcher",
        "start_workspace_cleanup_maintenance",
        "check_llm_and_image_provider_api_or_model_availability",
        "stop_workspace_cleanup_maintenance",
        "stop_presentation_generation_dispatcher",
        "stop_export_retention_sweeper",
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
    lifespan.start_export_retention_sweeper.assert_awaited_once_with()
    lifespan.stop_export_retention_sweeper.assert_awaited_once_with()
    lifespan.start_workspace_cleanup_maintenance.assert_awaited_once_with()
    lifespan.stop_workspace_cleanup_maintenance.assert_awaited_once_with()


def test_cleanup_maintenance_runs_when_chat_first_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=False, allowed_template_ids=()),
    )
    monkeypatch.setattr(lifespan, "workspace_commands_enabled", lambda: False)
    monkeypatch.setattr(
        lifespan,
        "validate_chat_first_workspace_rollout",
        lambda *_args, **_kwargs: SimpleNamespace(enabled=False),
    )
    monkeypatch.setattr(
        lifespan,
        "require_workspace_asset_malware_scan_safety",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(lifespan, "get_app_data_directory_env", lambda: str(tmp_path))

    common_steps = (
        "migrate_database_on_startup",
        "create_db_and_tables",
        "log_template_v2_operational_health",
        "start_template_v2_private_source_cleanup",
        "start_export_retention_sweeper",
        "start_presentation_generation_dispatcher",
        "start_workspace_cleanup_maintenance",
        "bootstrap_database_admin",
        "check_llm_and_image_provider_api_or_model_availability",
        "stop_workspace_cleanup_maintenance",
        "stop_presentation_generation_dispatcher",
        "stop_export_retention_sweeper",
        "stop_template_v2_private_source_cleanup",
        "dispose_engines",
    )
    workspace_only_steps = (
        "start_workspace_operation_outbox_dispatcher",
        "start_workspace_operation_runner",
        "start_workspace_asset_maintenance",
        "start_workspace_event_retention",
        "stop_workspace_event_retention",
        "stop_workspace_asset_maintenance",
        "stop_workspace_operation_runner",
        "stop_workspace_operation_outbox_dispatcher",
    )
    for name in (*common_steps, *workspace_only_steps):
        monkeypatch.setattr(lifespan, name, AsyncMock())

    async def enter_lifespan() -> None:
        async with lifespan.app_lifespan(FastAPI()):
            pass

    asyncio.run(enter_lifespan())

    lifespan.start_workspace_cleanup_maintenance.assert_awaited_once_with()
    lifespan.stop_workspace_cleanup_maintenance.assert_awaited_once_with()
    for name in workspace_only_steps:
        getattr(lifespan, name).assert_not_awaited()


def test_chat_first_workers_start_and_stop_in_dependency_order(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=False, allowed_template_ids=()),
    )
    monkeypatch.setattr(lifespan, "workspace_commands_enabled", lambda: True)
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")
    monkeypatch.setattr(
        lifespan,
        "require_workspace_asset_malware_scan_safety",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(lifespan, "get_app_data_directory_env", lambda: str(tmp_path))
    monkeypatch.setattr(
        lifespan,
        "register_presentation_generation_operation_handler",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        lifespan,
        "register_presentation_chat_operation_handlers",
        lambda **_kwargs: None,
    )

    for name in (
        "migrate_database_on_startup",
        "create_db_and_tables",
        "log_template_v2_operational_health",
        "start_template_v2_private_source_cleanup",
        "start_export_retention_sweeper",
        "start_presentation_generation_dispatcher",
        "bootstrap_database_admin",
        "check_llm_and_image_provider_api_or_model_availability",
        "stop_presentation_generation_dispatcher",
        "stop_export_retention_sweeper",
        "stop_template_v2_private_source_cleanup",
        "dispose_engines",
    ):
        monkeypatch.setattr(lifespan, name, AsyncMock())

    for name in (
        "start_workspace_cleanup_maintenance",
        "start_workspace_operation_outbox_dispatcher",
        "start_workspace_operation_runner",
        "start_workspace_asset_maintenance",
        "start_workspace_event_retention",
        "stop_workspace_event_retention",
        "stop_workspace_cleanup_maintenance",
        "stop_workspace_asset_maintenance",
        "stop_workspace_operation_runner",
        "stop_workspace_operation_outbox_dispatcher",
    ):
        monkeypatch.setattr(
            lifespan,
            name,
            AsyncMock(side_effect=lambda marker=name: calls.append(marker)),
        )

    async def enter_lifespan() -> None:
        async with lifespan.app_lifespan(FastAPI()):
            pass

    asyncio.run(enter_lifespan())

    assert calls == [
        "start_workspace_cleanup_maintenance",
        "start_workspace_operation_outbox_dispatcher",
        "start_workspace_operation_runner",
        "start_workspace_asset_maintenance",
        "start_workspace_event_retention",
        "stop_workspace_event_retention",
        "stop_workspace_cleanup_maintenance",
        "stop_workspace_asset_maintenance",
        "stop_workspace_operation_runner",
        "stop_workspace_operation_outbox_dispatcher",
    ]
