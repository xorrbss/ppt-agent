"""Fail-closed contracts shared by the trusted executor boundaries.

These values describe what an already-admitted operation may execute.  They do
not grant a lease, replace the database fence, or carry storage credentials.
"""

from __future__ import annotations

import os
import pathlib
import uuid
from dataclasses import dataclass
from typing import Literal

from services.trusted_executor.isolation_policy import (
    IsolationControl,
    PlatformIsolationReport,
    SandboxFilesystemSpec,
)


NetworkMode = Literal["none"]
SandboxRole = Literal["export"]


class TrustedExecutorContractError(ValueError):
    """A trusted-executor request is incomplete or weakens isolation."""


def _require_identity(value: str, *, label: str) -> str:
    candidate = value.strip()
    if not candidate or len(candidate) > 255 or any(char.isspace() for char in candidate):
        raise TrustedExecutorContractError(f"{label} is invalid")
    return candidate


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """The complete reuse boundary for one admitted presentation operation."""

    operation_id: uuid.UUID
    tenant_id: str
    owner_id: uuid.UUID
    presentation_id: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, uuid.UUID):
            raise TrustedExecutorContractError("operation id must be a UUID")
        if not isinstance(self.owner_id, uuid.UUID):
            raise TrustedExecutorContractError("owner id must be a UUID")
        if not isinstance(self.presentation_id, uuid.UUID):
            raise TrustedExecutorContractError("presentation id must be a UUID")
        object.__setattr__(
            self,
            "tenant_id",
            _require_identity(self.tenant_id, label="tenant id"),
        )

    @property
    def reuse_key(self) -> tuple[uuid.UUID, str, uuid.UUID, uuid.UUID]:
        return (
            self.operation_id,
            self.tenant_id,
            self.owner_id,
            self.presentation_id,
        )


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """Hard per-operation limits enforced by the OS process supervisor."""

    memory_bytes: int
    process_limit: int
    deadline_seconds: float
    cpu_rate_percent: int = 100
    temporary_bytes: int = 256 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.memory_bytes <= 0:
            raise TrustedExecutorContractError("memory limit must be positive")
        if not 1 <= self.process_limit <= 4096:
            raise TrustedExecutorContractError("process limit is invalid")
        if not 0 < self.deadline_seconds <= 3600:
            raise TrustedExecutorContractError("deadline is invalid")
        if not 1 <= self.cpu_rate_percent <= 100:
            raise TrustedExecutorContractError("CPU rate is invalid")
        if not 1 <= self.temporary_bytes <= self.memory_bytes:
            raise TrustedExecutorContractError("temporary storage limit is invalid")


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    """Non-negotiable isolation claims required before launch."""

    network_mode: NetworkMode = "none"
    root_filesystem_read_only: bool = True
    secrets_allowed: bool = False
    empty_image_prewarm_only: bool = True
    temporary_file_limit: int = 4096

    def __post_init__(self) -> None:
        if self.network_mode != "none":
            raise TrustedExecutorContractError("sandbox network must be disabled")
        if not self.root_filesystem_read_only:
            raise TrustedExecutorContractError("sandbox root must be read-only")
        if self.secrets_allowed:
            raise TrustedExecutorContractError("sandbox secrets are forbidden")
        if not self.empty_image_prewarm_only:
            raise TrustedExecutorContractError("only an empty image may be prewarmed")
        if not 1 <= self.temporary_file_limit <= 1_000_000:
            raise TrustedExecutorContractError("temporary file limit is invalid")

    def require_os_enforcement(self, report: PlatformIsolationReport) -> None:
        """Reject launch unless a platform launcher proved every required control."""

        report.require(
            (
                IsolationControl.NETWORK_NONE,
                IsolationControl.ROOT_FILESYSTEM_READ_ONLY,
                IsolationControl.STAGED_INPUTS_READ_ONLY,
                IsolationControl.TEMPORARY_BYTES_BOUNDED,
                IsolationControl.TEMPORARY_FILES_BOUNDED,
            )
        )


