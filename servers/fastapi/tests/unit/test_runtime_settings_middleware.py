import asyncio

from api.middlewares import RuntimeSettingsContextMiddleware
from utils.runtime_settings import (
    RuntimeSettings,
    replace_process_runtime_settings,
    resolve_runtime_settings,
)


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
