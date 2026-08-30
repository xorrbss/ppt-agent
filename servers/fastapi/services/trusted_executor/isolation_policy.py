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


class IsolationPolicyError(ValueError):
    """A launch filesystem layout does not preserve the isolation contract."""


class IsolationUnavailableError(RuntimeError):
    """The host cannot prove every isolation control required for a launch."""


class IsolationControl(str, Enum):
    """Controls that must be enforced outside the child process itself."""

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
        reason = "Windows Job Object does not enforce this control"
        resource_limits_supported = True
        resource_limits_reason = "Windows Job Object supports CPU, memory, process-count, and descendant ownership limits"
    elif target == "posix" and sys.platform.startswith("linux"):
        backend = "linux-cgroup-only"
        reason = "cgroup v2 alone does not enforce this control; namespace launcher is absent"
        resource_limits_supported = True
        resource_limits_reason = "cgroup v2 resource limits require a delegated launcher before use"
    else:
        backend = "unsupported"
        reason = "no supported OS sandbox launcher is configured"
        resource_limits_supported = False
        resource_limits_reason = "no supported resource-limit backend is configured"
    return PlatformIsolationReport(
        platform=target,
        backend=backend,
        resource_limits_supported=resource_limits_supported,
        resource_limits_reason=resource_limits_reason,
        controls=tuple(
            (control, EnforcementStatus.UNSUPPORTED, reason) for control in IsolationControl
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
    "IsolationControl",
    "IsolationPolicyError",
    "IsolationUnavailableError",
    "PlatformIsolationReport",
    "SandboxFilesystemSpec",
    "probe_platform_isolation",
]
