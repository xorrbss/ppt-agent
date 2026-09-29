"""Coverage for the analysis-phase lease/progress ticker.

The workspace operation runner already renews a handler's lease generically
every ~30s via its own heartbeat task, but only while the handler's blocking
work stays off the event loop. ``_run_analyzer_with_progress`` is the
defensive second signal (same fencing-checked extend_lease/progress APIs)
plus the only source of incremental, elapsed-time progress text while
``analyze_redesign_pptx`` (OCR + one vision-LLM call per page, minutes for a
large deck) runs with no progress callback of its own.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from services.raster_analysis_operation_handler import _run_analyzer_with_progress
import services.raster_analysis_operation_handler as handler_module


class _FakeContext:
    def __init__(self):
        self.extend_calls: list[int] = []
        self.progress_calls: list[dict] = []

    async def extend_lease(self, *, seconds: int) -> bool:
        self.extend_calls.append(seconds)
        return True

    async def progress(self, *, stage, current=None, total=None, percent=None, message=None) -> bool:
        self.progress_calls.append({"stage": stage, "percent": percent, "message": message})
        return True


def _slow_analyzer(source_path, *, orientation):
    time.sleep(0.18)
    return {"source_path": str(source_path), "orientation": orientation}


@pytest.mark.anyio
async def test_ticker_extends_lease_and_reports_elapsed_progress(monkeypatch, tmp_path):
    monkeypatch.setattr(handler_module, "_ANALYSIS_TICK_SECONDS", 0.05)
    context = _FakeContext()
    source_path = tmp_path / "source.pptx"
    source_path.write_bytes(b"")

    result = await _run_analyzer_with_progress(
        context, _slow_analyzer, source_path,
        orientation="landscape", analysis_mode="auto_redesign",
    )

    assert result == {"source_path": str(source_path), "orientation": "landscape"}
    # A ~0.18s call with a 0.05s tick should produce at least two renewals.
    assert len(context.extend_calls) >= 2
    assert all(seconds == handler_module._ANALYSIS_LEASE_WINDOW_SECONDS for seconds in context.extend_calls)
    assert len(context.progress_calls) >= 2
    assert all(call["stage"] == "raster_analysis" for call in context.progress_calls)
    assert all(
        call["message"].key == "raster.analysis.redesign.elapsed"
        for call in context.progress_calls
    )
    assert all("elapsed" in call["message"].params for call in context.progress_calls)


@pytest.mark.anyio
async def test_ticker_uses_analyze_wording_for_reconstruct_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(handler_module, "_ANALYSIS_TICK_SECONDS", 0.05)
    context = _FakeContext()
    source_path = tmp_path / "source.pptx"
    source_path.write_bytes(b"")

    await _run_analyzer_with_progress(
        context, _slow_analyzer, source_path,
        orientation="portrait", analysis_mode="reconstruct",
    )

    assert context.progress_calls
    assert all(
        call["message"].key == "raster.analysis.regions.elapsed"
        for call in context.progress_calls
    )


@pytest.mark.anyio
async def test_ticker_stops_promptly_on_fast_analyzer(monkeypatch, tmp_path):
    """A fast call must not leave the background ticker task running."""
    monkeypatch.setattr(handler_module, "_ANALYSIS_TICK_SECONDS", 5)
    context = _FakeContext()
    source_path = tmp_path / "source.pptx"
    source_path.write_bytes(b"")

    before = len(asyncio.all_tasks())
    await _run_analyzer_with_progress(
        context, lambda source_path, *, orientation: "done", source_path,
        orientation="landscape", analysis_mode="auto_redesign",
    )
    # The ticker task is awaited (via `finally: stop.set(); await ticker`)
    # before returning, so no stray task should remain.
    assert len(asyncio.all_tasks()) <= before
    assert context.extend_calls == []
