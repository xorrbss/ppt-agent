"""Unit tests for automatic Codex OAuth access-token refresh.

`refresh_access_token` and `persist_codex_token_result` are mocked in every
test — no real OpenAI/Codex network call is made, and no real database write
happens (the shared persistence function is exercised through wiring tests
instead of its real DB-backed implementation, which needs a migrated
`provider_settings` table not set up by these fixtures).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from utils import codex_token_refresh as refresh_module
from utils.codex_token_refresh import ensure_fresh_codex_access_token
from utils.oauth.openai_codex import CodexAccountProfile, TokenFailure, TokenSuccess
from utils.runtime_settings import RuntimeSettings


def _settings(overrides: dict[str, object]) -> RuntimeSettings:
    return RuntimeSettings.from_sources(environ={}, user_config=overrides)


def _expired_ms(seconds_ago: int = 60) -> int:
    return int(time.time() * 1000) - seconds_ago * 1000


def _future_ms(seconds_ahead: int = 3600) -> int:
    return int(time.time() * 1000) + seconds_ahead * 1000


def _dummy_profile() -> CodexAccountProfile:
    return CodexAccountProfile(account_id="acct", username="u", email="e@example.com", is_pro=True)


class _RefreshCounter:
    def __init__(self, result_factory):
        self.calls = 0
        self._lock = threading.Lock()
        self._result_factory = result_factory

    def __call__(self, refresh_token: str):
        with self._lock:
            self.calls += 1
        # Widen the race window so concurrent callers actually overlap.
        time.sleep(0.02)
        return self._result_factory(refresh_token)


def test_not_expiring_token_is_returned_without_refresh(monkeypatch):
    settings = _settings(
        {
            "CODEX_ACCESS_TOKEN": "stored-token",
            "CODEX_REFRESH_TOKEN": "stored-refresh",
            "CODEX_TOKEN_EXPIRES": str(_future_ms()),
            "CODEX_TOKEN_AUTO_REFRESH": "true",
        }
    )
    counter = _RefreshCounter(lambda rt: TokenSuccess(access="new", refresh="new-r", expires=_future_ms()))
    monkeypatch.setattr(refresh_module, "refresh_access_token", counter)

    token = ensure_fresh_codex_access_token(settings)

    assert token == "stored-token"
    assert counter.calls == 0


def test_missing_refresh_token_skips_refresh_even_if_expired(monkeypatch):
    settings = _settings(
        {
            "CODEX_ACCESS_TOKEN": "stored-token",
            "CODEX_TOKEN_EXPIRES": str(_expired_ms()),
            "CODEX_TOKEN_AUTO_REFRESH": "true",
        }
    )
    counter = _RefreshCounter(lambda rt: TokenSuccess(access="new", refresh="new-r", expires=_future_ms()))
    monkeypatch.setattr(refresh_module, "refresh_access_token", counter)

    token = ensure_fresh_codex_access_token(settings)

    assert token == "stored-token"
    assert counter.calls == 0


def test_knob_off_disables_automatic_refresh(monkeypatch):
    settings = _settings(
        {
            "CODEX_ACCESS_TOKEN": "stored-token",
            "CODEX_REFRESH_TOKEN": "stored-refresh",
            "CODEX_TOKEN_EXPIRES": str(_expired_ms()),
            "CODEX_TOKEN_AUTO_REFRESH": "false",
        }
    )
    counter = _RefreshCounter(lambda rt: TokenSuccess(access="new", refresh="new-r", expires=_future_ms()))
    monkeypatch.setattr(refresh_module, "refresh_access_token", counter)

    token = ensure_fresh_codex_access_token(settings)

    assert token == "stored-token"
    assert counter.calls == 0


def test_refresh_failure_falls_back_to_stored_token(monkeypatch, caplog):
    settings = _settings(
        {
            "CODEX_ACCESS_TOKEN": "stored-token",
            "CODEX_REFRESH_TOKEN": "stored-refresh",
            "CODEX_TOKEN_EXPIRES": str(_expired_ms()),
            "CODEX_TOKEN_AUTO_REFRESH": "true",
        }
    )
    monkeypatch.setattr(refresh_module, "resolve_runtime_settings", lambda s: settings if s is None else s)
    monkeypatch.setattr(refresh_module, "refresh_access_token", lambda rt: TokenFailure(reason="boom"))

    persist_calls = []
    monkeypatch.setattr(
        refresh_module,
        "persist_codex_token_result",
        lambda result: persist_calls.append(result) or _dummy_profile(),
    )

    with caplog.at_level("WARNING"):
        token = ensure_fresh_codex_access_token(settings)

    assert token == "stored-token"
    assert persist_calls == []
    assert any("Codex OAuth automatic refresh failed" in message for message in caplog.messages)


def test_expiring_token_refreshes_and_persists_through_shared_function(monkeypatch):
    settings = _settings(
        {
            "CODEX_ACCESS_TOKEN": "stored-token",
            "CODEX_REFRESH_TOKEN": "stored-refresh",
            "CODEX_TOKEN_EXPIRES": str(_expired_ms()),
            "CODEX_TOKEN_AUTO_REFRESH": "true",
        }
    )
    monkeypatch.setattr(refresh_module, "resolve_runtime_settings", lambda s: settings if s is None else s)
    counter = _RefreshCounter(
        lambda rt: TokenSuccess(access="new-token", refresh="new-refresh", expires=_future_ms())
    )
    monkeypatch.setattr(refresh_module, "refresh_access_token", counter)

    persist_calls = []
    monkeypatch.setattr(
        refresh_module,
        "persist_codex_token_result",
        lambda result: persist_calls.append(result) or _dummy_profile(),
    )

    token = ensure_fresh_codex_access_token(settings)

    assert token == "new-token"
    assert counter.calls == 1
    assert len(persist_calls) == 1
    assert persist_calls[0].access == "new-token"
    assert persist_calls[0].refresh == "new-refresh"


def test_concurrent_callers_single_flight_one_refresh(monkeypatch):
    """8 concurrent threads on an expiring token trigger exactly one refresh call.

    Mirrors the real caller shape: `_get_codex_access_token` can be invoked
    from worker threads inside a `ThreadPoolExecutor`
    (`utils/llm_calls/author_slide.py`).
    """
    initial_settings = _settings(
        {
            "CODEX_ACCESS_TOKEN": "stored-token",
            "CODEX_REFRESH_TOKEN": "stored-refresh",
            "CODEX_TOKEN_EXPIRES": str(_expired_ms()),
            "CODEX_TOKEN_AUTO_REFRESH": "true",
        }
    )

    state = {"current": initial_settings}
    state_lock = threading.Lock()

    def _fake_resolve(s):
        if s is not None:
            return s
        with state_lock:
            return state["current"]

    monkeypatch.setattr(refresh_module, "resolve_runtime_settings", _fake_resolve)

    counter = _RefreshCounter(
        lambda rt: TokenSuccess(access="new-token", refresh="new-refresh", expires=_future_ms())
    )
    monkeypatch.setattr(refresh_module, "refresh_access_token", counter)

    def _fake_persist(result: TokenSuccess) -> CodexAccountProfile:
        refreshed = _settings(
            {
                "CODEX_ACCESS_TOKEN": result.access,
                "CODEX_REFRESH_TOKEN": result.refresh,
                "CODEX_TOKEN_EXPIRES": str(result.expires),
                "CODEX_TOKEN_AUTO_REFRESH": "true",
            }
        )
        with state_lock:
            state["current"] = refreshed
        return _dummy_profile()

    monkeypatch.setattr(refresh_module, "persist_codex_token_result", _fake_persist)

    results: list[str | None] = [None] * 8
    errors: list[BaseException] = []

    def _worker(index: int) -> None:
        try:
            results[index] = ensure_fresh_codex_access_token(initial_settings)
        except BaseException as exc:  # noqa: BLE001 - surface to the main thread
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert counter.calls == 1
    assert all(result == "new-token" for result in results)


# ---------------------------------------------------------------------------
# Endpoint wiring: /codex/auth/refresh and friends must go through the same
# shared persistence function as the automatic-refresh path above.
# ---------------------------------------------------------------------------

def test_endpoint_store_token_delegates_to_shared_persist_function(monkeypatch):
    from api.v1.ppt.endpoints import codex_auth

    result = TokenSuccess(access="a", refresh="r", expires=_future_ms())
    calls = []

    def _fake_persist(token_result: TokenSuccess) -> CodexAccountProfile:
        calls.append(token_result)
        return _dummy_profile()

    monkeypatch.setattr(codex_auth, "persist_codex_token_result", _fake_persist)

    profile = asyncio.run(codex_auth._store_token(result))

    assert calls == [result]
    assert profile.account_id == "acct"


# ---------------------------------------------------------------------------
# `utils.llm_config._get_codex_access_token` wiring
# ---------------------------------------------------------------------------

def test_get_codex_access_token_delegates_to_ensure_fresh(monkeypatch):
    from fastapi import HTTPException

    import utils.llm_config as llm_config

    settings = _settings({"CODEX_ACCESS_TOKEN": "stored-token"})

    monkeypatch.setattr(llm_config, "ensure_fresh_codex_access_token", lambda s: "resolved-token")
    assert llm_config._get_codex_access_token(settings) == "resolved-token"

    monkeypatch.setattr(llm_config, "ensure_fresh_codex_access_token", lambda s: None)
    with pytest.raises(HTTPException) as exc_info:
        llm_config._get_codex_access_token(settings)
    assert exc_info.value.status_code == 400
