"""Concrete bounded stdin/stdout transport for one-shot trusted workers.

Artifact bytes cross this boundary only as streams.  The transport stages the
exact primary ArtifactRef (and export-bundle slide refs), supervises the child
tree, validates the worker result binding, uploads the output to the configured
object authority, and returns success stdout only after object-store readback.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import BinaryIO, Protocol

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.isolation_policy import PlatformIsolationReport
from services.trusted_executor.isolation_policy import SandboxFilesystemSpec
from services.trusted_executor.process_supervisor import (
    OperationProcessSupervisor,
    ProcessDeadlineExceeded,
    ProcessMemoryLimitExceeded,
    ProcessSupervisorError,
)
from services.trusted_executor.product_adapter import TrustedExecutorWorkItem
from services.trusted_executor.worker_dispatcher import (
    TrustedWorkerWireError,
    TrustedWorkerWireResult,
)


MAX_WIRE_STDOUT_BYTES = 16 * 1024 * 1024
MAX_WIRE_STDERR_BYTES = 1024 * 1024
MAX_WIRE_STDIN_BYTES = 2 * 1024 * 1024
MAX_EXPORT_BUNDLE_BYTES = 8 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


class TrustedWorkerArtifactAuthority(Protocol):
    """Streaming byte authority used by the trusted transport."""

    async def stage(
        self, *, reference: ArtifactRef, destination: Path
    ) -> None: ...

    async def publish(
        self, *, reference: ArtifactRef, source: Path
    ) -> None: ...


class ArtifactObjectStoreWireAdapter:
    """Concrete bridge from the product ArtifactObjectStore to worker refs."""

    def __init__(self, object_store: object) -> None:
        required = ("open_read_stream", "put_stream", "head")
        if any(not callable(getattr(object_store, name, None)) for name in required):
            raise TypeError("artifact object store does not implement the streaming contract")
        self._object_store = object_store

    @staticmethod
    def _product_ref(reference: ArtifactRef) -> object:
        from models.sql.public_artifact import ArtifactRef as ProductArtifactRef

        return ProductArtifactRef.model_validate(reference.to_wire())

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
        product_ref = self._product_ref(reference)
        opened = await self._object_store.open_read_stream(product_ref)
        destination.parent.mkdir(parents=True, exist_ok=True)

        def copy() -> None:
            temporary = destination.with_name(f".{destination.name}.stage-{os.getpid()}.tmp")
            try:
                with temporary.open("xb") as target:
                    while True:
                        chunk = opened.body.read(_CHUNK_BYTES)
                        if not chunk:
                            break
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                os.replace(temporary, destination)
            finally:
                opened.close()
                temporary.unlink(missing_ok=True)

        await asyncio.to_thread(copy)
        _verify_artifact_file(reference, destination)

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        _verify_artifact_file(reference, source)
        product_ref = self._product_ref(reference)
        with source.open("rb") as stream:
            await self._object_store.put_stream(product_ref, stream)
        metadata = await self._object_store.head(product_ref)
        if metadata is None or (
            metadata.size_bytes != reference.size_bytes
            or metadata.media_type != reference.media_type
        ):
            raise TrustedWorkerWireError("trusted_worker_output_readback_failed")
        opened = await self._object_store.open_read_stream(product_ref)

        def verify_readback() -> None:
            digest = hashlib.sha256()
            size = 0
            try:
                while True:
                    chunk = opened.body.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
            finally:
                opened.close()
            if size != reference.size_bytes or digest.hexdigest() != reference.sha256:
                raise TrustedWorkerWireError("trusted_worker_output_readback_failed")

        await asyncio.to_thread(verify_readback)


@dataclass(frozen=True, slots=True)
class _CapturedProcess:
    stdout: bytes
    stderr_tail: bytes
    exit_code: int
    stdout_overflow: bool


class SupervisedStdioWireTransport:
    """Job-owned concrete wire transport with bounded output and cancellation."""

    def __init__(
        self,
        *,
        artifact_authority: TrustedWorkerArtifactAuthority,
        isolation_report: PlatformIsolationReport,
        allow_local_development_isolation_gap: bool = False,
        zero_timeout_seconds: float = 5.0,
    ) -> None:
        if zero_timeout_seconds <= 0:
            raise ValueError("zero timeout must be positive")
        self._artifact_authority = artifact_authority
        self._isolation_report = isolation_report
        self._allow_local_development_isolation_gap = (
            allow_local_development_isolation_gap
        )
        self._zero_timeout_seconds = zero_timeout_seconds
        self._closed = False
        self._active: set[OperationProcessSupervisor] = set()
        self._active_lock = asyncio.Lock()

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
        if len(payload) > MAX_WIRE_STDIN_BYTES:
            raise TrustedWorkerWireError("trusted_worker_job_too_large")
        spec = work_item.launch_spec
        if spec.filesystem is None:
            raise TrustedWorkerWireError("trusted_worker_filesystem_missing")
        object_root = spec.filesystem.input_root
        output_root = spec.filesystem.sandbox_root / "output"
        spec.filesystem.sandbox_root.mkdir(parents=True, exist_ok=True)
        spec.filesystem.temporary_root.mkdir(parents=True, exist_ok=True)
        output_root.mkdir(parents=True, exist_ok=True)
        primary_path = object_root.joinpath(*job.input_artifact.storage_key.split("/"))
        if primary_path != spec.filesystem.staged_inputs[0]:
            raise TrustedWorkerWireError("trusted_worker_stage_binding_mismatch")
        await self._artifact_authority.stage(
            reference=job.input_artifact,
            destination=primary_path,
        )
        references = _export_bundle_references(job, primary_path)
        staged_paths = [primary_path]
        for reference in references:
            destination = object_root.joinpath(*reference.storage_key.split("/"))
            await self._artifact_authority.stage(
                reference=reference,
                destination=destination,
            )
            staged_paths.append(destination)
        spec = replace(
            spec,
            filesystem=SandboxFilesystemSpec(
                sandbox_root=spec.filesystem.sandbox_root,
                input_root=object_root,
                temporary_root=spec.filesystem.temporary_root,
                staged_inputs=tuple(staged_paths),
            ),
        )
        if self._allow_local_development_isolation_gap:
            if (
                os.name != "nt"
                or self._isolation_report.backend != "windows-job-object-only"
                or not self._isolation_report.resource_limits_supported
            ):
                raise TrustedWorkerWireError(
                    "trusted_worker_local_development_policy_unavailable"
                )
        else:
            spec.require_os_enforcement(self._isolation_report)

        child_environment = dict(environment)
        child_environment.pop("PUBLIC_WORKER_OBJECT_STORE_ROOT", None)
        child_environment["PUBLIC_WORKER_INPUT_STORE_ROOT"] = str(object_root)
        child_environment["PUBLIC_WORKER_OUTPUT_STORE_ROOT"] = str(output_root)
        if self._allow_local_development_isolation_gap:
            child_environment["PUBLIC_WORKER_LOCAL_DEVELOPMENT"] = "1"
        capability_key = child_environment.get(
            "PUBLIC_WORKER_CAPABILITY_KEY_FILE", ""
        ).strip()
        if not capability_key or not Path(capability_key).is_absolute():
            raise TrustedWorkerWireError("trusted_worker_capability_key_unavailable")

        supervisor = OperationProcessSupervisor(spec.identity, spec.limits)
        async with self._active_lock:
            if self._closed:
                await asyncio.to_thread(supervisor.close)
                raise TrustedWorkerWireError("trusted_worker_transport_closed")
            self._active.add(supervisor)
        try:
            process = await asyncio.to_thread(
                supervisor.launch,
                spec,
                cwd=spec.filesystem.sandbox_root,
                environment=child_environment,
                pipe_stdio=True,
            )
            communication = asyncio.create_task(
                asyncio.to_thread(_communicate, process, payload)
            )
            try:
                captured = await asyncio.shield(communication)
            except asyncio.CancelledError:
                await asyncio.to_thread(
                    supervisor.cancel, zero_timeout=self._zero_timeout_seconds
                )
                try:
                    await communication
                except BaseException:
                    pass
                raise
            if not await asyncio.to_thread(
                supervisor.wait_for_zero, timeout=self._zero_timeout_seconds
            ):
                await asyncio.to_thread(
                    supervisor.cancel, zero_timeout=self._zero_timeout_seconds
                )
                raise TrustedWorkerWireError("trusted_worker_descendants_remained")
            for reference, staged_path in zip(
                (job.input_artifact, *references), staged_paths, strict=True
            ):
                _verify_artifact_file(reference, staged_path)
        except asyncio.CancelledError:
            await asyncio.to_thread(
                supervisor.cancel, zero_timeout=self._zero_timeout_seconds
            )
            raise
        except ProcessDeadlineExceeded as error:
            raise TrustedWorkerWireError("deadline_exceeded") from error
        except ProcessMemoryLimitExceeded as error:
            raise TrustedWorkerWireError("memory_limit_exceeded") from error
        except ProcessSupervisorError as error:
            raise TrustedWorkerWireError("trusted_worker_supervision_failed") from error
        finally:
            async with self._active_lock:
                self._active.discard(supervisor)
            await asyncio.to_thread(supervisor.close)

        if captured.stdout_overflow:
            raise TrustedWorkerWireError("trusted_worker_result_too_large")
        output_reference = _validated_success_output(captured.stdout, job)
        if output_reference is not None:
            if captured.exit_code != 0:
                raise TrustedWorkerWireError("trusted_worker_success_exit_mismatch")
            output_path = output_root.joinpath(*output_reference.storage_key.split("/"))
            _verify_artifact_file(output_reference, output_path)
            await self._artifact_authority.publish(
                reference=output_reference,
                source=output_path,
            )
        return TrustedWorkerWireResult(
            stdout=captured.stdout,
            exit_code=captured.exit_code,
        )

    async def close(self) -> None:
        async with self._active_lock:
            self._closed = True
            active = tuple(self._active)
        for supervisor in active:
            await asyncio.to_thread(
                supervisor.cancel, zero_timeout=self._zero_timeout_seconds
            )


def _communicate(process: object, payload: bytes) -> _CapturedProcess:
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise TrustedWorkerWireError("trusted_worker_stdio_unavailable")
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    stdout_size = 0
    stderr_size = 0
    stdout_overflow = False
    read_error: BaseException | None = None

    def drain(stream: BinaryIO, *, stdout: bool) -> None:
        nonlocal stdout_size, stderr_size, stdout_overflow, read_error
        try:
            while True:
                chunk = stream.read(64 * 1024)
                if not chunk:
                    return
                if stdout:
                    if stdout_size + len(chunk) <= MAX_WIRE_STDOUT_BYTES:
                        stdout_parts.append(chunk)
                    else:
                        stdout_overflow = True
                    stdout_size += len(chunk)
                else:
                    stderr_parts.append(chunk)
                    stderr_size += len(chunk)
                    while stderr_size > MAX_WIRE_STDERR_BYTES and stderr_parts:
                        removed = stderr_parts.pop(0)
                        stderr_size -= len(removed)
        except BaseException as error:
            read_error = error

    stdout_thread = threading.Thread(
        target=drain, args=(process.stdout,), kwargs={"stdout": True}, daemon=True
    )
    stderr_thread = threading.Thread(
        target=drain, args=(process.stderr,), kwargs={"stdout": False}, daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()
    try:
        process.stdin.write(payload)
        process.stdin.close()
        exit_code = process.wait()
    finally:
        stdout_thread.join(timeout=5.0)
        stderr_thread.join(timeout=5.0)
    if stdout_thread.is_alive() or stderr_thread.is_alive():
        raise TrustedWorkerWireError("trusted_worker_stdio_drain_incomplete")
    if read_error is not None:
        raise TrustedWorkerWireError("trusted_worker_stdio_read_failed") from read_error
    return _CapturedProcess(
        stdout=b"".join(stdout_parts),
        stderr_tail=b"".join(stderr_parts)[-MAX_WIRE_STDERR_BYTES:],
        exit_code=exit_code,
        stdout_overflow=stdout_overflow,
    )


def _verify_artifact_file(reference: ArtifactRef, path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise TrustedWorkerWireError("trusted_worker_artifact_unavailable") from error
    if size != reference.size_bytes:
        raise TrustedWorkerWireError("trusted_worker_artifact_size_mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    if digest.hexdigest() != reference.sha256:
        raise TrustedWorkerWireError("trusted_worker_artifact_digest_mismatch")


def _export_bundle_references(job: IsolatedWorkerJob, path: Path) -> tuple[ArtifactRef, ...]:
    if job.role != "export":
        return ()
    if job.input_artifact.size_bytes > MAX_EXPORT_BUNDLE_BYTES:
        raise TrustedWorkerWireError("trusted_worker_export_bundle_too_large")
    try:
        with path.open("rb") as stream:
            value = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedWorkerWireError("trusted_worker_export_bundle_invalid") from error
    if not isinstance(value, Mapping) or not isinstance(value.get("slides"), list):
        raise TrustedWorkerWireError("trusted_worker_export_bundle_invalid")
    result: list[ArtifactRef] = []
    for entry in value["slides"]:
        raw = entry.get("artifactRef") if isinstance(entry, Mapping) else None
        if raw is None:
            raw = entry
        if not isinstance(raw, Mapping):
            raise TrustedWorkerWireError("trusted_worker_export_slide_ref_invalid")
        try:
            reference = ArtifactRef.from_wire(raw)
        except ValueError as error:
            raise TrustedWorkerWireError("trusted_worker_export_slide_ref_invalid") from error
        if (
            reference.owner_id != job.input_artifact.owner_id
            or reference.tenant_id != job.input_artifact.tenant_id
            or reference.kind != "slide-png"
            or reference.media_type != "image/png"
        ):
            raise TrustedWorkerWireError("trusted_worker_export_slide_binding_mismatch")
        result.append(reference)
    if not 1 <= len(result) <= 100:
        raise TrustedWorkerWireError("trusted_worker_export_slide_count_invalid")
    return tuple(result)


def _validated_success_output(payload: bytes, job: IsolatedWorkerJob) -> ArtifactRef | None:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise TrustedWorkerWireError("trusted_worker_result_invalid") from error
    if not isinstance(value, Mapping):
        raise TrustedWorkerWireError("trusted_worker_result_invalid")
    if value.get("ok") is not True:
        return None
    bindings = (
        value.get("role") == job.role,
        value.get("operationId") == str(job.operation_id),
        value.get("fencingToken") == job.fencing_token,
        value.get("inputDigest") == job.input_digest,
        value.get("attemptTokenSha256")
        == hashlib.sha256(job.attempt_token.encode("utf-8")).hexdigest(),
    )
    if not all(bindings) or not isinstance(value.get("outputArtifact"), Mapping):
        raise TrustedWorkerWireError("trusted_worker_result_binding_mismatch")
    try:
        reference = ArtifactRef.from_wire(value["outputArtifact"])
    except ValueError as error:
        raise TrustedWorkerWireError("trusted_worker_output_ref_invalid") from error
    if (
        reference.owner_id != job.input_artifact.owner_id
        or reference.tenant_id != job.input_artifact.tenant_id
        or reference.created_by_operation_id != job.operation_id
    ):
        raise TrustedWorkerWireError("trusted_worker_output_binding_mismatch")
    return reference


__all__ = [
    "ArtifactObjectStoreWireAdapter",
    "SupervisedStdioWireTransport",
    "TrustedWorkerArtifactAuthority",
]
