"""The guarded Responses transport records how the provider ended the call.

The fake clients mirror ``tests/unit/test_authored_provider_outcomes.py``: a
raw SDK client whose ``responses.create`` yields the scripted events, wrapped
by an ``llmai``-shaped client that drains them. Both the chatgpt (Codex) and
the openai (API key) shapes are exercised, because ``llmai==0.3.7`` reports a
finish reason on neither.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from utils.provider_idempotency import ProviderOutcomeError, provider_generate_call
from utils.provider_terminal_outcome import (
    FINISH_LENGTH,
    FINISH_STOP,
    ProviderTerminalOutcomeRecorder,
    terminal_outcome_from_response,
)


class _Responses:
    def __init__(self, events, *, streaming=True):
        self.events = events
        self.streaming = streaming

    def create(self, **_kwargs):
        if not self.streaming:
            return self.events
        return iter(self.events)


class _RawClient:
    def __init__(self, events, *, streaming=True):
        self.responses = _Responses(events, streaming=streaming)

    def with_options(self, **_options):
        return _RawClient(self.responses.events, streaming=self.responses.streaming)


class _StreamingClient:
    """Drains the raw stream the way both llmai Responses clients do."""

    def __init__(self, events):
        self._client = _RawClient(events)

    def generate(self, **_kwargs):
        text = ""
        for event in self._client.responses.create(stream=True):
            if getattr(event, "type", None) == "response.output_text.delta":
                text += event.delta
        return SimpleNamespace(content=text)


class _ChatGPTClient(_StreamingClient):
    pass


class _OpenAIClient(_StreamingClient):
    pass


_ChatGPTClient.__module__ = "llmai.chatgpt.client"
_OpenAIClient.__module__ = "llmai.openai.client"

_USAGE = SimpleNamespace(
    input_tokens=120,
    output_tokens=16_000,
    total_tokens=16_120,
    output_tokens_details=SimpleNamespace(reasoning_tokens=900),
)


def _delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _incomplete(reason="max_output_tokens", usage=_USAGE):
    return SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason=reason),
            usage=usage,
        ),
    )


def _completed(usage=_USAGE):
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(status="completed", incomplete_details=None, usage=usage),
    )


def _call(client, *, provider_id, outcome_scope=None):
    return provider_generate_call(
        client,
        provider_id=provider_id,
        kwargs={},
        idempotency_key=None,
        outcome_scope=outcome_scope,
    )


@pytest.mark.parametrize(
    ("client_type", "provider_id"),
    [(_ChatGPTClient, "codex"), (_OpenAIClient, "openai")],
)
def test_max_output_tokens_is_recorded_as_length_with_the_terminal_usage(
    client_type, provider_id
):
    call = _call(client_type([_delta("<html>"), _incomplete()]), provider_id=provider_id)

    assert call.terminal_outcome is None
    response = call()

    assert response.content == "<html>"
    outcome = call.terminal_outcome
    assert outcome is not None
    assert outcome.finish_reason == FINISH_LENGTH
    assert outcome.output_truncated is True
    assert outcome.raw_reason == "max_output_tokens"
    assert (outcome.usage.input_tokens, outcome.usage.output_tokens) == (120, 16_000)
    assert outcome.usage.total_tokens == 16_120
    assert outcome.usage.reasoning_tokens == 900


@pytest.mark.parametrize(
    ("client_type", "provider_id"),
    [(_ChatGPTClient, "codex"), (_OpenAIClient, "openai")],
)
def test_a_completed_stream_is_recorded_as_stop(client_type, provider_id):
    call = _call(client_type([_delta("<html></html>"), _completed()]), provider_id=provider_id)

    call()

    outcome = call.terminal_outcome
    assert outcome.finish_reason == FINISH_STOP
    assert outcome.output_truncated is False
    assert outcome.raw_reason is None
    assert outcome.usage.output_tokens == 16_000


def test_the_outcome_is_recorded_before_the_authored_guard_raises():
    """Under the authored scope the chatgpt guard still turns ``response.incomplete``
    into its durable outcome; the recorder has already seen the event."""

    call = _call(
        _ChatGPTClient([_delta("<html>"), _incomplete()]),
        provider_id="codex",
        outcome_scope="authored",
    )

    with pytest.raises(ProviderOutcomeError) as raised:
        call()

    assert raised.value.code == "public_provider_response_incomplete"
    assert call.terminal_outcome.finish_reason == FINISH_LENGTH


def test_other_incomplete_reasons_keep_their_own_name():
    outcome = terminal_outcome_from_response(_incomplete("content_filter").response)
    assert outcome.finish_reason == "content_filter"

    outcome = terminal_outcome_from_response(_incomplete("some_new_reason").response)
    assert outcome.finish_reason == "some_new_reason"
    assert outcome.raw_reason == "some_new_reason"


def test_a_dict_usage_keeps_its_nested_details_for_the_cached_token_reader():
    usage = {
        "input_tokens": 10,
        "output_tokens": 20,
        "input_tokens_details": {"cached_tokens": 4},
    }

    outcome = terminal_outcome_from_response(_incomplete(usage=usage).response)

    assert outcome.usage.input_tokens == 10
    assert outcome.usage.details == {"input_tokens_details": {"cached_tokens": 4}}


def test_a_missing_usage_is_none_not_zero():
    outcome = terminal_outcome_from_response(_incomplete(usage=None).response)

    assert outcome.finish_reason == FINISH_LENGTH
    assert outcome.usage is None


def test_a_non_streamed_response_is_recorded_from_its_status():
    final = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        usage=_USAGE,
        output_text="<html>",
    )

    class _PlainOpenAIClient:
        def __init__(self):
            self._client = _RawClient(final, streaming=False)

        def generate(self, **_kwargs):
            response = self._client.responses.create(stream=False)
            return SimpleNamespace(content=response.output_text)

    _PlainOpenAIClient.__module__ = "llmai.openai.client"
    call = _call(_PlainOpenAIClient(), provider_id="openai")

    assert call().content == "<html>"
    assert call.terminal_outcome.finish_reason == FINISH_LENGTH


def test_a_stream_closed_before_its_terminal_event_records_nothing():
    call = _call(_OpenAIClient([_delta("<html>"), _delta("<body>")]), provider_id="openai")

    call()

    assert call.terminal_outcome is None


def test_the_recorder_ignores_non_terminal_events():
    recorder = ProviderTerminalOutcomeRecorder()

    recorder.observe_event(_delta("x"))
    recorder.observe_event(SimpleNamespace(type="response.created", response=None))
    recorder.observe_response(SimpleNamespace(status="in_progress"))

    assert recorder.outcome is None
