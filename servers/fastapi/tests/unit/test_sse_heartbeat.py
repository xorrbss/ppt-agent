"""The SSE comment heartbeat that keeps a silent stream inside proxy timeouts.

Every case here uses sub-second intervals rather than the shipped default, so
the suite exercises the real timing path without sleeping for tens of seconds.
The pass-through cases are the ones that matter most: the wrapper races one
in-flight read against a timer, and re-creating that read on each heartbeat
would silently drop or duplicate a chunk.
"""

import asyncio

import pytest

from utils.sse_heartbeat import (
    DEFAULT_SSE_HEARTBEAT_INTERVAL_SECONDS,
    SSE_HEARTBEAT_FRAME,
    get_sse_heartbeat_interval_seconds,
    with_sse_heartbeat,
)


def _run(coro):
    return asyncio.run(coro)


async def _collect(source, *, interval_seconds: float) -> list[str]:
    return [
        frame
        async for frame in with_sse_heartbeat(
            source, interval_seconds=interval_seconds
        )
    ]


async def _chunks(*values: str, delay: float = 0.0):
    for value in values:
        if delay:
            await asyncio.sleep(delay)
        yield value


# --- heartbeat timing ---------------------------------------------------------


def test_heartbeat_is_emitted_while_the_source_is_silent():
    async def slow():
        await asyncio.sleep(0.25)
        yield "event: response\ndata: 1\n\n"

    frames = _run(_collect(slow(), interval_seconds=0.05))

    assert frames[-1] == "event: response\ndata: 1\n\n"
    assert frames[:-1], "a silent gap must produce at least one heartbeat"
    assert set(frames[:-1]) == {SSE_HEARTBEAT_FRAME}
    # ~0.25s of silence at a 0.05s interval; the bound only asserts that the
    # heartbeat repeats rather than firing once.
    assert len(frames[:-1]) >= 3


def test_no_heartbeat_when_the_source_is_chatty():
    frames = _run(_collect(_chunks("a", "b", "c"), interval_seconds=0.5))

    assert frames == ["a", "b", "c"]


def test_a_chunk_resets_the_timer_rather_than_trailing_a_heartbeat():
    """A chunk must never be followed immediately by a heartbeat."""

    async def paced():
        for value in ("a", "b", "c"):
            await asyncio.sleep(0.02)
            yield value

    frames = _run(_collect(paced(), interval_seconds=0.2))

    assert frames == ["a", "b", "c"]


def test_zero_interval_disables_the_heartbeat():
    async def slow():
        await asyncio.sleep(0.05)
        yield "only"

    frames = _run(_collect(slow(), interval_seconds=0))

    assert frames == ["only"]


# --- pass-through fidelity ----------------------------------------------------


def test_every_chunk_survives_a_heartbeat_between_them_in_order():
    """The in-flight read is carried across heartbeats, so nothing is lost."""

    expected = [f"chunk-{index}" for index in range(12)]

    async def interleaved():
        for value in expected:
            await asyncio.sleep(0.03)
            yield value

    frames = _run(_collect(interleaved(), interval_seconds=0.01))

    assert [frame for frame in frames if frame != SSE_HEARTBEAT_FRAME] == expected
    assert frames.count(SSE_HEARTBEAT_FRAME) > 0


def test_an_empty_source_yields_nothing():
    async def empty():
        return
        yield  # pragma: no cover - makes `empty` an async generator

    assert _run(_collect(empty(), interval_seconds=0.01)) == []


# --- failure and cancellation -------------------------------------------------


def test_source_exceptions_propagate():
    class SourceFailed(RuntimeError):
        pass

    async def failing():
        yield "first"
        await asyncio.sleep(0.02)
        raise SourceFailed("boom")

    async def scenario():
        seen: list[str] = []
        with pytest.raises(SourceFailed):
            async for frame in with_sse_heartbeat(
                failing(), interval_seconds=0.005
            ):
                seen.append(frame)
        return seen

    seen = _run(scenario())
    assert "first" in seen


def test_close_cancels_the_pending_read_and_closes_the_source():
    async def scenario():
        closed = asyncio.Event()

        async def source():
            try:
                yield "first"
                await asyncio.sleep(30)
                yield "never"  # pragma: no cover - the consumer closes first
            finally:
                closed.set()

        stream = with_sse_heartbeat(source(), interval_seconds=0.01)
        assert await stream.__anext__() == "first"
        assert await stream.__anext__() == SSE_HEARTBEAT_FRAME
        before = {task for task in asyncio.all_tasks() if not task.done()}
        await stream.aclose()
        # Let any cancelled read finalise before the assertion below.
        await asyncio.sleep(0)
        assert closed.is_set()
        leaked = {
            task
            for task in asyncio.all_tasks()
            if not task.done() and task not in before
        }
        assert leaked == set()

    _run(scenario())


def test_outward_cancellation_is_not_swallowed():
    async def scenario():
        async def source():
            await asyncio.sleep(30)
            yield "never"  # pragma: no cover - cancelled first

        async def consume():
            async for _ in with_sse_heartbeat(source(), interval_seconds=0.01):
                pass

        task = asyncio.ensure_future(consume())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()

    _run(scenario())


# --- interval knob ------------------------------------------------------------


def test_interval_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("SSE_HEARTBEAT_INTERVAL_SECONDS", raising=False)
    assert (
        get_sse_heartbeat_interval_seconds()
        == DEFAULT_SSE_HEARTBEAT_INTERVAL_SECONDS
    )


def test_interval_reads_an_explicit_override(monkeypatch):
    monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL_SECONDS", "7.5")
    assert get_sse_heartbeat_interval_seconds() == 7.5


def test_interval_is_clamped_and_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL_SECONDS", "9999")
    assert get_sse_heartbeat_interval_seconds() == 120.0
    monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL_SECONDS", "0")
    assert get_sse_heartbeat_interval_seconds() == 0.0
    monkeypatch.setenv("SSE_HEARTBEAT_INTERVAL_SECONDS", "not-a-number")
    assert (
        get_sse_heartbeat_interval_seconds()
        == DEFAULT_SSE_HEARTBEAT_INTERVAL_SECONDS
    )
