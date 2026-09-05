"""At-least-once dispatcher for workspace transactional outbox rows."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
import inspect
import logging
import os
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.database import async_session_maker
from services.workspace_chat_memory_outbox import (
    WORKSPACE_CHAT_MEMORY_TOPIC,
    deliver_workspace_chat_memory,
)
from services.workspace_outbox_claim_store import (
    WORKSPACE_OUTBOX_LEASE_SECONDS,
    WORKSPACE_OUTBOX_MAX_ATTEMPTS,
    WorkspaceOutboxClaim,
    claim_next_workspace_outbox,
    heartbeat_workspace_outbox_claim,
    mark_workspace_outbox_delivered,
    mark_workspace_outbox_failed,
    record_workspace_outbox_outcome,
)
from services.workspace_poll_backoff import IdleCycle
from services.workspace_worker_lane_probe import workspace_outbox_work_pending
from services.workspace_operation_runner import wake_workspace_operation_runner


logger = logging.getLogger(__name__)

WORKSPACE_OUTBOX_HEARTBEAT_SECONDS = 15
WORKSPACE_OUTBOX_POLL_SECONDS = 1.0
WORKSPACE_OUTBOX_MAX_CONCURRENCY = 4


class WorkspaceOutboxDeliveryError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = True) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


class _WorkspaceOutboxLeaseLost(WorkspaceOutboxDeliveryError):
    def __init__(self) -> None:
        super().__init__("workspace_outbox_lease_lost", retryable=True)


WorkspaceOutboxTopicHandler = Callable[
    [Mapping[str, Any]], Awaitable[None] | None
]


class WorkspaceOutboxTopicRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, WorkspaceOutboxTopicHandler] = {}

    def register(
        self,
        topic: str,
        handler: WorkspaceOutboxTopicHandler,
        *,
        replace: bool = False,
    ) -> None:
        normalized = topic.strip()
        if not normalized:
            raise ValueError("workspace outbox topic must not be empty")
        if normalized in self._handlers and not replace:
            raise ValueError(f"outbox handler already registered for {normalized}")
        self._handlers[normalized] = handler

    def resolve(self, topic: str) -> WorkspaceOutboxTopicHandler | None:
        return self._handlers.get(topic)


workspace_outbox_topic_registry = WorkspaceOutboxTopicRegistry()


def register_workspace_outbox_topic_handler(
    topic: str,
    handler: WorkspaceOutboxTopicHandler,
    *,
    replace: bool = False,
) -> None:
    workspace_outbox_topic_registry.register(topic, handler, replace=replace)


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _positive_float_env(name: str, default: float) -> float:
    try:
        return max(0.05, float(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _wake_operation_topic(_payload: Mapping[str, Any]) -> None:
    wake_workspace_operation_runner()


workspace_outbox_topic_registry.register(
    "workspace.operation.event", _wake_operation_topic
)
workspace_outbox_topic_registry.register(
    "workspace.operation.ready", _wake_operation_topic
)
workspace_outbox_topic_registry.register(
    WORKSPACE_CHAT_MEMORY_TOPIC, deliver_workspace_chat_memory
)


class WorkspaceOperationOutboxDispatcher:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] = async_session_maker,
        registry: WorkspaceOutboxTopicRegistry = workspace_outbox_topic_registry,
        max_concurrency: int | None = None,
        max_attempts: int | None = None,
        lease_seconds: int | None = None,
        heartbeat_seconds: int | None = None,
        poll_seconds: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.registry = registry
        self.max_concurrency = max_concurrency or _positive_int_env(
            "WORKSPACE_OUTBOX_MAX_CONCURRENCY", WORKSPACE_OUTBOX_MAX_CONCURRENCY
        )
        self.max_attempts = max_attempts or _positive_int_env(
            "WORKSPACE_OUTBOX_MAX_ATTEMPTS", WORKSPACE_OUTBOX_MAX_ATTEMPTS
        )
        self.lease_seconds = lease_seconds or _positive_int_env(
            "WORKSPACE_OUTBOX_LEASE_SECONDS", WORKSPACE_OUTBOX_LEASE_SECONDS
        )
        self.heartbeat_seconds = heartbeat_seconds or _positive_int_env(
            "WORKSPACE_OUTBOX_HEARTBEAT_SECONDS",
            WORKSPACE_OUTBOX_HEARTBEAT_SECONDS,
        )
        self.poll_seconds = poll_seconds or _positive_float_env(
            "WORKSPACE_OUTBOX_POLL_SECONDS", WORKSPACE_OUTBOX_POLL_SECONDS
        )
        self._loop_task: asyncio.Task[None] | None = None
        self._active: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._wake = asyncio.Event()

    async def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(
            self._run_loop(), name="workspace-operation-outbox"
        )

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
            self._loop_task = None
        tasks = tuple(self._active.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._active.clear()

    def wake(self) -> None:
        self._wake.set()

    async def dispatch_once(self) -> int:
        dispatched = 0
        while len(self._active) < self.max_concurrency:
            claim = await claim_next_workspace_outbox(
                session_factory=self.session_factory,
                lease_seconds=self.lease_seconds,
            )
            if claim is None:
                break
            task = asyncio.create_task(
                self._deliver(claim), name=f"workspace-outbox-{claim.outbox_id}"
            )
            self._active[claim.outbox_id] = task

            def _discard(
                done: asyncio.Task[None], outbox_id: uuid.UUID = claim.outbox_id
            ) -> None:
                if self._active.get(outbox_id) is done:
                    self._active.pop(outbox_id, None)
                self._wake.set()

            task.add_done_callback(_discard)
            dispatched += 1
        return dispatched

    async def _heartbeat(
        self,
        claim: WorkspaceOutboxClaim,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=self.heartbeat_seconds
                )
            except TimeoutError:
                pass
            if stop.is_set():
                return
            try:
                refreshed = await heartbeat_workspace_outbox_claim(
                    claim,
                    session_factory=self.session_factory,
                    lease_seconds=self.lease_seconds,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                # A heartbeat failure makes ownership uncertain.  Fence the
                # handler instead of allowing side effects under a stale lease.
                raise _WorkspaceOutboxLeaseLost() from error
            if not refreshed:
                raise _WorkspaceOutboxLeaseLost()

    @staticmethod
    async def _invoke_handler(
        handler: WorkspaceOutboxTopicHandler,
        payload: Mapping[str, Any],
    ) -> None:
        delivered = handler(payload)
        if inspect.isawaitable(delivered):
            await delivered

    async def _run_handler_while_owned(
        self,
        claim: WorkspaceOutboxClaim,
        handler: WorkspaceOutboxTopicHandler,
    ) -> None:
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(claim, stop_heartbeat),
            name=f"workspace-outbox-heartbeat-{claim.outbox_id}",
        )
        delivery = asyncio.create_task(
            self._invoke_handler(handler, claim.payload),
            name=f"workspace-outbox-handler-{claim.outbox_id}",
        )
        handler_error: BaseException | None = None
        try:
            done, _pending = await asyncio.wait(
                (delivery, heartbeat),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if heartbeat in done:
                try:
                    heartbeat.result()
                except _WorkspaceOutboxLeaseLost:
                    raise
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    raise _WorkspaceOutboxLeaseLost() from error
                # The heartbeat loop may only finish normally after the stop
                # signal, which is not set until delivery has completed.
                raise _WorkspaceOutboxLeaseLost()

            try:
                delivery.result()
            except BaseException as error:
                handler_error = error

            # Stop gracefully instead of cancelling a heartbeat mid-commit.
            # This ensures the claim's refreshed lease value is visible before
            # the delivered/failed compare-and-swap runs.
            stop_heartbeat.set()
            try:
                await heartbeat
            except _WorkspaceOutboxLeaseLost:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise _WorkspaceOutboxLeaseLost() from error

            if handler_error is not None:
                raise handler_error
        finally:
            stop_heartbeat.set()
            for task in (delivery, heartbeat):
                if not task.done():
                    task.cancel()
            await asyncio.gather(delivery, heartbeat, return_exceptions=True)

    async def _deliver(self, claim: WorkspaceOutboxClaim) -> None:
        failure: WorkspaceOutboxDeliveryError | None = None
        try:
            handler = self.registry.resolve(claim.topic)
            if handler is None:
                raise WorkspaceOutboxDeliveryError(
                    "unsupported_outbox_topic", retryable=False
                )
            await self._run_handler_while_owned(claim, handler)
        except _WorkspaceOutboxLeaseLost:
            record_workspace_outbox_outcome("lease_lost")
            logger.warning(
                "Workspace outbox lease lost; delivery fenced "
                "outbox_id=%s topic=%s",
                claim.outbox_id,
                claim.topic,
            )
            return
        except WorkspaceOutboxDeliveryError as error:
            failure = error
        except asyncio.CancelledError:
            # Leave the delivering row leased.  Another process reclaims it
            # after expiry, preserving at-least-once shutdown semantics.
            raise
        except Exception:
            logger.exception(
                "Workspace outbox delivery failed outbox_id=%s topic=%s",
                claim.outbox_id,
                claim.topic,
            )
            failure = WorkspaceOutboxDeliveryError(
                "workspace_outbox_delivery_failed", retryable=True
            )

        if failure is None:
            updated = await mark_workspace_outbox_delivered(
                claim, session_factory=self.session_factory
            )
        else:
            updated = await mark_workspace_outbox_failed(
                claim,
                code=failure.code,
                retryable=failure.retryable,
                max_attempts=self.max_attempts,
                session_factory=self.session_factory,
            )
        if not updated:
            record_workspace_outbox_outcome("lease_lost")
            logger.warning(
                "Workspace outbox finalization lost its lease; delivery fenced "
                "outbox_id=%s topic=%s",
                claim.outbox_id,
                claim.topic,
            )

    async def _wait_for_wake(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=self.poll_seconds)
        except TimeoutError:
            pass
        self._wake.clear()

    async def _run_loop(self) -> None:
        # An in-process enqueue wakes this loop directly, so the idle interval
        # only bounds how long a row written by another process waits.  The
        # read-only probe keeps that bound at one poll without paying for
        # ``BEGIN IMMEDIATE`` on every idle tick.
        cycle = IdleCycle(self.poll_seconds)
        while True:
            try:
                if cycle.due() or await workspace_outbox_work_pending(
                    session_factory=self.session_factory
                ):
                    cycle.record(await self.dispatch_once())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Workspace outbox dispatcher iteration failed")
            await self._wait_for_wake()


_workspace_outbox_dispatcher: WorkspaceOperationOutboxDispatcher | None = None


async def start_workspace_operation_outbox_dispatcher(
) -> WorkspaceOperationOutboxDispatcher:
    global _workspace_outbox_dispatcher
    if _workspace_outbox_dispatcher is None:
        _workspace_outbox_dispatcher = WorkspaceOperationOutboxDispatcher()
    await _workspace_outbox_dispatcher.start()
    return _workspace_outbox_dispatcher


async def stop_workspace_operation_outbox_dispatcher() -> None:
    global _workspace_outbox_dispatcher
    if _workspace_outbox_dispatcher is None:
        return
    await _workspace_outbox_dispatcher.stop()
    _workspace_outbox_dispatcher = None


def wake_workspace_operation_outbox_dispatcher() -> None:
    if _workspace_outbox_dispatcher is not None:
        _workspace_outbox_dispatcher.wake()
