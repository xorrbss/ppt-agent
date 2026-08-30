"""OS-enforced limits and content-free telemetry for one-shot public workers."""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import signal
import time
from typing import Any, Mapping


LIMIT_ENV = {
    "memory": "PPT_WORKER_MEMORY_LIMIT_BYTES",
    "cpu": "PPT_WORKER_CPU_MILLIS",
    "pids": "PPT_WORKER_PID_LIMIT",
    "openFiles": "PPT_WORKER_OPEN_FILE_LIMIT",
    "outputBytes": "PPT_WORKER_OUTPUT_LIMIT_BYTES",
    "wallClock": "PPT_WORKER_WALL_CLOCK_SECONDS",
}

SUPERVISOR_POLL_INTERVAL_MILLIS = 50
WINDOWS_CONTROL_ENFORCEMENT_METHODS = {
    "memory": "windows-job-object",
    "cpu": "windows-job-object",
    "pids": "windows-job-object",
    "openFiles": "high-frequency-supervisor+job-termination",
    "outputBytes": "worker-precommit-size+parent-postread/job-termination",
    "wallClock": "monotonic-supervisor+job-termination",
}
WINDOWS_CONTROL_RESIDUAL_RISKS = {
    "openFiles": (
        "A transient handle burst can occur between 50 ms supervisor samples; "
        "the next sample terminates the complete Job Object."
    ),
}
POSIX_CONTROL_ENFORCEMENT_METHODS = {
    "memory": "posix-rlimit-as",
    "cpu": "posix-rlimit-cpu",
    "pids": "posix-rlimit-nproc",
    "openFiles": "posix-rlimit-nofile",
    "outputBytes": "posix-rlimit-fsize+worker-precommit-size+parent-postread",
    "wallClock": "monotonic-supervisor+process-group-termination",
}
POSIX_CONTROL_RESIDUAL_RISKS: dict[str, str] = {}


class WorkerResourceBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkerResourceLimits:
    memory_bytes: int
    cpu_millis: int
    pid_limit: int
    open_file_limit: int
    output_bytes: int
    wall_clock_seconds: int

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "WorkerResourceLimits":
        values: dict[str, int] = {}
        for name, variable in LIMIT_ENV.items():
            try:
                value = int(environment.get(variable, ""))
            except ValueError as error:
                raise WorkerResourceBoundaryError(f"public_worker_resource_limit_invalid:{name}") from error
            if value <= 0:
                raise WorkerResourceBoundaryError(f"public_worker_resource_limit_invalid:{name}")
            values[name] = value
        return cls(
            memory_bytes=values["memory"],
            cpu_millis=values["cpu"],
            pid_limit=values["pids"],
            open_file_limit=values["openFiles"],
            output_bytes=values["outputBytes"],
            wall_clock_seconds=values["wallClock"],
        )

    def public_dict(self) -> dict[str, int]:
        return {
            "memoryBytes": self.memory_bytes,
            "cpuMillis": self.cpu_millis,
            "pidCount": self.pid_limit,
            "openFiles": self.open_file_limit,
            "outputBytes": self.output_bytes,
            "wallClockSeconds": self.wall_clock_seconds,
        }


