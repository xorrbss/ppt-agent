"""Bounded producer/worker pool for the per-slide authored pipeline stages.

The producer hands slide indexes to a fixed set of workers through a queue that
holds at most two worker windows, so at most that many slides are ever claimed
ahead of the workers.  A worker failure stops every other worker from claiming
more billable slides and surfaces the first failure to the caller.

The pool must also *finish* on failure.  If every worker dies while the queue is
still full, nothing drains it, so the producer's shutdown sentinels can never be
enqueued with a blocking put.  A 35-slide deck whose first eight slides failed
sat in exactly that state for 22 minutes until the admission deadline killed
the operation, reporting ``public_deadline_exceeded`` instead of the real slide
error.  Shutdown therefore never waits on queue capacity once workers stop.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


WORKER_EXIT_MESSAGE = "authored slide worker exited before completion"


async def run_slide_worker_pool(
    process_slide: Callable[[int], Awaitable[Any]],
    *,
    slide_count: int,
    worker_count: int,
) -> list[Any]:
    """Process every slide index once and return results in slide order.

    Raises the first worker failure after the remaining workers have drained,
    or ``RuntimeError`` if a worker exited without producing every slide.
    """

    if slide_count <= 0:
        return []
    worker_count = max(1, min(worker_count, slide_count))
    results: list[Any] = [None] * slide_count
    # The producer can be at most two worker windows ahead. This is real
    # backpressure rather than an unbounded queue prefilled with every slide.
    queue: asyncio.Queue[int | object] = asyncio.Queue(
        maxsize=max(1, worker_count * 2)
    )
    sentinel = object()
    stop_workers = asyncio.Event()

    def _drain_queue() -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            queue.task_done()

    async def producer() -> None:
        try:
            for index in range(slide_count):
                if stop_workers.is_set():
                    break
                await queue.put(index)
        finally:
            if stop_workers.is_set():
                # Workers are draining or already gone, so nothing guarantees
                # the queue ever has room again. Drop the unclaimed indexes
                # (no worker may claim them anyway) so every sentinel fits
                # without waiting: ``worker_count <= maxsize`` by construction.
                _drain_queue()
                for _ in range(worker_count):
                    queue.put_nowait(sentinel)
            else:
                for _ in range(worker_count):
                    await queue.put(sentinel)

    async def worker() -> None:
        while True:
            item = await queue.get()
            try:
                if item is sentinel:
                    return
                if stop_workers.is_set():
                    continue
                index = int(item)
                try:
                    results[index] = await process_slide(index)
                except BaseException:
                    # Already-returned provider work is checkpointed by the
                    # process_slide boundary. Other workers drain without
                    # claiming more billable slides.
                    stop_workers.set()
                    raise
            finally:
                queue.task_done()

    producer_task = asyncio.create_task(producer())
    worker_tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
    try:
        worker_outcomes = await asyncio.gather(
            *worker_tasks, return_exceptions=True
        )
    except asyncio.CancelledError:
        # User/server cancellation still interrupts the deck attempt. Any
        # provider result that already returned is checkpointed by the
        # process_slide boundary before the worker can finish cancellation.
        producer_task.cancel()
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(producer_task, *worker_tasks, return_exceptions=True)
        raise
    failures = [
        outcome
        for outcome in worker_outcomes
        if isinstance(outcome, BaseException)
    ]
    if failures:
        stop_workers.set()
        # No worker is left to consume; free the queue before waiting on the
        # producer so its shutdown can never block on a full queue.
        _drain_queue()
        producer_task.cancel()
        await asyncio.gather(producer_task, return_exceptions=True)
        raise failures[0]
    await producer_task
    completed_results = [result for result in results if result is not None]
    if len(completed_results) != slide_count:
        raise RuntimeError(WORKER_EXIT_MESSAGE)
    return completed_results


__all__ = ["WORKER_EXIT_MESSAGE", "run_slide_worker_pool"]
