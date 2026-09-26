"""Bounded spawned page workers with parent-owned vision calls.

Only plain page data and a duplex connection cross the process boundary.
The provider client and its tenant/runtime context stay in the caller process.
"""

from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from constants.settings_registry import get_setting_default
from services.source_content.raster_stage_timing import (
    activate,
    bind,
    current_run,
    page,
    record_call,
    stage,
    vision_stage,
)


class _VisionProxy:
    def __init__(self, connection: Any, model: str):
        self.connection = connection
        self.model = model

    def __call__(self, system: str, parts: list[str | bytes], schema: dict, name: str) -> dict | None:
        with stage(None, vision_stage(name)):
            self.connection.send((system, parts, schema, name))
            status, value, calls = self.connection.recv()
            for call in calls:
                record_call(call["wait_seconds"], call["latency_seconds"], call["name"])
        if status == "error":
            raise type(value["type"], (RuntimeError,), {})(value["message"])
        return value


def _run_page(page_data: Any, slide_size: tuple[int, int], tesseract: Path,
              tessdata: Path, connection: Any, has_vision: bool, model: str) -> tuple[Any, dict]:
    from services.source_content.raster_redesign import _page_result

    try:
        with activate() as run, page(page_data.page_number):
            result = _page_result(
                page_data,
                slide_size,
                tesseract_path=tesseract,
                tessdata_path=tessdata,
                structurer=_VisionProxy(connection, model) if has_vision else None,
            )
        return result, run.payload()["pages"][0]
    finally:
        connection.close()


def _page_count(page_count: int, override: int | None) -> int:
    configured = int(os.environ.get("RASTER_ANALYSIS_PROCESS_WORKERS", get_setting_default(
        "RASTER_ANALYSIS_PROCESS_WORKERS"
    )))
    if configured < 0:
        raise ValueError("raster_analysis_process_workers_invalid")
    limit = configured or min(4, max(1, (os.cpu_count() or 2) - 1))
    return max(1, min(page_count, override or limit))


def analyze_pages(pages: tuple[Any, ...], slide_size: tuple[int, int],
                  tesseract: Path, tessdata: Path, structurer: Any,
                  max_workers: int | None) -> list[Any]:
    """Return results in source order while limiting in-flight page images."""

    if not pages:
        return []
    workers = _page_count(len(pages), max_workers)
    run = current_run()
    model = str(getattr(structurer, "model", "vision")) if structurer is not None else ""

    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as processes:
        def bridge(page_data: Any) -> Any:
            parent, child = multiprocessing.Pipe(duplex=True)
            try:
                future = processes.submit(
                    _run_page, page_data, slide_size, tesseract, tessdata,
                    child, structurer is not None, model,
                )
                while not future.done() or parent.poll():
                    if not parent.poll(0.05):
                        continue
                    system, parts, schema, name = parent.recv()
                    with activate() as call_run, page(page_data.page_number):
                        try:
                            value = structurer(system, parts, schema, name)
                            status = "ok"
                        except Exception as error:
                            status, value = "error", error
                    calls = call_run.payload()["pages"][0]["vision_calls"]
                    if status == "error":
                        value = {"type": type(value).__name__, "message": str(value)}
                    parent.send((status, value, calls))
                result, timing = future.result()
                if run is not None:
                    run.import_page(timing)
                return result
            finally:
                parent.close()
                child.close()

        # A bound context per bridge preserves tenant/owner and runtime settings.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="raster-vision") as threads:
            return list(threads.map(bind(bridge), pages))
