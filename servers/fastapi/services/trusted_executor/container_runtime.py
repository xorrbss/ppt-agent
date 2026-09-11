"""Container runtime client protocol and the per-job create request builder.

The trusted executor never talks to a container engine directly.  It builds a
``ContainerCreateRequest`` (Docker Engine API ``HostConfig`` shape) from the
launch contract and hands it to an injected ``ContainerRuntimeClient``.  A real
Docker SDK adapter lives in ``container_runtime_docker``; unit tests inject a
fake.  Nothing here is isolation evidence: the request is what the parent asks
for, and only the runtime's own ``inspect`` output (see
``container_isolation_evidence``) proves what was enforced.

Path contract.  Staged inputs and the output directory are bind-mounted so the
container sees them at ``ContainerPathMapping.to_container(parent_path)``.  The
production mapping is the identity (parent staging root == container staging
root, both POSIX absolute), which keeps ``PUBLIC_WORKER_INPUT_STORE_ROOT`` and
``PUBLIC_WORKER_OUTPUT_STORE_ROOT`` byte-identical on both sides.  A different
container root only exists so the request builder can be exercised from a
Windows development checkout.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Protocol

from services.trusted_executor.contracts import ResourceLimits, SandboxLaunchSpec


CONTAINER_BACKEND_NAME = "linux-container-runtime"
CAPABILITY_KEY_FILE_NAME = "capability.key"
_CPU_PERIOD_MICROS = 100_000
# Variables whose values name host-side paths or host-side toolchains.  Inside
# the image they must come from the image's own ENV, never from the parent.
_HOST_BOUND_VARIABLES = frozenset(
    {
        "CHROME_PATH",
        "CHROMIUM_PATH",
        "COMSPEC",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PUBLIC_EXPORT_FONT_ALLOWLIST_DIR",
        "PUBLIC_EXPORT_HYBRID_COMMAND_JSON",
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
)


class ContainerRuntimeError(RuntimeError):
    """The container runtime client failed or returned an unusable answer."""


class ContainerAttachStream(Protocol):
    """Raw multiplexed attach stream (Docker ``stream=1``, ``tty=false``)."""

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> None: ...

    def close_write(self) -> None: ...

    def close(self) -> None: ...


class ContainerHandle(Protocol):
    """One created container; every method may raise ``ContainerRuntimeError``."""

    @property
    def id(self) -> str: ...

    def inspect(self) -> Mapping[str, object]: ...

    def attach(self) -> ContainerAttachStream: ...

    def start(self) -> None: ...

    def wait(self, *, timeout: float | None) -> int:
        """Block until exit and return the exit code; raise ``TimeoutError``."""
        ...

    def kill(self) -> None: ...

    def stats(self) -> Mapping[str, object]:
        """One non-streaming cgroup stats sample (Docker ``/stats?stream=false``)."""
        ...

    def remove(self) -> None: ...


class ContainerRuntimeClient(Protocol):
    def create(self, request: "ContainerCreateRequest") -> ContainerHandle: ...


@dataclass(frozen=True, slots=True)
class ContainerPathMapping:
    """Parent staging path <-> container mount path for one staging root."""

    parent_root: Path
    container_root: PurePosixPath

    def __post_init__(self) -> None:
        parent = Path(self.parent_root)
        container = PurePosixPath(self.container_root)
        if not parent.is_absolute() or not container.is_absolute():
            raise ContainerRuntimeError("staging roots must be absolute")
        object.__setattr__(self, "parent_root", parent)
        object.__setattr__(self, "container_root", container)

    @classmethod
    def identity(cls, staging_root: Path) -> "ContainerPathMapping":
        root = Path(staging_root)
        if not PurePosixPath(root.as_posix()).is_absolute() or root.drive:
            raise ContainerRuntimeError(
                "identity path mapping requires a POSIX absolute staging root"
            )
        return cls(parent_root=root, container_root=PurePosixPath(root.as_posix()))

    def to_container(self, path: Path) -> PurePosixPath:
        try:
            relative = Path(path).relative_to(self.parent_root)
        except ValueError as error:
            raise ContainerRuntimeError(
                "path is outside the mapped staging root"
            ) from error
        return self.container_root.joinpath(*relative.parts)


@dataclass(frozen=True, slots=True)
class ContainerImageSettings:
    """Immutable image identity and the non-root identity to run as."""

    image: str
    user: str
    shm_bytes: int = 256 * 1024 * 1024
    init: bool = True

    def __post_init__(self) -> None:
        image = self.image.strip()
        if not image or any(char.isspace() for char in image):
            raise ContainerRuntimeError("container image reference is invalid")
        if parse_numeric_user(self.user) is None:
            raise ContainerRuntimeError(
                "container user must be numeric uid[:gid] with a non-zero uid"
            )
        if self.shm_bytes <= 0:
            raise ContainerRuntimeError("shm size must be positive")
        object.__setattr__(self, "image", image)
        object.__setattr__(self, "user", self.user.strip())


def parse_numeric_user(value: object) -> tuple[int, int | None] | None:
    """Return ``(uid, gid)`` for a numeric non-root user string, else ``None``."""

    if not isinstance(value, str):
        return None
    parts = value.strip().split(":")
    if not 1 <= len(parts) <= 2 or not all(part.isdigit() for part in parts):
        return None
    uid = int(parts[0])
    gid = int(parts[1]) if len(parts) == 2 else None
    if uid == 0 or gid == 0:
        return None
    return uid, gid


@dataclass(frozen=True, slots=True)
class ContainerCreateRequest:
    """Docker Engine API ``POST /containers/create`` body, split by section."""

    image: str
    command: tuple[str, ...]
    environment: Mapping[str, str]
    working_dir: str
    user: str
    labels: Mapping[str, str]
    host_config: Mapping[str, object]
    name: str | None = None
    open_stdin: bool = True
    stdin_once: bool = True
    tty: bool = False
    extra: Mapping[str, object] = field(default_factory=dict)


def tmpfs_options(limits: ResourceLimits, *, file_limit: int) -> str:
    return f"rw,nosuid,nodev,size={limits.temporary_bytes},nr_inodes={file_limit}"


def build_host_config(
    spec: SandboxLaunchSpec,
    *,
    mapping: ContainerPathMapping,
    settings: ContainerImageSettings,
    capability_key_parent_path: Path,
    host_cpu_count: int | None = None,
) -> dict[str, object]:
    """Translate the launch contract into a ``HostConfig`` the runtime enforces."""

    if spec.filesystem is None:
        raise ContainerRuntimeError("sandbox filesystem staging is required")
    limits = spec.limits
    filesystem = spec.filesystem
    input_root = mapping.to_container(filesystem.input_root)
    output_root = mapping.to_container(filesystem.sandbox_root / "output")
    temporary_root = mapping.to_container(filesystem.temporary_root)
    key_container_path = mapping.to_container(capability_key_parent_path)
    host_config: dict[str, object] = {
        "NetworkMode": spec.policy.network_mode,
        "Memory": limits.memory_bytes,
        "MemorySwap": limits.memory_bytes,
        "PidsLimit": limits.process_limit,
        "ReadonlyRootfs": spec.policy.root_filesystem_read_only,
        "Tmpfs": {
            str(temporary_root): tmpfs_options(
                limits, file_limit=spec.policy.temporary_file_limit
            ),
        },
        "ShmSize": settings.shm_bytes,
        "Binds": [
            f"{filesystem.input_root.as_posix()}:{input_root}:ro",
            f"{(filesystem.sandbox_root / 'output').as_posix()}:{output_root}:rw",
            f"{capability_key_parent_path.as_posix()}:{key_container_path}:ro",
        ],
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges:true"],
        "Init": settings.init,
        "AutoRemove": False,
        "OomKillDisable": False,
    }
    if limits.cpu_rate_percent < 100:
        cpu_count = host_cpu_count or os.cpu_count() or 1
        host_config["CpuPeriod"] = _CPU_PERIOD_MICROS
        host_config["CpuQuota"] = (
            _CPU_PERIOD_MICROS * cpu_count * limits.cpu_rate_percent // 100
        )
    return host_config


def build_container_environment(
    spec: SandboxLaunchSpec,
    base: Mapping[str, str] | None,
    *,
    mapping: ContainerPathMapping,
    capability_key_parent_path: Path,
) -> dict[str, str]:
    """Apply the contract allowlist, then rewrite parent paths to mount paths."""

    if spec.filesystem is None:
        raise ContainerRuntimeError("sandbox filesystem staging is required")
    environment = spec.environment(None if base is None else dict(base))
    for name in tuple(environment):
        if name.upper() in _HOST_BOUND_VARIABLES:
            del environment[name]
    filesystem = spec.filesystem
    temporary = str(mapping.to_container(filesystem.temporary_root))
    environment.update(
        {
            "PUBLIC_WORKER_INPUT_STORE_ROOT": str(mapping.to_container(filesystem.input_root)),
            "PUBLIC_WORKER_OUTPUT_STORE_ROOT": str(
                mapping.to_container(filesystem.sandbox_root / "output")
            ),
            "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(
                mapping.to_container(capability_key_parent_path)
            ),
            "TMPDIR": temporary,
            "TEMP": temporary,
            "TMP": temporary,
        }
    )
    return environment


def build_container_create_request(
    spec: SandboxLaunchSpec,
    *,
    mapping: ContainerPathMapping,
    settings: ContainerImageSettings,
    capability_key_parent_path: Path,
    environment: Mapping[str, str] | None = None,
    labels: Mapping[str, str] | None = None,
    host_cpu_count: int | None = None,
) -> ContainerCreateRequest:
    if spec.filesystem is None:
        raise ContainerRuntimeError("sandbox filesystem staging is required")
    return ContainerCreateRequest(
        image=settings.image,
        command=(str(spec.executable.as_posix()), *spec.arguments),
        environment=build_container_environment(
            spec,
            environment,
            mapping=mapping,
            capability_key_parent_path=capability_key_parent_path,
        ),
        working_dir=str(mapping.to_container(spec.filesystem.sandbox_root)),
        user=settings.user,
        labels={
            "presenton.trusted-executor": "1",
            "presenton.operation-id": str(spec.identity.operation_id),
            "presenton.role": spec.role,
            **(labels or {}),
        },
        host_config=build_host_config(
            spec,
            mapping=mapping,
            settings=settings,
            capability_key_parent_path=capability_key_parent_path,
            host_cpu_count=host_cpu_count,
        ),
    )


def as_environment_list(environment: Mapping[str, str]) -> list[str]:
    return [f"{key}={value}" for key, value in sorted(environment.items())]


__all__ = [
    "CAPABILITY_KEY_FILE_NAME",
    "CONTAINER_BACKEND_NAME",
    "ContainerAttachStream",
    "ContainerCreateRequest",
    "ContainerHandle",
    "ContainerImageSettings",
    "ContainerPathMapping",
    "ContainerRuntimeClient",
    "ContainerRuntimeError",
    "as_environment_list",
    "build_container_create_request",
    "build_container_environment",
    "build_host_config",
    "parse_numeric_user",
    "tmpfs_options",
]
