"""Portable poll monitoring and Windows suspend/power primitives for serial benches."""
from __future__ import annotations

import ctypes
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from statistics import median
from typing import Any

SUSPEND_THRESHOLD_SECONDS = 30.0
POLL_GAP_THRESHOLD_SECONDS = 60.0
OVERRUN_FLOOR_SECONDS = 900.0
OVERRUN_MEDIAN_MULTIPLIER = 3.0
HEALTH_PROBE_INTERVAL_SECONDS = 10.0
LOST_TASK_SETTLE_SECONDS = 600.0
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
POWER_REQUEST_SYSTEM_REQUIRED = 1


def windows_interrupt_times() -> tuple[float, float] | None:
    if sys.platform != "win32":
        return None
    # The realtime API-set exports QueryInterruptTime on systems whose
    # kernel32 facade exports only QueryUnbiasedInterruptTime.
    try:
        realtime = ctypes.WinDLL("api-ms-win-core-realtime-l1-1-1", use_last_error=True)
        unbiased, biased = ctypes.c_ulonglong(), ctypes.c_ulonglong()
        realtime.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        realtime.QueryUnbiasedInterruptTime.restype = ctypes.c_int
        realtime.QueryInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
        realtime.QueryInterruptTime.restype = None
        if not realtime.QueryUnbiasedInterruptTime(ctypes.byref(unbiased)):
            return None
        realtime.QueryInterruptTime(ctypes.byref(biased))
        return biased.value / 10_000_000, unbiased.value / 10_000_000
    except (AttributeError, OSError):
        return None



class SuspendProbe:
    def __init__(self, read: Callable = windows_interrupt_times):
        self.read = read
        self.start = read()
        self.slept_seconds: float | None = None

    def sample(self) -> bool:
        current = self.read()
        if self.start is not None and current is not None:
            self.slept_seconds = max(0.0, (current[0] - self.start[0]) - (current[1] - self.start[1]))
        return self.slept_seconds is not None and self.slept_seconds > SUSPEND_THRESHOLD_SECONDS


class PollGapMonitor:
    """Count time outside completed requests, including the intentional poll sleep."""
    def __init__(self, clock: Callable = time.monotonic):
        self.clock = clock
        self.last_end = clock()
        self.max_gap_seconds = 0.0

    def begin(self) -> bool:
        self.max_gap_seconds = max(self.max_gap_seconds, self.clock() - self.last_end)
        return self.max_gap_seconds > POLL_GAP_THRESHOLD_SECONDS

    def end(self) -> None:
        self.last_end = self.clock()


class DeckWatch:
    def __init__(self, suspend: SuspendProbe | None = None, polls: PollGapMonitor | None = None):
        self.suspend = suspend or SuspendProbe()
        self.polls = polls or PollGapMonitor()
        self.detected = False

    def begin(self) -> None:
        gap = self.polls.begin()
        slept = self.suspend.sample()
        self.detected |= gap or slept

    def end(self) -> None:
        self.detected |= self.suspend.sample()
        self.polls.end()

    def evidence(self, power: str, overrun: Mapping[str, Any]) -> dict[str, Any]:
        return {"sleptSeconds": self.suspend.slept_seconds,
                "maxPollGapSeconds": round(self.polls.max_gap_seconds, 2),
                "powerGuard": power, **overrun}


def backend_overrun(timing: Mapping[str, Any], client_seconds: float,
                    finished_seconds: Sequence[float] = (),
                    stage_timeouts: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Advisory only: an overrun cannot distinguish a provider delay from sleep."""
    from services.content_brief_policy import DEFAULT_BASE_TIMEOUT_SECONDS

    configured = {"content_brief": DEFAULT_BASE_TIMEOUT_SECONDS, **(stage_timeouts or {})}
    overruns = []
    for name, stage in (timing.get("stages") or {}).items():
        if not isinstance(stage, Mapping):
            continue
        timeout = stage.get("timeoutSeconds", configured.get(name))
        wall = stage.get("wallMillis", stage.get("durationMillis"))
        if timeout is not None and wall is not None and wall > timeout * 1000:
            overruns.append({"stage": name, "wallMillis": wall, "timeoutSeconds": timeout})
    threshold = max(OVERRUN_FLOOR_SECONDS, OVERRUN_MEDIAN_MULTIPLIER * median(finished_seconds)) \
        if finished_seconds else None
    client_overrun = threshold is not None and client_seconds > threshold
    return {"overrun": bool(overruns or client_overrun), "stageOverruns": overruns,
            "clientOverrun": client_overrun, "clientThresholdSeconds": threshold}


class WindowsPower:
    """Typed Win32 calls; injected into power_guard for deterministic tests."""
    def __init__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.PowerCreateRequest.restype = ctypes.c_void_p
        self.kernel.PowerCreateRequest.argtypes = [ctypes.c_void_p]
        for name in ("PowerSetRequest", "PowerClearRequest"):
            method = getattr(self.kernel, name)
            method.argtypes = [ctypes.c_void_p, ctypes.c_int]
            method.restype = ctypes.c_int
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.kernel.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        self.kernel.SetThreadExecutionState.restype = ctypes.c_uint

    def create(self, reason: str):
        class ReasonUnion(ctypes.Union):
            _fields_ = [("simple", ctypes.c_wchar_p), ("padding", ctypes.c_void_p * 3)]

        class ReasonContext(ctypes.Structure):
            _fields_ = [("version", ctypes.c_uint), ("flags", ctypes.c_uint), ("reason", ReasonUnion)]

        context = ReasonContext(0, 1, ReasonUnion(simple=reason))
        handle = self.kernel.PowerCreateRequest(ctypes.byref(context))
        return None if handle in (None, ctypes.c_void_p(-1).value) else handle

    def set(self, handle):
        return self.kernel.PowerSetRequest(handle, POWER_REQUEST_SYSTEM_REQUIRED)

    def clear(self, handle):
        self.kernel.PowerClearRequest(handle, POWER_REQUEST_SYSTEM_REQUIRED)

    def close(self, handle):
        self.kernel.CloseHandle(handle)

    def execution(self, flags):
        return self.kernel.SetThreadExecutionState(flags)


@contextmanager
def power_guard(reason: str, *, api=None, platform: str | None = None):
    mode, handle = "none", None
    if (platform or sys.platform) == "win32":
        api = api or WindowsPower()
        try:
            handle = api.create(reason)
            if handle is not None and api.set(handle):
                mode = "power_request"
        except (AttributeError, OSError):
            pass
        if mode == "none":
            if handle is not None:
                api.close(handle)
                handle = None
            if api.execution(ES_CONTINUOUS | ES_SYSTEM_REQUIRED):
                mode = "execution_state"
    try:
        yield mode
    finally:
        if mode == "power_request":
            try:
                api.clear(handle)
            finally:
                api.close(handle)
        elif mode == "execution_state":
            api.execution(ES_CONTINUOUS)
