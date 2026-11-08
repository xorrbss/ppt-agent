"""Bounded, numeric-only private chart capture timing diagnostics."""
from __future__ import annotations

import asyncio
import json
import sys
from collections import deque
from contextlib import contextmanager, nullcontext
from time import perf_counter_ns

PREFIX = b"CHART_CAPTURE_TIMING_V1 "
VERSION = "chart-capture-timing/v1"
MAX_VALUE = (1 << 63) - 1
MAX_LINE_BYTES = 1024
MAX_RECORDS = 128
MAX_SNAPSHOTS = 8
PHASES = ("diagnostic_total", "callback", "material_wait", "setup_projection_target",
          "selection_all_on", "mutation_geometry", "final_screenshot", "support_cpu")
NUMERIC_KEYS = ("sequence", "event", "elapsed_ns", "completed_planes", "target_count",
                *(phase + "_ns" for phase in PHASES))
KEYS = frozenset(("version", *NUMERIC_KEYS))


def _encode(snapshot):
    return PREFIX + json.dumps(snapshot, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"


def parse_timing_line(line):
    """Accept only the exact fixed diagnostic contract; return no raw input."""
    try:
        if not isinstance(line, bytes) or len(line) > MAX_LINE_BYTES or not line.startswith(PREFIX):
            return None
        payload = line[len(PREFIX):]
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate timing key")
                value[key] = item
            return value
        value = json.loads(payload.decode("ascii"), object_pairs_hook=unique)
        if not isinstance(value, dict) or set(value) != KEYS or value.get("version") != VERSION:
            return None
        if any(type(value[key]) is not int or not 0 <= value[key] <= MAX_VALUE for key in NUMERIC_KEYS):
            return None
        if value["event"] not in (0, 1, 2) or value["sequence"] >= MAX_RECORDS:
            return None
        normalized = {"version": VERSION, **{key: value[key] for key in NUMERIC_KEYS}}
        return normalized if len(_encode(normalized)) <= MAX_LINE_BYTES else None
    except Exception:
        return None


async def drain_timing_stream(stream, snapshots):
    """Continuously drain stderr with a bounded line buffer and numeric deque."""
    current = bytearray()
    dropping = False
    accepted = 0
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            for byte in chunk:
                if byte == 10:
                    if not dropping and accepted < MAX_RECORDS:
                        parsed = parse_timing_line(bytes(current) + b"\n")
                        if parsed is not None:
                            snapshots.append(parsed)
                            accepted += 1
                    current.clear()
                    dropping = False
                elif not dropping:
                    if len(current) < MAX_LINE_BYTES - 1:
                        current.append(byte)
                    else:
                        current.clear()
                        dropping = True
    except asyncio.CancelledError:
        raise
    except Exception:
        # Optional diagnostic failures never replace the renderer result.
        pass


def new_snapshot_buffer():
    return deque(maxlen=MAX_SNAPSHOTS)


def log_timing_snapshots(logger, snapshots):
    try:
        for snapshot in snapshots:
            # Reconstruct exclusively from parsed fixed keys; never log stderr.
            logger.info("%s", _encode(snapshot).decode("ascii").rstrip("\n"))
    except Exception:
        pass


class ChartCaptureTiming:
    def __init__(self):
        self.started = self._now()
        self.values = {phase + "_ns": 0 for phase in PHASES}
        self.completed_planes = 0
        self.target_count = 0
        self.sent = 0
        self.finished = False
        self.disabled = self.started is None
        self.cpu_depth = 0
        self.outside_depth = 0
        self.cpu_started = None

    @staticmethod
    def _now():
        try:
            return perf_counter_ns()
        except Exception:
            return None

    def _add(self, phase, amount):
        try:
            key = phase + "_ns"
            if key in self.values and amount is not None:
                self.values[key] = min(MAX_VALUE, self.values[key] + max(0, amount))
        except Exception:
            pass

    def set_target_count(self, value):
        try:
            if type(value) is int:
                self.target_count = min(MAX_VALUE, max(0, value))
        except Exception:
            pass

    def emit(self, event):
        try:
            if self.disabled or self.sent >= MAX_RECORDS or event != 2 and self.sent >= MAX_RECORDS - 1:
                return
            now = self._now()
            if now is None or self.started is None:
                self.disabled = True
                return
            elapsed = max(0, now - self.started)
            snapshot = {"version": VERSION, "sequence": self.sent, "event": event,
                        "elapsed_ns": min(MAX_VALUE, elapsed), "completed_planes": self.completed_planes,
                        "target_count": self.target_count, **self.values}
            wire = _encode(snapshot)
            if len(wire) > MAX_LINE_BYTES:
                self.disabled = True
                return
            sink = getattr(sys.stderr, "buffer", None)
            if sink is not None:
                sink.write(wire)
                sink.flush()
            else:
                sys.stderr.write(wire.decode("ascii"))
                sys.stderr.flush()
            self.sent += 1
        except Exception:
            self.disabled = True

    def plane_completed(self):
        try:
            self.completed_planes = min(MAX_VALUE, self.completed_planes + 1)
            if self.completed_planes % 16 == 0:
                self.emit(1)
        except Exception:
            pass

    def finish(self):
        try:
            if not self.finished:
                self.finished = True
                self.emit(2)
        except Exception:
            pass

    @contextmanager
    def phase(self, phase):
        start = self._now()
        if start is None:
            self.disabled = True
        cpu = phase == "support_cpu"
        try:
            if cpu:
                self.cpu_depth += 1
                if self.cpu_depth == 1 and self.outside_depth == 0:
                    self.cpu_started = start
            else:
                if self.cpu_depth and self.outside_depth == 0 and self.cpu_started is not None and start is not None:
                    self._add("support_cpu", start - self.cpu_started)
                    self.cpu_started = None
                self.outside_depth += 1
        except Exception:
            pass
        try:
            yield
        finally:
            try:
                end = self._now()
                if end is None:
                    self.disabled = True
                if cpu:
                    if self.cpu_depth == 1 and self.cpu_started is not None and end is not None:
                        self._add("support_cpu", end - self.cpu_started)
                        self.cpu_started = None
                    self.cpu_depth = max(0, self.cpu_depth - 1)
                else:
                    if start is not None and end is not None:
                        self._add(phase, end - start)
                    self.outside_depth = max(0, self.outside_depth - 1)
                    if self.cpu_depth and self.outside_depth == 0:
                        self.cpu_started = end
            except Exception:
                pass


def timing_phase(timing, phase):
    return timing.phase(phase) if isinstance(timing, ChartCaptureTiming) else nullcontext()


def new_capture_timing():
    try:
        return ChartCaptureTiming()
    except Exception:
        return None
