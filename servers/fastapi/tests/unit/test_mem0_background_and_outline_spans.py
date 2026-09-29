"""Mem0 writes run off the generation path; the outline window logs its sub-spans."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid

import services.mem0_presentation_memory_service as mem0_module
from services import svg_outline_spans
from services.mem0_presentation_memory_service import Mem0PresentationMemoryService


def _service(monkeypatch):
    monkeypatch.setenv("MEM0_ENABLED", "true")
    return Mem0PresentationMemoryService()


def test_enqueue_returns_immediately_when_the_write_is_slow(monkeypatch):
    service = _service(monkeypatch)
    finished = []

    async def slow_write(value):
        await asyncio.sleep(0.3)
        finished.append(value)

    async def scenario():
        started = time.monotonic()
        service.enqueue(slow_write, 1)
        enqueue_seconds = time.monotonic() - started
        assert finished == []
        await service.drain(wait_seconds=2)
        return enqueue_seconds

    assert asyncio.run(scenario()) < 0.1
    assert finished == [1]  # normal shutdown loses no write


def test_enqueue_swallows_failures_and_logs(monkeypatch, caplog):
    service = _service(monkeypatch)

    async def failing_write():
        raise RuntimeError("mem0 backend down")

    async def scenario():
        service.enqueue(failing_write)
        await service.drain(wait_seconds=2)
        return len(service._background_tasks)

    with caplog.at_level(logging.ERROR, logger=mem0_module.LOGGER.name):
        assert asyncio.run(scenario()) == 0
    assert "Mem0 background write failed" in caplog.text


def test_enqueue_without_a_running_loop_never_raises(monkeypatch):
    service = _service(monkeypatch)

    async def write():
        return None

    coroutine = write()
    try:
        service.enqueue(lambda: coroutine)  # no loop: skipped, not raised
    finally:
        coroutine.close()


def test_drain_cancels_writes_that_outlive_the_bound(monkeypatch):
    service = _service(monkeypatch)

    async def hung_write():
        await asyncio.sleep(30)

    async def scenario():
        service.enqueue(hung_write)
        started = time.monotonic()
        await service.drain(wait_seconds=0.1)
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 2


def test_warm_up_never_raises_when_client_init_fails(monkeypatch):
    service = _service(monkeypatch)

    def boom():
        raise RuntimeError("init failed")

    monkeypatch.setattr(mem0_module, "get_shared_mem0_client", boom)
    asyncio.run(service.warm_up())


def test_client_init_does_not_block_the_event_loop(monkeypatch):
    service = _service(monkeypatch)
    monkeypatch.setattr(mem0_module, "get_shared_mem0_client", lambda: time.sleep(0.3))

    async def scenario():
        ticks = 0
        task = asyncio.create_task(service._get_client())
        while not task.done():
            await asyncio.sleep(0.02)
            ticks += 1
        return ticks

    assert asyncio.run(scenario()) >= 5


def test_outline_span_line_lists_every_key_and_derives_prepare_local(caplog):
    spans = svg_outline_spans.begin()
    spans.set("doc_load", 12)
    spans.set("prepare_total", 500)
    spans.set("plan_llm", 200)
    spans.set("scenes", 100)
    with caplog.at_level(logging.INFO, logger=svg_outline_spans.LOGGER.name):
        spans.emit(uuid.UUID(int=1))
        spans.emit(uuid.UUID(int=1))  # once per deck
    lines = [r.getMessage() for r in caplog.records if "svg_outline_span" in r.getMessage()]
    assert len(lines) == 1
    for key in svg_outline_spans.SPAN_KEYS:
        assert f"{key}_ms=" in lines[0]
    assert "doc_load_ms=12" in lines[0]
    assert "prepare_local_ms=200" in lines[0]
    assert "mem0_pre_ms=na" in lines[0]
