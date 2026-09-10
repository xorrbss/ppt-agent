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
    index_source: Callable[[], Awaitable[int | None]] | None = None,
) -> list[Any]:
    """Process every slide index once and return results in slide order.

    Raises the first worker failure after the remaining workers have been
    cancelled and reaped, or ``RuntimeError`` if a worker exited without
    producing every slide.

    ``index_source`` streams the indexes instead of producing ``range()``: it
    is awaited for the next slide whose authoring has landed and returns
    ``None`` at the end of the deck (see ``services.authored_tail_streaming``).
    Results stay in slide order either way, because a worker stores its result
    at the index it was handed.
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

    async def produce_indexes() -> None:
        if index_source is None:
            for index in range(slide_count):
                if stop_workers.is_set():
                    break
                await queue.put(index)
            return
        while not stop_workers.is_set():
            # The pool cancels and reaps this producer on any worker failure;
            # keep the pull in this task so its cleanup is awaited as well.
            index = await index_source()
            if index is None:
                break
            await queue.put(int(index))

    async def producer() -> None:
        try:
            await produce_indexes()
        except BaseException:
            stop_workers.set()
            raise
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
                    # process_slide boundary. The pool cancels and reaps every
                    # sibling before surfacing this failure.
                    stop_workers.set()
                    raise
            finally:
                queue.task_done()

    producer_task = asyncio.create_task(producer())
    worker_tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
    try:
        pending = {producer_task, *worker_tasks}
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            # Watch the producer too, including cancellation: FIRST_EXCEPTION
            # does not wake for a cancelled task and can strand its siblings.
            for task in done:
                task.result()
    finally:
        stop_workers.set()
        # Stop both active and queued provider work before returning any error,
        # and let cancellation cleanup release admission and DB resources.
        for task in (producer_task, *worker_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(producer_task, *worker_tasks, return_exceptions=True)
        _drain_queue()
    completed_results = [result for result in results if result is not None]
    if len(completed_results) != slide_count:
        raise RuntimeError(WORKER_EXIT_MESSAGE)
    return completed_results


__all__ = ["WORKER_EXIT_MESSAGE", "run_slide_worker_pool"]
