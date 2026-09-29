"""Operation-scoped container ownership for the trusted executor (Linux path).

This supervisor exposes the same surface ``OperationProcessSupervisor`` offers
to ``SupervisedStdioWireTransport``
(``launch``/``cancel``/``wait_for_zero``/``wait_for_termination``/``telemetry``/
``close`` plus the deadline and memory flags), but the boundary is a container
created through an injected ``ContainerRuntimeClient``: ``network_mode=none``,
cgroup memory and pids limits from ``ResourceLimits``, a read-only root, a
bounded tmpfs at the operation's temporary root, staged inputs bound
read-only, and the operation ``output`` directory bound writable so the parent
verifies output digests on its own filesystem.  The dispatcher and transport
never learn which backend runs.

Isolation evidence.  After ``create`` the supervisor inspects the container and
maps the runtime's answer onto ``PlatformIsolationReport``.  The container is
started only if that report is production eligible; otherwise it is removed
and ``ProcessSupervisorUnavailable`` is raised.  Neither the request the parent
sent nor any child environment marker is accepted as evidence.

Telemetry.  Job Object notifications have no container analogue, so the
watchdog thread samples cgroup stats (peak memory, pids, CPU) while enforcing
the deadline, and exit classification comes from the runtime: ``State.OOMKilled``
maps to ``ProcessMemoryLimitExceeded``, a deadline kill to
``ProcessDeadlineExceeded``, everything else to the plain exit code.
"""

from __future__ import annotations

import os
import pathlib
import threading
import time
from collections.abc import Mapping

from services.trusted_executor.container_runtime import (
    ContainerHandle,
    ContainerImageSettings,
    ContainerPathMapping,
    ContainerRuntimeClient,
    ContainerRuntimeError,
    build_container_create_request,
)
from services.trusted_executor.container_stdio import AttachDemultiplexer
from services.trusted_executor.container_supervisor_support import (
    CgroupSampleAccumulator,
    ContainerProcess,
    container_stopped,
    int_or_none,
    mapping_or_empty,
    stage_capability_key,
    verify_container_isolation,
)
from services.trusted_executor.contracts import ExecutionIdentity, ResourceLimits, SandboxLaunchSpec
from services.trusted_executor.isolation_policy import PlatformIsolationReport
from services.trusted_executor.process_supervisor import (
    ProcessDeadlineExceeded,
    ProcessSupervisorError,
    ProcessTerminationReason,
)
from constants.timeouts import BUSY_WAIT_POLL_INTERVAL_SECONDS, WATCHER_THREAD_JOIN_SECONDS


