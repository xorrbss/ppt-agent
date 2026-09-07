"""Bind spawned browser/renderer children to the lifetime of this process.

``atexit`` teardown only runs when the owning Python process exits gracefully.
When it is force-killed instead -- ``taskkill /F``, ``SIGKILL``, ``Ctrl-Break``,
a hard crash -- no handler runs, and every Chrome tree that process started is
stranded forever: a headless root plus three helpers, several hundred megabytes,
with a dead parent PID and nothing left that knows to reap it. Benchmark and
worker runs are interrupted that way routinely, so the strays accumulate.

The fix is to stop relying on the parent running cleanup code at all, and ask
the operating system to enforce the invariant.

Windows
    One process-wide Job Object created with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``.
    Every launched browser/renderer process is assigned to it, and children of a
    job member join the same job automatically, so assigning the root covers the
    whole Chrome tree. The kernel terminates every member when the job's last
    handle closes, which happens when this process dies by *any* means. The
    handle is therefore held in a module-level reference for the lifetime of the
    process, and is deliberately created non-inheritable (``lpJobAttributes``
    NULL): a child holding a handle to its own job would keep the job alive and
    defeat the guarantee.

    Assignment can still fail (an outer job that forbids nesting, a restricted
    token, a process that exited between spawn and assignment). Failure is
    logged and reported, never raised: the existing ``atexit`` teardown remains
    as the fallback and rendering must not be affected.

POSIX
    Deliberately unchanged -- see ``docs``-worthy rationale in the module notes
    below and in ``supports_os_enforced_teardown``. ``prctl(PR_SET_PDEATHSIG)``
    is not a safe analogue here because it fires on the death of the spawning
    *thread*, and these launchers run on asyncio worker threads whose exit must
    not kill a browser that outlives the render that started it.

There is an unavoidable microsecond-scale window between ``CreateProcess`` and
``AssignProcessToJobObject`` in which a parent death still strands the child.
Closing it would require ``CREATE_SUSPENDED`` plus a manual resume, which
``subprocess`` does not expose; the residual risk is far smaller than the defect
this removes.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)


# JOBOBJECTINFOCLASS::JobObjectExtendedLimitInformation
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100

_lock = threading.Lock()
_win32_api: Any = None
_job_handle: Any = None
_job_unavailable = False


def _is_windows() -> bool:
    """Seam so tests can exercise the non-Windows branch without touching ``os``."""

    return os.name == "nt"


class _Win32Api:
    """Lazily declared ``kernel32`` entry points and the job limit structure.

    Every entry point declares ``argtypes`` and ``restype``. Omitting ``restype``
    on a ``HANDLE``-returning call silently truncates the value to a 32-bit int
    on Win64, which corrupts the job in ways that only show up much later.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),  # ULONG_PTR
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(ExtendedLimitInformation),
            wintypes.DWORD,
        ]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        self.ctypes = ctypes
        self.kernel32 = kernel32
        self.ExtendedLimitInformation = ExtendedLimitInformation


def _api() -> _Win32Api:
    global _win32_api
    if _win32_api is None:
        _win32_api = _Win32Api()
    return _win32_api


def _create_kill_on_close_job() -> Any:
    """Create the unnamed, non-inheritable, kill-on-close job. ``None`` on failure."""

    api = _api()
    ctypes = api.ctypes
    handle = api.kernel32.CreateJobObjectW(None, None)
    if not handle:
        logger.warning(
            "CreateJobObjectW failed (winerror %s); browser children keep only "
            "the atexit teardown guarantee",
            ctypes.get_last_error(),
        )
        return None
    limits = api.ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assigned = api.kernel32.SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    if not assigned:
        logger.warning(
            "SetInformationJobObject(KILL_ON_JOB_CLOSE) failed (winerror %s); "
            "browser children keep only the atexit teardown guarantee",
            ctypes.get_last_error(),
        )
        api.kernel32.CloseHandle(handle)
        return None
    return handle


def _job_object() -> Any:
    """The one job handle this process owns, created on first use.

    The reference is intentionally never released: kill-on-close fires when the
    last handle closes, so holding it for the process lifetime is what makes the
    guarantee coincide with the parent's death.
    """

    global _job_handle, _job_unavailable
    with _lock:
        if _job_handle is not None or _job_unavailable:
            return _job_handle
        _job_handle = _create_kill_on_close_job()
        if _job_handle is None:
            _job_unavailable = True
        return _job_handle


def _assign_pid_to_job(job_handle: Any, pid: int) -> bool:
    api = _api()
    ctypes = api.ctypes
    process_handle = api.kernel32.OpenProcess(
        _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
    )
    if not process_handle:
        logger.warning(
            "OpenProcess failed for pid %s (winerror %s); falling back to atexit "
            "teardown for this child",
            pid,
            ctypes.get_last_error(),
        )
        return False
    try:
        if not api.kernel32.AssignProcessToJobObject(job_handle, process_handle):
            logger.warning(
                "AssignProcessToJobObject failed for pid %s (winerror %s); falling "
                "back to atexit teardown for this child",
                pid,
                ctypes.get_last_error(),
            )
            return False
        return True
    finally:
        api.kernel32.CloseHandle(process_handle)


def supports_os_enforced_teardown() -> bool:
    """Whether this platform can bind children to the parent's lifetime.

    POSIX returns ``False`` on purpose. The launchers pass ``start_new_session=True``
    so ``os.killpg`` can reap a whole Chrome tree, which also detaches the child
    from the parent's session. The direct analogue, ``prctl(PR_SET_PDEATHSIG)``
    in a ``preexec_fn``, fires when the *spawning thread* exits rather than the
    process; these launches happen on ``asyncio.to_thread`` workers, so a
    reusable Chrome meant to serve many renders would be killed the moment the
    thread that started it was recycled. ``preexec_fn`` is additionally unsafe in
    a threaded program and unavailable to ``asyncio.create_subprocess_exec``.
    A correct POSIX equivalent needs a supervisor process or a cgroup/systemd
    scope owning every browser, which is a larger change than this one.
    """

    return _is_windows()


def bind_to_parent_lifetime(process: Any) -> bool:
    """Ask the OS to kill ``process`` (and its children) when this process dies.

    ``process`` may be a ``subprocess.Popen``, an ``asyncio`` process, or a raw
    pid. Returns ``True`` only when the OS-level guarantee is in place; ``False``
    means the caller keeps the pre-existing ``atexit`` teardown as its only
    guarantee. This function never raises -- a failed binding must not break
    rendering.
    """

    pid = process if isinstance(process, int) else getattr(process, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return False
    if not _is_windows():
        return False
    try:
        job_handle = _job_object()
        if job_handle is None:
            return False
        return _assign_pid_to_job(job_handle, pid)
    except Exception:  # teardown hardening must never fail a render
        logger.warning(
            "could not bind pid %s to the kill-on-parent-death job", pid, exc_info=True
        )
        return False


__all__ = ["bind_to_parent_lifetime", "supports_os_enforced_teardown"]
