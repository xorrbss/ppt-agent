from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from api.main import app
from services import workspace_metrics_exporter as exporter
from services.workspace_metrics import WORKSPACE_METRICS


def test_worker_metrics_endpoint_is_opt_in_and_validated(monkeypatch) -> None:
    monkeypatch.delenv("WORKSPACE_OPERATION_METRICS_PORT", raising=False)
    assert exporter.workspace_worker_metrics_endpoint() is None

    monkeypatch.setenv("WORKSPACE_OPERATION_METRICS_PORT", "invalid")
    with pytest.raises(
        RuntimeError,
        match="workspace_operation_metrics_port_invalid",
    ):
        exporter.workspace_worker_metrics_endpoint()

    monkeypatch.setenv("WORKSPACE_OPERATION_METRICS_PORT", "9101")
    monkeypatch.setenv("WORKSPACE_OPERATION_METRICS_HOST", "0.0.0.0")
    assert exporter.workspace_worker_metrics_endpoint() == ("0.0.0.0", 9101)


def test_worker_exporter_serves_only_health_and_closed_schema_metrics(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        monkeypatch.setattr(
            exporter,
            "workspace_worker_metrics_endpoint",
            lambda: ("127.0.0.1", 0),
        )
        WORKSPACE_METRICS.reset()
        WORKSPACE_METRICS.observe_operation(
            {
                "name": "succeeded",
                "result_status": "succeeded",
                "command_type": "presentation.edit",
            }
        )
        server = await exporter.start_workspace_worker_metrics_exporter()
        assert server is not None
        try:
            # A scraper that disconnects mid-header must not leave an unhandled
            # exporter task or affect the next health/metrics request.
            _reader, abandoned = await asyncio.open_connection(
                server.host, server.port
            )
            abandoned.write(b"GET /metrics HTTP/1.1\r\n")
            await abandoned.drain()
            abandoned.close()
            await abandoned.wait_closed()
            await asyncio.sleep(0)
            for path, status, marker in (
                ("/healthz", b"200 OK", b"ok\n"),
                ("/metrics", b"200 OK", b"workspace_operation_total"),
                ("/private", b"404 Not Found", b"not found\n"),
            ):
                reader, writer = await asyncio.open_connection(server.host, server.port)
                writer.write(
                    f"GET {path} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode("ascii")
                )
                await writer.drain()
                response = await reader.read()
                writer.close()
                await writer.wait_closed()
                assert status in response
                assert marker in response
                assert b"Make the title concise" not in response
        finally:
            await server.close()
            WORKSPACE_METRICS.reset()

    asyncio.run(scenario())


def test_prometheus_scrape_example_covers_api_and_every_worker_boundary() -> None:
    scrape_path = (
        Path(__file__).resolve().parents[2]
        / "ops"
        / "prometheus"
        / "chat-first-workspace-scrape.example.yml"
    )
    document = yaml.safe_load(scrape_path.read_text(encoding="utf-8"))
    jobs = {job["job_name"]: job for job in document["scrape_configs"]}

    assert jobs["chat-first-workspace-api"]["metrics_path"] == "/metrics/workspace"
    assert jobs["chat-first-workspace-workers"]["metrics_path"] == "/metrics"
    assert jobs["chat-first-workspace-workers"]["static_configs"][0]["targets"] == [
        "worker-1:9101",
        "worker-2:9102",
    ]


def test_api_metrics_scrape_is_session_independent_and_closed_schema() -> None:
    WORKSPACE_METRICS.reset()
    WORKSPACE_METRICS.record_operation_admission("presentation.edit")
    client = TestClient(app)

    response = client.get("/metrics/workspace")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "workspace_operation_admission_total" in response.text
    assert "prompt" not in response.text.lower()
    assert "filename" not in response.text.lower()
    WORKSPACE_METRICS.reset()
