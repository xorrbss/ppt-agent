"""Truthful OS-isolation admission checks for trusted-executor sandboxes.

The Windows Job Object used by the process supervisor can own descendants and
limit CPU, memory, and process count.  It does *not* provide an outbound
network deny, a read-only filesystem, or a quota-enforced writable tmpfs.
This module keeps those missing controls explicit so an environment variable,
an empty profile, or a directory layout can never be treated as OS isolation.
"""

from __future__ import annotations

import os
import pathlib
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


ISOLATION_ATTESTATION_SCHEMA = "presenton.worker-isolation-attestation/v1"


class IsolationPolicyError(ValueError):
    """A launch filesystem layout does not preserve the isolation contract."""


class IsolationUnavailableError(RuntimeError):
    """The host cannot prove every isolation control required for a launch."""


class IsolationControl(str, Enum):
    """Controls that must be enforced outside the child process itself."""

    LOW_PRIVILEGE_IDENTITY = "low-privilege-identity"
    NETWORK_NONE = "network-none"
    ROOT_FILESYSTEM_READ_ONLY = "root-filesystem-read-only"
    STAGED_INPUTS_READ_ONLY = "staged-inputs-read-only"
    TEMPORARY_BYTES_BOUNDED = "temporary-bytes-bounded"
    TEMPORARY_FILES_BOUNDED = "temporary-files-bounded"


class EnforcementStatus(str, Enum):
    """Evidence state for one OS-level isolation control."""

    ENFORCED = "enforced"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class SandboxFilesystemSpec:
    """Operation-local staging layout; it is not itself OS enforcement evidence."""

    sandbox_root: pathlib.Path
    input_root: pathlib.Path
    temporary_root: pathlib.Path
    staged_inputs: tuple[pathlib.Path, ...]

    def __post_init__(self) -> None:
        root = pathlib.Path(self.sandbox_root)
        inputs = pathlib.Path(self.input_root)
        temporary = pathlib.Path(self.temporary_root)
        paths = (root, inputs, temporary, *(pathlib.Path(path) for path in self.staged_inputs))
        if any(not path.is_absolute() for path in paths):
            raise IsolationPolicyError("sandbox filesystem paths must be absolute")
        if inputs == temporary:
            raise IsolationPolicyError("input and temporary roots must differ")
        if not _is_descendant(inputs, root) or not _is_descendant(temporary, root):
            raise IsolationPolicyError("input and temporary roots must be inside the sandbox root")
        if not self.staged_inputs:
            raise IsolationPolicyError("sandbox requires at least one staged input")
        if any(not _is_descendant(pathlib.Path(path), inputs) for path in self.staged_inputs):
            raise IsolationPolicyError("staged inputs must be inside the input root")
        if len({pathlib.Path(path) for path in self.staged_inputs}) != len(self.staged_inputs):
            raise IsolationPolicyError("staged inputs must be unique")
        object.__setattr__(self, "sandbox_root", root)
        object.__setattr__(self, "input_root", inputs)
        object.__setattr__(self, "temporary_root", temporary)
        object.__setattr__(
            self,
            "staged_inputs",
            tuple(pathlib.Path(path) for path in self.staged_inputs),
        )


@dataclass(frozen=True, slots=True)
class PlatformIsolationReport:
    """Auditable capability result supplied by a platform sandbox launcher."""

    platform: str
    backend: str
    resource_limits_supported: bool
    resource_limits_reason: str
    controls: tuple[tuple[IsolationControl, EnforcementStatus, str], ...]

    def __post_init__(self) -> None:
        if not self.platform.strip() or not self.backend.strip():
            raise IsolationPolicyError("isolation platform and backend are required")
        seen: set[IsolationControl] = set()
        for control, _status, reason in self.controls:
            if control in seen:
                raise IsolationPolicyError(f"duplicate isolation control: {control.value}")
            if not reason.strip():
                raise IsolationPolicyError(f"isolation control {control.value} needs a reason")
            seen.add(control)
        if not self.resource_limits_reason.strip():
            raise IsolationPolicyError("resource limit capability needs a reason")

    def status_for(self, control: IsolationControl) -> EnforcementStatus:
        for candidate, status, _reason in self.controls:
            if candidate is control:
                return status
        return EnforcementStatus.UNVERIFIED

    def require(self, controls: Iterable[IsolationControl]) -> None:
        """Fail closed unless every requested control has OS-level evidence."""

        failures: list[str] = []
        by_control = {control: (status, reason) for control, status, reason in self.controls}
        for control in controls:
            status, reason = by_control.get(
                control,
                (EnforcementStatus.UNVERIFIED, "launcher supplied no evidence"),
            )
            if status is not EnforcementStatus.ENFORCED:
                failures.append(f"{control.value}={status.value} ({reason})")
        if failures:
            raise IsolationUnavailableError("sandbox isolation unavailable: " + "; ".join(failures))

    @property
    def production_eligible(self) -> bool:
        """Whether the report proves every production isolation control.

        This value is derived only from the report's launcher/probe evidence.
        Configuration names and environment variables cannot make it true.
        """

        return self.resource_limits_supported and all(
            self.status_for(control) is EnforcementStatus.ENFORCED
            for control in IsolationControl
        )

    def to_attestation(self) -> dict[str, object]:
        """Return stable, secret-free provenance for admission and receipts."""

        by_control = {
            control: (status, reason) for control, status, reason in self.controls
        }
        controls: dict[str, dict[str, str]] = {}
        for control in sorted(IsolationControl, key=lambda item: item.value):
            status, reason = by_control.get(
                control,
                (EnforcementStatus.UNVERIFIED, "launcher supplied no evidence"),
            )
            controls[control.value] = {
                "status": status.value,
                "reason": reason,
            }
        return {
            "schemaVersion": ISOLATION_ATTESTATION_SCHEMA,
            "platform": self.platform,
            "backend": self.backend,
            "resourceLimits": {
                "status": (
                    "supported" if self.resource_limits_supported else "unsupported"
                ),
                "reason": self.resource_limits_reason,
            },
            "controls": controls,
            "productionEligible": self.production_eligible,
        }


