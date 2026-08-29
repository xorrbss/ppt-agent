import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from api import lifespan
from services.workspace_runtime_profile import UnsupportedWorkspaceRuntimeError


@pytest.fixture(autouse=True)
def _stub_runtime_settings_coordinator(monkeypatch):
    monkeypatch.setattr(
        lifespan,
        "start_runtime_settings_coordinator",
        AsyncMock(),
    )
    monkeypatch.setattr(
        lifespan,
        "stop_runtime_settings_coordinator",
        AsyncMock(),
    )


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


def test_unsupported_sql_runtime_precedes_schema_mutation(monkeypatch):
    migrate = AsyncMock()
    create_tables = AsyncMock()
    monkeypatch.setattr(
        lifespan,
        "sql_engine",
        SimpleNamespace(url="mysql+aiomysql://db/app"),
    )
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
            raise AssertionError("unsupported runtime must fail before startup")

    with pytest.raises(
        UnsupportedWorkspaceRuntimeError,
        match="^chat_first_runtime_unsupported$",
    ):
        asyncio.run(enter_lifespan())

    migrate.assert_not_awaited()
    create_tables.assert_not_awaited()


def test_database_admin_bootstrap_runs_during_startup(monkeypatch, tmp_path):
    bootstrap_admin = AsyncMock()
    async_steps = (
            "migrate_database_on_startup",
            "create_db_and_tables",
            "import_template_packages_on_startup",
            "log_template_v2_operational_health",
        "start_template_v2_private_source_cleanup",
        "start_export_retention_sweeper",
        "start_source_locked_maintenance",
        "start_presentation_generation_dispatcher",
        "start_workspace_cleanup_maintenance",
        # The runner always owns presentation writes, so its workers always start.
        "start_workspace_operation_outbox_dispatcher",
        "start_workspace_operation_runner",
        "start_workspace_asset_maintenance",
        "start_workspace_event_retention",
        "check_llm_and_image_provider_api_or_model_availability",
        "stop_workspace_event_retention",
        "stop_workspace_asset_maintenance",
        "stop_workspace_operation_runner",
        "stop_workspace_operation_outbox_dispatcher",
        "stop_workspace_cleanup_maintenance",
        "stop_presentation_generation_dispatcher",
        "stop_export_retention_sweeper",
        "stop_source_locked_maintenance",
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
    lifespan.start_runtime_settings_coordinator.assert_awaited_once_with()
    lifespan.stop_runtime_settings_coordinator.assert_awaited_once_with()


def test_export_only_validation_skips_provider_availability(monkeypatch, tmp_path):
    provider_check = AsyncMock()
    monkeypatch.setenv("PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK", "true")
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=False, allowed_template_ids=()),
    )
    monkeypatch.setattr(lifespan, "get_app_data_directory_env", lambda: str(tmp_path))
    monkeypatch.setattr(lifespan, "bootstrap_database_admin", AsyncMock())
    monkeypatch.setattr(
        lifespan,
        "check_llm_and_image_provider_api_or_model_availability",
        provider_check,
    )
    for name in (
        "migrate_database_on_startup",
        "create_db_and_tables",
        "import_template_packages_on_startup",
        "log_template_v2_operational_health",
        "start_template_v2_private_source_cleanup",
        "start_export_retention_sweeper",
        "start_source_locked_maintenance",
        "start_presentation_generation_dispatcher",
        "start_workspace_cleanup_maintenance",
        "start_workspace_operation_outbox_dispatcher",
        "start_workspace_operation_runner",
        "start_workspace_asset_maintenance",
        "start_workspace_event_retention",
        "stop_workspace_event_retention",
        "stop_workspace_asset_maintenance",
        "stop_workspace_operation_runner",
        "stop_workspace_operation_outbox_dispatcher",
        "stop_workspace_cleanup_maintenance",
        "stop_presentation_generation_dispatcher",
        "stop_export_retention_sweeper",
        "stop_source_locked_maintenance",
        "stop_template_v2_private_source_cleanup",
        "dispose_engines",
    ):
        monkeypatch.setattr(lifespan, name, AsyncMock())

    async def enter_lifespan() -> None:
        async with lifespan.app_lifespan(FastAPI()):
            pass

    asyncio.run(enter_lifespan())

    provider_check.assert_not_awaited()


def test_chat_first_workers_start_and_stop_in_dependency_order(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(
        lifespan,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=False, allowed_template_ids=()),
    )
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
            "import_template_packages_on_startup",
            "log_template_v2_operational_health",
        "start_template_v2_private_source_cleanup",
        "start_export_retention_sweeper",
        "start_source_locked_maintenance",
        "start_presentation_generation_dispatcher",
        "bootstrap_database_admin",
        "check_llm_and_image_provider_api_or_model_availability",
        "stop_presentation_generation_dispatcher",
        "stop_export_retention_sweeper",
        "stop_source_locked_maintenance",
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
