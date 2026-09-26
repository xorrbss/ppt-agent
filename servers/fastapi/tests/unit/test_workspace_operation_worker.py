from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services import workspace_operation_worker as worker
from services.public_worker_profiles import get_public_worker_profile
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    workspace_operation_handler_registry,
)
from services.workspace_operation_runner import claim_next_workspace_operation
from services.workspace_runtime_profile import UnsupportedWorkspaceRuntimeError
from services.workspace_authored_preview_operation_handlers import (
    WorkspacePageRemakePreviewOperationHandler,
)


def test_workspace_worker_mode_defaults_to_embedded(monkeypatch) -> None:
    monkeypatch.delenv("WORKSPACE_OPERATION_WORKER_MODE", raising=False)

    assert worker.get_workspace_operation_worker_mode() == "embedded"
    assert worker.should_start_embedded_workspace_operation_worker() is True


def test_workspace_external_mode_disables_embedded_runner(monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_OPERATION_WORKER_MODE", " External ")

    assert worker.get_workspace_operation_worker_mode() == "external"
    assert worker.should_start_embedded_workspace_operation_worker() is False


def test_workspace_split_mode_keeps_core_runner_without_generation(monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_OPERATION_WORKER_MODE", "split")
    worker.register_workspace_operation_handlers()

    assert worker.should_start_embedded_workspace_operation_worker() is True
    kinds = worker.embedded_workspace_operation_kinds()
    assert kinds is not None
    assert "presentation.generate" not in kinds
    assert "export.pptx" in kinds
    assert "export.pdf" in kinds


def test_external_export_controller_claims_only_export_operations(monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_OPERATION_EXTERNAL_CONTROLLER", "export")

    assert worker.external_workspace_operation_kinds("ppt-core") == (
        "export.pptx",
        "export.pdf",
    )
    with pytest.raises(
        RuntimeError,
        match="workspace_operation_external_controller_invalid",
    ):
        worker.external_workspace_operation_kinds("generation")


def test_external_ppt_core_requires_explicit_controller_ownership(monkeypatch) -> None:
    monkeypatch.delenv("WORKSPACE_OPERATION_EXTERNAL_CONTROLLER", raising=False)

    with pytest.raises(
        RuntimeError,
        match="workspace_operation_external_controller_required",
    ):
        worker.external_workspace_operation_kinds("ppt-core")


def test_invalid_workspace_worker_mode_fails_closed(monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_OPERATION_WORKER_MODE", "sidecar")

    with pytest.raises(RuntimeError, match="workspace_operation_worker_mode_invalid"):
        worker.get_workspace_operation_worker_mode()


def test_external_worker_owns_migrations_runner_and_shutdown(monkeypatch, tmp_path) -> None:
    stop = asyncio.Event()
    stop.set()
    monkeypatch.setenv("WORKSPACE_OPERATION_WORKER_MODE", "external")
    monkeypatch.setenv("WORKSPACE_OPERATION_EXTERNAL_CONTROLLER", "generation")
    monkeypatch.setenv("PPT_PROCESS_PROFILE", "generation")
    registry = WorkspaceOperationHandlerRegistry()
    monkeypatch.setattr(worker, "workspace_operation_handler_registry", registry)
    monkeypatch.setattr(worker, "require_workspace_asset_malware_scan_safety", Mock())
    monkeypatch.setattr(worker, "get_app_data_directory_env", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(worker, "migrate_database_on_startup", AsyncMock())
    monkeypatch.setattr(worker, "create_db_and_tables", AsyncMock())
    monkeypatch.setattr(worker, "require_public_controller_database_authority", AsyncMock())
    monkeypatch.setattr(worker, "start_runtime_settings_coordinator", AsyncMock())
    monkeypatch.setattr(worker, "stop_runtime_settings_coordinator", AsyncMock())
    monkeypatch.setattr(
        worker,
        "register_operation_handlers",
        Mock(wraps=worker.register_operation_handlers),
    )
    metrics_exporter = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(
        worker,
        "start_workspace_worker_metrics_exporter",
        AsyncMock(return_value=metrics_exporter),
    )
    monkeypatch.setattr(worker, "start_workspace_operation_runner", AsyncMock())
    monkeypatch.setattr(worker, "stop_workspace_operation_runner", AsyncMock())
    for name in (
        "start_workspace_cleanup_maintenance",
        "stop_workspace_cleanup_maintenance",
        "start_workspace_asset_maintenance",
        "stop_workspace_asset_maintenance",
        "start_workspace_event_retention",
        "stop_workspace_event_retention",
    ):
        monkeypatch.setattr(worker, name, AsyncMock())
    monkeypatch.setattr(worker, "dispose_engines", AsyncMock())

    asyncio.run(worker.run_external_worker(stop))

    worker.migrate_database_on_startup.assert_awaited_once_with()
    worker.create_db_and_tables.assert_awaited_once_with()
    worker.require_public_controller_database_authority.assert_awaited_once_with()
    worker.start_runtime_settings_coordinator.assert_awaited_once_with()
    worker.register_operation_handlers.assert_called_once_with(controller="generation", registry=registry)
    profile = get_public_worker_profile("generation")
    assert profile.operation_kinds == (
        "presentation.generate",
        "raster.analysis",
        "quality.review",
        "slide.regenerate.preview",
        "slide.regenerate",
        "slide.regenerate.batch.preview",
        "slide.regenerate.batch",
        "template.apply",
        "design.apply",
        "presentation.slides.reorder",
    )
    assert profile.canonical_database_access is True
    assert profile.provider_network_access is True
    assert profile.browser_allowed is False
    assert profile.converter_allowed is False
    assert worker.external_workspace_operation_kinds(profile.role) == profile.operation_kinds
    assert type(registry.resolve("quality.review")).__name__ == "WorkspaceQualityReviewOperationHandler"
    assert registry.recovery_policy("quality.review") == "automatic"
    assert isinstance(
        registry.resolve("slide.regenerate.preview"),
        WorkspacePageRemakePreviewOperationHandler,
    )
    assert registry.recovery_policy("slide.regenerate.preview") == "automatic"
    assert type(registry.resolve("slide.regenerate")).__name__ == "WorkspacePageRemakeOperationHandler"
    assert registry.recovery_policy("slide.regenerate") == "automatic"
    for kind in ("template.apply", "design.apply"):
        assert registry.resolve(kind) is not None
        assert registry.recovery_policy(kind) == "automatic"
    assert registry.resolve("presentation.slides.reorder") is not None
    assert registry.resolve("slide.regenerate.batch.preview") is not None
    assert registry.recovery_policy("slide.regenerate.batch.preview") == "automatic"
    assert registry.resolve("slide.regenerate.batch") is not None
    assert registry.recovery_policy("slide.regenerate.batch") == "automatic"
    assert registry.resolve("raster.analysis") is not None
    assert registry.resolve("presentation.edit") is None
    assert registry.resolve("export.pptx") is None
    assert registry.resolve("export.pdf") is None
    worker.start_workspace_worker_metrics_exporter.assert_awaited_once_with()
    worker.start_workspace_operation_runner.assert_awaited_once_with(
        allowed_kinds=(
            "presentation.generate",
            "raster.analysis",
            "quality.review",
            "slide.regenerate.preview",
            "slide.regenerate",
            "slide.regenerate.batch.preview",
            "slide.regenerate.batch",
            "template.apply",
            "design.apply",
            "presentation.slides.reorder",
        )
    )
    worker.start_workspace_cleanup_maintenance.assert_awaited_once_with()
    worker.start_workspace_asset_maintenance.assert_awaited_once_with()
    worker.start_workspace_event_retention.assert_awaited_once_with()
    worker.stop_workspace_event_retention.assert_awaited_once_with()
    worker.stop_workspace_asset_maintenance.assert_awaited_once_with()
    worker.stop_workspace_cleanup_maintenance.assert_awaited_once_with()
    worker.stop_workspace_operation_runner.assert_awaited_once_with()
    worker.stop_runtime_settings_coordinator.assert_awaited_once_with()
    metrics_exporter.close.assert_awaited_once_with()
    worker.dispose_engines.assert_awaited_once_with()


def test_worker_registration_includes_all_durable_remake_handlers() -> None:
    # An external worker that misses a handler fails only in production, so the
    # registration list is asserted against the process registry it publishes to.
    worker.register_workspace_operation_handlers()

    assert workspace_operation_handler_registry.resolve("slide.regenerate.batch") is not None
    assert workspace_operation_handler_registry.recovery_policy("slide.regenerate.batch") == "automatic"
    for kind in (
        "slide.regenerate.preview",
        "slide.regenerate.batch.preview",
    ):
        assert workspace_operation_handler_registry.resolve(kind) is not None
        assert workspace_operation_handler_registry.recovery_policy(kind) == "automatic"


def test_external_worker_requires_a_supported_sql_runtime(monkeypatch) -> None:
    monkeypatch.setenv("WORKSPACE_OPERATION_WORKER_MODE", "external")
    monkeypatch.setattr(
        worker,
        "sql_engine",
        SimpleNamespace(url="mysql+aiomysql://db/app"),
    )

    with pytest.raises(
        UnsupportedWorkspaceRuntimeError,
        match="^chat_first_runtime_unsupported$",
    ):
        asyncio.run(worker.run_external_worker(asyncio.Event()))


def test_generation_controller_can_claim_a_slide_order_operation(monkeypatch, tmp_path) -> None:
    """D found this in a real run: a presentation.slides.reorder operation
    sat "queued" (retryCount 0) for 3+ minutes because no external
    controller's allowed_kinds included the kind -- neither the "generation"
    role's claim allowlist (external_workspace_operation_kinds) nor its
    declared manifest (services/public_worker_profiles.py's
    PublicWorkerProfile.operation_kinds) listed it, and the "generation"
    branch of run_external_worker never registered a handler for it either.
    This exercises the real DB claim query with exactly the allowlist the
    "generation" external controller process uses, the same seam
    claim_next_workspace_operation is exercised through elsewhere in this
    file (test_external_worker_owns_migrations_runner_and_shutdown mocks the
    runner instead, so it never proves a row is actually claimable).
    """
    import test_workspace_operation_runner as runner_test

    monkeypatch.setenv("WORKSPACE_OPERATION_EXTERNAL_CONTROLLER", "generation")
    allowed_kinds = worker.external_workspace_operation_kinds("generation")
    assert "presentation.slides.reorder" in allowed_kinds

    async def _claim():
        engine, maker = await runner_test._database(tmp_path / "reorder-claim.db")
        try:
            _, operation_ids = await runner_test._seed_operations(maker, kind="presentation.slides.reorder")
            claim = await claim_next_workspace_operation(session_factory=maker, allowed_kinds=allowed_kinds)
            return claim, operation_ids
        finally:
            await engine.dispose()

    claim, operation_ids = asyncio.run(_claim())
    assert claim is not None
    assert claim.kind == "presentation.slides.reorder"
    assert claim.operation_id == operation_ids[0]