class WorkerResourceBoundary:
    enforcement_layer = "unavailable"

    def __init__(self, limits: WorkerResourceLimits) -> None:
        self.limits = limits
        self.peak_rss_bytes = 0
        self.peak_private_bytes = 0
        self.peak_handles = 0
        self.peak_process_count = 0
        self.peak_cpu_millis = 0
        self._pid: int | None = None

    @property
    def creation_kwargs(self) -> dict[str, Any]:
        raise NotImplementedError

    def attach(self, pid: int) -> None:
        self._pid = pid

    def resume(self) -> None:
        """Resume a child after an enforcement boundary owns it."""

        return

    def sample(self) -> None:
        raise NotImplementedError

    def terminate(self) -> None:
        if self._pid is None:
            return
        try:
            if os.name == "nt":
                handle = _kernel32.OpenProcess(0x0001, False, self._pid)
                if handle:
                    _kernel32.TerminateProcess(handle, 1)
                    _kernel32.CloseHandle(handle)
            else:
                os.killpg(self._pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    def close(self) -> None:
        return

    def telemetry(self) -> dict[str, Any]:
        try:
            self.sample()
        except WorkerResourceBoundaryError:
            # A limit-triggered termination is itself part of the evidence.
            # Retain the last sampled peaks and report cleanup independently.
            pass
        recovered = self._active_process_count() == 0
        methods = (
            WINDOWS_CONTROL_ENFORCEMENT_METHODS
            if self.enforcement_layer == "windows-job-object"
            else POSIX_CONTROL_ENFORCEMENT_METHODS
            if self.enforcement_layer == "posix-rlimit"
            else {}
        )
        residual_risks = (
            WINDOWS_CONTROL_RESIDUAL_RISKS
            if self.enforcement_layer == "windows-job-object"
            else POSIX_CONTROL_RESIDUAL_RISKS
            if self.enforcement_layer == "posix-rlimit"
            else {"enforcement": "No supported resource enforcement layer is active."}
        )
        return {
            "schemaVersion": "public-worker-resource-telemetry/v1",
            "enforcementLayer": self.enforcement_layer,
            "resourceLimitsEnforced": self.enforcement_layer in {
                "windows-job-object", "posix-rlimit"
            }
            and set(methods) == set(LIMIT_ENV),
            "controlEnforcementMethods": dict(methods),
            "residualRisks": dict(residual_risks),
            "supervisorPollIntervalMillis": SUPERVISOR_POLL_INTERVAL_MILLIS,
            "limits": self.limits.public_dict(),
            "peakRssBytes": self.peak_rss_bytes,
            "peakPrivateBytes": self.peak_private_bytes,
            "peakHandleCount": self.peak_handles,
            "peakProcessCount": self.peak_process_count,
            "peakCpuMillis": self.peak_cpu_millis,
            "recycledWithinBaseline": recovered,
            "finalProcessCount": self._active_process_count(),
        }

    def terminate_and_verify(self, *, timeout_seconds: float = 5.0) -> dict[str, Any]:
        """Kill the complete owned boundary and prove it returned to zero."""

        self.terminate()
        deadline = time.monotonic() + timeout_seconds
        while self._active_process_count() and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.telemetry()

    def _active_process_count(self) -> int:
        raise NotImplementedError


if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = wintypes.WPARAM
    JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010
    THREAD_SUSPEND_RESUME = 0x0002

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ULONG_PTR),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
            ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
            ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ULONG_PTR),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ThreadID", wintypes.DWORD), ("th32OwnerProcessID", wintypes.DWORD),
            ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
        ]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _psapi = ctypes.WinDLL("psapi", use_last_error=True)
    _kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    _kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    _kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD
    ]
    _kernel32.SetInformationJobObject.restype = wintypes.BOOL
    _kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenProcess.restype = wintypes.HANDLE
    _kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL
    _kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    _kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    _kernel32.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    _kernel32.Thread32First.restype = wintypes.BOOL
    _kernel32.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(THREADENTRY32)]
    _kernel32.Thread32Next.restype = wintypes.BOOL
    _kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel32.OpenThread.restype = wintypes.HANDLE
    _kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    _kernel32.ResumeThread.restype = wintypes.DWORD
    _kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    _kernel32.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)
    ]
    _kernel32.GetProcessHandleCount.restype = wintypes.BOOL
    _kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
    ]
    _kernel32.GetProcessTimes.restype = wintypes.BOOL
    _kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateJobObject.restype = wintypes.BOOL
    _kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    _kernel32.TerminateProcess.restype = wintypes.BOOL
    _psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD
    ]
    _psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

    class WindowsJobBoundary(WorkerResourceBoundary):
        enforcement_layer = "windows-job-object"

        def __init__(self, limits: WorkerResourceLimits) -> None:
            super().__init__(limits)
            self._job = _kernel32.CreateJobObjectW(None, None)
            if not self._job:
                raise WorkerResourceBoundaryError("public_worker_job_object_unavailable")
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = (
                JOB_OBJECT_LIMIT_JOB_TIME | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
                | JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            info.BasicLimitInformation.PerJobUserTimeLimit = limits.cpu_millis * 10_000
            info.BasicLimitInformation.ActiveProcessLimit = limits.pid_limit
            info.JobMemoryLimit = limits.memory_bytes
            if not _kernel32.SetInformationJobObject(
                self._job, 9, ctypes.byref(info), ctypes.sizeof(info)
            ):
                self.close()
                raise WorkerResourceBoundaryError("public_worker_job_limits_unavailable")

        @property
        def creation_kwargs(self) -> dict[str, Any]:
            import subprocess
            flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_SUSPENDED", 0x00000004)
                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
            )
            return {"creationflags": flags}

        def attach(self, pid: int) -> None:
            super().attach(pid)
            process = _kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ | 0x0100 | 0x0001,
                False,
                pid,
            )
            if not process:
                raise WorkerResourceBoundaryError("public_worker_process_handle_unavailable")
            try:
                if not _kernel32.AssignProcessToJobObject(self._job, process):
                    error = ctypes.get_last_error()
                    raise WorkerResourceBoundaryError(
                        f"public_worker_job_assignment_failed:{error}"
                    )
            finally:
                _kernel32.CloseHandle(process)

        def resume(self) -> None:
            if self._pid is None:
                raise WorkerResourceBoundaryError("public_worker_resume_before_attach")
            snapshot = _kernel32.CreateToolhelp32Snapshot(0x00000004, 0)
            if snapshot in (0, ctypes.c_void_p(-1).value):
                raise WorkerResourceBoundaryError("public_worker_thread_snapshot_unavailable")
            resumed = 0
            try:
                entry = THREADENTRY32()
                entry.dwSize = ctypes.sizeof(entry)
                current = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
                while current:
                    if int(entry.th32OwnerProcessID) == self._pid:
                        thread = _kernel32.OpenThread(
                            THREAD_SUSPEND_RESUME, False, entry.th32ThreadID
                        )
                        if thread:
                            try:
                                previous = _kernel32.ResumeThread(thread)
                                if previous != 0xFFFFFFFF:
                                    resumed += 1
                            finally:
                                _kernel32.CloseHandle(thread)
                    current = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
            finally:
                _kernel32.CloseHandle(snapshot)
            if resumed != 1:
                raise WorkerResourceBoundaryError(
                    f"public_worker_primary_thread_resume_failed:{resumed}"
                )

        def _accounting(self) -> JOBOBJECT_BASIC_ACCOUNTING_INFORMATION:
            value = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
            if not _kernel32.QueryInformationJobObject(
                self._job, 1, ctypes.byref(value), ctypes.sizeof(value), None
            ):
                raise WorkerResourceBoundaryError("public_worker_job_accounting_unavailable")
            return value

        def _process_ids(self) -> list[int]:
            capacity = max(8, self.limits.pid_limit + 2)
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ULONG_PTR))
            returned = wintypes.DWORD()
            if not _kernel32.QueryInformationJobObject(
                self._job, 3, buffer, len(buffer), ctypes.byref(returned)
            ):
                raise WorkerResourceBoundaryError(
                    f"public_worker_job_process_list_unavailable:{ctypes.get_last_error()}"
                )
            assigned = ctypes.c_uint32.from_buffer(buffer, 0).value
            count = ctypes.c_uint32.from_buffer(buffer, 4).value
            if assigned > capacity or count > capacity:
                raise WorkerResourceBoundaryError(
                    f"public_worker_job_process_list_insufficient:{assigned}:{count}:{capacity}"
                )
            array_type = ULONG_PTR * capacity
            ids = array_type.from_buffer(buffer, 8)
            return [int(ids[index]) for index in range(count) if ids[index]]

        def sample(self) -> None:
            extended = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            if not _kernel32.QueryInformationJobObject(
                self._job, 9, ctypes.byref(extended), ctypes.sizeof(extended), None
            ):
                raise WorkerResourceBoundaryError(
                    f"public_worker_job_extended_accounting_unavailable:{ctypes.get_last_error()}"
                )
            self.peak_private_bytes = max(self.peak_private_bytes, int(extended.PeakJobMemoryUsed))
            rss = private = handles = cpu_millis = 0
            ids = self._process_ids()
            for pid in ids:
                process = _kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_VM_READ | 0x0400, False, pid
                )
                if not process:
                    continue
                try:
                    memory = PROCESS_MEMORY_COUNTERS_EX()
                    memory.cb = ctypes.sizeof(memory)
                    if _psapi.GetProcessMemoryInfo(
                        process, ctypes.byref(memory), ctypes.sizeof(memory)
                    ):
                        rss += int(memory.WorkingSetSize)
                        private += int(memory.PrivateUsage)
                    count = wintypes.DWORD()
                    if _kernel32.GetProcessHandleCount(process, ctypes.byref(count)):
                        handles += int(count.value)
                    creation = wintypes.FILETIME()
                    exit_time = wintypes.FILETIME()
                    kernel = wintypes.FILETIME()
                    user = wintypes.FILETIME()
                    if _kernel32.GetProcessTimes(
                        process,
                        ctypes.byref(creation), ctypes.byref(exit_time),
                        ctypes.byref(kernel), ctypes.byref(user),
                    ):
                        kernel_ticks = (int(kernel.dwHighDateTime) << 32) | int(kernel.dwLowDateTime)
                        user_ticks = (int(user.dwHighDateTime) << 32) | int(user.dwLowDateTime)
                        cpu_millis += int((kernel_ticks + user_ticks) / 10_000)
                finally:
                    _kernel32.CloseHandle(process)
            accounting = self._accounting()
            self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
            self.peak_private_bytes = max(self.peak_private_bytes, private)
            self.peak_handles = max(self.peak_handles, handles)
            self.peak_process_count = max(self.peak_process_count, int(accounting.ActiveProcesses))
            job_cpu = int((accounting.TotalUserTime + accounting.TotalKernelTime) / 10_000)
            self.peak_cpu_millis = max(self.peak_cpu_millis, job_cpu, cpu_millis)
            if (
                handles > self.limits.open_file_limit
                or len(ids) > self.limits.pid_limit
                or private > self.limits.memory_bytes
                or cpu_millis > self.limits.cpu_millis
            ):
                self.terminate()
                raise WorkerResourceBoundaryError("public_worker_resource_limit_exceeded")

        def _active_process_count(self) -> int:
            return int(self._accounting().ActiveProcesses)

        def terminate(self) -> None:
            if self._job:
                _kernel32.TerminateJobObject(self._job, 1)

        def close(self) -> None:
            if getattr(self, "_job", None):
                _kernel32.CloseHandle(self._job)
                self._job = None


