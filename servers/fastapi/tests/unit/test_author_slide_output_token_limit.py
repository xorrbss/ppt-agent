"""A document the provider cut at its output-token ceiling is its own failure.

The stream client here is shaped like ``llmai``'s chatgpt client: it drains the
raw Responses events through ``self._client.responses`` (which the guarded
transport wraps) and translates them into llmai stream chunks, building the
completion chunk's usage from ``response.completed`` only -- so a stream that
ends with ``response.incomplete`` arrives with ``usage=None``, exactly the
0.3.7 behaviour the reader has to see through.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from services.provider_usage_instrumentation import response_usage
from services.generation_core.authoring.author_slide import Brand
from services.generation_core.authoring.author_slide_fallback import (
    reraise_author_call_error,
    text_only_fallback_applies,
)
from services.generation_core.authoring.author_slide_stream import (
    OUTPUT_TOKEN_LIMIT_REASON,
    AuthoredStreamAborted,
    raise_if_output_token_limited,
    stream_author_call,
)
from utils.provider_terminal_outcome import (
    ProviderTerminalOutcome,
    ProviderTerminalUsage,
)

_DOCUMENT = (
    "<!DOCTYPE html><html><head></head><body>"
    "<h1 data-qid='s.title'>authored</h1></body></html>"
)
_TRUNCATED = _DOCUMENT[: _DOCUMENT.index("</body>")] + "<p>cut mid-"
_USAGE = SimpleNamespace(input_tokens=120, output_tokens=16_000, total_tokens=16_120)


def _run(coro):
    return asyncio.run(coro)


# --- the llmai-shaped stream client over a guarded raw transport --------------


def _delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _incomplete(usage=_USAGE):
    return SimpleNamespace(
        type="response.incomplete",
        response=SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            usage=usage,
        ),
    )


def _completed(usage=_USAGE):
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(status="completed", incomplete_details=None, usage=usage),
    )


class _Responses:
    def __init__(self, events):
        self.events = events

    def create(self, **_kwargs):
        return iter(self.events)


class _RawClient:
    def __init__(self, events):
        self.responses = _Responses(events)

    def with_options(self, **_options):
        return _RawClient(self.responses.events)


class _CompletionChunk:
    """A mutable completion chunk, like llmai's pydantic model."""

    type = "completion"

    def __init__(self, content, usage):
        self.content = content
        self.usage = usage


class _CodexStreamClient:
    def __init__(self, raw_events):
        self._client = _RawClient(raw_events)
        self.kwargs = None

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return self._events()

    def _events(self):
        text = ""
        final_response = None
        for event in self._client.responses.create(stream=True):
            if event.type == "response.output_text.delta":
                text += event.delta
                yield SimpleNamespace(type="content", chunk=event.delta)
            elif event.type == "response.completed":
                final_response = event.response
        usage = getattr(final_response, "usage", None)
        yield _CompletionChunk(text, usage)


_CodexStreamClient.__module__ = "llmai.chatgpt.client"


def _stream(raw_events):
    return _run(
        stream_author_call(
            _CodexStreamClient(raw_events), {"model": "test-model", "max_tokens": 16_000}
        )
    )


def test_length_without_a_document_end_aborts_as_output_token_limit():
    with pytest.raises(AuthoredStreamAborted) as raised:
        _stream([_delta(_TRUNCATED[:30]), _delta(_TRUNCATED[30:]), _incomplete()])

    assert raised.value.reason == OUTPUT_TOKEN_LIMIT_REASON
    assert raised.value.retryable is True
    assert raised.value.chars == len(_TRUNCATED)


def test_length_with_the_document_end_present_is_kept():
    response = _stream([_delta(_DOCUMENT), _incomplete()])

    assert response.content == _DOCUMENT


def test_stop_with_a_truncated_document_is_returned_unchanged():
    """A model that stopped on its own mid-document is still admission's case."""

    response = _stream([_delta(_TRUNCATED), _completed()])

    assert response.content == _TRUNCATED
    assert response.usage is _USAGE


