"""Async generation admission failures reach the client as typed errors.

Before this mapping every non-HTTP exception from admission was ``print``-ed
and replaced by an untyped 500, so a retryable CAS-key contention between two
decks looked identical to a crash and left no traceback in the log.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.error_handling import install_api_error_handling
from api.v1.ppt.endpoints import presentation_generate
from services import presentation_generation_pipeline as generation_pipeline
from api.v1.ppt.endpoints.presentation_generate_admission import PRESENTATION_GENERATE_ROUTER
from services.artifact_store import ArtifactKeyBusy, ArtifactNotFound
from services.database import get_async_session

REQUEST_ID = "admission-request-1"
LOGGER_NAME = "api.v1.ppt.endpoints.presentation_generate_errors"


@pytest.fixture
def post_generate(monkeypatch):
    def run(error: Exception):
        async def failing_queue(*_args, **_kwargs):
            raise error

        async def session():
            yield SimpleNamespace(get_bind=lambda: None)

        monkeypatch.setattr(presentation_generate, "_require_local_legacy_async_generation", lambda _bind: None)
        monkeypatch.setattr(generation_pipeline, "queue_presentation_generation", failing_queue)
        app = FastAPI()
        install_api_error_handling(app)
        app.include_router(PRESENTATION_GENERATE_ROUTER, prefix="/api/v1/ppt")
        app.dependency_overrides[get_async_session] = session
        client = TestClient(app, raise_server_exceptions=False)
        return client.post(
            "/api/v1/ppt/presentation/generate/async",
            json={"content": "hello"},
            headers={"X-Request-ID": REQUEST_ID},
        )

    return run


def _logged(caplog) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == LOGGER_NAME]


def test_cas_key_contention_is_a_retryable_typed_conflict(post_generate, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    response = post_generate(ArtifactKeyBusy())

    assert response.status_code == 409
    body = response.json()
    assert body["request_id"] == REQUEST_ID == response.headers["X-Request-ID"]
    assert body["error"]["code"] == "artifact_key_busy"
    assert body["error"]["retryable"] is True
    assert "cas" not in str(body)
    record, = _logged(caplog)
    assert REQUEST_ID in record.getMessage() and "artifact.cas_key_busy" in record.getMessage()
    assert record.exc_info and record.exc_info[0] is ArtifactKeyBusy


def test_other_artifact_store_failures_are_typed_and_logged(post_generate, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    response = post_generate(ArtifactNotFound("artifact.worker_output_missing"))

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "artifact_store_failed"
    assert body["error"]["retryable"] is False
    assert "worker_output_missing" not in str(body)
    record, = _logged(caplog)
    assert record.levelno == logging.ERROR and REQUEST_ID in record.getMessage()
    assert record.exc_info and record.exc_info[0] is ArtifactNotFound


def test_unexpected_failure_stays_generic_but_keeps_its_traceback(post_generate, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    response = post_generate(RuntimeError("provider secret response"))

    assert response.status_code == 500
    body = response.json()
    assert body["detail"] == "Presentation generation failed"
    assert body["request_id"] == REQUEST_ID
    assert "provider secret" not in str(body)
    record, = _logged(caplog)
    assert record.levelno == logging.ERROR and REQUEST_ID in record.getMessage()
    assert record.exc_info and record.exc_info[0] is RuntimeError
