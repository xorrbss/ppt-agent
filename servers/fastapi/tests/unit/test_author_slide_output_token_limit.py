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


from services.provider_usage_instrumentation import response_usage
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


# --- the non-streaming seam ---------------------------------------------------


def _outcome(finish_reason, usage=None):
    return ProviderTerminalOutcome(finish_reason, usage=usage)


# --- how the abort travels through the attempt loop ---------------------------


# --- the batch lane hands it to the rescue ------------------------------------


def _profile():
    return SimpleNamespace(strict_dom_gate=True, allow_summarization=True)


# --- the guarded (non-streaming) chatgpt path names the ceiling too -----------


def _guard_incomplete_error():
    from utils.provider_idempotency import ProviderOutcomeError

    return ProviderOutcomeError(
        "public_provider_response_incomplete", recovery_allowed=False
    )


# --- the author-stage rescue flags it by name ---------------------------------


def test_terminal_usage_stands_in_for_a_missing_usage_everywhere_it_is_read():
    usage = ProviderTerminalUsage(
        input_tokens=10,
        output_tokens=20,
        details={"input_tokens_details": {"cached_tokens": 4}},
    )

    assert response_usage(SimpleNamespace(usage=usage)) == ("reported", 10, 20, 4)
