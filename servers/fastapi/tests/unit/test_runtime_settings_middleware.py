import asyncio
import json

import pytest

from api.middlewares import RuntimeSettingsContextMiddleware
from services.provider_settings import ProviderSettingsRecord
from utils.runtime_settings import (
    RuntimeSettings,
    get_process_runtime_settings,
    replace_process_runtime_settings,
    resolve_runtime_settings,
)
from utils.user_config import refresh_process_runtime_settings


RUNTIME_SETTINGS_REFRESH_PATH = "/_presenton/runtime-settings/refresh"


async def _invoke_middleware(
    middleware,
    *,
    method="GET",
    path="/",
    headers=(),
    client=("127.0.0.1", 1),
):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await middleware(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": list(headers),
            "client": client,
            "server": ("test", 80),
        },
        receive,
        send,
    )
    return messages


def test_request_middleware_uses_published_snapshot_without_loading_user_config(
    monkeypatch,
):
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={"LLM": "openai", "OPENAI_API_KEY": "request-key"},
    )
    previous = replace_process_runtime_settings(settings)
    observed = []

    async def app(scope, receive, send):
        del receive
        observed.append(resolve_runtime_settings())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RuntimeSettingsContextMiddleware(app)
    monkeypatch.setattr(
        "utils.user_config.read_user_config_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("request attempted synchronous user config I/O")
        ),
    )

    async def invoke():
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await middleware(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "server": ("test", 80),
            },
            receive,
            send,
        )
        return messages

    try:
        messages = asyncio.run(invoke())
    finally:
        replace_process_runtime_settings(previous)

    assert observed == [settings]
    assert messages[0]["status"] == 204


def test_explicit_loopback_refresh_publishes_saved_user_config_for_next_request(
    monkeypatch,
    tmp_path,
):
    config_path = tmp_path / "userConfig.json"
    refresh_token = "electron-control-token"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("CAN_CHANGE_KEYS", "true")
    monkeypatch.setenv("PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN", refresh_token)
    monkeypatch.setattr(
        "utils.user_config.read_user_config_file",
        lambda _path: json.loads(config_path.read_text(encoding="utf-8")),
    )
    config_path.write_text(
        json.dumps(
            {
                "LLM": "openai",
                "OPENAI_API_KEY": "old-key",
                "OPENAI_MODEL": "old-model",
            }
        ),
        encoding="utf-8",
    )
    refresh_process_runtime_settings()

    config_path.write_text(
        json.dumps(
            {
                "LLM": "google",
                "GOOGLE_API_KEY": "new-key",
                "GOOGLE_MODEL": "new-model",
            }
        ),
        encoding="utf-8",
    )
    assert get_process_runtime_settings().get("OPENAI_MODEL") == "old-model"

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    async def import_config(_session):
        return ProviderSettingsRecord(
            revision=1,
            config=json.loads(config_path.read_text(encoding="utf-8")),
        )

    monkeypatch.setattr("api.middlewares.async_session_maker", SessionContext)
    monkeypatch.setattr(
        "api.middlewares.import_legacy_file_to_provider_settings",
        import_config,
    )

    downstream_calls = 0
    observed = []

    async def app(scope, receive, send):
        del scope, receive
        nonlocal downstream_calls
        downstream_calls += 1
        observed.append(resolve_runtime_settings())
        await send({"type": "http.response.start", "status": 418, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RuntimeSettingsContextMiddleware(app)
    messages = asyncio.run(
        _invoke_middleware(
            middleware,
            method="POST",
            path=RUNTIME_SETTINGS_REFRESH_PATH,
            headers=[
                (b"x-presenton-runtime-settings-token", refresh_token.encode()),
            ],
        )
    )

    assert messages[0]["status"] == 204
    assert downstream_calls == 0
    published = get_process_runtime_settings()
    assert published.get("LLM") == "google"
    assert published.get("GOOGLE_API_KEY") == "new-key"
    assert published.get("GOOGLE_MODEL") == "new-model"
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    assert response_body == b""

    next_request_messages = asyncio.run(_invoke_middleware(middleware))

    assert next_request_messages[0]["status"] == 418
    assert downstream_calls == 1
    assert observed == [published]


def test_secure_loopback_refresh_writes_in_memory_settings_without_legacy_file(
    monkeypatch,
):
    refresh_token = "electron-control-token"
    monkeypatch.setenv("PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN", refresh_token)
    saved = []

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    async def save_record(_session, config):
        saved.append(config)
        return ProviderSettingsRecord(revision=7, config={"LLM": "openai"})

    monkeypatch.setattr("api.middlewares.async_session_maker", SessionContext)
    monkeypatch.setattr("api.middlewares.save_provider_settings_record", save_record)
    monkeypatch.setattr(
        "api.middlewares.import_legacy_file_to_provider_settings",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("secure refresh read the compatibility file")
        ),
    )
    middleware = RuntimeSettingsContextMiddleware(
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("private refresh reached the application")
        )
    )
    body = json.dumps(
        {"LLM": "openai", "OPENAI_API_KEY": "provider-secret"}
    ).encode()
    messages = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    asyncio.run(
        middleware(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": RUNTIME_SETTINGS_REFRESH_PATH,
                "raw_path": RUNTIME_SETTINGS_REFRESH_PATH.encode(),
                "query_string": b"",
                "headers": [
                    (
                        b"x-presenton-runtime-settings-token",
                        refresh_token.encode(),
                    ),
                    (b"content-type", b"application/json"),
                ],
                "client": ("127.0.0.1", 1),
                "server": ("test", 80),
            },
            receive,
            send,
        )
    )

    assert messages[0]["status"] == 204
    assert saved == [{"LLM": "openai", "OPENAI_API_KEY": "provider-secret"}]


