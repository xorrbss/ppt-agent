import asyncio

from templates import layout_code_validation


def test_layout_code_validation_forwards_internal_auth_headers(monkeypatch):
    observed: dict[str, object] = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def json(self, **_kwargs):
            return {
                "layout_code": "export const layout = {}",
                "layoutId": "generated",
                "layoutName": "Generated",
                "layoutDescription": "Generated editable layout",
                "schemaJSON": {"type": "object"},
            }

    class FakeSession:
        def __init__(self, *, timeout):
            observed["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def post(self, url, *, json, headers):
            observed.update(url=url, json=json, headers=headers)
            return FakeResponse()

    async def fake_headers():
        return {"Cookie": "presenton_session=test-token"}

    monkeypatch.setenv(
        "LAYOUT_CODE_VALIDATION_URL",
        "http://next.test/",
    )
    monkeypatch.setattr(
        layout_code_validation,
        "authenticated_internal_request_headers",
        fake_headers,
    )
    monkeypatch.setattr(
        layout_code_validation.aiohttp,
        "ClientSession",
        FakeSession,
    )

    validated = asyncio.run(
        layout_code_validation.validate_layout_code(
            "export const layout = {}"
        )
    )

    assert observed["url"] == (
        "http://next.test/api/validate-layout-code"
    )
    assert observed["headers"] == {
        "Content-Type": "application/json",
        "Cookie": "presenton_session=test-token",
    }
    assert validated.layout_id == "generated"
    assert validated.schema_json_value == {"type": "object"}
