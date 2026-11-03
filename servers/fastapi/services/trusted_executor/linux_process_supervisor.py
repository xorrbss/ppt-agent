"""Development-only Linux process-session ownership for the trusted executor.

``PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND=linux-local`` is the Linux counterpart
of ``windows-local``: the worker runs on the controller host, and the only
boundary is operation ownership of its process tree.  The Windows backend gets
that from a Job Object; Linux has no unprivileged equivalent, so this
supervisor:

* launches the worker as the leader of a new session (``setsid``), so every
  ordinary descendant shares that session id;
* samples ``/proc`` and tracks the session's members plus any process whose
  parent chain reaches a tracked process;
* enforces the operation deadline, an aggregate resident-memory ceiling and a
  process-count ceiling by killing every tracked process, and
* proves the tracked tree reached zero before reporting success.

Limits are enforced by sampling, so they are reactive rather than hard, and a
descendant that both leaves the session and outlives its parent between two
samples escapes ownership.  This is why the matching isolation report
(``linux_local_development_isolation_report``) is never production eligible
and only the explicit development policy may use it.  Production Linux uses
the ``container`` backend.
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import BinaryIO, Mapping

from constants.timeouts import BUSY_WAIT_POLL_INTERVAL_SECONDS, WATCHER_THREAD_JOIN_SECONDS
from services.trusted_executor.contracts import ExecutionIdentity, ResourceLimits, SandboxLaunchSpec
from services.trusted_executor.process_supervisor import (
    ProcessDeadlineExceeded,
    ProcessMemoryLimitExceeded,
    ProcessSupervisorCapability,
    ProcessSupervisorError,
    ProcessSupervisorUnavailable,
    ProcessTerminationReason,
)

LINUX_SESSION_SUPERVISOR_BACKEND = "linux-process-session"
ENFORCEMENT_LAYER = "linux-process-session-sampled"
SAMPLE_INTERVAL_SECONDS = 0.1
_PROC = pathlib.Path("/proc")


class ProcessCountLimitExceeded(ProcessSupervisorError):
    """The operation tree exceeded its process-count ceiling and was killed."""


@dataclass(frozen=True, slots=True)
class _ProcStat:
    pid: int
    state: str
    ppid: int
    session: int
    cpu_ticks: int
    rss_pages: int
    start_ticks: int


def _read_stat(pid: int) -> _ProcStat | None:
    try:
        raw = (_PROC / str(pid) / "stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None
    # ``comm`` is parenthesised and may itself contain spaces or parentheses.
    head, _, tail = raw.rpartition(")")
    if not head:
        return None
    fields = tail.split()
    try:
        return _ProcStat(
            pid=pid,
            state=fields[0],
            ppid=int(fields[1]),
            session=int(fields[3]),
            cpu_ticks=int(fields[11]) + int(fields[12]),
            rss_pages=int(fields[21]),
            start_ticks=int(fields[19]),
        )
    except (IndexError, ValueError):
        return None


def _all_stats() -> dict[int, _ProcStat]:
    stats: dict[int, _ProcStat] = {}
    for entry in _PROC.iterdir():
        if entry.name.isdigit():
            stat = _read_stat(int(entry.name))
            if stat is not None:
                stats[stat.pid] = stat
    return stats


def probe_linux_session_supervisor() -> ProcessSupervisorCapability:
    if not sys.platform.startswith("linux"):
        return ProcessSupervisorCapability(
            backend="unsupported",
            host_support_detected=False,
            enforcement_ready=False,
            reason=f"linux process-session supervision is unsupported on {sys.platform}",
        )
    if _read_stat(os.getpid()) is None:
        return ProcessSupervisorCapability(
            backend=LINUX_SESSION_SUPERVISOR_BACKEND,
            host_support_detected=False,
            enforcement_ready=False,
            reason="/proc process accounting is not readable",
        )
    return ProcessSupervisorCapability(
        backend=LINUX_SESSION_SUPERVISOR_BACKEND,
        host_support_detected=True,
        enforcement_ready=True,
        reason=(
            "process-session ownership with sampled deadline, RSS and process-count "
            "ceilings; no namespace, identity or filesystem isolation is provided"
        ),
    )


class LinuxSupervisedProcess:
    """The session leader; its descendants are owned by the supervisor."""

    def __init__(self, supervisor: LinuxSessionProcessSupervisor, popen: subprocess.Popen) -> None:
        self._supervisor = supervisor
        self._popen = popen
        self.pid = popen.pid
        self.stdin: BinaryIO | None = popen.stdin
        self.stdout: BinaryIO | None = popen.stdout
        self.stderr: BinaryIO | None = popen.stderr
        self._closed = False

    def poll(self) -> int | None:
        if self._closed:
            raise ProcessSupervisorError("process handle is closed")
        return self._popen.poll()

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        try:
            code = self._popen.wait(timeout)
        except subprocess.TimeoutExpired as error:
            raise TimeoutError(f"process {self.pid} did not exit before the wait timeout") from error
        self._supervisor.raise_for_limit_breach()
        return code

    def close(self) -> None:
        if self._closed:
            return
        for stream in (self.stdin, self.stdout, self.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        self._closed = True


class LinuxSessionProcessSupervisor:
    """Own every process for one operation identity through its process session."""

    def __init__(self, identity: ExecutionIdentity, limits: ResourceLimits) -> None:
        capability = probe_linux_session_supervisor()
        if not capability.enforcement_ready:
            raise ProcessSupervisorUnavailable(capability.reason)
        self.identity = identity
        self.limits = limits
        self._lock = threading.RLock()
        self._closed = False
        self._terminated = False
        self._deadline_exceeded = threading.Event()
        self._memory_limit_exceeded = threading.Event()
        self._process_limit_exceeded = threading.Event()
        self._termination_complete = threading.Event()
        self._descendant_zero_verified = threading.Event()
        self._termination_reason: ProcessTerminationReason | None = None
        self._termination_error: ProcessSupervisorError | None = None
        self._stop = threading.Event()
        self._deadline_at = time.monotonic() + limits.deadline_seconds
        self._processes: list[LinuxSupervisedProcess] = []
        self._session: int | None = None
        # Every process ever owned, keyed by (pid, start time) so a recycled
        # pid is never mistaken for an owned one -> last cumulative CPU ticks.
        self._seen_cpu_ticks: dict[tuple[int, int], int] = {}
        self._tracked: dict[int, int] = {}
        self._peak_rss_bytes = 0
        self._peak_process_count = 0
        self._killed_count = 0
        self._page_size = os.sysconf("SC_PAGE_SIZE")
        self._clock_ticks = os.sysconf("SC_CLK_TCK")
        self._sampler = threading.Thread(
            target=self._sample_loop,
            name=f"trusted-executor-linux-session-{identity.operation_id}",
            daemon=True,
        )
        self._sampler.start()

    # -- state -----------------------------------------------------------

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
            return len(self._refresh_members())

    def raise_for_limit_breach(self) -> None:
        if self._deadline_exceeded.is_set():
            raise ProcessDeadlineExceeded("operation process tree exceeded its immutable deadline")
        if self._memory_limit_exceeded.is_set():
            raise ProcessMemoryLimitExceeded("operation process tree exceeded its memory ceiling")
        if self._process_limit_exceeded.is_set():
            raise ProcessCountLimitExceeded("operation process tree exceeded its process-count ceiling")

    # -- membership ------------------------------------------------------

    def _refresh_members(self) -> dict[int, _ProcStat]:
        """Return live (non-zombie) owned processes and update accounting."""

        with self._lock:
            if self._session is None:
                return {}
            stats = _all_stats()
            owned = {pid for pid, stat in stats.items() if stat.session == self._session}
            owned |= {pid for pid, start in self._tracked.items()
                      if pid in stats and stats[pid].start_ticks == start}
            # Follow parent links so a descendant that called setsid() while
            # its parent is still tracked stays owned.
            changed = True
            while changed:
                changed = False
                for pid, stat in stats.items():
                    if pid not in owned and stat.ppid in owned:
                        owned.add(pid)
                        changed = True
            self._tracked = {pid: stats[pid].start_ticks for pid in owned}
            live = {pid: stats[pid] for pid in owned if stats[pid].state != "Z"}
            for pid, stat in live.items():
                key = (pid, stat.start_ticks)
                self._seen_cpu_ticks[key] = max(self._seen_cpu_ticks.get(key, 0), stat.cpu_ticks)
            rss = sum(stat.rss_pages for stat in live.values()) * self._page_size
            self._peak_rss_bytes = max(self._peak_rss_bytes, rss)
            self._peak_process_count = max(self._peak_process_count, len(live))
            return live

    def _sample_loop(self) -> None:
        while not self._stop.wait(SAMPLE_INTERVAL_SECONDS):
            if time.monotonic() >= self._deadline_at:
                self._expire(self._deadline_exceeded, "deadline_exceeded")
                return
            try:
                with self._lock:
                    if self._closed or self._terminated:
                        continue
                    live = self._refresh_members()
                    rss = sum(stat.rss_pages for stat in live.values()) * self._page_size
            except ProcessSupervisorError:
                continue
            if rss > self.limits.memory_bytes:
                self._expire(self._memory_limit_exceeded, "memory_limit_exceeded")
                return
            if len(live) > self.limits.process_limit:
                self._expire(self._process_limit_exceeded, "cancelled")
                return

    def _expire(self, flag: threading.Event, reason: ProcessTerminationReason) -> None:
        with self._lock:
            if self._closed or self._terminated:
                return
            flag.set()
        self._terminate_and_verify(reason=reason, zero_timeout=5.0, raise_on_failure=False)

    # -- lifecycle -------------------------------------------------------

    def launch(
        self,
        spec: SandboxLaunchSpec,
        *,
        cwd: pathlib.Path,
        environment: Mapping[str, str] | None = None,
        pipe_stdio: bool = False,
    ) -> LinuxSupervisedProcess:
        """Start the worker as a new session leader owned by this operation."""

        if spec.identity != self.identity or spec.limits != self.limits:
            raise ProcessSupervisorError("launch spec does not match this operation supervisor")
        working_directory = pathlib.Path(cwd)
        if not working_directory.is_absolute() or not working_directory.is_dir():
            raise ProcessSupervisorError("sandbox working directory must be an existing absolute directory")
        if time.monotonic() >= self._deadline_at:
            self._expire(self._deadline_exceeded, "deadline_exceeded")
            raise ProcessDeadlineExceeded("operation deadline elapsed before launch")
        child_environment = spec.environment(None if environment is None else dict(environment))
        stdio = subprocess.PIPE if pipe_stdio else subprocess.DEVNULL
        with self._lock:
            self._require_open()
            if self._session is not None:
                raise ProcessSupervisorError("an operation supervisor owns exactly one session")
            try:
                popen = subprocess.Popen(  # noqa: S603 - argv is the admitted worker command
                    [str(spec.executable), *spec.arguments],
                    cwd=str(working_directory),
                    env=child_environment,
                    stdin=stdio,
                    stdout=stdio,
                    stderr=stdio,
                    bufsize=0,
                    close_fds=True,
                    start_new_session=True,
                )
            except OSError as error:
                raise ProcessSupervisorError(f"worker launch failed: {error.__class__.__name__}") from error
            # start_new_session makes the child the leader: session id == pid.
            self._session = popen.pid
            leader = _read_stat(popen.pid)
            if leader is not None:
                self._tracked = {popen.pid: leader.start_ticks}
            # Account for the leader even if it exits before the first sample.
            self._refresh_members()
            process = LinuxSupervisedProcess(self, popen)
            self._processes.append(process)
            return process

    def cancel(self, *, exit_code: int = 1, zero_timeout: float = 5.0) -> None:
        """Kill the complete operation tree and prove the owned count is zero."""

        del exit_code  # Signals carry no caller-chosen exit code on Linux.
        self._terminate_and_verify(reason="cancelled", zero_timeout=zero_timeout, raise_on_failure=True)

    def wait_for_termination(self, *, timeout: float) -> ProcessTerminationReason | None:
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
            self._reap_leaders()
            if self.active_process_count == 0:
                return True
            if time.monotonic() >= expires:
                return False
            time.sleep(BUSY_WAIT_POLL_INTERVAL_SECONDS)

    def _reap_leaders(self) -> None:
        for process in self._processes:
            try:
                process._popen.poll()
            except OSError:
                pass

    def _kill_owned(self) -> None:
        live = self._refresh_members()
        for pid in live:
            try:
                os.kill(pid, signal.SIGKILL)
                self._killed_count += 1
            except ProcessLookupError:
                pass
        # No killpg(): every owned process is signalled individually above,
        # and a group kill keyed on a possibly recycled leader pid could reach
        # an unrelated process group.

    def _terminate_and_verify(
        self,
        *,
        reason: ProcessTerminationReason,
        zero_timeout: float,
        raise_on_failure: bool,
    ) -> None:
        error: ProcessSupervisorError | None = None
        with self._lock:
            self._require_open()
            if not self._terminated:
                self._kill_owned()
                self._terminated = True
                self._termination_reason = reason
        expires = time.monotonic() + zero_timeout
        while True:
            self._reap_leaders()
            with self._lock:
                remaining = self._refresh_members()
                if remaining:
                    # Anything forked after the first kill is killed again.
                    self._kill_owned()
            if not remaining:
                break
            if time.monotonic() >= expires:
                error = ProcessSupervisorError("the operation process session still owns live processes")
                break
            time.sleep(BUSY_WAIT_POLL_INTERVAL_SECONDS)
        with self._lock:
            if error is None:
                self._descendant_zero_verified.set()
            elif self._termination_error is None:
                self._termination_error = error
            self._termination_complete.set()
        if error is not None and raise_on_failure:
            raise error

    def telemetry(self) -> dict[str, object]:
        with self._lock:
            self._require_open()
            live = self._refresh_members()
            cpu_millis = sum(self._seen_cpu_ticks.values()) * 1000 // max(1, self._clock_ticks)
            return {
                "peakPrivateBytes": self._peak_rss_bytes,
                "peakProcessCount": self._peak_process_count,
                "peakCpuMillis": cpu_millis,
                "totalProcessCount": len(self._seen_cpu_ticks),
                "terminatedProcessCount": self._killed_count,
                "finalProcessCount": len(live),
                "resourceLimitsEnforced": True,
                "enforcementLayer": ENFORCEMENT_LAYER,
                "recycledWithinBaseline": not live,
                "deadlineExceeded": self._deadline_exceeded.is_set(),
                "memoryLimitExceeded": self._memory_limit_exceeded.is_set(),
                "terminationReason": self._termination_reason,
                "limits": {
                    "memoryBytes": self.limits.memory_bytes,
                    "pidCount": self.limits.process_limit,
                    "wallClockSeconds": self.limits.deadline_seconds,
                    "cpuRatePercent": self.limits.cpu_rate_percent,
                    "temporaryBytes": self.limits.temporary_bytes,
                },
            }

    def _require_open(self) -> None:
        if self._closed:
            raise ProcessSupervisorError("operation process supervisor is closed")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
        self._terminate_and_verify(reason="closed", zero_timeout=5.0, raise_on_failure=True)
        self._stop.set()
        if threading.current_thread() is not self._sampler:
            self._sampler.join(timeout=WATCHER_THREAD_JOIN_SECONDS)
        with self._lock:
            self._closed = True
        for process in self._processes:
            process.close()

    def __enter__(self) -> LinuxSessionProcessSupervisor:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


__all__ = [
    "ENFORCEMENT_LAYER",
    "LINUX_SESSION_SUPERVISOR_BACKEND",
    "LinuxSessionProcessSupervisor",
    "LinuxSupervisedProcess",
    "ProcessCountLimitExceeded",
    "probe_linux_session_supervisor",
]
