"""Phase 3: the authoring call read as a stream, and stopped early when bad.

The fake client is a synchronous iterator of ``llmai``-shaped stream events,
which is exactly what ``client.generate(..., stream=True)`` returns, so the
thread-side pump and the abort rules are exercised as they run in production.
"""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from services.generation_core.authoring import author_slide as author_slide_mod
from services.generation_core.authoring.author_slide_stream import (
    AuthoredStreamAborted,
    PROLOGUE_GRACE_CHARS,
    StreamedAuthorResponse,
    TRAILING_CHATTER_CHARS,
    author_stream_char_budget,
    stream_author_call,
)


def _run(coro):
    return asyncio.run(coro)


def _content(chunk):
    return SimpleNamespace(type="content", chunk=chunk)


def _completion(text, *, usage=None):
    return SimpleNamespace(type="completion", content=text, usage=usage)


class _FakeStreamClient:
    """One provider call that yields the given events, and records the request."""

    def __init__(self, events):
        self._events = list(events)
        self.kwargs = None
        self.closed = False
        self.consumed = 0

    def generate(self, **kwargs):
        self.kwargs = kwargs
        return self._iterator()

    def _iterator(self):
        try:
            for event in self._events:
                self.consumed += 1
                yield event
        finally:
            self.closed = True


class _OpenStreamClient(_FakeStreamClient):
    """A stream that never ends by itself, so only an early stop can end it.

    Whatever the reader decides has to be decided while the provider is still
    sending: the iterator blocks after its scripted events until the test
    releases it.
    """

    def __init__(self, events):
        super().__init__(events)
        self.release = threading.Event()

    def _iterator(self):
        try:
            for event in self._events:
                self.consumed += 1
                yield event
            assert self.release.wait(timeout=5), "the reader never stopped"
        finally:
            self.closed = True


_DOCUMENT = (
    "<!DOCTYPE html><html><head></head><body>"
    "<h1 data-qid='s.title'>authored</h1></body></html>"
)


def _stream(client, **kwargs):
    return _run(
        stream_author_call(
            client,
            {"model": "test-model", "max_tokens": 16_000, **kwargs.pop("call", {})},
            **kwargs,
        )
    )


def test_budget_is_four_characters_per_output_token_with_a_floor():
    assert author_stream_char_budget({"max_tokens": 16_000}) == 64_000
    assert author_stream_char_budget({"max_tokens": 40_000}) == 160_000
    assert author_stream_char_budget({}) == 64_000


def test_a_complete_document_returns_the_providers_own_completion():
    usage = SimpleNamespace(input_tokens=10, output_tokens=20)
    client = _FakeStreamClient(
        [
            _content(_DOCUMENT[:40]),
            _content(_DOCUMENT[40:]),
            _completion(_DOCUMENT, usage=usage),
        ]
    )

    response = _stream(client)

    # The receipt keeps the provider's usage; nothing is reconstructed.
    assert response.usage is usage
    assert response.content == _DOCUMENT
    assert client.kwargs["stream"] is True


def test_a_stream_without_a_completion_event_still_returns_its_text():
    client = _FakeStreamClient([_content(_DOCUMENT)])

    response = _stream(client)

    assert isinstance(response, StreamedAuthorResponse)
    assert response.content == _DOCUMENT


def test_prose_with_no_document_is_aborted_after_the_grace_window():
    prose = "이 슬라이드는 다음과 같이 구성하면 좋겠습니다. " * 200
    assert len(prose) > PROLOGUE_GRACE_CHARS
    client = _OpenStreamClient([_content(prose)])

    try:
        with pytest.raises(AuthoredStreamAborted) as raised:
            _stream(client)
    finally:
        client.release.set()

    # The abort happened while the provider was still sending.
    assert raised.value.reason == "no_document_prologue"
    assert raised.value.retryable is True


def test_a_runaway_document_is_aborted_at_its_own_output_budget():
    client = _OpenStreamClient(
        [_content("<html>"), *[_content("x" * 400) for _ in range(10)]]
    )

    try:
        with pytest.raises(AuthoredStreamAborted) as raised:
            _run(
                stream_author_call(
                    client, {"model": "m", "max_tokens": 1}, max_output_chars=1_000
                )
            )
    finally:
        client.release.set()

    assert raised.value.reason == "output_budget_exceeded"
    assert raised.value.chars > 1_000


def test_trailing_chatter_after_the_document_stops_the_read():
    chatter = "그리고 추가로 설명드리면 " * 100
    assert len(chatter) > TRAILING_CHATTER_CHARS
    client = _OpenStreamClient([_content(_DOCUMENT), _content(chatter)])

    try:
        response = _stream(client)
    finally:
        client.release.set()

    assert response.content == _DOCUMENT
    assert "추가로" not in response.content


def test_a_short_tail_after_the_document_is_kept_and_not_an_abort():
    client = _FakeStreamClient([_content(_DOCUMENT + "\n"), _completion(_DOCUMENT)])

    response = _stream(client)

    assert response.content == _DOCUMENT


def test_a_provider_error_mid_stream_reaches_the_caller():
    class _Failing(_FakeStreamClient):
        def _iterator(self):
            yield _content("<html>")
            raise RuntimeError("provider dropped the stream")

    with pytest.raises(RuntimeError, match="dropped the stream"):
        _stream(_Failing([]))


def test_the_knob_is_off_by_default_so_the_call_path_is_unchanged(monkeypatch):
    monkeypatch.delenv("AUTHORED_AUTHOR_STREAMING_ENABLED", raising=False)
    assert author_slide_mod.is_authored_author_streaming_enabled() is False
    monkeypatch.setenv("AUTHORED_AUTHOR_STREAMING_ENABLED", "true")
    assert author_slide_mod.is_authored_author_streaming_enabled() is True