def probe_platform_isolation(*, platform: str | None = None) -> PlatformIsolationReport:
    """Return conservative host capability, never a declaration that policy passed.

    A platform-specific launcher may replace this result with evidence from an
    AppContainer/container/namespace implementation.  The current executor
    has no such launcher, so every required filesystem and network control is
    deliberately unsupported.  In particular, Windows Job Objects are not a
    substitute for a firewall, filesystem namespace, or tmpfs quota.
    """

    target = platform if platform is not None else os.name
    if target == "nt":
        backend = "windows-job-object-only"
        resource_limits_supported = True
        resource_limits_reason = "Windows Job Object supports CPU, memory, process-count, and descendant ownership limits"
        reasons = {
            IsolationControl.LOW_PRIVILEGE_IDENTITY: (
                "launcher does not create a restricted token or AppContainer identity"
            ),
            IsolationControl.NETWORK_NONE: (
                "no AppContainer network isolation or outbound-deny policy was applied"
            ),
            IsolationControl.ROOT_FILESYSTEM_READ_ONLY: (
                "no AppContainer or ACL read-only root policy was applied"
            ),
            IsolationControl.STAGED_INPUTS_READ_ONLY: (
                "no AppContainer or ACL read-only staged-input policy was applied"
            ),
            IsolationControl.TEMPORARY_BYTES_BOUNDED: (
                "Job Object memory limits do not enforce a temporary-storage byte quota"
            ),
            IsolationControl.TEMPORARY_FILES_BOUNDED: (
                "Job Object limits do not enforce a temporary-file count quota"
            ),
        }
    elif target == "posix" and sys.platform.startswith("linux"):
        backend = "linux-cgroup-only"
        resource_limits_supported = True
        resource_limits_reason = "cgroup v2 resource limits require a delegated launcher before use"
        reasons = {
            control: "cgroup v2 alone does not enforce this control; namespace launcher is absent"
            for control in IsolationControl
        }
    else:
        backend = "unsupported"
        resource_limits_supported = False
        resource_limits_reason = "no supported resource-limit backend is configured"
        reasons = {
            control: "no supported OS sandbox launcher is configured"
            for control in IsolationControl
        }
    return PlatformIsolationReport(
        platform=target,
        backend=backend,
        resource_limits_supported=resource_limits_supported,
        resource_limits_reason=resource_limits_reason,
        controls=tuple(
            (control, EnforcementStatus.UNSUPPORTED, reasons[control])
            for control in IsolationControl
        ),
    )


LINUX_LOCAL_DEVELOPMENT_ISOLATION_BACKEND = "linux-process-session-only"


def linux_local_development_isolation_report() -> PlatformIsolationReport:
    """Report what the Linux development launcher actually provides: nothing.

    ``linux-local`` runs the worker as an ordinary child in its own process
    session, owned by ``LinuxSessionProcessSupervisor``.  That gives operation
    ownership of the tree, a wall-clock deadline and sampled memory/process
    ceilings, and nothing else: no identity drop, network namespace, read-only
    mounts or tmpfs quota.  Every control is therefore unsupported, the report
    is never production eligible, and only the explicit development policy may
    accept the gap (``SupervisedStdioWireTransport``).
    """

    reasons = {
        IsolationControl.LOW_PRIVILEGE_IDENTITY: "the worker runs as the controller's own user",
        IsolationControl.NETWORK_NONE: "no network namespace or outbound-deny policy is applied",
        IsolationControl.ROOT_FILESYSTEM_READ_ONLY: "no read-only mount namespace is applied",
        IsolationControl.STAGED_INPUTS_READ_ONLY: "staged inputs are ordinary files writable by the worker user",
        IsolationControl.TEMPORARY_BYTES_BOUNDED: "no tmpfs or quota bounds the temporary directory",
        IsolationControl.TEMPORARY_FILES_BOUNDED: "no tmpfs or quota bounds temporary file count",
    }
    return PlatformIsolationReport(
        platform="posix",
        backend=LINUX_LOCAL_DEVELOPMENT_ISOLATION_BACKEND,
        resource_limits_supported=True,
        resource_limits_reason=(
            "process-session ownership with a wall-clock deadline and sampled "
            "aggregate RSS/process-count ceilings; a process that leaves the "
            "session escapes them"
        ),
        controls=tuple(
            (control, EnforcementStatus.UNSUPPORTED, reasons[control])
            for control in IsolationControl
        ),
    )


def _is_descendant(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = [
    "EnforcementStatus",
    "LINUX_LOCAL_DEVELOPMENT_ISOLATION_BACKEND",
    "ISOLATION_ATTESTATION_SCHEMA",
    "IsolationControl",
    "IsolationPolicyError",
    "IsolationUnavailableError",
    "PlatformIsolationReport",
    "SandboxFilesystemSpec",
    "linux_local_development_isolation_report",
    "probe_platform_isolation",
]
