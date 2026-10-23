"""Suspend recovery and heartbeat independence on the default runner path."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import threading

import pytest
from sqlalchemy import update

from models.sql.workspace import WorkspaceOperation
from services import workspace_operation_heartbeat_thread as threaded
from services import workspace_operation_lease as lease
from services import workspace_operation_runner as runner_module
from services.workspace_operation_handler_registry import (
    WorkspaceOperationCancelled,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from tests.unit.test_workspace_operation_runner import (
    OWNER_ID,
    _database,
    _drain_once,
    _operation,
    _seed_operations,
)
from utils.datetime_utils import as_utc
from utils.owner_context import get_current_owner_id


class Clock:
    def __init__(self):
        self.wall = datetime(2026, 10, 23, tzinfo=timezone.utc)
        self.tick = 0.0

    def advance(self, seconds, *, monotonic=True):
        self.wall += timedelta(seconds=seconds)
        if monotonic:
            self.tick += seconds

    def install(self, monkeypatch):
        monkeypatch.setattr(runner_module, "_utc_now", lambda: self.wall)
        monkeypatch.setattr(runner_module, "monotonic", lambda: self.tick)
        monkeypatch.setattr(lease, "get_current_utc_datetime", lambda: self.wall)
        monkeypatch.delenv("WORKSPACE_OPERATION_HEARTBEAT_SECONDS", raising=False)
        monkeypatch.delenv("WORKSPACE_OPERATION_POLL_SECONDS", raising=False)
        monkeypatch.delenv("WORKSPACE_POLL_IDLE_CEILING_SECONDS", raising=False)


async def running_operations(maker, clock, *, kind="test.operation", count=1, expires_in=300):
    _, ids = await _seed_operations(maker, count=count, kind=kind)
    async with maker() as session:
        await session.execute(update(WorkspaceOperation).where(
            WorkspaceOperation.id.in_(ids)
        ).values(
            state="running", attempt_number=1, attempt_token="attempt", fencing_token=1,
            heartbeat_at=clock.wall, lease_expires_at=clock.wall + timedelta(seconds=expires_in),
        ))
        await session.commit()
    registry = WorkspaceOperationHandlerRegistry()
    registry.register(kind, lambda context: None, recovery_policy="automatic")
    return ids, registry


@pytest.mark.parametrize("monotonic_runs_during_suspend", [True, False])
@pytest.mark.parametrize("kind", ["export.pptx", "slide.regenerate.batch"])
def test_resume_grace_saves_live_holder_and_eventually_recovers_dead_holder(
    tmp_path, monkeypatch, monotonic_runs_during_suspend, kind,
):
    clock = Clock()
    clock.install(monkeypatch)

    async def scenario():
        engine, maker = await _database(tmp_path / "resume.db")
        try:
            ids, registry = await running_operations(maker, clock, kind=kind, count=2)

            async def sweep():
                return await runner_module.recover_expired_workspace_operations(
                    session_factory=maker, registry=registry,
                )

            assert await sweep() == 0
            clock.advance(1800, monotonic=monotonic_runs_during_suspend)
            assert await sweep() == 0
            clock.advance(5)
            assert await lease.heartbeat_workspace_operation(
                ids[0], "attempt", fencing_token=1, session_factory=maker,
            )
            assert await sweep() == 0
            clock.advance(29)
            assert await sweep() == 0
            clock.advance(1)
            assert await sweep() == 1
            assert (await _operation(maker, ids[0])).state == "running"
            assert (await _operation(maker, ids[1])).state == "queued"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_expiry_without_a_reclaimer_gap_is_unchanged(tmp_path, monkeypatch):
    clock = Clock()
    clock.install(monkeypatch)

    async def scenario():
        engine, maker = await _database(tmp_path / "expiry.db")
        try:
            ids, registry = await running_operations(maker, clock, expires_in=10)
            assert await runner_module.recover_expired_workspace_operations(
                session_factory=maker, registry=registry,
            ) == 0
            clock.advance(11)
            assert await runner_module.recover_expired_workspace_operations(
                session_factory=maker, registry=registry,
            ) == 1
            assert (await _operation(maker, ids[0])).state == "queued"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_resume_grace_does_not_cover_leases_expired_before_the_gap(tmp_path, monkeypatch):
    clock = Clock()
    clock.install(monkeypatch)

    async def scenario():
        engine, maker = await _database(tmp_path / "already-expired.db")
        try:
            assert await runner_module.recover_expired_workspace_operations(session_factory=maker) == 0
            ids, registry = await running_operations(maker, clock, expires_in=-1)
            clock.advance(1800)
            assert await runner_module.recover_expired_workspace_operations(
                session_factory=maker, registry=registry,
            ) == 1
            assert (await _operation(maker, ids[0])).state == "queued"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("monotonic_runs_during_suspend", [True, False])
def test_watchdog_gives_handler_a_tick_window_when_its_own_thread_resumes(
    monkeypatch, monotonic_runs_during_suspend,
):
    clock = Clock()
    monkeypatch.setattr(threaded, "monotonic", lambda: clock.tick)
    monkeypatch.setattr(threaded, "time", lambda: clock.wall.timestamp())
    watchdog = threaded.HandlerLoopWatchdog()
    sleeps = []

    class Finished(Exception):
        pass

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            clock.advance(1800, monotonic=monotonic_runs_during_suspend)
        elif len(sleeps) == 2:
            assert watchdog.remaining() >= 35
            watchdog.tick()
            clock.advance(1)
        else:
            assert watchdog.remaining() == threaded.HANDLER_LIVENESS_SECONDS - 1
            raise Finished

    monkeypatch.setattr(threaded.asyncio, "sleep", sleep)

    async def scenario():
        with pytest.raises(Finished):
            await watchdog.watch()

    asyncio.run(scenario())


def test_heartbeat_after_reclaim_snapshot_still_wins_the_existing_cas(tmp_path, monkeypatch):
    clock = Clock()
    clock.install(monkeypatch)

    async def scenario():
        engine, maker = await _database(tmp_path / "cas.db")
        try:
            ids, registry = await running_operations(maker, clock, expires_in=-1)
            original = runner_module._public_admission_for_operation

            async def renew_before_recovery_lock(session, operation_id):
                assert await lease.heartbeat_workspace_operation(
                    operation_id, "attempt", fencing_token=1, session_factory=maker,
                )
                return await original(session, operation_id)

            monkeypatch.setattr(runner_module, "_public_admission_for_operation", renew_before_recovery_lock)
            assert await runner_module.recover_expired_workspace_operations(
                session_factory=maker, registry=registry,
            ) == 0
            assert (await _operation(maker, ids[0])).state == "running"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_default_runner_renews_beyond_lease_while_handler_loop_is_blocked(tmp_path, monkeypatch):
    clock = Clock()
    clock.install(monkeypatch)
    renewals = []
    unblocked = threading.Event()
    original = lease.heartbeat_workspace_operation
    handler_thread = threading.get_ident()
    heartbeat_threads = []
    heartbeat_engines = []

    async def scenario():
        engine, maker = await _database(tmp_path / "blocked.db")
        try:
            _, ids = await _seed_operations(maker, owner_id=OWNER_ID)

            async def renew(*args, **kwargs):
                clock.advance(1)
                assert threading.get_ident() != handler_thread
                assert get_current_owner_id() == OWNER_ID
                heartbeat_threads.append(threading.current_thread())
                own_engine = kwargs["session_factory"].kw["bind"]
                heartbeat_engines.append(own_engine)
                assert own_engine is not engine
                alive = await original(*args, **kwargs)
                renewals.append(alive)
                operation = await _operation(kwargs["session_factory"], ids[0])
                assert as_utc(operation.lease_expires_at) > clock.wall
                if len(renewals) >= 6:
                    unblocked.set()
                return alive

            monkeypatch.setattr(lease, "heartbeat_workspace_operation", renew)

            async def handler(context):
                # Deliberately block the handler's event loop, while fake wall
                # time advances three times past its original two-second lease.
                assert unblocked.wait(5), "independent heartbeat never renewed"
                return WorkspaceOperationResult()

            registry = WorkspaceOperationHandlerRegistry()
            registry.register("test.operation", handler)
            runner = runner_module.WorkspaceOperationRunner(
                session_factory=maker, registry=registry, lease_seconds=2,
                heartbeat_seconds=0.05, poll_seconds=0.05,
            )
            assert await _drain_once(runner) == 1
            assert (await _operation(maker, ids[0])).state == "succeeded"
            assert len(renewals) >= 6 and all(renewals)
            assert all(not thread.is_alive() for thread in heartbeat_threads)
            assert all(own.pool.checkedout() == 0 for own in heartbeat_engines)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_watchdog_stops_renewing_a_hung_handler_and_disposes_thread(tmp_path, monkeypatch):
    clock = Clock()
    clock.install(monkeypatch)
    monkeypatch.setattr(threaded, "monotonic", lambda: clock.tick)
    monkeypatch.setattr(threaded, "time", lambda: clock.wall.timestamp())
    monkeypatch.setattr(threaded, "HANDLER_LIVENESS_SECONDS", 3)
    monkeypatch.setattr(threaded, "HANDLER_TICK_SECONDS", 0.01)
    stopped = threading.Event()
    renewals = []
    threads = []
    original = lease.heartbeat_workspace_operation
    original_watch = threaded.HandlerLoopWatchdog.watch

    async def watch(self):
        try:
            await original_watch(self)
        finally:
            stopped.set()

    async def renew(*args, **kwargs):
        threads.append(threading.current_thread())
        result = await original(*args, **kwargs)
        renewals.append(result)
        clock.advance(1)
        return result

    monkeypatch.setattr(threaded.HandlerLoopWatchdog, "watch", watch)
    monkeypatch.setattr(lease, "heartbeat_workspace_operation", renew)

    async def scenario():
        engine, maker = await _database(tmp_path / "watchdog.db")
        try:
            _, ids = await _seed_operations(maker)

            async def handler(context):
                assert stopped.wait(5), "watchdog kept renewing the hung handler"
                await asyncio.sleep(1)
                return WorkspaceOperationResult()

            registry = WorkspaceOperationHandlerRegistry()
            registry.register("test.operation", handler)
            runner = runner_module.WorkspaceOperationRunner(
                session_factory=maker, registry=registry, lease_seconds=2,
                heartbeat_seconds=0.05, poll_seconds=0.05,
            )
            assert await _drain_once(runner) == 1
            assert renewals == [True] * 3
            assert (await _operation(maker, ids[0])).state == "running"
            assert all(not thread.is_alive() for thread in threads)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("field,value", [("attempt_token", "stale"), ("fencing_token", 999)])
def test_threaded_heartbeat_rejects_stale_authority(tmp_path, field, value):
    async def scenario():
        engine, maker = await _database(tmp_path / "stale.db")
        try:
            _, ids = await _seed_operations(maker)
            claim = await runner_module.claim_next_workspace_operation(session_factory=maker)
            before = await _operation(maker, ids[0])
            with pytest.raises(WorkspaceOperationCancelled):
                await threaded.run_threaded_attempt_heartbeat(
                    replace(claim, **{field: value}), session_factory=maker,
                    heartbeat_seconds=0.05, poll_seconds=0.05, lease_seconds=lambda: 300,
                )
            after = await _operation(maker, ids[0])
            assert after.lease_expires_at == before.lease_expires_at
            assert after.fencing_token == before.fencing_token
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cancellation_during_thread_cleanup_still_disposes_its_engine(tmp_path, monkeypatch):
    disposing = threading.Event()
    disposed = threading.Event()
    threads = []
    original_factory = threaded._heartbeat_session_factory

    def factory(source):
        engine, maker = original_factory(source)
        original_dispose = engine.dispose

        async def dispose():
            threads.append(threading.current_thread())
            disposing.set()
            await asyncio.sleep(0.1)
            await original_dispose()
            disposed.set()

        # AsyncEngine uses slots; patch its class, filtering to this engine.
        async def dispose_engine(self, *args, **kwargs):
            if self is engine:
                await dispose()
            else:
                await original_type_dispose(self, *args, **kwargs)

        original_type_dispose = type(engine).dispose
        monkeypatch.setattr(type(engine), "dispose", dispose_engine)
        return engine, maker

    monkeypatch.setattr(threaded, "_heartbeat_session_factory", factory)

    async def scenario():
        engine, maker = await _database(tmp_path / "shutdown.db")
        try:
            await _seed_operations(maker)
            claim = await runner_module.claim_next_workspace_operation(session_factory=maker)
            heartbeat = asyncio.create_task(threaded.run_threaded_attempt_heartbeat(
                replace(claim, attempt_token="stale"), session_factory=maker,
                heartbeat_seconds=0.05, poll_seconds=0.05, lease_seconds=lambda: 300,
            ))
            assert await asyncio.to_thread(disposing.wait, 5)
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            assert disposed.is_set()
            assert all(not thread.is_alive() for thread in threads)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
