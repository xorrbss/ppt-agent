from __future__ import annotations

import asyncio

import pytest

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
