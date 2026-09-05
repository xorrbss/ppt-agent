"""Observability metric constructors and process probes for authored runs.

Split out of ``services/authored_presentation_generation.py`` to keep that
module under the file-size ratchet.  This module owns the small vocabulary a
generation run uses to report a metric -- observed, unavailable, or not
applicable -- together with the probes that produce the values: the Windows
worker peak-memory and handle-count readings, the render-runtime counter delta,
the blocked-reference scan over rendered HTML, and the artifact/object-store
size and latency observations.

The probes deliberately fail closed by raising rather than reporting a guessed
number, because a receipt that invents a measurement is worse than one that
records the measurement as unavailable.
"""

from __future__ import annotations

from collections.abc import Mapping
import os

from utils.slide_capture import _asset_references


def _observed_metric(value: object) -> dict[str, object]:
    return {"state": "observed", "value": value}


def _unavailable_metric(reason: str) -> dict[str, object]:
    return {"state": "unavailable", "reason": reason}


def _not_applicable_metric(reason: str) -> dict[str, object]:
    return {"state": "not_applicable", "reason": reason}


def _runtime_counter_delta(
    before: Mapping[str, int], after: Mapping[str, int], *names: str
) -> int:
    return sum(max(0, int(after.get(name, 0)) - int(before.get(name, 0))) for name in names)


def _worker_peak_memory_metric() -> dict[str, object]:
    """Read this worker's lifetime peak working set without third-party packages."""

    if os.name != "nt":
        return _not_applicable_metric("worker_resource_not_applicable")
    try:
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(ProcessMemoryCounters),
            wintypes.DWORD,
        )
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        process_handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            process_handle, ctypes.byref(counters), counters.cb
        ):
            raise RuntimeError("worker_memory_peak_collection_failed")
        return _observed_metric(int(counters.PeakWorkingSetSize))
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise RuntimeError("worker_memory_peak_collection_failed") from error


def _worker_handle_count_metric() -> dict[str, object]:
    """Read the current Windows worker handle count from the process kernel."""

    if os.name != "nt":
        return _not_applicable_metric("worker_resource_not_applicable")
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.GetProcessHandleCount.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.GetProcessHandleCount.restype = wintypes.BOOL

        count = wintypes.DWORD()
        process_handle = kernel32.GetCurrentProcess()
        if not kernel32.GetProcessHandleCount(process_handle, ctypes.byref(count)):
            raise RuntimeError("worker_handle_count_collection_failed")
        return _observed_metric(int(count.value))
    except (AttributeError, OSError, TypeError, ValueError) as error:
        raise RuntimeError("worker_handle_count_collection_failed") from error


def _maximum_observed_metric(
    first: Mapping[str, object], second: Mapping[str, object]
) -> dict[str, object]:
    values = [
        metric.get("value")
        for metric in (first, second)
        if metric.get("state") == "observed"
    ]
    numeric = [
        int(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if numeric:
        return _observed_metric(max(numeric))
    return dict(second)


def _blocked_renderer_reference_count(htmls: object) -> int:
    if not isinstance(htmls, (list, tuple)):
        return 0
    blocked_schemes = {"http", "https", "file", "ftp", "ws", "wss"}
    count = 0
    for html in htmls:
        if not isinstance(html, str):
            continue
        for reference in _asset_references(html):
            scheme = reference.split(":", 1)[0].strip().lower()
            if scheme in blocked_schemes or reference.startswith("//"):
                count += 1
    return count


def _object_store_latency_metric(*sources: object) -> dict[str, object]:
    """Consume artifact-agent latency metadata without binding its container type."""

    keys = ("object_store_latency_ms", "objectStoreLatencyMs")
    for source in sources:
        if isinstance(source, Mapping):
            for key in keys:
                value = source.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return _observed_metric(max(0, round(float(value))))
        for key in keys:
            value = getattr(source, key, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return _observed_metric(max(0, round(float(value))))
    # The current authored publisher writes to its local immutable artifact
    # store. Object-store I/O is therefore not an executed stage on this path.
    return _not_applicable_metric("artifact_store_not_used")


def _artifact_bytes_metric(artifact_path: object) -> dict[str, object]:
    try:
        return _observed_metric(os.path.getsize(os.fspath(artifact_path)))
    except (OSError, TypeError, ValueError) as error:
        raise RuntimeError("artifact_bytes_collection_failed") from error
