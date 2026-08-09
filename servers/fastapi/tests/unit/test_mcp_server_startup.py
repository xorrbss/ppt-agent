import asyncio

import httpx

import mcp_server


def test_mcp_cli_defaults_to_production_safe_reload_setting():
    args = mcp_server.parse_args([])

    assert args.port == 8001
    assert args.reload == "false"
    assert mcp_server.parse_args(["--reload", "true"]).reload == "true"


def test_mcp_runtime_passes_reload_false_to_uvicorn_by_default(monkeypatch):
    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeMcp:
        async def run_async(self, **kwargs):
            captured["run_kwargs"] = kwargs

    def fake_from_openapi(**kwargs):
        captured["openapi_kwargs"] = kwargs
        return FakeMcp()

    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(mcp_server.FastMCP, "from_openapi", fake_from_openapi)

    asyncio.run(mcp_server.run_mcp_server(mcp_server.parse_args([])))

    assert captured["run_kwargs"] == {
        "transport": "http",
        "host": "127.0.0.1",
        "port": 8001,
        "uvicorn_config": {"reload": False},
    }
    assert captured["client_kwargs"]["event_hooks"] == {
        "request": [mcp_server.attach_request_auth_headers]
    }


def test_mcp_forwards_verified_bearer_and_cookie_to_fastapi(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "get_http_headers",
        lambda include=None: {
            "authorization": "Bearer sk-presenton-test",
            "cookie": "presenton_session=test-session",
        },
    )
    request = httpx.Request("POST", "http://127.0.0.1:8000/api/v1/example")

    asyncio.run(mcp_server.attach_request_auth_headers(request))

    assert request.headers["Authorization"] == "Bearer sk-presenton-test"
    assert request.headers["Cookie"] == "presenton_session=test-session"


def test_mcp_does_not_overwrite_existing_fastapi_credentials(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "get_http_headers",
        lambda include=None: {
            "authorization": "Bearer incoming",
            "cookie": "presenton_session=incoming",
        },
    )
    request = httpx.Request(
        "POST",
        "http://127.0.0.1:8000/api/v1/example",
        headers={
            "Authorization": "Bearer existing",
            "Cookie": "presenton_session=existing",
        },
    )

    asyncio.run(mcp_server.attach_request_auth_headers(request))

    assert request.headers["Authorization"] == "Bearer existing"
    assert request.headers["Cookie"] == "presenton_session=existing"
