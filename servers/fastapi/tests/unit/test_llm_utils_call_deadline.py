"""The structured call's transport deadline and cancellation contract.

``asyncio.wait_for`` cancels the awaiting coroutine, but a thread running the
provider SDK cannot be interrupted.  Before these tests a timed-out content
brief kept running to completion behind the cancelled await, the retry ran
concurrently with it, and the orphan held a default-executor slot.  The
contract now: the caller's deadline reaches the transport (SDK request
timeout, ``max_retries`` pinned to zero), a streaming transport is told to
abort at its next event, and the cancelled await does not return until the
worker has actually finished -- so no retry starts while a call is in flight.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from utils import llm_utils
from utils.llm_utils import generate_structured_with_schema_retries
from utils.structured_call_budget import StructuredProviderCallBudget


class _FakeSdkTimeout(RuntimeError):
    """Stands in for the SDK's ``APITimeoutError``."""


class _Responses:
    """A Responses resource whose stream produces an event every few ms forever."""

    def __init__(self, owner):
        self.owner = owner

    def create(self, **_kwargs):
        def events():
            try:
                while True:
                    time.sleep(0.005)
                    yield SimpleNamespace(type="response.output_text.delta")
            finally:
                self.owner.stream_closed.set()

        return events()


class _RawClient:
    def __init__(self, owner, options=None):
        self.owner = owner
        self.options = options or {}
        self.responses = _Responses(owner)

    def with_options(self, **options):
        clone = _RawClient(self.owner, options)
        self.owner.cloned_options = options
        return clone


