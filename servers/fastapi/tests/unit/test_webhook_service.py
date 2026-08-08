"""Webhook delivery: HMAC-SHA256 signs the exact posted bytes (never the shared
secret), and a failed delivery (connection error OR non-2xx) is retried with
backoff before giving up."""
import asyncio
import hashlib
import hmac
import json

from models.sql.webhook_subscription import WebhookSubscription
from services import webhook_service as webhook_module
from services.webhook_service import WebhookService


class _FakeResponse:
    def __init__(self, status: int = 200):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    """Captures posts; each post() consumes the next behaviour — a status int for
    a response, or an Exception instance to raise (simulating a connection error)."""

    def __init__(self, captured: dict, behaviors: list):
        self._captured = captured
        self._behaviors = list(behaviors)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data=None, headers=None):
        self._captured.update(url=url, data=data, headers=headers)
        self._captured["attempts"] = self._captured.get("attempts", 0) + 1
        behavior = self._behaviors.pop(0) if self._behaviors else 200
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeResponse(status=behavior)


async def _no_sleep(_seconds):
    return None


def _send(sub: WebhookSubscription, data: dict, behaviors=(200,)) -> dict:
    captured: dict = {}
    original_session = webhook_module.aiohttp.ClientSession
    original_sleep = webhook_module.asyncio.sleep
    webhook_module.aiohttp.ClientSession = lambda: _FakeSession(captured, behaviors)
    webhook_module.asyncio.sleep = _no_sleep  # don't actually wait out the backoff
    try:
        asyncio.run(WebhookService.send_request_to_webhook(sub, data))
    finally:
        webhook_module.aiohttp.ClientSession = original_session
        webhook_module.asyncio.sleep = original_sleep
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


def test_webhook_retries_failures_then_succeeds():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    # 1st: non-2xx, 2nd: connection error, 3rd: success.
    captured = _send(
        sub, {"x": 1}, behaviors=[500, ConnectionError("boom"), 200]
    )
    assert captured["attempts"] == 3


def test_webhook_gives_up_after_max_attempts():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    # Every attempt fails — the service stops after _MAX_ATTEMPTS and never raises.
    captured = _send(sub, {"x": 1}, behaviors=[500, 500, 500])
    assert captured["attempts"] == webhook_module._MAX_ATTEMPTS


def test_webhook_succeeds_first_try_makes_one_attempt():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    captured = _send(sub, {"x": 1}, behaviors=[200])
    assert captured["attempts"] == 1
