"""Helpers behind ``ContainerProcessSupervisor``: process facade, per-job key
staging, per-job isolation verification, and cgroup telemetry accumulation.

Capability key.  ``PUBLIC_WORKER_CAPABILITY_KEY_FILE`` names a parent-side file
the sessionless worker reads to verify its own job capability.  The sandbox
policy forbids secrets (``SandboxPolicy.secrets_allowed=False``); that is
honoured by never placing key bytes in argv or the environment.  The parent
copies the key into ``<sandbox_root>/capability.key`` for this job only, the
supervisor bind-mounts that single file read-only and points the variable at
the mount path, and the copy is unlinked when the supervisor closes.  The file
therefore lives exactly as long as the container, is visible to one operation,
and carries a verification key for that job's capability, not a storage or
provider credential.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Mapping
from typing import BinaryIO

from services.trusted_executor.container_isolation_evidence import (
    ContainerIsolationExpectation,
    build_container_isolation_report,
)
from services.trusted_executor.container_runtime import (
    CAPABILITY_KEY_FILE_NAME,
    ContainerHandle,
    ContainerImageSettings,
    ContainerPathMapping,
    ContainerRuntimeError,
    parse_numeric_user,
)
from services.trusted_executor.container_stdio import AttachDemultiplexer
from services.trusted_executor.contracts import SandboxLaunchSpec
from services.trusted_executor.isolation_policy import (
    EnforcementStatus,
    PlatformIsolationReport,
)
from services.trusted_executor.process_supervisor import (
    ProcessDeadlineExceeded,
    ProcessMemoryLimitExceeded,
    ProcessSupervisorError,
    ProcessSupervisorUnavailable,
)


ENFORCEMENT_LAYER = "linux-container-cgroup"
_KEY_MIN_BYTES = 32
_KEY_MAX_BYTES = 4096


def mapping_or_empty(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def container_stopped(handle: ContainerHandle | None) -> bool:
    """True once the runtime reports the container is no longer running."""

    if handle is None:
        return True
    try:
        state = mapping_or_empty(handle.inspect().get("State"))
    except ContainerRuntimeError:
        return False
    return state.get("Running") is not True


class ContainerProcess:
    """The started container, presented like a supervised process."""

    def __init__(self, supervisor: object, handle: ContainerHandle, demultiplexer: AttachDemultiplexer | None) -> None:
        self._supervisor = supervisor
        self._handle = handle
        self._demultiplexer = demultiplexer
        self.container_id = handle.id
        self.pid = 0
        self.stdin: BinaryIO | None = demultiplexer.stdin if demultiplexer else None  # type: ignore[assignment]
        self.stdout: BinaryIO | None = demultiplexer.stdout if demultiplexer else None
        self.stderr: BinaryIO | None = demultiplexer.stderr if demultiplexer else None

    def poll(self) -> int | None:
        state = mapping_or_empty(self._handle.inspect().get("State"))
        return None if state.get("Running") is True else int_or_none(state.get("ExitCode"))

    def wait(self, timeout: float | None = None) -> int:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be non-negative")
        try:
            code = self._handle.wait(timeout=timeout)
        except ContainerRuntimeError as error:
            raise ProcessSupervisorError("container wait failed") from error
        self._supervisor._record_exit(code)  # type: ignore[attr-defined]
        if self._supervisor.deadline_exceeded:  # type: ignore[attr-defined]
            raise ProcessDeadlineExceeded("operation container exceeded its immutable deadline")
        if self._supervisor.memory_limit_exceeded:  # type: ignore[attr-defined]
            raise ProcessMemoryLimitExceeded("operation container exceeded its hard memory limit")
        return code

    def close(self) -> None:
        if self._demultiplexer is not None:
            self._demultiplexer.close()


def stage_capability_key(
    spec: SandboxLaunchSpec,
    source: Mapping[str, str],
    settings: ContainerImageSettings,
) -> pathlib.Path:
    """Copy the parent key into the operation sandbox for one job (see module doc)."""

    parent_value = source.get("PUBLIC_WORKER_CAPABILITY_KEY_FILE", "").strip()
    parent_path = pathlib.Path(parent_value)
    if not parent_value or not parent_path.is_absolute():
        raise ProcessSupervisorError("capability key file is required")
    try:
        key = parent_path.read_bytes()
    except OSError as error:
        raise ProcessSupervisorError("capability key file is unavailable") from error
    if not _KEY_MIN_BYTES <= len(key) <= _KEY_MAX_BYTES:
        raise ProcessSupervisorError("capability key file is invalid")
    if spec.filesystem is None:
        raise ProcessSupervisorError("sandbox filesystem staging is required")
    copy = spec.filesystem.sandbox_root / CAPABILITY_KEY_FILE_NAME
    try:
        with copy.open("xb") as target:
            target.write(key)
        if os.name != "nt":
            os.chmod(copy, 0o440)
            user = parse_numeric_user(settings.user)
            if user is not None and user[1] is not None:
                try:
                    os.chown(copy, -1, user[1])
                except PermissionError:
                    pass
    except OSError as error:
        copy.unlink(missing_ok=True)
        raise ProcessSupervisorError("capability key staging failed") from error
    return copy


def verify_container_isolation(
    spec: SandboxLaunchSpec,
    handle: ContainerHandle,
    *,
    mapping: ContainerPathMapping,
    platform: str,
) -> PlatformIsolationReport:
    """Inspect the created container; refuse unless every control is proven."""

    if spec.filesystem is None:
        raise ProcessSupervisorError("sandbox filesystem staging is required")
    try:
        inspect = handle.inspect()
    except ContainerRuntimeError as error:
        raise ProcessSupervisorUnavailable("container inspect failed") from error
    report = build_container_isolation_report(
        inspect,
        ContainerIsolationExpectation(
            limits=spec.limits,
            policy=spec.policy,
            input_root=mapping.to_container(spec.filesystem.input_root),
            temporary_root=mapping.to_container(spec.filesystem.temporary_root),
            input_source=spec.filesystem.input_root.as_posix(),
        ),
        platform=platform,
    )
    if not report.production_eligible:
        raise ProcessSupervisorUnavailable(
            "container isolation unproven: " + "; ".join(unproven_controls(report))
        )
    return report


def unproven_controls(report: PlatformIsolationReport) -> list[str]:
    failures = [
        f"{control.value}={status.value} ({reason})"
        for control, status, reason in report.controls
        if status is not EnforcementStatus.ENFORCED
    ]
    if not report.resource_limits_supported:
        failures.append(f"resource-limits ({report.resource_limits_reason})")
    return failures


class CgroupSampleAccumulator:
    """Peak memory, pids and CPU from one-shot cgroup stats samples."""

    def __init__(self) -> None:
        self.peak_memory = 0
        self.peak_pids = 0
        self.cpu_nanos = 0
        self.samples = 0

    def observe(self, stats: Mapping[str, object]) -> None:
        memory = mapping_or_empty(stats.get("memory_stats"))
        candidates = (
            int_or_none(memory.get("usage")),
            int_or_none(memory.get("max_usage")),
            int_or_none(mapping_or_empty(memory.get("stats")).get("peak")),
        )
        pids = int_or_none(mapping_or_empty(stats.get("pids_stats")).get("current"))
        cpu = int_or_none(
            mapping_or_empty(mapping_or_empty(stats.get("cpu_stats")).get("cpu_usage")).get("total_usage")
        )
        self.samples += 1
        self.peak_memory = max(self.peak_memory, *(value for value in candidates if value is not None), 0)
        if pids is not None:
            self.peak_pids = max(self.peak_pids, pids)
        if cpu is not None:
            self.cpu_nanos = max(self.cpu_nanos, cpu)

    def telemetry(self, *, stopped: bool, report: PlatformIsolationReport | None) -> dict[str, object]:
        return {
            "peakPrivateBytes": self.peak_memory,
            "peakProcessCount": self.peak_pids,
            "peakCpuMillis": self.cpu_nanos // 1_000_000,
            "totalProcessCount": self.peak_pids,
            "terminatedProcessCount": self.peak_pids if stopped else 0,
            "finalProcessCount": 0 if stopped else max(1, self.peak_pids),
            "resourceLimitsEnforced": report is not None and report.resource_limits_supported,
            "enforcementLayer": ENFORCEMENT_LAYER,
            "recycledWithinBaseline": stopped,
            "cgroupSampleCount": self.samples,
            "containerIsolationEvidence": report.to_attestation() if report is not None else None,
        }


__all__ = [
    "CgroupSampleAccumulator",
    "ContainerProcess",
    "ENFORCEMENT_LAYER",
    "container_stopped",
    "int_or_none",
    "mapping_or_empty",
    "stage_capability_key",
    "unproven_controls",
    "verify_container_isolation",
]