class _Stats:
    """Counters shared by the client and the per-call shallow copy the
    transport adapter makes of it (``copy(client)``), so the test reads what
    the copy recorded.  ``in_flight`` counts calls currently inside
    ``generate``; ``max_in_flight`` records the most that ever overlapped."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    def enter(self):
        with self.lock:
            self.calls += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)

    def leave(self):
        with self.lock:
            self.in_flight -= 1


class _BlockingClient:
    """A non-streaming transport: ``generate`` blocks until the SDK timeout fires."""

    def __init__(self, *, honour_timeout=True):
        self._client = _RawClient(self)
        self.honour_timeout = honour_timeout
        self.cloned_options = None
        self.release = threading.Event()
        self.stream_closed = threading.Event()
        self.stats = _Stats()

    @property
    def calls(self):
        return self.stats.calls

    @property
    def in_flight(self):
        return self.stats.in_flight

    @property
    def max_in_flight(self):
        return self.stats.max_in_flight

    def generate(self, **_kwargs):
        self.stats.enter()
        try:
            timeout = self._client.options.get("timeout") if self.honour_timeout else None
            if isinstance(timeout, httpx.Timeout):
                # A blocked request is waiting on its read phase.
                timeout = timeout.read
            if not self.release.wait(timeout=timeout):
                raise _FakeSdkTimeout("request timed out")
            return SimpleNamespace(content={"result": "ok"})
        finally:
            self.stats.leave()


_BlockingClient.__module__ = "llmai.other.client"


class _StreamingClient(_BlockingClient):
    """A chatgpt-shaped transport: even a non-streaming ``generate`` consumes a stream."""

    def generate(self, **_kwargs):
        self.stats.enter()
        try:
            for _event in self._client.responses.create(stream=True):
                pass
            return SimpleNamespace(content={"result": "ok"})
        finally:
            self.stats.leave()


_StreamingClient.__module__ = "llmai.chatgpt.client"


def _structured(client, **overrides):
    return generate_structured_with_schema_retries(
        client,
        "test-model",
        messages=[],
        response_format=object(),
        json_schema={},
        **overrides,
    )


def _timeout_fields(timeout):
    return {"connect": timeout.connect, "read": timeout.read, "write": timeout.write, "pool": timeout.pool}


def test_call_deadline_reaches_the_transport_and_pins_max_retries(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")
    client = _BlockingClient()
    client.release.set()

    result = asyncio.run(_structured(client, call_timeout_seconds=12.5))

    assert result == {"result": "ok"}
    # The caller's deadline replaces the generic 360 s guard, and the SDK may
    # not silently replay a bounded call after its timeout.
    # The transport gets the deadline remaining when the request starts, which
    # a fine-grained monotonic clock reports a hair under the nominal value.
    assert client.cloned_options["max_retries"] == 0
    assert set(client.cloned_options) == {"max_retries", "timeout"}
    assert _timeout_fields(client.cloned_options["timeout"]) == pytest.approx(
        _timeout_fields(httpx.Timeout(12.5)), abs=0.05)


def test_without_a_caller_deadline_the_generic_guard_stays(monkeypatch):
    monkeypatch.setenv("LLM", "ollama")
    client = _BlockingClient()
    client.release.set()

    asyncio.run(_structured(client))

    assert set(client.cloned_options) == {"timeout"}
    assert _timeout_fields(client.cloned_options["timeout"]) == pytest.approx(
        _timeout_fields(httpx.Timeout(connect=30.0, read=360.0, write=60.0, pool=30.0)), abs=0.05)


def test_timed_out_call_is_not_running_when_the_retry_starts(monkeypatch):
    """The transport ends the request at the deadline and the cancelled await
    waits for the worker, so the retry never overlaps the first call."""

    monkeypatch.setenv("LLM", "ollama")
    client = _BlockingClient()

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                _structured(client, call_timeout_seconds=0.2), timeout=0.2
            )
        # Proof: the moment the timeout surfaces, nothing is in flight.
        assert client.in_flight == 0
        assert client.calls == 1

        client.release.set()
        retry = await _structured(client, call_timeout_seconds=0.2, is_retry=True)
        assert retry == {"result": "ok"}

    asyncio.run(scenario())

    assert client.calls == 2
    assert client.max_in_flight == 1


def test_streaming_transport_is_aborted_at_its_next_event(monkeypatch):
    """A chatgpt-shaped call keeps streaming past its inactivity timeout; the
    abort flag ends it at the next event and closes the stream."""

    monkeypatch.setenv("LLM", "ollama")
    client = _StreamingClient()

    async def scenario():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                _structured(client, call_timeout_seconds=5.0), timeout=0.1
            )
        assert client.in_flight == 0
        assert client.stream_closed.is_set()

    asyncio.run(scenario())
    assert client.calls == 1
    assert client.max_in_flight == 1


def test_unbounded_transport_is_reported_after_the_grace(monkeypatch, caplog):
    """A transport that ignores both the timeout and the abort cannot be
    stopped; the cancel returns after the grace and says so instead of
    pretending the call ended."""

    monkeypatch.setenv("LLM", "ollama")
    monkeypatch.setattr(llm_utils, "PROVIDER_CALL_ABORT_GRACE_SECONDS", 0.1)
    client = _BlockingClient(honour_timeout=False)

    async def scenario():
        started = time.monotonic()
        try:
            with caplog.at_level("WARNING", logger=llm_utils.LOGGER.name):
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        _structured(client, call_timeout_seconds=0.05), timeout=0.05
                    )
            assert time.monotonic() - started < 2.0
            assert "provider_call_abort_unconfirmed" in caplog.text
            assert client.in_flight == 1
        finally:
            # Always let the orphan finish, or the loop's executor shutdown
            # would wait on it forever after a failed assertion.
            client.release.set()

    asyncio.run(scenario())
    deadline = time.monotonic() + 2.0
    while client.in_flight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert client.in_flight == 0


def test_shared_call_budget_caps_both_runs_at_the_documented_limit(monkeypatch):
    """Two runs of one logical call (the brief and its retry) that share a
    budget make at most ``PUBLIC_STRUCTURED_MAX_PHYSICAL_CALLS`` requests."""

    monkeypatch.setenv("LLM", "ollama")

    class _Client:
        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            self.calls += 1
            return SimpleNamespace(content=None)  # unparsable: triggers a parse retry

    client = _Client()
    budget = StructuredProviderCallBudget()

    async def scenario():
        monkeypatch.setattr(llm_utils.asyncio, "sleep", _no_sleep)
        with pytest.raises(HTTPException) as first:
            await _structured(client, call_budget=budget)
        assert first.value.status_code == 400
        assert client.calls == 2
        assert budget.remaining == 0

        with pytest.raises(HTTPException):
            await _structured(client, call_budget=budget, is_retry=True)

    asyncio.run(scenario())
    # The second run made no physical call at all.
    assert client.calls == 2


async def _no_sleep(_seconds):
    return None
