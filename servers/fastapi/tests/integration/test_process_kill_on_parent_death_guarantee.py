"""End-to-end proof that a force-killed owner no longer strands its children.

This is the test that actually demonstrates the fix. It reproduces the leak
exactly: an intermediate Python process spawns a long-lived grandchild, then the
intermediate is destroyed with ``taskkill /F`` -- which runs no ``atexit`` hook
and, without ``/T``, does not touch the grandchild either. That is precisely how
192 ``chrome-headless-shell`` processes accumulated on the bench machine.

Two runs are compared so the assertion cannot pass vacuously:

* unbound -- the pre-fix behaviour; the grandchild must SURVIVE the force kill.
* bound   -- with ``bind_to_parent_lifetime``; the grandchild must DIE, killed by
  the kernel when the intermediate's last handle to the kill-on-close job closed.

A dummy ``time.sleep`` child stands in for Chrome: the guarantee under test is a
property of the Job Object, not of the binary, and a dummy keeps the test fast
and free of any Chrome discovery dependency. Waits are bounded polls, never
fixed sleeps.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

import pytest

from utils import process_kill_on_parent_death as kill_module


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="kill-on-parent-death is enforced through Win32 Job Objects"
)

_FASTAPI_ROOT = pathlib.Path(kill_module.__file__).resolve().parents[1]

# argv: <report path> <"bind"|"unbind">.  Writes "<pid> <bound|unbound>" only
# once the grandchild exists, atomically, so the parent never reads a half line.
_INTERMEDIATE = """
import os, subprocess, sys, time
from utils.process_kill_on_parent_death import bind_to_parent_lifetime

report, mode = sys.argv[1], sys.argv[2]
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
bound = bind_to_parent_lifetime(child) if mode == "bind" else False
staging = report + ".partial"
with open(staging, "w", encoding="utf-8") as handle:
    handle.write("%d %s" % (child.pid, "bound" if bound else "unbound"))
os.replace(staging, report)
time.sleep(600)
"""

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _pid_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _wait_until(predicate, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def _force_kill(pid: int) -> None:
    """Kill one process only -- no ``/T`` -- so the grandchild's fate is the test."""

    subprocess.run(
        ["taskkill", "/F", "/PID", str(pid)],
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _start_intermediate(tmp_path: pathlib.Path, mode: str) -> tuple[subprocess.Popen, int, str]:
    script = tmp_path / f"intermediate_{mode}.py"
    script.write_text(_INTERMEDIATE, encoding="utf-8")
    report = tmp_path / f"grandchild_{mode}.txt"

    environment = dict(os.environ)
    existing = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = (
        f"{_FASTAPI_ROOT}{os.pathsep}{existing}" if existing else str(_FASTAPI_ROOT)
    )

    process = subprocess.Popen(
        [sys.executable, str(script), str(report), mode],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert _wait_until(report.is_file, timeout=60), "intermediate never spawned a child"
    grandchild_pid, binding = report.read_text(encoding="utf-8").split()
    return process, int(grandchild_pid), binding


def test_an_unbound_grandchild_survives_a_force_killed_parent(
    tmp_path: pathlib.Path,
) -> None:
    """The control: this is the leak, and it must still reproduce."""

    intermediate, grandchild_pid, binding = _start_intermediate(tmp_path, "unbind")
    try:
        assert binding == "unbound"
        assert _pid_is_alive(grandchild_pid)

        _force_kill(intermediate.pid)
        intermediate.wait(timeout=30)

        # Give the kernel the same window the bound case is allowed; the point is
        # that nothing ever comes to collect this process.
        assert not _wait_until(
            lambda: not _pid_is_alive(grandchild_pid), timeout=10
        ), "control is vacuous: the grandchild died without a job binding"
    finally:
        _force_kill(grandchild_pid)
        if intermediate.poll() is None:
            _force_kill(intermediate.pid)
            intermediate.wait(timeout=30)


def test_a_bound_grandchild_dies_with_a_force_killed_parent(
    tmp_path: pathlib.Path,
) -> None:
    """The guarantee: no ``atexit`` hook runs, and the grandchild dies anyway.

    Nothing in the intermediate participates in its own teardown -- it is torn
    down by ``taskkill /F``. The only thing that can reap the grandchild is the
    kernel closing the intermediate's kill-on-close job.
    """

    intermediate, grandchild_pid, binding = _start_intermediate(tmp_path, "bind")
    try:
        assert binding == "bound", "AssignProcessToJobObject was refused on this host"
        assert _pid_is_alive(grandchild_pid)

        _force_kill(intermediate.pid)
        intermediate.wait(timeout=30)

        assert _wait_until(
            lambda: not _pid_is_alive(grandchild_pid), timeout=30
        ), "the grandchild outlived its force-killed parent"
    finally:
        if _pid_is_alive(grandchild_pid):
            _force_kill(grandchild_pid)
        if intermediate.poll() is None:
            _force_kill(intermediate.pid)
            intermediate.wait(timeout=30)
