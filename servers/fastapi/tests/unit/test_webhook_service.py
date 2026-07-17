"""Webhook delivery signs the exact posted bytes with HMAC-SHA256 and never
puts the shared secret on the wire (the old code sent it as a Bearer token)."""
import asyncio
import hashlib
import hmac
import json

from models.sql.webhook_subscription import WebhookSubscription
from services import webhook_service as webhook_module
from services.webhook_service import WebhookService


class _FakeResponse:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Captures the single post() the service makes."""

    def __init__(self, captured: dict):
        self._captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data=None, headers=None):
        self._captured.update(url=url, data=data, headers=headers)
        return _FakeResponse()


def _send(sub: WebhookSubscription, data: dict) -> dict:
    captured: dict = {}
    original = webhook_module.aiohttp.ClientSession
    webhook_module.aiohttp.ClientSession = lambda: _FakeSession(captured)
    try:
        asyncio.run(WebhookService.send_request_to_webhook(sub, data))
    finally:
        webhook_module.aiohttp.ClientSession = original
    return captured


def test_webhook_signs_body_with_hmac_and_hides_secret():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret="s3cr3t", event="ppt.done"
    )
    data = {"b": 2, "a": 1}

    captured = _send(sub, data)

    body = captured["data"]
    assert isinstance(body, (bytes, bytearray))
    # what we sign is exactly what we post, and it decodes to the payload
    assert json.loads(body) == data
    # the shared secret must never cross the wire
    assert "Authorization" not in captured["headers"]
    assert "s3cr3t" not in json.dumps(captured["headers"])
    expected = hmac.new(b"s3cr3t", bytes(body), hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Presenton-Signature"] == f"sha256={expected}"


def test_webhook_without_secret_sends_no_signature():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )

    captured = _send(sub, {"x": 1})

    assert "X-Presenton-Signature" not in captured["headers"]
    assert "Authorization" not in captured["headers"]
    assert json.loads(captured["data"]) == {"x": 1}
