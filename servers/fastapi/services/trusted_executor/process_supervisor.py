"""Operation-scoped OS process ownership for the trusted executor.

Windows launches are suspended, assigned to a Job Object, and only then
resumed.  This closes the usual launch/assignment race in which an unowned
process can create descendants.  Linux support is intentionally capability
only until a delegated cgroup v2 hierarchy is supplied by deployment.
"""

from __future__ import annotations

import ctypes
import os
import pathlib
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO, Literal, Mapping

from services.trusted_executor.contracts import ExecutionIdentity, ResourceLimits, SandboxLaunchSpec


class ProcessSupervisorError(RuntimeError):
    """The process boundary could not preserve its hard isolation contract."""


class ProcessSupervisorUnavailable(ProcessSupervisorError):
    """The current host cannot enforce the requested process boundary."""


class ProcessDeadlineExceeded(ProcessSupervisorError):
    """The immutable operation deadline terminated the process tree."""


class ProcessMemoryLimitExceeded(ProcessSupervisorError):
    """The operation Job exceeded its hard aggregate memory limit."""


ProcessTerminationReason = Literal[
    "cancelled",
    "deadline_exceeded",
    "memory_limit_exceeded",
    "launch_failed",
    "closed",
]


@dataclass(frozen=True, slots=True)
class ProcessSupervisorCapability:
    backend: Literal["windows-job-object", "linux-cgroup-v2", "unsupported"]
    host_support_detected: bool
    enforcement_ready: bool
    reason: str


def probe_process_supervisor(*, cgroup_root: pathlib.Path | None = None) -> ProcessSupervisorCapability:
    """Describe enforceability without weakening an unavailable boundary."""

    if os.name == "nt":
        try:
            ctypes.WinDLL("kernel32", use_last_error=True)
        except OSError as error:
            return ProcessSupervisorCapability(
                backend="windows-job-object",
                host_support_detected=False,
                enforcement_ready=False,
                reason=f"Windows Job Object APIs unavailable: {error.__class__.__name__}",
            )
        return ProcessSupervisorCapability(
            backend="windows-job-object",
            host_support_detected=True,
            enforcement_ready=True,
            reason="Windows Job Object APIs detected",
        )

    if sys.platform.startswith("linux"):
        controllers = pathlib.Path("/sys/fs/cgroup/cgroup.controllers")
        host_support = controllers.is_file()
        if not host_support:
            reason = "cgroup v2 unified hierarchy was not detected"
        elif cgroup_root is None:
            reason = "an explicit delegated cgroup v2 root is required"
        else:
            root = pathlib.Path(cgroup_root)
            required = (root / "cgroup.procs", root / "cgroup.controllers")
            delegated = root.is_dir() and all(path.is_file() for path in required) and os.access(root, os.W_OK)
            reason = (
                "delegated cgroup v2 root detected; launcher integration is not implemented"
                if delegated
                else "the configured cgroup v2 root is not a writable delegated hierarchy"
            )
        return ProcessSupervisorCapability(
            backend="linux-cgroup-v2",
            host_support_detected=host_support,
            enforcement_ready=False,
            reason=reason,
        )

    return ProcessSupervisorCapability(
        backend="unsupported",
        host_support_detected=False,
        enforcement_ready=False,
        reason=f"process supervision is unsupported on {sys.platform}",
    )


if os.name == "nt":
    from ctypes import wintypes

    _CREATE_SUSPENDED = 0x00000004
    _CREATE_UNICODE_ENVIRONMENT = 0x00000400
    _CREATE_NO_WINDOW = 0x08000000
    _EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    _INFINITE = 0xFFFFFFFF
    _STARTF_USESTDHANDLES = 0x00000100
    _STILL_ACTIVE = 259
    _WAIT_OBJECT_0 = 0
    _WAIT_TIMEOUT = 258
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1)

    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x00000001
    _JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x00000004
    _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
    _JOB_OBJECT_ASSOCIATE_COMPLETION_PORT_INFORMATION = 7
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION = 15
    _JOB_OBJECT_MSG_JOB_MEMORY_LIMIT = 10

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _JOBOBJECT_CPU_RATE_CONTROL_INFORMATION(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    class _JOBOBJECT_ASSOCIATE_COMPLETION_PORT(ctypes.Structure):
        _fields_ = [
            ("CompletionKey", ctypes.c_void_p),
            ("CompletionPort", wintypes.HANDLE),
        ]

    class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_STRUCT(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong),
            ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong),
            ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD),
            ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD),
            ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [
            ("StartupInfo", _STARTUPINFOW),
            ("lpAttributeList", ctypes.c_void_p),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.CreateProcessW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(_PROCESS_INFORMATION),
    )
    _kernel32.CreateProcessW.restype = wintypes.BOOL
    _kernel32.ResumeThread.argtypes = (wintypes.HANDLE,)
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateIoCompletionPort.argtypes = (
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.c_size_t,
        wintypes.DWORD,
    )
    _kernel32.CreateIoCompletionPort.restype = wintypes.HANDLE
    _kernel32.GetQueuedCompletionStatus.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.DWORD,
    )
    _kernel32.GetQueuedCompletionStatus.restype = wintypes.BOOL
    _kernel32.InitializeProcThreadAttributeList.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_size_t),
    )
    _kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    _kernel32.UpdateProcThreadAttribute.argtypes = (
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    )
    _kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    _kernel32.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
    _kernel32.DeleteProcThreadAttributeList.restype = None

    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    _windows_inheritable_handle_lock = threading.Lock()


