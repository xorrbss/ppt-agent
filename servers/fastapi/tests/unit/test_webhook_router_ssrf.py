"""Webhook subscription must refuse SSRF-capable targets through the standard
error envelope, and store public targets unchanged."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.error_handling import install_api_error_handling
from api.v1.webhook.router import API_V1_WEBHOOK_ROUTER
from services import outbound_url_guard as guard
from services.database import get_async_session
from tests.conftest import FakeAsyncSession

_PUBLIC = "93.184.216.34"


@pytest.fixture
def client(fake_async_session: FakeAsyncSession, monkeypatch):
    monkeypatch.setattr(
        guard, "DEFAULT_RESOLVER", lambda _hostname, _port: (_PUBLIC,)
    )
    app = FastAPI()
    install_api_error_handling(app)
    app.include_router(API_V1_WEBHOOK_ROUTER)

    async def session_dependency():
        yield fake_async_session

    app.dependency_overrides[get_async_session] = session_dependency
    return TestClient(app), fake_async_session


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://127.0.0.1:8000/api/v1/ppt/presentation/all", "egress_private_address_denied"),
        ("http://10.0.0.8/hook", "egress_private_address_denied"),
        ("http://169.254.169.254/latest/meta-data/", "egress_private_address_denied"),
        ("http://[fd00::1]/hook", "egress_private_address_denied"),
        ("http://localhost:8000/hook", "egress_metadata_or_loopback_denied"),
        ("http://metadata.google.internal/", "egress_metadata_or_loopback_denied"),
        ("ftp://hooks.example.com/hook", "egress_scheme_denied"),
        ("https://user:pw@hooks.example.com/hook", "egress_url_credentials_denied"),
    ],
)
def test_subscribe_rejects_non_public_targets_with_422_envelope(client, url, reason):
    http, session = client

    response = http.post(
        "/api/v1/webhook/subscribe",
        json={"url": url, "event": "presentation.generation.completed"},
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]["code"] == "webhook_url_rejected"
    assert payload["detail"]["reason"] == reason
    assert payload["error"]["code"] == "webhook_url_rejected"
    assert payload["error"]["status_code"] == 422
    assert payload["request_id"] == response.headers["X-Request-ID"]
    assert session.added == []
    assert session.commit_count == 0


def test_subscribe_rejects_hostname_resolving_to_private_address(client, monkeypatch):
    http, session = client
    monkeypatch.setattr(
        guard, "DEFAULT_RESOLVER", lambda _hostname, _port: (_PUBLIC, "10.1.1.1")
    )

    response = http.post(
        "/api/v1/webhook/subscribe",
        json={"url": "https://rebind.example.com/hook", "event": "presentation.generation.completed"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["reason"] == "egress_private_address_denied"
    assert session.added == []


def test_subscribe_stores_public_target(client):
    http, session = client

    response = http.post(
        "/api/v1/webhook/subscribe",
        json={
            "url": "https://hooks.example.com/presenton",
            "secret": "s3cr3t",
            "event": "presentation.generation.completed",
        },
    )

    assert response.status_code == 201
    assert response.json()["id"].startswith("webhook-")
    assert len(session.added) == 1
    assert session.added[0].url == "https://hooks.example.com/presenton"
    assert session.commit_count == 1
