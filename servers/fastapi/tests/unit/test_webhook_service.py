"""Webhook delivery: HMAC-SHA256 signs the exact posted bytes (never the shared
secret), a failed delivery (connection error OR non-2xx) is retried with
backoff before giving up, and every attempt goes through the outbound URL
guard (DNS pinned, redirects rejected, private targets never contacted)."""
import asyncio
import hashlib
import hmac
import json

from models.sql.webhook_subscription import WebhookSubscription
from services import webhook_service as webhook_module
from services.webhook_service import WebhookService
from tests.conftest import FakeAsyncSession
from utils.datetime_utils import get_current_utc_datetime

_PUBLIC_ANSWER = "93.184.216.34"


class _FakeSessionContext:
    def __init__(self, session: FakeAsyncSession):
        self._session = session

    async def __aenter__(self) -> FakeAsyncSession:
        return self._session

    async def __aexit__(self, *_exc) -> bool:
        return False


def _fake_session_maker_for(sub: WebhookSubscription):
    """A ``_session_maker()``-shaped seam backed by a FakeAsyncSession that
    already holds ``sub`` under its own id, so ``_record_delivery_outcome``
    can find and mutate it without touching a real database."""
    session = FakeAsyncSession(get_results={sub.id: sub})
    return (lambda: _FakeSessionContext(session)), session


def _offline_resolver(_hostname: str, _port: int) -> tuple[str, ...]:
    return (_PUBLIC_ANSWER,)


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

    def __init__(self, captured: dict, behaviors: list, target):
        self._captured = captured
        self._behaviors = list(behaviors)
        self._captured["target"] = target

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data=None, headers=None, **kwargs):
        self._captured.update(url=url, data=data, headers=headers, post_kwargs=kwargs)
        self._captured["attempts"] = self._captured.get("attempts", 0) + 1
        behavior = self._behaviors.pop(0) if self._behaviors else 200
        if isinstance(behavior, Exception):
            raise behavior
        return _FakeResponse(status=behavior)


async def _no_sleep(_seconds):
    return None


def _send(
    sub: WebhookSubscription, data: dict, behaviors=(200,), resolver=None
) -> dict:
    captured: dict = {"attempts": 0}
    session_maker, outcome_session = _fake_session_maker_for(sub)
    original_build = webhook_module._build_session
    original_resolver = webhook_module._resolver
    original_sleep = webhook_module.asyncio.sleep
    original_session_maker = webhook_module._session_maker
    webhook_module._build_session = lambda target: _FakeSession(
        captured, behaviors, target
    )
    webhook_module._resolver = resolver or _offline_resolver
    webhook_module.asyncio.sleep = _no_sleep  # don't actually wait out the backoff
    webhook_module._session_maker = session_maker
    try:
        asyncio.run(WebhookService.send_request_to_webhook(sub, data))
    finally:
        webhook_module._build_session = original_build
        webhook_module._resolver = original_resolver
        webhook_module.asyncio.sleep = original_sleep
        webhook_module._session_maker = original_session_maker
    captured["outcome_session"] = outcome_session
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


def test_webhook_posts_over_pinned_session_without_following_redirects():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    captured = _send(sub, {"x": 1}, behaviors=[200])

    assert captured["target"].hostname == "example.test"
    assert captured["target"].resolved_ips == frozenset({_PUBLIC_ANSWER})
    assert captured["post_kwargs"]["allow_redirects"] is False


def test_webhook_redirect_is_denied_and_not_retried():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    captured = _send(sub, {"x": 1}, behaviors=[302, 200, 200])
    assert captured["attempts"] == 1


def test_webhook_to_private_target_is_never_contacted():
    sub = WebhookSubscription(
        url="http://127.0.0.1:8000/hook", secret=None, event="ppt.done"
    )
    captured = _send(sub, {"x": 1}, behaviors=[200])
    assert captured["attempts"] == 0


# --- Retry classification: a DNS outage is retried, a policy rejection isn't --


def test_webhook_dns_failure_is_retried_and_exhausts_all_attempts():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    calls = {"count": 0}

    def flaky_resolver(_hostname, _port):
        calls["count"] += 1
        raise OSError("dns unavailable")

    captured = _send(sub, {"x": 1}, behaviors=[200], resolver=flaky_resolver)

    # egress_dns_unavailable is retryable: every attempt re-resolves and the
    # loop runs out its full budget without ever reaching a session/post.
    assert calls["count"] == webhook_module._MAX_ATTEMPTS
    assert captured["attempts"] == 0
    assert "target" not in captured


def test_webhook_policy_rejection_terminates_immediately_without_retry():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    calls = {"count": 0}

    def rebinding_resolver(_hostname, _port):
        calls["count"] += 1
        # A private answer is a deterministic policy denial (rebinding), not
        # a transient DNS outage, so it must not be retried.
        return ("10.1.1.1",)

    captured = _send(sub, {"x": 1}, behaviors=[200], resolver=rebinding_resolver)

    assert calls["count"] == 1
    assert captured["attempts"] == 0
    assert "target" not in captured


def test_webhook_redirect_policy_rejection_after_connect_is_not_retried_again():
    # Belt-and-suspenders: a redirect (already covered above) is the other
    # non-retryable path and must show the same one-resolver-call shape.
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    calls = {"count": 0}

    def resolver(_hostname, _port):
        calls["count"] += 1
        return (_PUBLIC_ANSWER,)

    captured = _send(sub, {"x": 1}, behaviors=[302, 200, 200], resolver=resolver)

    assert calls["count"] == 1
    assert captured["attempts"] == 1
    assert captured["target"].resolved_ips == frozenset({_PUBLIC_ANSWER})


# --- Terminal rejection is recorded on the subscription, not only logged ---


def test_webhook_terminal_rejection_is_recorded_on_the_subscription():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )

    def rebinding_resolver(_hostname, _port):
        return ("10.1.1.1",)  # resolves private: a terminal policy denial

    captured = _send(sub, {"x": 1}, behaviors=[200], resolver=rebinding_resolver)

    assert sub.last_delivery_rejected_code == "egress_private_address_denied"
    assert sub.last_delivery_rejected_at is not None
    assert captured["outcome_session"].commit_count == 1


def test_webhook_success_clears_a_previously_recorded_rejection():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )
    sub.last_delivery_rejected_code = "egress_private_address_denied"
    sub.last_delivery_rejected_at = get_current_utc_datetime()

    captured = _send(sub, {"x": 1}, behaviors=[200])

    assert sub.last_delivery_rejected_code is None
    assert sub.last_delivery_rejected_at is None
    assert captured["outcome_session"].commit_count == 1


def test_webhook_dns_outage_exhaustion_does_not_record_a_terminal_rejection():
    sub = WebhookSubscription(
        url="https://example.test/hook", secret=None, event="ppt.done"
    )

    def flaky_resolver(_hostname, _port):
        raise OSError("dns unavailable")

    captured = _send(sub, {"x": 1}, behaviors=[200], resolver=flaky_resolver)

    # A DNS outage is transient and retried until the budget is exhausted; it
    # must not be written to the row like a deterministic policy rejection.
    assert sub.last_delivery_rejected_code is None
    assert captured["outcome_session"].commit_count == 0