def _windows_error(action: str) -> ProcessSupervisorError:
    return ProcessSupervisorError(f"{action} failed with Windows error {ctypes.get_last_error()}")


class SupervisedProcess:
    """A process handle whose descendants remain owned by its operation Job."""

    def __init__(
        self,
        supervisor: OperationProcessSupervisor,
        pid: int,
        handle: int,
        *,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
        stderr: BinaryIO | None = None,
    ) -> None:
        self._supervisor = supervisor
        self.pid = pid
        self._handle = handle
        self._closed = False
        self.stdin = stdin
        self.stdout = stdout
        self.stderr = stderr

    def poll(self) -> int | None:
        if self._closed:
            raise ProcessSupervisorError("process handle is closed")
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise _windows_error("GetExitCodeProcess")
        return None if code.value == _STILL_ACTIVE else int(code.value)

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        milliseconds = _INFINITE if timeout is None else min(round(timeout * 1000), _INFINITE - 1)
        result = _kernel32.WaitForSingleObject(self._handle, milliseconds)
        if result == _WAIT_TIMEOUT:
            raise TimeoutError(f"process {self.pid} did not exit before the wait timeout")
        if result != _WAIT_OBJECT_0:
            raise _windows_error("WaitForSingleObject")
        code = self.poll()
        if code is None:
            raise ProcessSupervisorError("process signalled without an exit code")
        if self._supervisor.deadline_exceeded:
            raise ProcessDeadlineExceeded("operation process tree exceeded its immutable deadline")
        if self._supervisor.memory_limit_exceeded:
            raise ProcessMemoryLimitExceeded("operation process tree exceeded its hard memory limit")
        return code

    def close(self) -> None:
        if not self._closed:
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass
            _kernel32.CloseHandle(self._handle)
            self._closed = True


