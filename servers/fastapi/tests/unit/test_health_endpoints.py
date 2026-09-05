"""EXT-08: liveness (/health) and readiness (/ready) endpoints.

Both routes are mounted on the root app outside ``/api`` (``api/main.py``) so
that ``SessionAuthMiddleware`` never protects them; see the module docstring
in ``api/health.py`` for why. These tests exercise that bypass end to end
(a real, cookie-less request) and directly at the middleware predicate level,
plus the liveness/readiness contract itself.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.health as health_module
from api.main import app
from api.middlewares import SessionAuthMiddleware


@pytest.fixture(autouse=True)
def _auth_enabled(monkeypatch):
    # These tests exist to prove the bypass holds even when auth is "on".
    monkeypatch.delenv("DISABLE_AUTH", raising=False)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    async def execute(self, *_args, **_kwargs):
        return None


class _BrokenSession:
    async def __aenter__(self):
        raise RuntimeError("database unavailable")

    async def __aexit__(self, *_exc_info):
        return False


def _passing_ready_dependencies(monkeypatch):
    monkeypatch.setattr(health_module, "async_session_maker", lambda: _FakeSession())
    monkeypatch.setattr(health_module, "find_chrome", lambda: "/usr/bin/chromium")
    monkeypatch.delenv("NEXT_INTERNAL_URL", raising=False)
    monkeypatch.delenv("LLM", raising=False)


def test_liveness_returns_ok_without_touching_the_database(monkeypatch):
    def _must_not_be_called():
        raise AssertionError("liveness must not open a database session")

    monkeypatch.setattr(health_module, "async_session_maker", _must_not_be_called)

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_health_and_ready_paths_are_classified_as_not_requiring_auth():
    middleware = SessionAuthMiddleware(app=app)
    assert middleware._requires_auth("/health") is False
    assert middleware._requires_auth("/ready") is False


def test_liveness_is_reachable_without_a_session_when_auth_is_enabled():
    # No cookies, no Authorization header: a protected path would 401/428 here.
    response = TestClient(app).get("/health")
    assert response.status_code == 200


def test_ready_is_reachable_without_a_session_when_auth_is_enabled(monkeypatch):
    _passing_ready_dependencies(monkeypatch)

    response = TestClient(app).get("/ready")

    assert response.status_code in (200, 503)


def test_ready_returns_503_when_the_database_check_fails(monkeypatch):
    monkeypatch.setattr(health_module, "async_session_maker", lambda: _BrokenSession())
    monkeypatch.setattr(health_module, "find_chrome", lambda: "/usr/bin/chromium")
    monkeypatch.delenv("NEXT_INTERNAL_URL", raising=False)
    monkeypatch.delenv("LLM", raising=False)

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"]["ok"] is False
    assert "detail" in body["checks"]["database"]


def test_ready_returns_200_when_every_check_passes(monkeypatch):
    _passing_ready_dependencies(monkeypatch)

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["ok"] is True
    assert body["checks"]["chrome"]["ok"] is True
    assert body["checks"]["provider_credentials"]["ok"] is True
    # NEXT_INTERNAL_URL is unset in this test: the check is skipped, not failed.
    assert body["checks"]["nextjs"] == {
        "ok": True,
        "skipped": True,
        "detail": "NEXT_INTERNAL_URL not set",
    }


def test_ready_fails_when_chrome_is_not_discoverable(monkeypatch):
    monkeypatch.setattr(health_module, "async_session_maker", lambda: _FakeSession())
    monkeypatch.setattr(health_module, "find_chrome", lambda: None)
    monkeypatch.delenv("NEXT_INTERNAL_URL", raising=False)
    monkeypatch.delenv("LLM", raising=False)

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["checks"]["chrome"]["ok"] is False
