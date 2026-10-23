"""Lock contention cannot terminalize a review or revive a cancelled waiter."""
import asyncio
import subprocess
import sys
import threading

import pytest

from services.svg_final_pptx_review import render_final_review_inputs
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError, RetryableWorkspaceOperationError,
)
from tests.unit import test_svg_final_review_render_availability as availability
from workers import public_svg_pptx_render as office

office_host = availability.office_host


@pytest.mark.parametrize("wait_seconds,render_seconds,reason", [
    (100, 80, "svg_final_pptx_render_busy"),
    (50, 80, "svg_final_pptx_render_timeout"),
    (20, 80, None),
])
def test_slow_contending_reviews_defer_or_render_with_enough_budget(
        office_host, monkeypatch, wait_seconds, render_seconds, reason):
    clock = [0.0]
    first_entered = threading.Event()
    original_render = office._render.side_effect
    child_type = office.subprocess.Popen
    original_communicate = child_type.communicate

    def wait(_mutex, milliseconds):
        acquired = office_host.mutex.acquire(timeout=milliseconds / 1000)
        if not acquired:
            office_host.second_waiting.set()
        return 0 if acquired else 258

    def slow_render(request):
        if office_host.launches == 1:
            first_entered.set()
            assert office_host.second_waiting.wait(5), "Second review did not contend"
            clock[0] += wait_seconds
        return original_render(request)

    def communicate(child, *, input, timeout):
        if child.pid == 2 and render_seconds > timeout:
            office_host.timeouts.append(timeout)
            clock[0] += timeout
            raise subprocess.TimeoutExpired("render", timeout)
        return original_communicate(child, input=input, timeout=timeout)

    monkeypatch.setattr(office, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sys.modules["win32event"], "WaitForSingleObject", wait)
    monkeypatch.setattr(office._render, "side_effect", slow_render)
    monkeypatch.setattr(child_type, "communicate", communicate)
    first_claim, first_inputs = availability._inputs()
    second_claim, second_inputs = availability._inputs()

    async def reviews():
        first = asyncio.create_task(render_final_review_inputs(first_inputs, first_claim))
        assert await asyncio.to_thread(first_entered.wait, 5)
        second = asyncio.create_task(render_final_review_inputs(second_inputs, second_claim))
        return await asyncio.gather(first, second, return_exceptions=True)

    first, second = asyncio.run(reviews())
    assert first["proof"]["status"] == "measured"
    if reason is None:
        assert second["proof"]["status"] == "measured"
    else:
        assert isinstance(second, RetryableWorkspaceOperationError)
        assert not isinstance(second, NonRetryableWorkspaceOperationError)
        assert second.code == reason and second.retryable is True
    assert office_host.launches == (1 if reason == "svg_final_pptx_render_busy" else 2)
    assert office_host.max_inflight == 1
    assert not office_host.mutex.locked()
    if office_host.launches == 2:
        assert office_host.timeouts == [120, 120 - wait_seconds]


def test_cancelled_review_exits_wait_before_owner_releases_lock(office_host, monkeypatch):
    finished = threading.Event()
    real_render = office.render_final_pptx
    wait = sys.modules["win32event"].WaitForSingleObject
    waits = []

    def observed_wait(mutex, milliseconds):
        waits.append(milliseconds)
        return wait(mutex, milliseconds)

    def observed_render(*args, **kwargs):
        try:
            return real_render(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(sys.modules["win32event"], "WaitForSingleObject", observed_wait)
    monkeypatch.setattr(office, "render_final_pptx", observed_render)
    claim, inputs = availability._inputs()
    office_host.mutex.acquire()

    async def cancel_review():
        task = asyncio.create_task(render_final_review_inputs(inputs, claim))
        assert await asyncio.to_thread(office_host.second_waiting.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await asyncio.to_thread(finished.wait, 2), "Cancelled waiter kept its executor thread"
        assert office_host.mutex.locked()

    try:
        asyncio.run(cancel_review())
    finally:
        office_host.mutex.release()
    assert office_host.launches == 0
    assert max(waits) <= office.LOCK_WAIT_SLICE_SECONDS * 1000


def test_cancellation_during_acquisition_releases_without_render(office_host, tmp_path, monkeypatch):
    cancelled = threading.Event()
    wait = sys.modules["win32event"].WaitForSingleObject

    def cancel_after_acquisition(*args):
        state = wait(*args)
        cancelled.set()
        return state

    monkeypatch.setattr(sys.modules["win32event"], "WaitForSingleObject", cancel_after_acquisition)
    result = office.render_final_pptx(tmp_path / "absent.pptx", tmp_path / "render",
        page_count=1, width_px=16, height_px=9, timeout=120, cancellation_event=cancelled)
    assert result["reasonCode"] == "svg_final_pptx_render_cancelled"
    assert office_host.launches == 0 and not office_host.mutex.locked()


def test_lock_wait_deadline_is_distinct_from_render_deadline(office_host, tmp_path, monkeypatch):
    clock = [0.0]
    waits = []

    def wait(_mutex, milliseconds):
        waits.append(milliseconds)
        clock[0] += milliseconds / 1000
        return 258

    monkeypatch.setattr(office, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sys.modules["win32event"], "WaitForSingleObject", wait)
    result = office.render_final_pptx(tmp_path / "absent.pptx", tmp_path / "render",
        page_count=1, width_px=16, height_px=9, timeout=120, lock_wait_timeout=0.2)
    assert result["reasonCode"] == "svg_final_pptx_render_busy" and result["retryable"] is True
    assert clock[0] == pytest.approx(0.2)
    assert max(waits) <= office.LOCK_WAIT_SLICE_SECONDS * 1000
    assert office_host.launches == 0


def test_uncontended_render_timeout_stays_nonretryable(office_host, tmp_path, monkeypatch):
    monkeypatch.setattr(office, "_render_serialized",
        lambda *_args, **_kwargs: office._unavailable("svg_final_pptx_render_timeout"))
    result = office.render_final_pptx(tmp_path / "absent.pptx", tmp_path / "render",
        page_count=1, width_px=16, height_px=9, timeout=120)
    assert result["reasonCode"] == "svg_final_pptx_render_timeout"
    assert result.get("retryable") is not True
