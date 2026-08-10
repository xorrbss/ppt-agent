from __future__ import annotations

import asyncio

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from services import template_v2_pptx_worker as worker
from services.template_v2_pptx_worker import (
    get_template_v2_pptx_worker_mode,
    run_external_worker,
    should_start_embedded_worker,
)


def test_worker_mode_defaults_to_embedded(monkeypatch) -> None:
    monkeypatch.delenv("TEMPLATE_V2_PPTX_WORKER_MODE", raising=False)

    assert get_template_v2_pptx_worker_mode() == "embedded"
    assert should_start_embedded_worker() is True


def test_external_and_invalid_modes_disable_embedded_worker(monkeypatch) -> None:
    monkeypatch.setenv("TEMPLATE_V2_PPTX_WORKER_MODE", " External ")
    assert get_template_v2_pptx_worker_mode() == "external"
    assert should_start_embedded_worker() is False

    monkeypatch.setenv("TEMPLATE_V2_PPTX_WORKER_MODE", "sidecar")
    assert get_template_v2_pptx_worker_mode() == "invalid"
    assert should_start_embedded_worker() is False


def test_external_worker_requires_explicit_mode(monkeypatch) -> None:
    monkeypatch.delenv("TEMPLATE_V2_PPTX_WORKER_MODE", raising=False)

    with pytest.raises(
        RuntimeError,
        match="template_v2_external_worker_mode_required",
    ):
        asyncio.run(run_external_worker(asyncio.Event()))


def test_external_worker_runs_runtime_settings_coordinator(monkeypatch, tmp_path):
    stop = asyncio.Event()
    stop.set()
    monkeypatch.setenv("TEMPLATE_V2_PPTX_WORKER_MODE", "external")
    monkeypatch.setattr(
        worker,
        "get_structured_template_policy",
        lambda: SimpleNamespace(creation_enabled=True, allowed_template_ids=("a",)),
    )
    monkeypatch.setattr(worker, "require_template_v2_database_safety", Mock())
    monkeypatch.setattr(
        worker, "get_app_data_directory_env", lambda: str(tmp_path / "data")
    )
    for name in (
        "migrate_database_on_startup",
        "create_db_and_tables",
        "start_runtime_settings_coordinator",
        "log_template_v2_operational_health",
        "start_template_v2_private_source_cleanup",
        "start_template_v2_pptx_dispatcher",
        "stop_runtime_settings_coordinator",
        "stop_template_v2_pptx_dispatcher",
        "stop_template_v2_private_source_cleanup",
        "dispose_engines",
    ):
        monkeypatch.setattr(worker, name, AsyncMock())

    asyncio.run(worker.run_external_worker(stop))

    worker.start_runtime_settings_coordinator.assert_awaited_once_with()
    worker.stop_runtime_settings_coordinator.assert_awaited_once_with()
    worker.dispose_engines.assert_awaited_once_with()
