from __future__ import annotations

import ctypes
import os
import pathlib
import sys
import time
import uuid

import pytest

from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from services.trusted_executor.process_supervisor import OperationProcessSupervisor


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows Job Object evidence requires Windows")


def _pid_is_active(pid: int) -> bool:
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32))
    kernel32.GetExitCodeProcess.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_uint32()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


@pytest.mark.parametrize("termination", ["cancel", "timeout", "oom"])
def test_windows_job_object_leaves_zero_process_tree_after_cancel_timeout_or_oom(
    tmp_path: pathlib.Path,
    termination: str,
) -> None:
    pid_file = tmp_path / "owned-pids.txt"
    oom_trigger = tmp_path / "allocate-memory"
    child_code = (
        "import os,pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()},{child.pid}',encoding='ascii');"
        "trigger=pathlib.Path(sys.argv[2]);"
        "\nwhile not trigger.exists(): time.sleep(0.01)"
        "\nchunks=[]"
        "\nwhile True: chunks.append(bytearray(8*1024*1024))"
    )
    identity = ExecutionIdentity(
        operation_id=uuid.uuid4(),
        tenant_id="tenant-process-supervisor-test",
        owner_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
    )
    limits = ResourceLimits(
        memory_bytes=(96 if termination == "oom" else 256) * 1024 * 1024,
        process_limit=8,
        deadline_seconds=2.0 if termination == "timeout" else 20,
        cpu_rate_percent=50,
        temporary_bytes=16 * 1024 * 1024,
    )
    spec = SandboxLaunchSpec(
        identity=identity,
        role="render",
        executable=pathlib.Path(sys.executable),
        arguments=("-c", child_code, str(pid_file), str(oom_trigger)),
        limits=limits,
        policy=SandboxPolicy(),
    )

    with OperationProcessSupervisor(identity, limits) as supervisor:
        supervisor.launch(spec, cwd=tmp_path)
        evidence_deadline = time.monotonic() + 5
        pid_evidence = ""
        while time.monotonic() < evidence_deadline:
            if pid_file.is_file():
                pid_evidence = pid_file.read_text(encoding="ascii")
                if len(pid_evidence.split(",")) == 2 and all(pid_evidence.split(",")):
                    break
            time.sleep(0.01)
        assert pid_evidence, "owned parent/child did not publish PID evidence"
        parent_pid, child_pid = (int(value) for value in pid_evidence.split(","))
        assert supervisor.active_process_count >= 2

        if termination == "cancel":
            supervisor.cancel()
        elif termination == "oom":
            oom_trigger.touch()
            assert supervisor.wait_for_termination(timeout=10) == "memory_limit_exceeded"
            assert supervisor.memory_limit_exceeded
        else:
            assert supervisor.wait_for_termination(timeout=5) == "deadline_exceeded"
            assert supervisor.deadline_exceeded

        assert supervisor.descendant_zero_verified
        assert supervisor.active_process_count == 0
        assert not _pid_is_active(parent_pid)
        assert not _pid_is_active(child_pid)