@pytest.mark.parametrize(
    ("configured_token", "provided_token", "method", "client"),
    [
        (None, b"control-token", "POST", ("127.0.0.1", 1)),
        ("control-token", None, "POST", ("127.0.0.1", 1)),
        ("control-token", b"wrong-token", "POST", ("127.0.0.1", 1)),
        ("control-token", b"control-token", "GET", ("127.0.0.1", 1)),
        ("control-token", b"control-token", "POST", ("192.0.2.10", 1)),
        ("control-token", b"\xff", "POST", ("127.0.0.1", 1)),
    ],
)
def test_runtime_refresh_rejects_unauthorized_control_requests_without_loading(
    monkeypatch,
    configured_token,
    provided_token,
    method,
    client,
):
    if configured_token is None:
        monkeypatch.delenv(
            "PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN",
            raising=False,
        )
    else:
        monkeypatch.setenv(
            "PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN",
            configured_token,
        )
    refresh_calls = []
    monkeypatch.setattr(
        "api.middlewares.refresh_process_runtime_settings",
        lambda: refresh_calls.append(True),
    )
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={"LLM": "openai", "OPENAI_MODEL": "old-model"},
    )
    replace_process_runtime_settings(settings)
    downstream_calls = 0

    async def app(scope, receive, send):
        del scope, receive
        nonlocal downstream_calls
        downstream_calls += 1
        await send({"type": "http.response.start", "status": 418, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    headers = []
    if provided_token is not None:
        headers.append((b"x-presenton-runtime-settings-token", provided_token))
    middleware = RuntimeSettingsContextMiddleware(app)

    messages = asyncio.run(
        _invoke_middleware(
            middleware,
            method=method,
            path=RUNTIME_SETTINGS_REFRESH_PATH,
            headers=headers,
            client=client,
        )
    )

    assert messages[0]["status"] == 404
    assert b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    ) == b""
    assert refresh_calls == []
    assert downstream_calls == 0
    assert get_process_runtime_settings() is settings


def test_runtime_refresh_failure_is_empty_and_keeps_the_previous_snapshot(monkeypatch):
    refresh_token = "control-token"
    secret = "provider-secret-must-not-be-reflected"
    monkeypatch.setenv("PRESENTON_RUNTIME_SETTINGS_REFRESH_TOKEN", refresh_token)
    settings = RuntimeSettings.from_sources(
        environ={},
        user_config={"LLM": "openai", "OPENAI_MODEL": "old-model"},
    )
    replace_process_runtime_settings(settings)

    def fail_refresh(**_kwargs):
        raise RuntimeError(secret)

    class SessionContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return None

    async def import_config(_session):
        return ProviderSettingsRecord(1, {})

    monkeypatch.setattr("api.middlewares.async_session_maker", SessionContext)
    monkeypatch.setattr(
        "api.middlewares.import_legacy_file_to_provider_settings",
        import_config,
    )

    monkeypatch.setattr(
        "api.middlewares.refresh_process_runtime_settings",
        fail_refresh,
    )

    async def app(_scope, _receive, _send):
        raise AssertionError("private refresh must not reach the application")

    middleware = RuntimeSettingsContextMiddleware(app)
    messages = asyncio.run(
        _invoke_middleware(
            middleware,
            method="POST",
            path=RUNTIME_SETTINGS_REFRESH_PATH,
            headers=[
                (b"x-presenton-runtime-settings-token", refresh_token.encode()),
            ],
        )
    )

    assert messages[0]["status"] == 500
    assert b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    ) == b""
    assert secret not in repr(messages)
    assert get_process_runtime_settings() is settings