@dataclass(frozen=True, slots=True)
class SandboxLaunchSpec:
    """A sessionless launch request bound to one operation and one role."""

    identity: ExecutionIdentity
    role: SandboxRole
    executable: pathlib.Path
    arguments: tuple[str, ...]
    limits: ResourceLimits
    policy: SandboxPolicy
    filesystem: SandboxFilesystemSpec | None = None

    def __post_init__(self) -> None:
        if self.role != "export":
            raise TrustedExecutorContractError("sandbox role is invalid")
        executable = pathlib.Path(self.executable)
        if not executable.is_absolute():
            raise TrustedExecutorContractError("sandbox executable must be absolute")
        if any(not value or "\x00" in value for value in self.arguments):
            raise TrustedExecutorContractError("sandbox arguments are invalid")
        object.__setattr__(self, "executable", executable)

    def require_os_enforcement(self, report: PlatformIsolationReport) -> None:
        """Require both operation staging and OS proof before an isolated launch."""

        if self.filesystem is None:
            raise TrustedExecutorContractError("sandbox filesystem staging is required")
        self.policy.require_os_enforcement(report)

    def environment(self, base: dict[str, str] | None = None) -> dict[str, str]:
        """Return a minimal environment without credentials or inherited sessions."""

        source = os.environ if base is None else base
        allowed = {
            "CHROME_PATH",
            "CHROMIUM_PATH",
            "COMSPEC",
            "LANG",
            "LANGUAGE",
            "LC_ALL",
            "LOCALAPPDATA",
            "PATH",
            "PATHEXT",
            "PPT_WORKER_CPU_MILLIS",
            "PPT_WORKER_MEMORY_LIMIT_BYTES",
            "PPT_WORKER_NETWORK_MODE",
            "PPT_WORKER_OPEN_FILE_LIMIT",
            "PPT_WORKER_OUTPUT_LIMIT_BYTES",
            "PPT_WORKER_PID_LIMIT",
            "PPT_WORKER_REPLICAS",
            "PPT_WORKER_ROOT_FILESYSTEM",
            "PPT_WORKER_RUN_AS_NON_ROOT",
            "PPT_WORKER_SCRATCH",
            "PPT_WORKER_SECCOMP_PROFILE",
            "PPT_WORKER_WALL_CLOCK_SECONDS",
            # The export role may assemble an editable hybrid deck. Both name
            # read-only local resources: the assembler argv and the licensed
            # server font allowlist. Neither is a credential or a session.
            "PUBLIC_EXPORT_FONT_ALLOWLIST_DIR",
            "PUBLIC_EXPORT_HYBRID_COMMAND_JSON",
            "PUBLIC_EXPORT_HYBRID_SEMANTIC_ENABLED",
            "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES",
            "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS",
            "PUBLIC_WORKER_CAPABILITY_KEY_FILE",
            "PUBLIC_WORKER_INPUT_STORE_ROOT",
            "PUBLIC_WORKER_LOCAL_DEVELOPMENT",
            "PUBLIC_WORKER_OBJECT_STORE_ROOT",
            "PUBLIC_WORKER_OUTPUT_STORE_ROOT",
            "PUBLIC_WORKER_OS_ISOLATION",
            "PUBLIC_WORKER_ROLE",
            "PUBLIC_WORKER_SESSIONLESS",
            "PUPPETEER_EXECUTABLE_PATH",
            "PYTHONHOME",
            "PYTHONPATH",
            "SYSTEMROOT",
            "TEMP",
            "TMP",
            "TMPDIR",
            "VIRTUAL_ENV",
            "WINDIR",
        }
        result = {key: value for key, value in source.items() if key.upper() in allowed}
        result.update(
            {
                "TRUSTED_EXECUTOR_NETWORK": "none",
                "TRUSTED_EXECUTOR_OPERATION_ID": str(self.identity.operation_id),
                "TRUSTED_EXECUTOR_ROLE": self.role,
                "TRUSTED_EXECUTOR_SESSIONLESS": "1",
            }
        )
        return result


__all__ = [
    "ExecutionIdentity",
    "NetworkMode",
    "ResourceLimits",
    "SandboxLaunchSpec",
    "SandboxPolicy",
    "SandboxRole",
    "SandboxFilesystemSpec",
    "TrustedExecutorContractError",
]
