import asyncio

import pytest

from services.authored_slide_worker_pool import (
    WORKER_EXIT_MESSAGE,
    run_slide_worker_pool,
)


class _SlideFailure(RuntimeError):
    pass


def _run(coro, *, timeout: float = 5.0):
    """Fail the test instead of hanging when the pool never finishes."""

    async def bounded():
        return await asyncio.wait_for(coro, timeout=timeout)

    return asyncio.run(bounded())


def test_every_slide_is_processed_once_in_order():
    seen: list[int] = []

    async def process_slide(index: int) -> tuple[int, str]:
        seen.append(index)
        await asyncio.sleep(0)
        return (index, f"slide-{index}")

    results = _run(run_slide_worker_pool(process_slide, slide_count=35, worker_count=8))

    assert results == [(index, f"slide-{index}") for index in range(35)]
    assert sorted(seen) == list(range(35))


def test_all_workers_failing_on_a_full_queue_finishes_immediately():
    """Regression: a 35-slide deck whose first eight slides failed sat until
    the admission deadline because the producer's shutdown sentinels could not
    be enqueued behind the unclaimed slides once every worker had died."""

    claimed: list[int] = []
    release = asyncio.Event()

    async def process_slide(index: int):
        claimed.append(index)
        # Hold every worker until the producer has filled the queue, so the
        # failures land on a full queue exactly as in production.
        await release.wait()
        raise _SlideFailure(f"slide {index}")

    async def scenario():
        pool = asyncio.create_task(
            run_slide_worker_pool(process_slide, slide_count=35, worker_count=8)
        )
        # Producer window: 8 claimed by workers + 16 queued (2 windows) = 24.
        for _ in range(50):
            await asyncio.sleep(0)
        release.set()
        with pytest.raises(_SlideFailure):
            await pool

    _run(scenario(), timeout=5.0)
    # Only the first worker window was ever claimed; no slide past it was
    # handed to a worker after the failures.
    assert sorted(claimed) == list(range(8))


def test_one_failure_stops_further_claims_and_surfaces_the_error():
    claimed: list[int] = []

    async def process_slide(index: int):
        claimed.append(index)
        await asyncio.sleep(0)
        if index == 3:
            raise _SlideFailure("slide 3")
        return index

    with pytest.raises(_SlideFailure):
        _run(run_slide_worker_pool(process_slide, slide_count=40, worker_count=4))

    assert 3 in claimed
    assert len(claimed) < 40


def test_missing_result_is_reported_not_hidden():
    async def process_slide(index: int):
        return None if index == 2 else index

    with pytest.raises(RuntimeError, match=WORKER_EXIT_MESSAGE):
        _run(run_slide_worker_pool(process_slide, slide_count=5, worker_count=2))


def test_cancellation_stops_producer_and_workers():
    started = asyncio.Event()

    async def process_slide(index: int):
        started.set()
        await asyncio.sleep(60)
        return index

    async def scenario():
        pool = asyncio.create_task(
            run_slide_worker_pool(process_slide, slide_count=20, worker_count=3)
        )
        await started.wait()
        pool.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pool
        # No producer or worker task may be left running once the pool unwinds.
        leftovers = [
            task
            for task in asyncio.all_tasks()
            if not task.done()
            and task.get_coro().__qualname__.startswith("run_slide_worker_pool.")
        ]
        assert leftovers == []

    _run(scenario())