class OperationProcessSupervisor:
    """Own every process for one immutable operation identity."""

    def __init__(self, identity: ExecutionIdentity, limits: ResourceLimits) -> None:
        capability = probe_process_supervisor()
        if not capability.enforcement_ready or capability.backend != "windows-job-object":
            raise ProcessSupervisorUnavailable(capability.reason)
        self.identity = identity
        self.limits = limits
        self._lock = threading.RLock()
        self._closed = False
        self._terminated = False
        self._deadline_exceeded = threading.Event()
        self._memory_limit_exceeded = threading.Event()
        self._termination_complete = threading.Event()
        self._descendant_zero_verified = threading.Event()
        self._termination_reason: ProcessTerminationReason | None = None
        self._termination_error: ProcessSupervisorError | None = None
        self._watchdog_stop = threading.Event()
        self._notification_stop = threading.Event()
        self._deadline_at = time.monotonic() + limits.deadline_seconds
        self._processes: list[SupervisedProcess] = []
        self._job = _kernel32.CreateJobObjectW(None, None)
        if not self._job:
            raise _windows_error("CreateJobObjectW")
        self._completion_port = _kernel32.CreateIoCompletionPort(
            _INVALID_HANDLE_VALUE,
            None,
            0,
            1,
        )
        if not self._completion_port:
            _kernel32.CloseHandle(self._job)
            self._job = None
            raise _windows_error("CreateIoCompletionPort")
        try:
            self._configure_job()
        except BaseException:
            _kernel32.CloseHandle(self._completion_port)
            _kernel32.CloseHandle(self._job)
            self._completion_port = None
            self._job = None
            raise
        self._notification_watcher = threading.Thread(
            target=self._monitor_job_notifications,
            name=f"trusted-executor-job-events-{identity.operation_id}",
            daemon=True,
        )
        self._notification_watcher.start()
        self._watchdog = threading.Thread(
            target=self._enforce_deadline,
            name=f"trusted-executor-deadline-{identity.operation_id}",
            daemon=True,
        )
        self._watchdog.start()

    @property
    def deadline_exceeded(self) -> bool:
        return self._deadline_exceeded.is_set()

    @property
    def memory_limit_exceeded(self) -> bool:
        return self._memory_limit_exceeded.is_set()

    @property
    def termination_reason(self) -> ProcessTerminationReason | None:
        with self._lock:
            return self._termination_reason

    @property
    def descendant_zero_verified(self) -> bool:
        return self._descendant_zero_verified.is_set()

    @property
    def active_process_count(self) -> int:
        with self._lock:
            self._require_open()
            info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION_STRUCT()
            if not _kernel32.QueryInformationJobObject(
                self._job,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(info),
                ctypes.sizeof(info),
                None,
            ):
                raise _windows_error("QueryInformationJobObject")
            return int(info.ActiveProcesses)

    def _configure_job(self) -> None:
        association = _JOBOBJECT_ASSOCIATE_COMPLETION_PORT()
        association.CompletionKey = id(self)
        association.CompletionPort = self._completion_port
        if not _kernel32.SetInformationJobObject(
            self._job,
            _JOB_OBJECT_ASSOCIATE_COMPLETION_PORT_INFORMATION,
            ctypes.byref(association),
            ctypes.sizeof(association),
        ):
            raise _windows_error("SetInformationJobObject(completion port)")

        extended = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        extended.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | _JOB_OBJECT_LIMIT_JOB_MEMORY
            | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        extended.BasicLimitInformation.ActiveProcessLimit = self.limits.process_limit
        extended.JobMemoryLimit = self.limits.memory_bytes
        if not _kernel32.SetInformationJobObject(
            self._job,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
        ):
            raise _windows_error("SetInformationJobObject(extended limits)")

        cpu = _JOBOBJECT_CPU_RATE_CONTROL_INFORMATION()
        cpu.ControlFlags = _JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | _JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP
        cpu.CpuRate = self.limits.cpu_rate_percent * 100
        if not _kernel32.SetInformationJobObject(
            self._job,
            _JOB_OBJECT_CPU_RATE_CONTROL_INFORMATION,
            ctypes.byref(cpu),
            ctypes.sizeof(cpu),
        ):
            raise _windows_error("SetInformationJobObject(CPU hard cap)")

    def launch(
        self,
        spec: SandboxLaunchSpec,
        *,
        cwd: pathlib.Path,
        environment: Mapping[str, str] | None = None,
        pipe_stdio: bool = False,
    ) -> SupervisedProcess:
        """Launch suspended, assign to this operation Job, then resume."""

        if spec.identity != self.identity or spec.limits != self.limits:
            raise ProcessSupervisorError("launch spec does not match this operation supervisor")
        working_directory = pathlib.Path(cwd)
        if not working_directory.is_absolute() or not working_directory.is_dir():
            raise ProcessSupervisorError("sandbox working directory must be an existing absolute directory")
        if time.monotonic() >= self._deadline_at:
            self._expire_deadline()
            raise ProcessDeadlineExceeded("operation deadline elapsed before launch")

        child_environment = spec.environment(
            None if environment is None else dict(environment)
        )
        environment_block = "\x00".join(
            f"{key}={value}"
            for key, value in sorted(
                child_environment.items(), key=lambda item: item[0].upper()
            )
        ) + "\x00\x00"
        command_line = subprocess.list2cmdline([str(spec.executable), *spec.arguments])
        command_buffer = ctypes.create_unicode_buffer(command_line)
        environment_buffer = ctypes.create_unicode_buffer(environment_block)
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(startup)
        process_info = _PROCESS_INFORMATION()
        parent_fds: tuple[int, int, int] | None = None
        child_fds: tuple[int, int, int] | None = None
        attribute_list = None
        attribute_buffer = None
        handle_array = None

        if pipe_stdio:
            stdin_read, stdin_write = os.pipe()
            stdout_read, stdout_write = os.pipe()
            stderr_read, stderr_write = os.pipe()
            parent_fds = (stdin_write, stdout_read, stderr_read)
            child_fds = (stdin_read, stdout_write, stderr_write)
            try:
                import msvcrt

                child_handles = tuple(
                    msvcrt.get_osfhandle(descriptor) for descriptor in child_fds
                )
                for handle in child_handles:
                    os.set_handle_inheritable(handle, True)
                for descriptor in parent_fds:
                    os.set_handle_inheritable(
                        msvcrt.get_osfhandle(descriptor), False
                    )
                startup.hStdInput = child_handles[0]
                startup.hStdOutput = child_handles[1]
                startup.hStdError = child_handles[2]
                startup.dwFlags |= _STARTF_USESTDHANDLES

                attribute_size = ctypes.c_size_t()
                _kernel32.InitializeProcThreadAttributeList(
                    None, 1, 0, ctypes.byref(attribute_size)
                )
                attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
                attribute_list = ctypes.cast(attribute_buffer, ctypes.c_void_p)
                if not _kernel32.InitializeProcThreadAttributeList(
                    attribute_list, 1, 0, ctypes.byref(attribute_size)
                ):
                    raise _windows_error("InitializeProcThreadAttributeList")
                handle_array = (wintypes.HANDLE * len(child_handles))(*child_handles)
                if not _kernel32.UpdateProcThreadAttribute(
                    attribute_list,
                    0,
                    _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                    ctypes.cast(handle_array, ctypes.c_void_p),
                    ctypes.sizeof(handle_array),
                    None,
                    None,
                ):
                    raise _windows_error("UpdateProcThreadAttribute(handle list)")
            except BaseException:
                for descriptor in (*parent_fds, *child_fds):
                    try:
                        os.close(descriptor)
                    except OSError:
                        pass
                if attribute_list is not None:
                    _kernel32.DeleteProcThreadAttributeList(attribute_list)
                raise

        startup_pointer: ctypes.c_void_p
        creation_flags = _CREATE_SUSPENDED | _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW
        if pipe_stdio:
            startup_ex = _STARTUPINFOEXW()
            startup_ex.StartupInfo = startup
            startup_ex.StartupInfo.cb = ctypes.sizeof(startup_ex)
            startup_ex.lpAttributeList = attribute_list
            startup_pointer = ctypes.cast(ctypes.byref(startup_ex), ctypes.c_void_p)
            creation_flags |= _EXTENDED_STARTUPINFO_PRESENT
        else:
            startup_pointer = ctypes.cast(ctypes.byref(startup), ctypes.c_void_p)

        launch_lock = _windows_inheritable_handle_lock if pipe_stdio else self._lock
        with launch_lock, self._lock:
            self._require_open()
            created = _kernel32.CreateProcessW(
                str(spec.executable),
                command_buffer,
                None,
                None,
                pipe_stdio,
                creation_flags,
                environment_buffer,
                str(working_directory),
                startup_pointer,
                ctypes.byref(process_info),
            )
            if child_fds is not None:
                for descriptor in child_fds:
                    os.close(descriptor)
                child_fds = None
            if attribute_list is not None:
                _kernel32.DeleteProcThreadAttributeList(attribute_list)
                attribute_list = None
            if not created:
                if parent_fds is not None:
                    for descriptor in parent_fds:
                        os.close(descriptor)
                raise _windows_error("CreateProcessW")
            try:
                if not _kernel32.AssignProcessToJobObject(self._job, process_info.hProcess):
                    raise _windows_error("AssignProcessToJobObject")
                if _kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
                    raise _windows_error("ResumeThread")
                streams: tuple[BinaryIO | None, BinaryIO | None, BinaryIO | None]
                if parent_fds is None:
                    streams = (None, None, None)
                else:
                    streams = (
                        os.fdopen(parent_fds[0], "wb", buffering=0),
                        os.fdopen(parent_fds[1], "rb", buffering=0),
                        os.fdopen(parent_fds[2], "rb", buffering=0),
                    )
                    parent_fds = None
                process = SupervisedProcess(
                    self,
                    int(process_info.dwProcessId),
                    process_info.hProcess,
                    stdin=streams[0],
                    stdout=streams[1],
                    stderr=streams[2],
                )
                self._processes.append(process)
                process_info.hProcess = None
                return process
            except BaseException:
                self._terminate_and_verify(
                    reason="launch_failed",
                    exit_code=1,
                    zero_timeout=5.0,
                    raise_on_failure=False,
                )
                raise
            finally:
                if parent_fds is not None:
                    for descriptor in parent_fds:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                if child_fds is not None:
                    for descriptor in child_fds:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                if attribute_list is not None:
                    _kernel32.DeleteProcThreadAttributeList(attribute_list)
                if process_info.hThread:
                    _kernel32.CloseHandle(process_info.hThread)
                if process_info.hProcess:
                    _kernel32.CloseHandle(process_info.hProcess)

    def cancel(self, *, exit_code: int = 1, zero_timeout: float = 5.0) -> None:
        """Terminate the complete operation tree and prove active process count zero."""

        if not 0 <= exit_code <= 0xFFFFFFFF:
            raise ValueError("exit code must fit an unsigned 32-bit integer")
        self._terminate_and_verify(
            reason="cancelled",
            exit_code=exit_code,
            zero_timeout=zero_timeout,
            raise_on_failure=True,
        )

    def wait_for_termination(self, *, timeout: float) -> ProcessTerminationReason | None:
        """Wait for supervisor-owned termination and return its verified reason."""

        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        if not self._termination_complete.wait(timeout):
            return None
        with self._lock:
            if self._termination_error is not None:
                raise self._termination_error
            return self._termination_reason

    def wait_for_zero(self, *, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        expires = time.monotonic() + timeout
        while True:
            if self.active_process_count == 0:
                return True
            if time.monotonic() >= expires:
                return False
            time.sleep(0.01)

    def _enforce_deadline(self) -> None:
        remaining = max(0.0, self._deadline_at - time.monotonic())
        if not self._watchdog_stop.wait(remaining):
            self._expire_deadline()

    def _monitor_job_notifications(self) -> None:
        while not self._notification_stop.is_set():
            message = wintypes.DWORD()
            completion_key = ctypes.c_size_t()
            overlapped = ctypes.c_void_p()
            received = _kernel32.GetQueuedCompletionStatus(
                self._completion_port,
                ctypes.byref(message),
                ctypes.byref(completion_key),
                ctypes.byref(overlapped),
                100,
            )
            if not received:
                if ctypes.get_last_error() == _WAIT_TIMEOUT:
                    continue
                if self._notification_stop.is_set():
                    return
                continue
            if message.value == _JOB_OBJECT_MSG_JOB_MEMORY_LIMIT:
                with self._lock:
                    if self._closed or self._terminated:
                        return
                    self._memory_limit_exceeded.set()
                self._terminate_and_verify(
                    reason="memory_limit_exceeded",
                    exit_code=0xC0000017,
                    zero_timeout=5.0,
                    raise_on_failure=False,
                )
                return

    def _expire_deadline(self) -> None:
        with self._lock:
            if self._closed or self._terminated:
                return
            self._deadline_exceeded.set()
        self._terminate_and_verify(
            reason="deadline_exceeded",
            exit_code=0xC000013A,
            zero_timeout=5.0,
            raise_on_failure=False,
        )

    def _terminate_and_verify(
        self,
        *,
        reason: ProcessTerminationReason,
        exit_code: int,
        zero_timeout: float,
        raise_on_failure: bool,
    ) -> None:
        error: ProcessSupervisorError | None = None
        with self._lock:
            self._require_open()
            if not self._terminated:
                if not _kernel32.TerminateJobObject(self._job, exit_code):
                    error = _windows_error("TerminateJobObject")
                else:
                    self._terminated = True
                    self._termination_reason = reason
        if error is None and not self.wait_for_zero(timeout=zero_timeout):
            error = ProcessSupervisorError("Job Object still owns active descendants after termination")
        with self._lock:
            if error is None:
                self._descendant_zero_verified.set()
            elif self._termination_error is None:
                self._termination_error = error
            self._termination_complete.set()
        if error is not None and raise_on_failure:
            raise error

    def _require_open(self) -> None:
        if self._closed or not self._job:
            raise ProcessSupervisorError("operation process supervisor is closed")

    def close(self) -> None:
        self._watchdog_stop.set()
        self._notification_stop.set()
        with self._lock:
            if self._closed:
                return
        self._terminate_and_verify(
            reason="closed",
            exit_code=1,
            zero_timeout=5.0,
            raise_on_failure=True,
        )
        if threading.current_thread() is not self._watchdog:
            self._watchdog.join(timeout=1.0)
        if threading.current_thread() is not self._notification_watcher:
            self._notification_watcher.join(timeout=1.0)
        with self._lock:
            if self._closed:
                return
            job = self._job
            completion_port = self._completion_port
            self._job = None
            self._completion_port = None
            self._closed = True
        _kernel32.CloseHandle(job)
        _kernel32.CloseHandle(completion_port)
        for process in self._processes:
            process.close()

    def __enter__(self) -> OperationProcessSupervisor:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = [
    "OperationProcessSupervisor",
    "ProcessDeadlineExceeded",
    "ProcessMemoryLimitExceeded",
    "ProcessSupervisorCapability",
    "ProcessSupervisorError",
    "ProcessSupervisorUnavailable",
    "ProcessTerminationReason",
    "SupervisedProcess",
    "probe_process_supervisor",
]