def test_usage_is_recovered_from_the_terminal_event_when_the_chunk_lost_it():
    response = _stream([_delta(_DOCUMENT), _incomplete()])

    assert response.usage is not None
    assert response.usage.output_tokens == 16_000
    # The receipt reads the same fields, so the token counts survive.
    assert response_usage(response) == ("reported", 120, 16_000, None)


def test_a_completion_that_keeps_its_own_usage_is_not_overwritten():
    response = _stream([_delta(_DOCUMENT), _completed()])

    assert response.usage is _USAGE


# --- the non-streaming seam ---------------------------------------------------


def _outcome(finish_reason, usage=None):
    return ProviderTerminalOutcome(finish_reason, usage=usage)


def test_the_non_streaming_check_uses_the_same_rule():
    with pytest.raises(AuthoredStreamAborted) as raised:
        raise_if_output_token_limited(_outcome("length"), _TRUNCATED)
    assert raised.value.reason == OUTPUT_TOKEN_LIMIT_REASON

    raise_if_output_token_limited(_outcome("length"), _DOCUMENT + "\n  ")
    raise_if_output_token_limited(_outcome("stop"), _TRUNCATED)
    raise_if_output_token_limited(None, _TRUNCATED)


# --- how the abort travels through the attempt loop ---------------------------


def test_the_abort_survives_the_author_call_boundary_untouched():
    abort = AuthoredStreamAborted(OUTPUT_TOKEN_LIMIT_REASON, chars=60_000)

    with pytest.raises(AuthoredStreamAborted) as raised:
        reraise_author_call_error(abort)

    assert raised.value is abort
    # Dropping the images does not change the output budget.
    assert text_only_fallback_applies(abort) is False


# --- the batch lane hands it to the rescue ------------------------------------


def _profile():
    return SimpleNamespace(strict_dom_gate=True, allow_summarization=True)


# --- the guarded (non-streaming) chatgpt path names the ceiling too -----------


def _guard_incomplete_error():
    from utils.provider_idempotency import ProviderOutcomeError

    return ProviderOutcomeError(
        "public_provider_response_incomplete", recovery_allowed=False
    )


def test_the_guard_verdict_becomes_the_ceiling_abort_only_for_a_length_outcome():
    from services.generation_core.authoring.author_slide_stream import (
        output_token_limit_from_provider_outcome,
    )

    aborted = output_token_limit_from_provider_outcome(
        _guard_incomplete_error(), _outcome("length")
    )
    assert isinstance(aborted, AuthoredStreamAborted)
    assert aborted.reason == OUTPUT_TOKEN_LIMIT_REASON

    # Any other incomplete reason keeps the guard's terminal verdict.
    assert output_token_limit_from_provider_outcome(
        _guard_incomplete_error(), _outcome("content_filter")
    ) is None
    assert output_token_limit_from_provider_outcome(
        _guard_incomplete_error(), None
    ) is None
    # And an unrelated provider code is never rewritten.
    assert output_token_limit_from_provider_outcome(
        RuntimeError("public_provider_response_failed"), _outcome("length")
    ) is None


# --- the author-stage rescue flags it by name ---------------------------------


def _plan():
    return SimpleNamespace(
        contents=("분기 실적 요약\n매출 120억 원\n영업이익 18억 원",),
        roles=("CONTENT",),
        brand=Brand(topic="주제", language="Korean", primary="#2563EB", fonts="Noto Sans KR"),
        header_contract_at=lambda _index: None,
    )


def test_terminal_usage_stands_in_for_a_missing_usage_everywhere_it_is_read():
    usage = ProviderTerminalUsage(
        input_tokens=10,
        output_tokens=20,
        details={"input_tokens_details": {"cached_tokens": 4}},
    )

    assert response_usage(SimpleNamespace(usage=usage)) == ("reported", 10, 20, 4)
