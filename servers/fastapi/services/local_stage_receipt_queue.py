"""Batched, non-blocking persistence for local stage-timing receipts.

A local stage receipt records how long a CPU-only stage took.  It carries no
provider usage and no cost, nothing downstream waits on it, and losing one
costs a row in a timing report.  Writing each of them in its own transaction
made the generation lane pay ~5 database round trips -- and one commit against
the single SQLite writer lock -- for a measurement.

So they go through a bounded in-process queue instead: producers hand over a
validated receipt and return immediately, and one background task per event
loop drains the queue in batches, writing up to ``_MAX_BATCH`` rows per
transaction.  The queue is bounded because unbounded telemetry buffers turn a
slow database into an out-of-memory crash; on overflow the receipt is dropped
and counted, with at most one warning per minute.

Provider receipts never come through here: their write is part of the cost
record and stays synchronous.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

if TYPE_CHECKING:  # pragma: no cover - typing only
    from services.provider_usage_receipt_service import ProviderUsageReceiptInput

LOGGER = logging.getLogger(__name__)

_MAX_QUEUE = 256
_MAX_BATCH = 32
_FLUSH_INTERVAL_SECONDS = 0.25
_OVERFLOW_WARNING_INTERVAL_SECONDS = 60.0
_SHUTDOWN_DRAIN_SECONDS = 5.0

_OBSERVABILITY = {
    "enqueued": 0,
    "dropped": 0,
    "persisted": 0,
    "failed": 0,
    "batches": 0,
}


class _LoopQueue:
    """One queue and one drain task, bound to a single event loop."""

    __slots__ = ("queue", "task", "last_overflow_warning", "stopping")

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self.task: asyncio.Task[None] | None = None
        self.last_overflow_warning: float = 0.0
        self.stopping: bool = False


_QUEUES: WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopQueue] = (
    WeakKeyDictionary()
)


def local_stage_queue_observability_snapshot() -> dict[str, int]:
    """Low-cardinality, content-free counters for the local stage sink."""

    return dict(_OBSERVABILITY)


def _loop_queue() -> _LoopQueue | None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    state = _QUEUES.get(loop)
    if state is None:
        state = _LoopQueue()
        _QUEUES[loop] = state
    if state.task is None or state.task.done():
        state.stopping = False
        state.task = loop.create_task(
            _drain_forever(state), name="local-stage-receipt-drain"
        )
    return state


def enqueue_local_stage_receipt(receipt: "ProviderUsageReceiptInput") -> bool:
    """Hand one receipt to the batch writer; ``False`` means it was dropped."""

    state = _loop_queue()
    if state is None:
        return False
    try:
        state.queue.put_nowait(receipt)
    except asyncio.QueueFull:
        _OBSERVABILITY["dropped"] += 1
        now = time.monotonic()
        if now - state.last_overflow_warning >= _OVERFLOW_WARNING_INTERVAL_SECONDS:
            state.last_overflow_warning = now
            LOGGER.warning(
                "Local stage receipt queue full; dropped %d receipt(s) so far",
                _OBSERVABILITY["dropped"],
            )
        return False
    _OBSERVABILITY["enqueued"] += 1
    return True


def _take_batch(state: _LoopQueue) -> list["ProviderUsageReceiptInput"]:
    batch: list["ProviderUsageReceiptInput"] = []
    while len(batch) < _MAX_BATCH:
        try:
            batch.append(state.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return batch


async def _write_batch(batch: list["ProviderUsageReceiptInput"]) -> None:
    """Persist one batch in a single transaction; never raise to the caller."""

    if not batch:
        return
    try:
        from services.database import async_session_maker
        from services.provider_usage_receipt_service import (
            record_provider_usage_receipt,
        )

        async with async_session_maker() as session:
            written = 0
            for receipt in batch:
                try:
                    await record_provider_usage_receipt(session, receipt)
                    written += 1
                except Exception:
                    # One malformed or conflicting receipt must not discard the
                    # rest of the batch; it is dropped and counted.
                    _OBSERVABILITY["failed"] += 1
            await session.commit()
            _OBSERVABILITY["persisted"] += written
            _OBSERVABILITY["batches"] += 1
    except Exception:
        # Do not log the exception: provider transports can attach response
        # content to database errors raised through their own drivers.
        _OBSERVABILITY["failed"] += len(batch)


async def _drain_forever(state: _LoopQueue) -> None:
    while True:
        if state.stopping and state.queue.empty():
            return
        try:
            receipt = await asyncio.wait_for(
                state.queue.get(), timeout=_FLUSH_INTERVAL_SECONDS
            )
        except (TimeoutError, asyncio.TimeoutError):
            continue
        batch = [receipt]
        deadline = time.monotonic() + _FLUSH_INTERVAL_SECONDS
        while len(batch) < _MAX_BATCH:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                batch.append(
                    await asyncio.wait_for(state.queue.get(), timeout=remaining)
                )
            except (TimeoutError, asyncio.TimeoutError):
                break
        await _write_batch(batch)


async def flush_local_stage_queue(*, stop: bool = True) -> int:
    """Drain everything queued on this loop and return the row count written.

    Registered by the application lifespan on shutdown so a process exit does
    not silently discard the last quarter second of stage timings.  The drain
    task is asked to finish rather than cancelled, so a batch already inside a
    transaction commits.  ``stop=False`` empties the queue and keeps the drain
    task alive, for tests and measurement.
    """

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0
    state = _QUEUES.get(loop)
    if state is None:
        return 0
    task = state.task
    if task is not None and not task.done():
        state.stopping = True
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=_SHUTDOWN_DRAIN_SECONDS
            )
        except (TimeoutError, asyncio.TimeoutError):
            task.cancel()
        except Exception:
            pass
    if stop:
        state.task = None
    written = 0
    while True:
        batch = _take_batch(state)
        if not batch:
            break
        await _write_batch(batch)
        written += len(batch)
    if not stop:
        state.stopping = False
        _loop_queue()
    return written


__all__ = [
    "enqueue_local_stage_receipt",
    "flush_local_stage_queue",
    "local_stage_queue_observability_snapshot",
]
