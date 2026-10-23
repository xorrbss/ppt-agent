"""Heartbeat cleanup observes thread exit and keeps one cancellation-safe budget."""

import asyncio
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import workspace_operation_heartbeat_thread as threaded
from tests.unit.test_workspace_operation_lease import _claim

TEST_WAIT_SECONDS = 2.0
SHUTDOWN_SECONDS = 0.05


class HeartbeatFailure(Exception):
    pass


@pytest.fixture
def delayed_exit(monkeypatch):
    tail_started = threading.Event()
    release = threading.Event()
    threads = []
    engine = SimpleNamespace(dispose=AsyncMock())

    class Thread(threading.Thread):
        def run(self):
            try:
                super().run()
            finally:
                tail_started.set()
                release.wait(TEST_WAIT_SECONDS)

    def make_thread(**kwargs):
        thread = Thread(**kwargs)
        threads.append(thread)
        return thread

    monkeypatch.setattr(threaded, "threading", SimpleNamespace(
        Thread=make_thread, Event=threading.Event, Lock=threading.Lock,
    ))
    monkeypatch.setattr(threaded, "_heartbeat_session_factory", lambda _: (engine, object()))
    yield SimpleNamespace(
        tail_started=tail_started, release=release, threads=threads, engine=engine,
    )
    release.set()
    for thread in threads:
        thread.join(TEST_WAIT_SECONDS)
        assert not thread.is_alive()


async def wait_for_event(event):
    assert await asyncio.to_thread(event.wait, TEST_WAIT_SECONDS)


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
def test_cleanup_waits_for_thread_exit_despite_repeated_cancellation(
    monkeypatch, delayed_exit, outcome,
):
    started = threading.Event()

    async def heartbeat_loop(*args, **kwargs):
        started.set()
        if outcome == "cancelled":
            await asyncio.Future()
        if outcome == "failed":
            raise HeartbeatFailure

    monkeypatch.setattr(threaded, "run_attempt_heartbeat_loop", heartbeat_loop)

    async def scenario():
        heartbeat = asyncio.create_task(threaded.run_threaded_attempt_heartbeat(
            _claim(), session_factory=object(), heartbeat_seconds=1,
            poll_seconds=1, lease_seconds=lambda: 300,
        ))
        try:
            await wait_for_event(started)
            if outcome == "cancelled":
                heartbeat.cancel()
            await wait_for_event(delayed_exit.tail_started)
            delayed_exit.engine.dispose.assert_awaited_once()
            for _ in range(2):
                heartbeat.cancel()
                await asyncio.sleep(0)
                assert not heartbeat.done()
            delayed_exit.release.set()
            with pytest.raises(asyncio.CancelledError):
                await heartbeat
            assert all(not thread.is_alive() for thread in delayed_exit.threads)
        finally:
            delayed_exit.release.set()
            await asyncio.gather(heartbeat, return_exceptions=True)

    asyncio.run(scenario())


def test_unresponsive_thread_exit_keeps_shutdown_bounded(monkeypatch, delayed_exit, caplog):
    monkeypatch.setattr(threaded, "HEARTBEAT_SHUTDOWN_SECONDS", SHUTDOWN_SECONDS)
    monkeypatch.setattr(threaded, "run_attempt_heartbeat_loop", AsyncMock())

    async def scenario():
        with caplog.at_level(logging.ERROR, logger=threaded.__name__):
            await asyncio.wait_for(threaded.run_threaded_attempt_heartbeat(
                _claim(), session_factory=object(), heartbeat_seconds=1,
                poll_seconds=1, lease_seconds=lambda: 300,
            ), TEST_WAIT_SECONDS)
        delayed_exit.engine.dispose.assert_awaited_once()
        assert delayed_exit.tail_started.is_set()
        assert any(thread.is_alive() for thread in delayed_exit.threads)
        assert "Heartbeat shutdown timed out" in caplog.text

    asyncio.run(scenario())
