"""Focused authored provider outcome and recovery-boundary contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from utils.llm_calls import author_deck as author_deck_mod
from utils.llm_calls.author_slide import Brand
from services.authored_batch_policy import is_authored_provider_overload
from utils.provider_idempotency import (
    PROVIDER_CALL_TIMEOUT_SECONDS,
    ProviderOutcomeError,
    provider_generate_call,
)


_VALID_HTML = (
    "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'></head>"
    "<body><h1 data-qid='title'>제목</h1></body></html>"
)


class _Responses:
    def __init__(self, events):
        self.events = events

    def create(self, **_kwargs):
        return iter(self.events)


class _RawClient:
    def __init__(self, events):
        self.responses = _Responses(events)
        self.options = None
        self.cloned_options = None

    def with_options(self, **options):
        cloned = _RawClient(self.responses.events)
        cloned.options = options
        # Kept on the original so a test can inspect what the clone received.
        self.cloned_options = options
        return cloned


class _ChatGPTClient:
    def __init__(self, events, *, result_content="unused", error=None):
        self._client = _RawClient(events)
        self.result_content = result_content
        self.error = error

    def generate(self, **_kwargs):
        if self.error is not None:
            raise self.error
        for _event in self._client.responses.create():
            pass
        return SimpleNamespace(content=self.result_content)


_ChatGPTClient.__module__ = "llmai.chatgpt.client"


def _call(client):
    return provider_generate_call(
        client,
        provider_id="codex",
        kwargs={},
        idempotency_key=None,
        outcome_scope="authored",
    )


@pytest.mark.parametrize(
    ("event_type", "expected_code", "recovery_allowed"),
    [
        ("response.failed", "public_provider_response_failed", True),
        ("response.incomplete", "public_provider_response_incomplete", False),
    ],
)
def test_terminal_response_events_have_distinct_durable_outcomes(
    event_type, expected_code, recovery_allowed
):
    client = _ChatGPTClient([SimpleNamespace(type=event_type)])

    with pytest.raises(ProviderOutcomeError) as raised:
        _call(client)()

    assert raised.value.code == expected_code
    assert raised.value.retryable is False
    assert raised.value.recovery_allowed is recovery_allowed


def test_empty_output_and_bridge_exception_are_not_terminal_event_aliases():
    empty = _ChatGPTClient([], result_content="  ")
    with pytest.raises(ProviderOutcomeError) as raised_empty:
        _call(empty)()
    assert raised_empty.value.code == "public_provider_empty_output"
    assert raised_empty.value.retryable is False
    assert raised_empty.value.recovery_allowed is True

    class ProviderUnavailable(RuntimeError):
        status_code = 503

    broken = _ChatGPTClient([], error=ProviderUnavailable("private detail"))
    with pytest.raises(ProviderOutcomeError) as raised_bridge:
        _call(broken)()
    assert raised_bridge.value.code == "public_provider_bridge_exception"
    assert raised_bridge.value.retryable is False
    assert raised_bridge.value.recovery_allowed is True
    assert "private detail" not in str(raised_bridge.value)


class _PlainClient:
    """A transport with no idempotency support but a cloneable raw client."""

    def __init__(self, error=None):
        self._client = _RawClient([])
        self.error = error

    def generate(self, **_kwargs):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(content="ok")


_PlainClient.__module__ = "llmai.other.client"


class _UncloneableClient:
    def __init__(self):
        self._client = SimpleNamespace()

    def generate(self, **_kwargs):
        return SimpleNamespace(content="ok")


_UncloneableClient.__module__ = "llmai.other.client"


def test_every_cloneable_transport_gets_the_bounded_call_timeout():
    """The timeout is applied even when nothing else needs adapting."""

    adapted = _ChatGPTClient([])
    _call(adapted)()
    assert adapted._client.options is None  # the shared client is never mutated
    assert adapted._client.cloned_options == {
        "max_retries": 0,
        "timeout": PROVIDER_CALL_TIMEOUT_SECONDS,
    }

    plain = _PlainClient()
    generate = provider_generate_call(
        plain,
        provider_id="anthropic",
        kwargs={},
        idempotency_key=None,
        outcome_scope=None,
    )
    generate()
    # Cloned purely for the timeout: no retry/idempotency adaptation applies.
    assert plain._client.options is None
    assert plain._client.cloned_options == {
        "timeout": PROVIDER_CALL_TIMEOUT_SECONDS
    }


def test_call_timeout_is_a_distinct_recoverable_outcome():
    class ReadTimeout(RuntimeError):
        pass

    timed_out = _ChatGPTClient([], error=ReadTimeout("private endpoint detail"))
    with pytest.raises(ProviderOutcomeError) as raised:
        _call(timed_out)()

    assert raised.value.code == "public_provider_call_timeout"
    assert raised.value.code != "public_provider_bridge_exception"
    assert raised.value.retryable is False
    assert raised.value.recovery_allowed is True
    assert "private endpoint detail" not in str(raised.value)

    # A stuck call is an overload signal: the job drops to serial authoring.
    assert is_authored_provider_overload(raised.value) is True


def test_unenforceable_timeout_is_reported_rather_than_assumed(caplog):
    with caplog.at_level("WARNING"):
        provider_generate_call(
            _UncloneableClient(),
            provider_id="uncloneable-provider",
            kwargs={},
            idempotency_key=None,
            outcome_scope=None,
        )()

    assert "provider_call_timeout_not_enforced" in caplog.text
    assert "uncloneable-provider" in caplog.text


def test_provider_outcome_gets_only_the_explicit_single_recovery(monkeypatch):
    brand = Brand(
        topic="주제",
        language="Korean",
        primary="#2563EB",
        fonts="Noto Sans KR",
    )
    calls = []

    async def succeeds_after_empty(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ProviderOutcomeError(
                "public_provider_empty_output", recovery_allowed=True
            )
        return _VALID_HTML

    monkeypatch.setattr(author_deck_mod, "author_slide_html", succeeds_after_empty)
    result = asyncio.run(
        author_deck_mod._author_one_resilient(
            "slide", "design", brand, "COVER", 0, 1, raise_on_failure=True
        )
    )

    assert result == _VALID_HTML
    assert [call["receipt_attempt_ordinal"] for call in calls] == [1, 2]
    assert all(call["allow_provider_fallback"] is False for call in calls)

    calls.clear()

    async def always_failed(*_args, **kwargs):
        calls.append(kwargs)
        raise ProviderOutcomeError(
            "public_provider_response_failed", recovery_allowed=True
        )

    monkeypatch.setattr(author_deck_mod, "author_slide_html", always_failed)
    with pytest.raises(ProviderOutcomeError) as terminal:
        asyncio.run(
            author_deck_mod._author_one_resilient(
                "slide", "design", brand, "COVER", 0, 1,
                raise_on_failure=True,
            )
        )
    assert terminal.value.code == "public_provider_response_failed"
    assert terminal.value.retryable is False
    assert [call["receipt_attempt_ordinal"] for call in calls] == [1, 2]
