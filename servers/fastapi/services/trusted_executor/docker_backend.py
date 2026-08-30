"""Docker Linux sandbox backend for production trusted-worker execution."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Mapping
import uuid
from collections.abc import Awaitable

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.isolation_policy import (
    EnforcementStatus,
    IsolationControl,
    IsolationUnavailableError,
    PlatformIsolationReport,
)
from services.trusted_executor.product_adapter import TrustedExecutorWorkItem
from services.trusted_executor.worker_dispatcher import (
    TrustedWorkerWireError,
    TrustedWorkerWireResult,
)
from services.trusted_executor.wire_transport import (
    MAX_WIRE_STDERR_BYTES,
    MAX_WIRE_STDOUT_BYTES,
    TrustedWorkerArtifactAuthority,
    _export_bundle_references,
    _validated_success_output,
    _verify_artifact_file,
)


_IMMUTABLE_REPOSITORY_DIGEST = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_IMMUTABLE_LOCAL_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DockerSandboxConfiguration:
    docker_executable: Path
    image_reference: str
    report: PlatformIsolationReport


def probe_docker_sandbox(
    *,
    docker_executable: Path,
    image_reference: str,
    timeout_seconds: float = 10.0,
) -> DockerSandboxConfiguration:
    """Prove the Linux daemon, hard-limit features, and local immutable image."""

    executable = Path(docker_executable)
    image = image_reference.strip()
    if not executable.is_absolute() or not executable.is_file():
        raise IsolationUnavailableError("docker sandbox executable is unavailable")
    repository_digest = _IMMUTABLE_REPOSITORY_DIGEST.fullmatch(image) is not None
    local_image_id = _IMMUTABLE_LOCAL_IMAGE_ID.fullmatch(image) is not None
    if not repository_digest and not local_image_id:
        raise IsolationUnavailableError(
            "docker sandbox image must be an immutable name@sha256 or sha256 image ID"
        )
    try:
        info_process = subprocess.run(
            [str(executable), "info", "--format", "{{json .}}"],
            check=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
        info = json.loads(info_process.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        raise IsolationUnavailableError("docker Linux daemon probe failed") from error
    required = (
        info.get("OSType") == "linux",
        info.get("MemoryLimit") is True,
        info.get("CpuCfsQuota") is True,
        info.get("PidsLimit") is True,
        "null" in (info.get("Plugins", {}).get("Network") or []),
        any(
            str(option).startswith("name=seccomp")
            for option in (info.get("SecurityOptions") or [])
        ),
    )
    if not all(required):
        raise IsolationUnavailableError(
            "docker Linux daemon lacks network/resource/seccomp enforcement"
        )
    try:
        inspected = subprocess.run(
            [
                str(executable),
                "image",
                "inspect",
                image,
                "--format",
                "{{json .RepoDigests}} {{.Id}} {{.Os}}",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise IsolationUnavailableError(
            "immutable docker worker image is not present locally"
        ) from error
    if not inspected.endswith(" linux"):
        raise IsolationUnavailableError("docker worker image must target Linux")
    try:
        digest_payload, inspected_id, _os_name = inspected.rsplit(" ", 2)
        repo_digests = json.loads(digest_payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise IsolationUnavailableError("docker worker image digest probe failed") from error
    if repository_digest and image not in repo_digests:
        raise IsolationUnavailableError("docker worker image digest binding failed")
    if local_image_id and inspected_id != image:
        raise IsolationUnavailableError("docker worker image digest binding failed")

    reason = (
        "Docker Linux backend verified; per-run flags enforce network-none, "
        "read-only root/input, output-only write, bounded tmpfs, and hard resources"
    )
    report = PlatformIsolationReport(
        platform="docker-linux",
        backend="docker-linux-runc",
        resource_limits_supported=True,
        resource_limits_reason=reason,
        controls=tuple(
            (control, EnforcementStatus.ENFORCED, reason)
            for control in IsolationControl
        ),
    )
    return DockerSandboxConfiguration(
        docker_executable=executable,
        image_reference=image,
        report=report,
    )


class DockerTrustedWorkerWireTransport:
    """Run each immutable worker job in a separately named hardened container."""

    def __init__(
        self,
        *,
        configuration: DockerSandboxConfiguration,
        artifact_authority: TrustedWorkerArtifactAuthority,
        cleanup_timeout_seconds: float = 10.0,
    ) -> None:
        if cleanup_timeout_seconds <= 0:
            raise ValueError("docker cleanup timeout must be positive")
        self._configuration = configuration
        self._artifact_authority = artifact_authority
        self._cleanup_timeout_seconds = cleanup_timeout_seconds
        self._active: set[str] = set()
        self._active_lock = asyncio.Lock()
        self._closed = False

    async def execute(
        self,
        *,
        work_item: TrustedExecutorWorkItem,
        job: IsolatedWorkerJob,
        payload: bytes,
        environment: Mapping[str, str],
    ) -> TrustedWorkerWireResult:
        if self._closed:
            raise TrustedWorkerWireError("trusted_worker_transport_closed")
        spec = work_item.launch_spec
        spec.require_os_enforcement(self._configuration.report)
        if spec.filesystem is None:
            raise TrustedWorkerWireError("trusted_worker_filesystem_missing")
        input_root = spec.filesystem.input_root
        output_root = spec.filesystem.sandbox_root / "output"
        temporary_root = spec.filesystem.temporary_root
        input_root.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(parents=True, exist_ok=True)
        temporary_root.mkdir(parents=True, exist_ok=True)
        primary_path = input_root.joinpath(*job.input_artifact.storage_key.split("/"))
        await _before_deadline(
            self._artifact_authority.stage(
                reference=job.input_artifact,
                destination=primary_path,
            ),
            job=job,
        )
        for reference in _export_bundle_references(job, primary_path):
            await _before_deadline(
                self._artifact_authority.stage(
                    reference=reference,
                    destination=input_root.joinpath(*reference.storage_key.split("/")),
                ),
                job=job,
            )

        capability_key = Path(
            environment.get("PUBLIC_WORKER_CAPABILITY_KEY_FILE", "").strip()
        )
        if not capability_key.is_absolute() or not capability_key.is_file():
            raise TrustedWorkerWireError("trusted_worker_capability_key_unavailable")
        container_name = (
            f"presenton-worker-{str(job.operation_id)[:12]}-"
            f"{job.fencing_token}-{uuid.uuid4().hex[:8]}"
        )
        command = self._docker_run_command(
            container_name=container_name,
            work_item=work_item,
            job=job,
            input_root=input_root,
            output_root=output_root,
            capability_key=capability_key,
        )
        async with self._active_lock:
            if self._closed:
                raise TrustedWorkerWireError("trusted_worker_transport_closed")
            self._active.add(container_name)
        process: asyncio.subprocess.Process | None = None
        stdout_task: asyncio.Task[tuple[bytes, bool]] | None = None
        stderr_task: asyncio.Task[tuple[bytes, bool]] | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            stdout_task = asyncio.create_task(
                _read_bounded(process.stdout, MAX_WIRE_STDOUT_BYTES)
            )
            stderr_task = asyncio.create_task(
                _read_bounded(process.stderr, MAX_WIRE_STDERR_BYTES, keep_tail=True)
            )
            assert process.stdin is not None
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            try:
                await asyncio.wait_for(
                    process.wait(), timeout=spec.limits.deadline_seconds
                )
            except asyncio.TimeoutError as error:
                await self._force_remove(container_name)
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task)
                raise TrustedWorkerWireError("deadline_exceeded") from error
            stdout, stdout_overflow = await stdout_task
            _stderr_tail, _stderr_overflow = await stderr_task
            state = await self._inspect_state(container_name)
            if state.get("OOMKilled") is True:
                raise TrustedWorkerWireError("memory_limit_exceeded")
            if stdout_overflow:
                raise TrustedWorkerWireError("trusted_worker_result_too_large")
            output_reference = _validated_success_output(stdout, job)
            if output_reference is not None:
                if process.returncode != 0:
                    raise TrustedWorkerWireError("trusted_worker_success_exit_mismatch")
                output_path = output_root.joinpath(
                    *output_reference.storage_key.split("/")
                )
                _verify_artifact_file(output_reference, output_path)
                await _before_deadline(
                    self._artifact_authority.publish(
                        reference=output_reference,
                        source=output_path,
                    ),
                    job=job,
                )
            return TrustedWorkerWireResult(
                stdout=stdout,
                exit_code=int(process.returncode or 0),
            )
        except asyncio.CancelledError:
            if process is not None:
                if process.returncode is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
            await self._force_remove(container_name)
            pending = tuple(
                task for task in (stdout_task, stderr_task) if task is not None
            )
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            raise
        finally:
            await self._force_remove(container_name)
            async with self._active_lock:
                self._active.discard(container_name)

    def _docker_run_command(
        self,
        *,
        container_name: str,
        work_item: TrustedExecutorWorkItem,
        job: IsolatedWorkerJob,
        input_root: Path,
        output_root: Path,
        capability_key: Path,
    ) -> tuple[str, ...]:
        limits = work_item.launch_spec.limits
        worker_command = work_item.launch_spec.arguments
        if not worker_command or not worker_command[0].startswith("/"):
            raise TrustedWorkerWireError(
                "trusted_worker_container_command_must_be_absolute"
            )
        return (
            str(self._configuration.docker_executable),
            "run",
            "--interactive",
            "--name",
            container_name,
            "--network",
            "none",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(limits.process_limit),
            "--memory",
            str(limits.memory_bytes),
            "--memory-swap",
            str(limits.memory_bytes),
            "--cpus",
            f"{limits.cpu_rate_percent / 100:.2f}",
            "--tmpfs",
            (
                f"/tmp:rw,noexec,nosuid,nodev,size={limits.temporary_bytes},"
                f"nr_inodes={work_item.launch_spec.policy.temporary_file_limit}"
            ),
            "--mount",
            f"type=bind,source={input_root},target=/workspace/input,readonly",
            "--mount",
            f"type=bind,source={output_root},target=/workspace/output",
            "--mount",
            f"type=bind,source={capability_key},target=/run/worker-capability.key,readonly",
            "--env",
            "PUBLIC_WORKER_INPUT_STORE_ROOT=/workspace/input",
            "--env",
            "PUBLIC_WORKER_OUTPUT_STORE_ROOT=/workspace/output",
            "--env",
            "PUBLIC_WORKER_CAPABILITY_KEY_FILE=/run/worker-capability.key",
            "--env",
            f"PUBLIC_WORKER_ROLE={job.role}",
            "--env",
            "PUBLIC_WORKER_OS_ISOLATION=nonroot-readonly-tmpfs-network-none",
            "--env",
            "PUBLIC_WORKER_SESSIONLESS=1",
            "--env",
            "HOME=/tmp",
            "--env",
            "TMPDIR=/tmp",
            "--entrypoint",
            worker_command[0],
            self._configuration.image_reference,
            *worker_command[1:],
        )

    async def _inspect_state(self, container_name: str) -> Mapping[str, object]:
        process = await asyncio.create_subprocess_exec(
            str(self._configuration.docker_executable),
            "container",
            "inspect",
            container_name,
            "--format",
            "{{json .State}}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return {}
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, Mapping) else {}

    async def _force_remove(self, container_name: str) -> None:
        process = await asyncio.create_subprocess_exec(
            str(self._configuration.docker_executable),
            "container",
            "rm",
            "--force",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            await asyncio.wait_for(
                process.wait(), timeout=self._cleanup_timeout_seconds
            )
        except asyncio.TimeoutError as error:
            process.kill()
            await process.wait()
            raise TrustedWorkerWireError("trusted_worker_container_cleanup_timeout") from error
        if await self._inspect_state(container_name):
            raise TrustedWorkerWireError("trusted_worker_container_remained")

    async def close(self) -> None:
        async with self._active_lock:
            self._closed = True
            active = tuple(self._active)
        for container_name in active:
            await self._force_remove(container_name)


async def _read_bounded(
    stream: asyncio.StreamReader | None,
    limit: int,
    *,
    keep_tail: bool = False,
) -> tuple[bytes, bool]:
    if stream is None:
        raise TrustedWorkerWireError("trusted_worker_stdio_unavailable")
    chunks: list[bytes] = []
    size = 0
    overflow = False
    while True:
        chunk = await stream.read(64 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if keep_tail:
            chunks.append(chunk)
            while sum(map(len, chunks)) > limit and chunks:
                chunks.pop(0)
            overflow = overflow or size > limit
        elif size <= limit:
            chunks.append(chunk)
        else:
            overflow = True
    data = b"".join(chunks)
    return (data[-limit:] if keep_tail else data), overflow


async def _before_deadline(
    awaitable: Awaitable[object], *, job: IsolatedWorkerJob
) -> object:
    remaining = job.deadline_epoch_seconds - time.time()
    if remaining <= 0:
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise TrustedWorkerWireError("deadline_exceeded")
    try:
        return await asyncio.wait_for(awaitable, timeout=remaining)
    except asyncio.TimeoutError as error:
        raise TrustedWorkerWireError("deadline_exceeded") from error


__all__ = [
    "DockerSandboxConfiguration",
    "DockerTrustedWorkerWireTransport",
    "probe_docker_sandbox",
]
