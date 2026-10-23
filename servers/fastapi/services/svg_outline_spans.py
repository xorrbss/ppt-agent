"""Sub-span timings for the svg generation outline window.

The attempt timing receipt books everything from the operation boundary to the
end of per-deck setup as one ``outline`` stage. This module records the
sub-steps of that window and emits ONE structured log line per deck::

    svg_outline_span presentation=<id> doc_load_ms=... mem0_pre_ms=... ...

Optional setup observations use the attempt's existing timing recorder.
A step that did not run in this attempt is
reported as ``na``. Spans travel in a ContextVar so the deep call sites (asset
offer) need no extra parameters; every helper is a no-op outside a window.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
import logging
import time

from services.canvas_bench_speed import BenchSpeedArm, validate_speed_arm

LOGGER = logging.getLogger(__name__)

SPAN_KEYS = (
    "doc_load", "mem0_pre", "content_brief", "outline_first_chunk", "outline_total",
    "outline_post", "job_semaphore_wait", "prepare_total", "prepare_local",
    "title", "plan_llm", "scenes", "icon_sheets", "header_design",
    "header_representative", "header_selection",
)
_SETUP_KEYS = frozenset({"title", "plan_llm", "scenes", "icon_sheets",
                        "header_design", "header_representative", "header_selection"})


class OutlineSpans:
    def __init__(self, *, bench_speed_arm: BenchSpeedArm | None = None) -> None:
        self.ms: dict[str, float] = {}
        self.bench_speed_arm = validate_speed_arm(bench_speed_arm)
        self._emitted = False
        self.recorder = None

    def add(self, name: str, ms: float) -> None:
        self.ms[name] = self.ms.get(name, 0.0) + max(0.0, ms)

    def set(self, name: str, ms: float) -> None:
        self.ms[name] = max(0.0, ms)

    @contextmanager
    def span(self, name: str, *, recorder=None) -> Iterator[None]:
        started = time.monotonic()
        before = sum(self.ms.get(key, 0.0) for key in _SETUP_KEYS)
        try:
            yield
        finally:
            ended = time.monotonic()
            elapsed = (ended - started) * 1000
            self.add(name, elapsed)
            recorder = recorder if recorder is not None else self.recorder
            if name == "prepare_total":
                elapsed = max(0.0, elapsed - (sum(self.ms.get(key, 0.0) for key in _SETUP_KEYS) - before))
                self.add("prepare_local", elapsed)
                if recorder is not None:
                    recorder.record_observation("setup.prepare_local", started, ended, duration_ms=elapsed)
            elif name in _SETUP_KEYS and recorder is not None:
                recorder.record_observation(f"setup.{name}", started, ended)

    def emit(self, presentation_id: object) -> None:
        """Log the line once per deck; never raises."""
        if self._emitted:
            return
        self._emitted = True
        try:
            ms = dict(self.ms)
            if "prepare_total" in ms and "prepare_local" not in ms:
                offer = sum(ms.get(k, 0.0) for k in ("plan_llm", "scenes", "icon_sheets"))
                ms["prepare_local"] = max(0.0, ms["prepare_total"] - offer)
            parts = " ".join(
                f"{key}_ms={ms[key]:.0f}" if key in ms else f"{key}_ms=na" for key in SPAN_KEYS
            )
            if self.bench_speed_arm is not None:
                parts += f" bench_speed_arm={self.bench_speed_arm}"
            LOGGER.info("svg_outline_span presentation=%s %s", presentation_id, parts)
        except Exception:  # noqa: BLE001 - observability must not fail a deck
            LOGGER.debug("svg_outline_span emit failed", exc_info=True)


_CURRENT: ContextVar[OutlineSpans | None] = ContextVar("svg_outline_spans", default=None)


def begin(*, bench_speed_arm: BenchSpeedArm | None = None) -> OutlineSpans:
    spans = OutlineSpans(bench_speed_arm=bench_speed_arm)
    _CURRENT.set(spans)
    return spans


def current() -> OutlineSpans | None:
    return _CURRENT.get()


@contextmanager
def span(name: str, *, recorder=None) -> Iterator[None]:
    spans = _CURRENT.get()
    if spans is None:
        if recorder is not None and name in _SETUP_KEYS:
            with recorder.observe(f"setup.{name}"):
                yield
        else:
            yield
        return
    with spans.span(name, recorder=recorder):
        yield


def emit(presentation_id: object) -> None:
    spans = _CURRENT.get()
    if spans is not None:
        spans.emit(presentation_id)


__all__ = ["OutlineSpans", "SPAN_KEYS", "begin", "current", "emit", "span"]
