"""Per-page, per-stage wall-clock timing of one raster analysis run.

Measurement only: nothing here changes what an analysis produces, and the
result lands in ``RasterAnalysis.stage_timings``, which the analysis digest
excludes. Every function is a no-op when no run is active.

- ``activate()`` starts a run for the calling context.
- ``page_task(fn)`` wraps a per-page callable for a thread pool: each call runs
  in its own copy of the submitting context (worker threads do not inherit
  context variables) inside a page scope keyed by ``page.page_number``.
- ``stage(page_index, name)`` times a stage. Time is exclusive: while a nested
  stage runs, the enclosing one is paused, so a page's stages sum to at most its
  wall time and the rest is reported as ``other``.
- ``record_call(wait, latency)`` records one vision call: time waiting for the
  client's concurrency gate and the call itself.

Seconds are floats from ``time.perf_counter``: monotonic and high-resolution
(``time.monotonic`` ticks in ~16 ms steps on Windows).
"""

from __future__ import annotations

import contextvars
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, TypeVar

STAGES = ("ocr", "rereads", "number_units", "structure_calls", "tables", "transcription", "vision_other", "plan")
RESIDUAL = "other"
_VISION_STAGE_BY_NAME = {
    "slide_structure": "structure_calls",
    "table_structure": "tables",
    "line_transcripts": "transcription",
}
T = TypeVar("T")


def vision_stage(call_name: str) -> str:
    """The stage a vision call of this response-schema name belongs to."""

    return _VISION_STAGE_BY_NAME.get(call_name, "vision_other")


@dataclass
class _Page:
    stages: dict[str, float] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    wall: float = 0.0


@dataclass
class _Frame:
    page: int
    name: str
    resumed: float


class TimingRun:
    """Thread-safe accumulator for one analysis run."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pages: dict[int, _Page] = {}
        self._unpaged_calls: list[dict[str, Any]] = []
        self.started = time.perf_counter()
        self.wall = 0.0

    def _page(self, number: int) -> _Page:
        return self._pages.setdefault(number, _Page())

    def add_stage(self, page: int, name: str, seconds: float) -> None:
        with self._lock:
            stages = self._page(page).stages
            stages[name] = stages.get(name, 0.0) + max(0.0, seconds)

    def add_page_wall(self, page: int, seconds: float) -> None:
        with self._lock:
            self._page(page).wall += max(0.0, seconds)

    def add_call(self, page: int | None, call: dict[str, Any]) -> None:
        with self._lock:
            (self._page(page).calls if page is not None else self._unpaged_calls).append(call)

    def payload(self) -> dict[str, Any]:
        """The ``RasterStageTimings`` payload (plain JSON types)."""

        with self._lock:
            pages = []
            for number in sorted(self._pages):
                record = self._pages[number]
                stages = dict(record.stages)
                stages[RESIDUAL] = max(0.0, record.wall - sum(stages.values()))
                pages.append({
                    "page_number": number,
                    "wall_seconds": record.wall,
                    "stages": stages,
                    "vision_calls": list(record.calls),
                })
            calls = [call for page in pages for call in page["vision_calls"]] + list(self._unpaged_calls)
            names = sorted({name for page in pages for name in page["stages"]})
            return {
                "clock": "monotonic",
                "wall_seconds": self.wall,
                "pages": pages,
                "stage_totals": {name: sum(p["stages"].get(name, 0.0) for p in pages) for name in names},
                "stage_page_max": {name: max(p["stages"].get(name, 0.0) for p in pages) for name in names},
                "vision_call_count": len(calls),
                "vision_wait_seconds": sum(call["wait_seconds"] for call in calls),
                "vision_latency_seconds": sum(call["latency_seconds"] for call in calls),
                "unpaged_vision_calls": list(self._unpaged_calls),
            }


_RUN: contextvars.ContextVar[TimingRun | None] = contextvars.ContextVar("raster_timing_run", default=None)
# The page scope's stack of open stages. A list owned by one page task (one
# thread); a new page scope always installs its own list.
_STACK: contextvars.ContextVar[list[_Frame] | None] = contextvars.ContextVar("raster_timing_stack", default=None)
_PAGE: contextvars.ContextVar[int | None] = contextvars.ContextVar("raster_timing_page", default=None)


def current_run() -> TimingRun | None:
    return _RUN.get()


@contextmanager
def activate() -> Iterator[TimingRun]:
    run = TimingRun()
    token = _RUN.set(run)
    try:
        yield run
    finally:
        run.wall = time.perf_counter() - run.started
        _RUN.reset(token)


@contextmanager
def page(page_number: int) -> Iterator[None]:
    run = _RUN.get()
    if run is None:
        yield
        return
    page_token, stack_token = _PAGE.set(page_number), _STACK.set([])
    started = time.perf_counter()
    try:
        yield
    finally:
        run.add_page_wall(page_number, time.perf_counter() - started)
        _STACK.reset(stack_token)
        _PAGE.reset(page_token)


@contextmanager
def stage(page_index: int | None, name: str) -> Iterator[None]:
    """Time ``name`` on ``page_index`` (None: the current page scope)."""

    if name not in STAGES:
        raise ValueError(f"raster_timing_stage_unknown:{name}")
    run = _RUN.get()
    number = page_index if page_index is not None else _PAGE.get()
    if run is None or number is None:
        yield
        return
    stack = _STACK.get()
    if stack is None:  # an explicit page outside any page scope: inclusive time
        started = time.perf_counter()
        try:
            yield
        finally:
            run.add_stage(number, name, time.perf_counter() - started)
        return
    now = time.perf_counter()
    if stack:
        parent = stack[-1]
        run.add_stage(parent.page, parent.name, now - parent.resumed)
    frame = _Frame(number, name, now)
    stack.append(frame)
    try:
        yield
    finally:
        now = time.perf_counter()
        run.add_stage(frame.page, frame.name, now - frame.resumed)
        stack.pop()
        if stack:
            stack[-1].resumed = now


def record_call(wait: float, latency: float, name: str | None = None) -> None:
    """One vision call: seconds waiting for the concurrency gate, then in the call."""

    run = _RUN.get()
    if run is None:
        return
    call = {"name": name, "wait_seconds": max(0.0, wait), "latency_seconds": max(0.0, latency)}
    run.add_call(_PAGE.get(), call)


def bind(fn: Callable[..., T]) -> Callable[..., T]:
    """``fn`` run in a fresh copy of the current context on every call (any thread)."""

    context = contextvars.copy_context()

    def bound(*args: Any, **kwargs: Any) -> T:
        return context.copy().run(fn, *args, **kwargs)

    return bound


def page_task(fn: Callable[..., T], number: Callable[..., int] = lambda page, *_: page.page_number) -> Callable[..., T]:
    """``bind(fn)`` inside a page scope whose number is ``number(*args)``."""

    def scoped(*args: Any, **kwargs: Any) -> T:
        with page(number(*args)):
            return fn(*args, **kwargs)

    return bind(scoped)
