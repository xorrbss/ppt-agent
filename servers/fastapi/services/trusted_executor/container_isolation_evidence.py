"""Build a ``PlatformIsolationReport`` from a container runtime's inspect output.

Every control is ENFORCED only when the runtime's own ``inspect`` answer proves
it against what the launch contract required.  A missing field, an unexpected
value, or a weaker limit maps to UNSUPPORTED with the observed value in the
reason, and the existing fail-closed launcher/transport then refuses.  The
request the parent sent, configuration names, and environment variables are
never consulted: they are what was asked for, not what was enforced.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from utils.coerce import as_mapping
from services.trusted_executor.container_runtime import (
    CONTAINER_BACKEND_NAME,
    parse_numeric_user,
)
from services.trusted_executor.contracts import ResourceLimits, SandboxPolicy
from services.trusted_executor.isolation_policy import (
    EnforcementStatus,
    IsolationControl,
    PlatformIsolationReport,
)


_SIZE_SUFFIX = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
_SIZE_PATTERN = re.compile(r"\A(\d+)([kmgKMG]?)\Z")


@dataclass(frozen=True, slots=True)
class ContainerIsolationExpectation:
    """What the contract required; compared against inspect, never trusted."""

    limits: ResourceLimits
    policy: SandboxPolicy
    input_root: PurePosixPath
    temporary_root: PurePosixPath
    input_source: str


def _bounded_positive(value: object, limit: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= limit


def _parse_size(value: str) -> int | None:
    match = _SIZE_PATTERN.match(value.strip())
    if match is None:
        return None
    return int(match.group(1)) * _SIZE_SUFFIX[match.group(2).lower()]


def _tmpfs_option(options: object, name: str) -> str | None:
    if not isinstance(options, str):
        return None
    for item in options.split(","):
        key, _separator, value = item.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def _network_none(inspect: Mapping[str, object]) -> tuple[bool, str]:
    host_config = as_mapping(inspect.get("HostConfig"))
    mode = host_config.get("NetworkMode")
    networks = as_mapping(as_mapping(inspect.get("NetworkSettings")).get("Networks"))
    attached = sorted(str(name) for name in networks)
    if mode != "none":
        return False, f"HostConfig.NetworkMode={mode!r}"
    if any(name != "none" for name in attached):
        return False, f"NetworkSettings.Networks={attached}"
    return True, "HostConfig.NetworkMode=none and no network endpoints attached"


def _root_read_only(inspect: Mapping[str, object]) -> tuple[bool, str]:
    value = as_mapping(inspect.get("HostConfig")).get("ReadonlyRootfs")
    if value is True:
        return True, "HostConfig.ReadonlyRootfs=true"
    return False, f"HostConfig.ReadonlyRootfs={value!r}"


def _inputs_read_only(
    inspect: Mapping[str, object], expectation: ContainerIsolationExpectation
) -> tuple[bool, str]:
    destination = str(expectation.input_root)
    mounts = inspect.get("Mounts")
    if isinstance(mounts, list):
        for mount in mounts:
            entry = as_mapping(mount)
            if entry.get("Destination") != destination:
                continue
            if entry.get("Type") != "bind":
                return False, f"Mounts[{destination}].Type={entry.get('Type')!r}"
            if entry.get("Source") != expectation.input_source:
                return False, f"Mounts[{destination}].Source={entry.get('Source')!r}"
            if entry.get("RW") is not False:
                return False, f"Mounts[{destination}].RW={entry.get('RW')!r}"
            return True, f"Mounts[{destination}] is a read-only bind of the staged inputs"
    binds = as_mapping(inspect.get("HostConfig")).get("Binds")
    if isinstance(binds, list):
        expected = f"{expectation.input_source}:{destination}:ro"
        for bind in binds:
            if not isinstance(bind, str):
                continue
            if bind == expected:
                return True, f"HostConfig.Binds mounts the staged inputs read-only at {destination}"
            if bind.endswith(f":{destination}") or f":{destination}:" in bind:
                return False, f"HostConfig.Binds[{destination}]={bind!r}"
    return False, f"no mount of the staged inputs at {destination} was reported"


def _tmpfs(
    inspect: Mapping[str, object], expectation: ContainerIsolationExpectation
) -> tuple[tuple[bool, str], tuple[bool, str]]:
    tmpfs = as_mapping(as_mapping(inspect.get("HostConfig")).get("Tmpfs"))
    options = tmpfs.get(str(expectation.temporary_root))
    if options is None:
        reason = f"HostConfig.Tmpfs has no entry for {expectation.temporary_root}"
        return (False, reason), (False, reason)
    size = _tmpfs_option(options, "size")
    inodes = _tmpfs_option(options, "nr_inodes")
    size_bytes = _parse_size(size) if size is not None else None
    byte_limit = expectation.limits.temporary_bytes
    file_limit = expectation.policy.temporary_file_limit
    if size_bytes is None or size_bytes > byte_limit:
        bytes_result = (False, f"HostConfig.Tmpfs size={size!r} does not bound {byte_limit} bytes")
    else:
        bytes_result = (True, f"HostConfig.Tmpfs size={size_bytes} bytes at {expectation.temporary_root}")
    if inodes is None or not inodes.isdigit() or int(inodes) > file_limit:
        files_result = (False, f"HostConfig.Tmpfs nr_inodes={inodes!r} does not bound {file_limit} files")
    else:
        files_result = (True, f"HostConfig.Tmpfs nr_inodes={int(inodes)} at {expectation.temporary_root}")
    return bytes_result, files_result


def _low_privilege(inspect: Mapping[str, object]) -> tuple[bool, str]:
    user = as_mapping(inspect.get("Config")).get("User")
    parsed = parse_numeric_user(user)
    if parsed is None:
        return False, f"Config.User={user!r} is not a numeric non-root identity"
    return True, f"Config.User={user} is a numeric non-root identity"


def _resource_limits(
    inspect: Mapping[str, object], expectation: ContainerIsolationExpectation
) -> tuple[bool, str]:
    host_config = as_mapping(inspect.get("HostConfig"))
    memory = host_config.get("Memory")
    pids = host_config.get("PidsLimit")
    problems: list[str] = []
    if not _bounded_positive(memory, expectation.limits.memory_bytes):
        problems.append(f"HostConfig.Memory={memory!r}")
    if not _bounded_positive(pids, expectation.limits.process_limit):
        problems.append(f"HostConfig.PidsLimit={pids!r}")
    if problems:
        return False, "cgroup limits not proven: " + ", ".join(problems)
    return True, f"cgroup memory={memory} bytes and pids={pids} enforced by the container runtime"


def build_container_isolation_report(
    inspect: Mapping[str, object],
    expectation: ContainerIsolationExpectation,
    *,
    platform: str = "linux",
) -> PlatformIsolationReport:
    """Map inspect fields onto controls; anything unproven is UNSUPPORTED."""

    temporary_bytes, temporary_files = _tmpfs(inspect, expectation)
    findings = {
        IsolationControl.LOW_PRIVILEGE_IDENTITY: _low_privilege(inspect),
        IsolationControl.NETWORK_NONE: _network_none(inspect),
        IsolationControl.ROOT_FILESYSTEM_READ_ONLY: _root_read_only(inspect),
        IsolationControl.STAGED_INPUTS_READ_ONLY: _inputs_read_only(inspect, expectation),
        IsolationControl.TEMPORARY_BYTES_BOUNDED: temporary_bytes,
        IsolationControl.TEMPORARY_FILES_BOUNDED: temporary_files,
    }
    limits_supported, limits_reason = _resource_limits(inspect, expectation)
    return PlatformIsolationReport(
        platform=platform,
        backend=CONTAINER_BACKEND_NAME,
        resource_limits_supported=limits_supported,
        resource_limits_reason=limits_reason,
        controls=tuple(
            (
                control,
                EnforcementStatus.ENFORCED if proven else EnforcementStatus.UNSUPPORTED,
                reason,
            )
            for control, (proven, reason) in findings.items()
        ),
    )


__all__ = [
    "ContainerIsolationExpectation",
    "build_container_isolation_report",
]
