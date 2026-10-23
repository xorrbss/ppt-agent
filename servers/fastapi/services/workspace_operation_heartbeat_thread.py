"""Attempt heartbeat with database connections owned by a separate event loop."""

from __future__ import annotations

import asyncio
from contextvars import copy_context
import logging
import threading
from time import monotonic, time
from collections.abc import Callable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from constants.timeouts import BUSY_WAIT_POLL_INTERVAL_SECONDS
from services.database import (
    connect_args as database_connect_args,
    sql_engine,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
)
from services.workspace_operation_lease import (
    WORKSPACE_OPERATION_HEARTBEAT_SECONDS,
    run_attempt_heartbeat_loop,
)

LOGGER = logging.getLogger(__name__)
HANDLER_LIVENESS_SECONDS = 900.0
HANDLER_TICK_SECONDS = 1.0
HEARTBEAT_SHUTDOWN_SECONDS = 35.0


class HandlerLoopWatchdog:
    """Only the handler loop may refresh this thread-safe liveness signal."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_tick = monotonic()

    def tick(self) -> None:
        with self._lock:
            self._last_tick = monotonic()

    def remaining(self) -> float:
        with self._lock:
            return HANDLER_LIVENESS_SECONDS - (monotonic() - self._last_tick)

    async def watch(self) -> None:
        previous_tick, previous_wall = monotonic(), time()
        resume_window = WORKSPACE_OPERATION_HEARTBEAT_SECONDS + 5.0
        while True:
            tick, wall = monotonic(), time()
            if max(tick - previous_tick, wall - previous_wall) > resume_window:
                # This observer also stops during suspend. Give the handler one
                # heartbeat window to post its first tick after both resume.
                with self._lock:
                    self._last_tick = max(
                        self._last_tick, tick - HANDLER_LIVENESS_SECONDS + resume_window
                    )
            previous_tick, previous_wall = tick, wall
            # Missing ticks suspend renewal, not cancellation polling. A later
            # handler tick lets the renewal CAS decide whether we still own it.
            await asyncio.sleep(HANDLER_TICK_SECONDS)


def _heartbeat_session_factory(
    source: async_sessionmaker[AsyncSession],
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Construct on the heartbeat thread; never borrow the handler's pool."""

    bound_engine = source.kw.get("bind")
    if not isinstance(bound_engine, AsyncEngine):
        raise TypeError("workspace_operation_heartbeat_engine_required")
    url = bound_engine.url
    if url.get_backend_name() == "sqlite" and url.database in (None, "", ":memory:"):
        # Anonymous in-memory SQLite databases belong to one connection. Use a
        # file or a named shared-memory URI for independently owned connections.
        raise ValueError("workspace_operation_heartbeat_shared_database_required")
    kwargs = {"connect_args": dict(database_connect_args) if bound_engine is sql_engine else {}}
    if url.get_backend_name() != "sqlite":
        kwargs.update(pool_size=1, max_overflow=0, pool_pre_ping=True)
    engine = create_async_engine(url, **kwargs)
    if url.get_backend_name() == "sqlite":
        @event.listens_for(engine.sync_engine, "connect")
        def configure_sqlite(connection, _record):
            # Journal mode belongs to database startup. Changing it here can
            # contend with the handler's existing connections/transactions.
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
    options = {**source.kw, "bind": engine}
    return engine, async_sessionmaker(class_=source.class_, **options)


async def run_threaded_attempt_heartbeat(
    claim: WorkspaceOperationClaim,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    heartbeat_seconds: float,
    poll_seconds: float,
    lease_seconds: Callable[[], int],
) -> None:
    """Bridge thread completion to the runner, including cancel/fence failures.

    Shutdown cancels the heartbeat on its own loop, disposes its engine there,
    and observes actual thread exit without borrowing the handler's executor.
    A daemon thread is a final safeguard against an unresponsive database driver.
    """

    handler_loop = asyncio.get_running_loop()
    completed = handler_loop.create_future()
    watchdog = HandlerLoopWatchdog()
    stopping = threading.Event()
    state_lock = threading.Lock()
    worker_loop: asyncio.AbstractEventLoop | None = None
    worker_task: asyncio.Task | None = None
    tick_handle: asyncio.TimerHandle | None = None

    def tick() -> None:
        nonlocal tick_handle
        watchdog.tick()
        tick_handle = handler_loop.call_later(HANDLER_TICK_SECONDS, tick)

    def finish(error: BaseException | None) -> None:
        if completed.done():
            return
        if error is None:
            completed.set_result(None)
        else:
            completed.set_exception(error)

    async def serve() -> None:
        nonlocal worker_loop, worker_task
        with state_lock:
            worker_loop = asyncio.get_running_loop()
            worker_task = asyncio.current_task()
        if stopping.is_set():
            return
        engine, maker = _heartbeat_session_factory(session_factory)
        tasks: list[asyncio.Task] = []
        try:
            tasks = [
                asyncio.create_task(run_attempt_heartbeat_loop(
                    claim,
                    session_factory=maker,
                    heartbeat_seconds=heartbeat_seconds,
                    poll_seconds=poll_seconds,
                    lease_seconds=lease_seconds,
                    renewal_allowed=lambda: watchdog.remaining() > 0,
                )),
                asyncio.create_task(watchdog.watch()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()

            async def close() -> None:
                try:
                    await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    await engine.dispose()

            cleanup = asyncio.create_task(close())
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                # The runner can finish concurrently with a fence/watchdog
                # failure. Its stop signal must not interrupt engine disposal.
                await cleanup
                raise

    def run() -> None:
        error = None
        try:
            asyncio.run(serve())
        except BaseException as caught:
            error = caught
        try:
            handler_loop.call_soon_threadsafe(finish, error)
        except RuntimeError:
            # A timed-out driver may finish after the application's loop closed.
            pass

    context = copy_context()
    thread = threading.Thread(
        target=context.run,
        args=(run,),
        name=f"workspace-heartbeat-{claim.operation_id}",
        daemon=True,
    )
    tick()
    thread.start()

    async def wait_for_thread_exit() -> None:
        # A completion callback can run before the thread that posted it exits.
        exited = handler_loop.create_future()
        poll_handle: asyncio.TimerHandle | None = None

        def check_exit() -> None:
            nonlocal poll_handle
            if thread.is_alive():
                poll_handle = handler_loop.call_later(BUSY_WAIT_POLL_INTERVAL_SECONDS, check_exit)
            else:
                exited.set_result(None)

        check_exit()
        try:
            await exited
        finally:
            if poll_handle is not None:
                poll_handle.cancel()

    try:
        await completed
    finally:
        if tick_handle is not None:
            tick_handle.cancel()
        stopping.set()
        with state_lock:
            if worker_loop is not None and worker_task is not None:
                try:
                    worker_loop.call_soon_threadsafe(worker_task.cancel)
                except RuntimeError:
                    pass
        cleanup = asyncio.create_task(asyncio.wait_for(
            wait_for_thread_exit(), HEARTBEAT_SHUTDOWN_SECONDS,
        ))
        cancelled = False
        try:
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
        except TimeoutError:
            LOGGER.error("Heartbeat shutdown timed out for operation %s", claim.operation_id)
        if cancelled:
            raise asyncio.CancelledError