class ContainerProcessSupervisor:
    """Own one container for one immutable operation identity."""

    def __init__(
        self,
        identity: ExecutionIdentity,
        limits: ResourceLimits,
        *,
        client: ContainerRuntimeClient,
        settings: ContainerImageSettings,
        mapping: ContainerPathMapping,
        sample_interval_seconds: float = 1.0,
        platform: str = "linux",
    ) -> None:
        if sample_interval_seconds <= 0:
            raise ValueError("sample interval must be positive")
        self.identity = identity
        self.limits = limits
        self._client = client
        self._settings = settings
        self._mapping = mapping
        self._sample_interval = sample_interval_seconds
        self._platform = platform
        self._lock = threading.RLock()
        self._closed = False
        self._terminated = False
        self._exit_code: int | None = None
        self._oom_killed = False
        self._deadline_exceeded = threading.Event()
        self._memory_limit_exceeded = threading.Event()
        self._termination_complete = threading.Event()
        self._descendant_zero_verified = threading.Event()
        self._termination_reason: ProcessTerminationReason | None = None
        self._termination_error: ProcessSupervisorError | None = None
        self._watchdog_stop = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._deadline_at = time.monotonic() + limits.deadline_seconds
        self._handle: ContainerHandle | None = None
        self._process: ContainerProcess | None = None
        self._key_copy: pathlib.Path | None = None
        self._isolation_report: PlatformIsolationReport | None = None
        self._samples = CgroupSampleAccumulator()

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
    def isolation_report(self) -> PlatformIsolationReport | None:
        return self._isolation_report

    @property
    def active_process_count(self) -> int:
        with self._lock:
            self._require_open()
        if container_stopped(self._handle):
            return 0
        pids = int_or_none(mapping_or_empty(self._sample_stats().get("pids_stats")).get("current"))
        return max(1, pids or 0)

    def launch(
        self,
        spec: SandboxLaunchSpec,
        *,
        cwd: pathlib.Path,
        environment: Mapping[str, str] | None = None,
        pipe_stdio: bool = False,
    ) -> ContainerProcess:
        """Create, verify from inspect, attach, then start; never start unverified."""

        if spec.identity != self.identity or spec.limits != self.limits:
            raise ProcessSupervisorError("launch spec does not match this operation supervisor")
        working_directory = pathlib.Path(cwd)
        if not working_directory.is_absolute() or not working_directory.is_dir():
            raise ProcessSupervisorError("sandbox working directory must be an existing absolute directory")
        if time.monotonic() >= self._deadline_at:
            self._expire_deadline()
            raise ProcessDeadlineExceeded("operation deadline elapsed before launch")
        with self._lock:
            self._require_open()
            if self._handle is not None:
                raise ProcessSupervisorError("container supervisor owns one container per operation")
            source = os.environ if environment is None else environment
            self._key_copy = stage_capability_key(spec, source, self._settings)
            request = build_container_create_request(
                spec,
                mapping=self._mapping,
                settings=self._settings,
                capability_key_parent_path=self._key_copy,
                environment=source,
                labels={"presenton.tenant-id": spec.identity.tenant_id},
            )
            try:
                self._handle = self._client.create(request)
            except ContainerRuntimeError as error:
                raise ProcessSupervisorError("container create failed") from error
            try:
                self._isolation_report = verify_container_isolation(
                    spec, self._handle, mapping=self._mapping, platform=self._platform
                )
                demultiplexer = None
                if pipe_stdio:
                    demultiplexer = AttachDemultiplexer(
                        self._handle.attach(),
                        name=f"trusted-executor-attach-{self.identity.operation_id}",
                    )
                self._handle.start()
            except ProcessSupervisorError:
                self._terminate_and_verify(reason="launch_failed", zero_timeout=5.0, raise_on_failure=False)
                raise
            except ContainerRuntimeError as error:
                self._terminate_and_verify(reason="launch_failed", zero_timeout=5.0, raise_on_failure=False)
                raise ProcessSupervisorError("container attach or start failed") from error
            self._process = ContainerProcess(self, self._handle, demultiplexer)
            self._watchdog = threading.Thread(
                target=self._watch,
                name=f"trusted-executor-container-watchdog-{self.identity.operation_id}",
                daemon=True,
            )
            self._watchdog.start()
            return self._process

    def cancel(self, *, exit_code: int = 1, zero_timeout: float = 5.0) -> None:
        if not 0 <= exit_code <= 0xFFFFFFFF:
            raise ValueError("exit code must fit an unsigned 32-bit integer")
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
        """The runtime tears down the whole cgroup once PID 1 exits: stopped == zero."""

        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        expires = time.monotonic() + timeout
        while True:
            if container_stopped(self._handle):
                return True
            if time.monotonic() >= expires:
                return False
            time.sleep(BUSY_WAIT_POLL_INTERVAL_SECONDS)

    def _record_exit(self, code: int) -> None:
        with self._lock:
            self._exit_code = code
            if self._handle is None:
                return
            try:
                state = mapping_or_empty(self._handle.inspect().get("State"))
            except ContainerRuntimeError:
                state = {}
            if state.get("OOMKilled") is True and not self._terminated:
                self._oom_killed = True
                self._memory_limit_exceeded.set()
                self._terminated = True
                self._termination_reason = "memory_limit_exceeded"
                self._descendant_zero_verified.set()
                self._termination_complete.set()

    def _terminate_and_verify(
        self, *, reason: ProcessTerminationReason, zero_timeout: float, raise_on_failure: bool
    ) -> None:
        error: ProcessSupervisorError | None = None
        with self._lock:
            self._require_open()
            if not self._terminated:
                if self._handle is not None and not container_stopped(self._handle):
                    try:
                        self._handle.kill()
                    except ContainerRuntimeError as kill_error:
                        if not container_stopped(self._handle):
                            error = ProcessSupervisorError("container kill failed")
                            error.__cause__ = kill_error
                if error is None:
                    self._terminated = True
                    self._termination_reason = reason
        if error is None and not self.wait_for_zero(timeout=zero_timeout):
            error = ProcessSupervisorError("container still running after termination")
        with self._lock:
            if error is None:
                self._descendant_zero_verified.set()
            elif self._termination_error is None:
                self._termination_error = error
            self._termination_complete.set()
        if error is not None and raise_on_failure:
            raise error

    def _watch(self) -> None:
        while not self._watchdog_stop.is_set():
            remaining = self._deadline_at - time.monotonic()
            if remaining <= 0:
                self._expire_deadline()
                return
            self._sample_stats()
            if self._watchdog_stop.wait(min(self._sample_interval, remaining)):
                return

    def _expire_deadline(self) -> None:
        with self._lock:
            if self._closed or self._terminated:
                return
            self._deadline_exceeded.set()
        self._terminate_and_verify(reason="deadline_exceeded", zero_timeout=5.0, raise_on_failure=False)

    def _sample_stats(self) -> Mapping[str, object]:
        handle = self._handle
        if handle is None:
            return {}
        try:
            stats = handle.stats()
        except ContainerRuntimeError:
            return {}
        with self._lock:
            self._samples.observe(stats)
        return stats

    def telemetry(self) -> dict[str, object]:
        with self._lock:
            self._require_open()
            self._sample_stats()
            return {
                **self._samples.telemetry(
                    stopped=container_stopped(self._handle), report=self._isolation_report
                ),
                "deadlineExceeded": self._deadline_exceeded.is_set(),
                "memoryLimitExceeded": self._memory_limit_exceeded.is_set(),
                "terminationReason": self._termination_reason,
                "containerId": self._handle.id if self._handle is not None else None,
                "containerExitCode": self._exit_code,
                "containerOomKilled": self._oom_killed,
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
            raise ProcessSupervisorError("operation container supervisor is closed")

    def close(self) -> None:
        self._watchdog_stop.set()
        with self._lock:
            if self._closed:
                return
        self._terminate_and_verify(reason="closed", zero_timeout=5.0, raise_on_failure=True)
        if self._watchdog is not None and threading.current_thread() is not self._watchdog:
            self._watchdog.join(timeout=WATCHER_THREAD_JOIN_SECONDS)
        with self._lock:
            if self._closed:
                return
            self._closed = True
            handle, process, key_copy = self._handle, self._process, self._key_copy
            self._handle = self._process = self._key_copy = None
        if process is not None:
            process.close()
        if handle is not None:
            try:
                handle.remove()
            except ContainerRuntimeError:
                pass
        if key_copy is not None:
            key_copy.unlink(missing_ok=True)

    def __enter__(self) -> "ContainerProcessSupervisor":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()


class ContainerSupervisorFactory:
    """Bind runtime client and image settings; callable as ``(identity, limits)``."""

    def __init__(
        self,
        *,
        client: ContainerRuntimeClient,
        settings: ContainerImageSettings,
        mapping: ContainerPathMapping,
        sample_interval_seconds: float = 1.0,
        platform: str = "linux",
    ) -> None:
        self.client = client
        self.settings = settings
        self.mapping = mapping
        self._sample_interval = sample_interval_seconds
        self._platform = platform

    def __call__(self, identity: ExecutionIdentity, limits: ResourceLimits) -> ContainerProcessSupervisor:
        return ContainerProcessSupervisor(
            identity,
            limits,
            client=self.client,
            settings=self.settings,
            mapping=self.mapping,
            sample_interval_seconds=self._sample_interval,
            platform=self._platform,
        )


__all__ = ["ContainerProcessSupervisor", "ContainerSupervisorFactory"]
