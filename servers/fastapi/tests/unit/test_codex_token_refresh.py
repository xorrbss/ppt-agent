"""Offline Codex OAuth refresh tests.

Expiry tests mock refresh/persistence. The auth401 tests exercise the real SDK,
OAuth parser, managed settings CAS and process monitor with stub HTTP and a
private SQLite database; no live credentials or provider endpoints are used.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time

import pytest

from utils import codex_token_refresh as refresh_module
from utils.codex_token_refresh import ensure_fresh_codex_access_token
from utils.oauth.openai_codex import CodexAccountProfile, TokenFailure, TokenSuccess
from utils.runtime_settings import RuntimeSettings


@pytest.fixture
def auth401_http(monkeypatch, tmp_path):
    """Real SDK, OAuth parser and settings CAS; only HTTP is in memory."""
    import json
    from types import SimpleNamespace
    import httpx
    from openai import OpenAI
    from sqlalchemy import create_engine, select
    from models.sql.provider_settings import ProviderSettings
    from utils.datetime_utils import get_current_utc_datetime
    from utils.runtime_settings import publish_process_runtime_settings
    from services.provider_settings_legacy import mirror_provider_settings_to_legacy_file

    config = {"CODEX_ACCESS_TOKEN": "fixture-old-access", "CODEX_REFRESH_TOKEN": "fixture-old-refresh",
              "CODEX_TOKEN_EXPIRES": "9999999999999", "CODEX_ACCOUNT_ID": "fixture-account"}
    path = tmp_path / "settings.json"
    db_path = tmp_path / "settings.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setenv("USER_CONFIG_PATH", str(path))
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("CAN_CHANGE_KEYS", "true")
    for key in config:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("utils.runtime_settings._process_runtime_settings_envelope", None)
    monkeypatch.setattr(refresh_module, "_unauthorized_failure", None, raising=False)
    monkeypatch.setattr(refresh_module, "_unauthorized_unchanged", None, raising=False)
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    table = ProviderSettings.__table__
    table.create(engine)
    with engine.begin() as connection:
        connection.execute(table.insert().values(id=1, revision=11, config=config,
                                                 updated_at=get_current_utc_datetime()))
    mirror_provider_settings_to_legacy_file(config, 11)
    initial = _settings(config)
    publish_process_runtime_settings(11, initial)
    state = SimpleNamespace(requests=[], refreshes=0, refresh_status=200, retry_status=200,
                            rotate=True, barrier=None, during_refresh=None, engine=engine,
                            path=path, db_path=db_path, initial=initial, config=config,
                            expire_transport=False, refreshed_access="fixture-new-access")
    lock = threading.Lock()

    def handler(request):
        if request.url.path.endswith("/oauth/token"):
            with lock:
                state.refreshes += 1
            if state.during_refresh:
                state.during_refresh()
            if state.expire_transport:
                raise httpx.ConnectError("fixture-secret-refresh", request=request)
            body = {"access_token": state.refreshed_access, "expires_in": 3600}
            if state.rotate:
                body["refresh_token"] = "fixture-new-refresh"
            if state.refresh_status != 200:
                body = {"error": "fixture-secret-refresh"}
            return httpx.Response(state.refresh_status, json=body)
        assert request.url.path.endswith("/responses")
        token = request.headers["authorization"]
        with lock:
            state.requests.append((token, request.content))
        if token == "Bearer fixture-old-access":
            if state.barrier:
                state.barrier.wait(timeout=20)
            return httpx.Response(401, json={"error": {"message": "fixture-secret-response"}})
        if state.retry_status != 200:
            return httpx.Response(state.retry_status, json={"error": {"message": "fixture-secret-response"}})
        response = {"id": "resp_fixture", "status": "completed", "object": "response",
                    "output": [{"id": "msg_fixture", "type": "message", "role": "assistant",
                                "status": "completed", "content": [{"type": "output_text",
                                "text": "ok", "annotations": []}]}],
                    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        event = {"type": "response.completed", "response": response}
        return httpx.Response(200, text="data: " + json.dumps(event) + "\n\n",
                              headers={"content-type": "text/event-stream"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("llmai.chatgpt.client.OpenAI",
                        lambda **kw: OpenAI(**kw, http_client=http_client, max_retries=0))
    monkeypatch.setattr("utils.oauth.openai_codex.httpx.post", http_client.post)

    def stored():
        with engine.connect() as connection:
            return connection.execute(select(table.c.revision, table.c.config)).one()
    state.stored = stored
    try:
        yield state
    finally:
        http_client.close()
        engine.dispose()


def _auth401_call():
    from llmai.shared import ChatGPTClientConfig
    from utils.llm_config import get_client
    from utils.provider_idempotency import provider_generate_call

    client = get_client(config=ChatGPTClientConfig(access_token="fixture-old-access"))
    return provider_generate_call(client, provider_id="codex", idempotency_key=None,
                                  kwargs={"model": "gpt-6-sol", "messages": []})


def _auth401_run(call):
    async def run():
        return await asyncio.to_thread(call)
    return asyncio.run(run())


@pytest.mark.parametrize("rotate", [True, False])
def test_auth401_http_refresh_retry_and_revision(auth401_http, rotate):
    import json
    from utils.runtime_settings import get_process_runtime_settings_envelope
    from services.provider_settings_legacy import LEGACY_PROVIDER_SETTINGS_REVISION_KEY

    state = auth401_http
    state.rotate = rotate
    result = _auth401_run(_auth401_call())
    assert result.content[0].text == "ok"
    assert state.refreshes == 1 and len(state.requests) == 2
    assert state.requests[0][1] == state.requests[1][1]
    record = state.stored()
    assert record.revision == 12
    assert record.config["CODEX_ACCESS_TOKEN"] == "fixture-new-access"
    assert record.config["CODEX_REFRESH_TOKEN"] == ("fixture-new-refresh" if rotate else "fixture-old-refresh")
    mirror = json.loads(state.path.read_text(encoding="utf-8"))
    assert mirror[LEGACY_PROVIDER_SETTINGS_REVISION_KEY] == 12
    assert mirror["CODEX_REFRESH_TOKEN"] == record.config["CODEX_REFRESH_TOKEN"]
    assert get_process_runtime_settings_envelope().revision == 12


@pytest.mark.parametrize("failure", ["second_401", "refresh_http", "refresh_transport"])
def test_auth401_http_terminal_no_paid_replay(auth401_http, failure, caplog):
    from utils.provider_overload import ProviderOutcomeError, is_provider_overload
    from utils.llm_client_error_handler import handle_llm_client_exceptions
    from services.workspace_error_registry import resolve_workspace_error

    state = auth401_http
    state.retry_status = 401 if failure == "second_401" else 200
    state.refresh_status = 400 if failure == "refresh_http" else 200
    state.expire_transport = failure == "refresh_transport"
    with pytest.raises(ProviderOutcomeError) as raised:
        _auth401_run(_auth401_call())
    error = raised.value
    assert error.code == "codex_auth_reauth_required"
    assert not error.retryable and not error.recovery_allowed and not is_provider_overload(error)
    assert handle_llm_client_exceptions(error) is error
    public = resolve_workspace_error(error.code)
    assert public.code == error.code and not public.retryable
    assert "ChatGPT" in public.safe_message and "Settings" in public.safe_message
    assert state.refreshes == 1
    assert len(state.requests) == (2 if failure == "second_401" else 1)
    assert "fixture-secret" not in caplog.text


@pytest.mark.parametrize("refresh_status,unchanged_access", [(200, False), (400, False), (200, True)])
def test_auth401_20_parallel_pages_single_flight(auth401_http, refresh_status, unchanged_access, record_property):
    from utils.runtime_settings import runtime_settings_context
    from utils.provider_overload import ProviderOutcomeError

    state = auth401_http
    state.refresh_status = refresh_status
    if unchanged_access:
        state.refreshed_access = "fixture-old-access"
    state.barrier = threading.Barrier(20)
    calls = [_auth401_call() for _ in range(20)]

    async def run():
        # asyncio.to_thread propagates the stale job snapshot to every caller.
        asyncio.get_running_loop().set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=20))
        with runtime_settings_context(state.initial):
            return await asyncio.gather(*(asyncio.to_thread(call) for call in calls),
                                        return_exceptions=True)

    start = time.monotonic()
    results = asyncio.run(run())
    record_property("wall_seconds", round(time.monotonic() - start, 3))
    record_property("response_requests", len(state.requests))
    record_property("refresh_requests", state.refreshes)
    assert state.refreshes == 1
    if refresh_status == 200 and not unchanged_access:
        assert all(not isinstance(result, BaseException) and result.content[0].text == "ok" for result in results)
        assert len(state.requests) == 40 and state.stored().revision == 12
    else:
        assert all(isinstance(result, ProviderOutcomeError) and result.code == "codex_auth_reauth_required" for result in results)
        assert len(state.requests) == (40 if unchanged_access else 20)
        assert state.stored().revision == (12 if unchanged_access else 11)


def test_auth401_stale_refresh_cannot_overwrite_newer_revision(auth401_http):
    import json
    from models.sql.provider_settings import ProviderSettings
    from services.provider_settings_legacy import mirror_provider_settings_to_legacy_file
    from utils.provider_overload import ProviderOutcomeError

    state = auth401_http
    newer = {**state.config, "CODEX_ACCESS_TOKEN": "other-process-access",
             "CODEX_REFRESH_TOKEN": "other-process-refresh"}

    def other_process_commit():
        with state.engine.begin() as connection:
            connection.execute(ProviderSettings.__table__.update().values(config=newer, revision=12))
        mirror_provider_settings_to_legacy_file(newer, 12)
    state.during_refresh = other_process_commit
    with pytest.raises(ProviderOutcomeError) as raised:
        _auth401_run(_auth401_call())
    assert raised.value.code == "codex_auth_reauth_required"
    assert state.stored().revision == 12 and state.stored().config == newer
    assert json.loads(state.path.read_text(encoding="utf-8"))["CODEX_ACCESS_TOKEN"] == "other-process-access"
    assert state.refreshes == 1 and len(state.requests) == 1


def test_auth401_api_key_http_401_is_unchanged(monkeypatch):
    import httpx
    from openai import OpenAI
    from llmai.shared import OpenAIClientConfig
    from llmai.shared.errors import LLMError
    from utils.llm_config import get_client

    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"error": {"message": "fixture denied"}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        monkeypatch.setattr("llmai.openai.client.OpenAI", lambda **kw: OpenAI(**kw, http_client=http_client, max_retries=0))
        client = get_client(config=OpenAIClientConfig(api_key="fixture-api-key"))
        with pytest.raises(LLMError) as raised:
            _auth401_run(lambda: client.generate(model="gpt-6-sol", messages=[]))
    assert raised.value.status_code == 401 and len(requests) == 1


def _auth401_other_process(db_path, pipe):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from services.runtime_settings_coordinator import RuntimeSettingsCoordinator
    from utils.runtime_settings import get_process_runtime_settings_envelope

    async def run():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        coordinator = RuntimeSettingsCoordinator(session_factory=async_sessionmaker(engine))
        try:
            await coordinator.initialize()
            pipe.send(get_process_runtime_settings_envelope().revision)
            await asyncio.to_thread(pipe.recv)
            await coordinator.poll_once()
            current = get_process_runtime_settings_envelope()
            pipe.send({"revision": current.revision,
                       "access_matches": current.settings.get("CODEX_ACCESS_TOKEN") == "fixture-new-access",
                       "refresh_matches": current.settings.get("CODEX_REFRESH_TOKEN") == "fixture-new-refresh"})
        finally:
            await engine.dispose()
    asyncio.run(run())
    pipe.close()


def test_auth401_rotated_tokens_converge_in_another_process(auth401_http):
    import multiprocessing

    state = auth401_http
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe()
    process = context.Process(target=_auth401_other_process, args=(state.db_path.as_posix(), child))
    process.start()
    child.close()
    try:
        assert parent.poll(30) and parent.recv() == 11
        assert _auth401_run(_auth401_call()).content[0].text == "ok"
        parent.send("poll")
        assert parent.poll(30)
        assert parent.recv() == {"revision": 12, "access_matches": True, "refresh_matches": True}
    finally:
        parent.close()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        assert process.exitcode == 0
        process.close()


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
    (`services/generation_core/authoring/author_slide.py`).
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
