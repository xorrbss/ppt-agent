"""F2: the image-generation gate is a semaphore, not a lock.

docs/authored-quality-speed-programme-design-20260905.md section 4 (F2):
``services/image_generation_service.py`` used to serialize every non-parallel
image call behind one process-wide ``asyncio.Lock``. It is now an
``asyncio.Semaphore`` sized by ``IMAGE_GENERATION_CONCURRENCY`` (default 3,
clamped 1-8), so up to N provider calls can overlap without going fully
unbounded (``ENABLE_PARALLEL_IMAGE_GENERATION=true`` still means unbounded).
"""

import asyncio

from services.image_generation_service import _get_image_generation_gate
from utils.get_env_programme import get_image_generation_concurrency


def test_image_generation_concurrency_env_default_and_clamp(monkeypatch):
    monkeypatch.delenv("IMAGE_GENERATION_CONCURRENCY", raising=False)
    assert get_image_generation_concurrency() == 3

    monkeypatch.setenv("IMAGE_GENERATION_CONCURRENCY", "0")
    assert get_image_generation_concurrency() == 1

    monkeypatch.setenv("IMAGE_GENERATION_CONCURRENCY", "99")
    assert get_image_generation_concurrency() == 8

    monkeypatch.setenv("IMAGE_GENERATION_CONCURRENCY", "2")
    assert get_image_generation_concurrency() == 2


def test_gate_admits_up_to_concurrency_then_blocks_the_next(monkeypatch):
    """Two calls overlap at concurrency 2; a third waits for one to finish."""
    monkeypatch.setenv("IMAGE_GENERATION_CONCURRENCY", "2")

    both_holding = asyncio.Event()
    release_holders = asyncio.Event()
    third_admitted = asyncio.Event()
    active = 0
    max_active = 0

    async def holder():
        nonlocal active, max_active
        async with _get_image_generation_gate():
            active += 1
            max_active = max(max_active, active)
            if active == 2:
                both_holding.set()
            await release_holders.wait()
            active -= 1

    async def prober():
        nonlocal active, max_active
        async with _get_image_generation_gate():
            active += 1
            max_active = max(max_active, active)
            third_admitted.set()
            active -= 1

    async def scenario():
        first = asyncio.create_task(holder())
        second = asyncio.create_task(holder())
        await asyncio.wait_for(both_holding.wait(), timeout=1)

        third = asyncio.create_task(prober())
        # Give the loop a chance to schedule the third task; it must still
        # be waiting on the semaphore because concurrency is exhausted.
        await asyncio.sleep(0.02)
        assert not third_admitted.is_set()
        assert active == 2

        release_holders.set()
        await asyncio.wait_for(asyncio.gather(first, second, third), timeout=1)
        assert third_admitted.is_set()

    asyncio.run(scenario())
    assert max_active == 2


def test_gate_is_reused_within_one_event_loop(monkeypatch):
    """The gate is created lazily per loop and shared by every caller on it."""
    monkeypatch.setenv("IMAGE_GENERATION_CONCURRENCY", "4")

    async def scenario():
        first = _get_image_generation_gate()
        second = _get_image_generation_gate()
        assert first is second

    asyncio.run(scenario())