class PosixRlimitBoundary(WorkerResourceBoundary):
    enforcement_layer = "posix-rlimit"

    def __init__(self, limits: WorkerResourceLimits) -> None:
        if not Path("/proc/self/status").exists():
            raise WorkerResourceBoundaryError("public_worker_procfs_required")
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise WorkerResourceBoundaryError("public_worker_non_root_required")
        super().__init__(limits)
        self._seen_pids: set[int] = set()

    @property
    def creation_kwargs(self) -> dict[str, Any]:
        import resource

        limits = self.limits

        def apply_limits() -> None:
            os.setsid()
            resource.setrlimit(resource.RLIMIT_AS, (limits.memory_bytes, limits.memory_bytes))
            cpu_seconds = max(1, math.ceil(limits.cpu_millis / 1000))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            resource.setrlimit(resource.RLIMIT_NPROC, (limits.pid_limit, limits.pid_limit))
            resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_file_limit, limits.open_file_limit))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.output_bytes, limits.output_bytes))

        return {"preexec_fn": apply_limits}

    def _tree(self) -> list[int]:
        if self._pid is None:
            return []
        selected = {self._pid}
        changed = True
        while changed:
            changed = False
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit() or int(entry.name) in selected:
                    continue
                try:
                    parent = int((entry / "stat").read_text().split()[3])
                except (FileNotFoundError, PermissionError, ValueError, IndexError):
                    continue
                if parent in selected:
                    selected.add(int(entry.name))
                    changed = True
        return [pid for pid in selected if (Path("/proc") / str(pid)).exists()]

    def sample(self) -> None:
        rss = private = handles = cpu_ticks = 0
        pids = self._tree()
        self._seen_pids.update(pids)
        for pid in pids:
            root = Path("/proc") / str(pid)
            try:
                values = {}
                for line in (root / "status").read_text().splitlines():
                    key, _, rest = line.partition(":")
                    values[key] = rest.strip().split()[0]
                rss += int(values.get("VmRSS", 0)) * 1024
                private += int(values.get("VmSize", 0)) * 1024
                handles += len(list((root / "fd").iterdir()))
                stat = (root / "stat").read_text().split()
                cpu_ticks += int(stat[13]) + int(stat[14])
            except (FileNotFoundError, PermissionError, ValueError, IndexError):
                continue
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
        self.peak_private_bytes = max(self.peak_private_bytes, private)
        self.peak_handles = max(self.peak_handles, handles)
        self.peak_process_count = max(self.peak_process_count, len(pids))
        self.peak_cpu_millis = max(self.peak_cpu_millis, int(cpu_ticks * 1000 / os.sysconf("SC_CLK_TCK")))
        if len(pids) > self.limits.pid_limit or handles > self.limits.open_file_limit:
            self.terminate()
            raise WorkerResourceBoundaryError("public_worker_resource_limit_exceeded")

    def _active_process_count(self) -> int:
        self._seen_pids.update(self._tree())
        return sum((Path("/proc") / str(pid)).exists() for pid in self._seen_pids)


def create_worker_resource_boundary(limits: WorkerResourceLimits) -> WorkerResourceBoundary:
    if os.name == "nt":
        return WindowsJobBoundary(limits)
    if os.name == "posix":
        return PosixRlimitBoundary(limits)
    raise WorkerResourceBoundaryError("public_worker_resource_enforcement_unavailable")


__all__ = [
    "LIMIT_ENV",
    "WorkerResourceBoundary",
    "WorkerResourceBoundaryError",
    "WorkerResourceLimits",
    "SUPERVISOR_POLL_INTERVAL_MILLIS",
    "WINDOWS_CONTROL_ENFORCEMENT_METHODS",
    "WINDOWS_CONTROL_RESIDUAL_RISKS",
    "POSIX_CONTROL_ENFORCEMENT_METHODS",
    "POSIX_CONTROL_RESIDUAL_RISKS",
    "create_worker_resource_boundary",
]
