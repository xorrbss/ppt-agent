"""Concrete bounded stdin/stdout transport for one-shot trusted workers.

Artifact bytes cross this boundary only as streams.  The transport stages the
exact primary ArtifactRef (and export-bundle slide refs), supervises the child
tree, validates the worker result binding, uploads the output to the configured
object authority, and returns success stdout only after object-store readback.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import shutil
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import time
from typing import BinaryIO, Protocol

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.isolation_policy import PlatformIsolationReport, SandboxFilesystemSpec
from services.trusted_executor.process_supervisor import (
    OperationProcessSupervisor,
    ProcessDeadlineExceeded,
    ProcessMemoryLimitExceeded,
    ProcessSupervisorError,
)
from services.trusted_executor.product_adapter import TrustedExecutorWorkItem
from services.trusted_executor.dispatch_evidence import (
    DISPATCH_EVIDENCE_KEY,
    begin_supervised_invocation,
    seal_supervised_dispatch_evidence,
)
from services.trusted_executor.wire_bundle_references import (
    MAX_EXPORT_BUNDLE_BYTES as MAX_EXPORT_BUNDLE_BYTES,
    _input_bundle_references as _input_bundle_references,
    _staged_bundle_slides as _staged_bundle_slides,
    _render_output_bundle_references as _render_output_bundle_references,
    _validated_success_output as _validated_success_output,
)
from services.trusted_executor.worker_dispatcher import (
    TrustedWorkerWireError,
    TrustedWorkerWireResult,
)
from constants.timeouts import SUBPROCESS_DRAIN_JOIN_SECONDS


MAX_WIRE_STDOUT_BYTES = 16 * 1024 * 1024
MAX_WIRE_STDERR_BYTES = 1024 * 1024
MAX_WIRE_STDIN_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_ARTIFACT_BYTES = 512 * 1024 * 1024
DEFAULT_STAGING_CAPACITY_BYTES = 4 * 1024 * 1024 * 1024
_CHUNK_BYTES = 1024 * 1024


class TrustedWorkerArtifactAuthority(Protocol):
    """Streaming byte authority used by the trusted transport."""

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None: ...

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None: ...


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

        cancelled = threading.Event()

        def copy() -> None:
            temporary = destination.with_name(f".{destination.name}.stage-{uuid.uuid4().hex}.tmp")
            digest = hashlib.sha256()
            size = 0
            try:
                with temporary.open("xb") as target:
                    while True:
                        if cancelled.is_set():
                            return
                        chunk = opened.body.read(_CHUNK_BYTES)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > reference.size_bytes:
                            raise TrustedWorkerWireError("trusted_worker_artifact_size_mismatch")
                        digest.update(chunk)
                        target.write(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                if size != reference.size_bytes:
                    raise TrustedWorkerWireError("trusted_worker_artifact_size_mismatch")
                if digest.hexdigest() != reference.sha256:
                    raise TrustedWorkerWireError("trusted_worker_artifact_digest_mismatch")
                if not cancelled.is_set():
                    os.replace(temporary, destination)
            finally:
                try:
                    opened.close()
                finally:
                    temporary.unlink(missing_ok=True)

        # Cancelling a to_thread await does not stop its filesystem writes.
        # Retain the copy task until its stream and temporary file are closed,
        # including when another cancellation arrives while draining it.
        copying = asyncio.create_task(asyncio.to_thread(copy))
        try:
            await asyncio.shield(copying)
        except asyncio.CancelledError:
            cancelled.set()
            while not copying.done():
                try:
                    await asyncio.shield(copying)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not copying.cancelled():
                copying.exception()
            raise

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        _verify_artifact_file(reference, source)
        product_ref = self._product_ref(reference)
        with source.open("rb") as stream:
            await self._object_store.put_stream(product_ref, stream)
        metadata = await self._object_store.head(product_ref)
        if metadata is None or (
            metadata.size_bytes != reference.size_bytes or metadata.media_type != reference.media_type
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


@dataclass(frozen=True, slots=True)
class _StagedArtifact:
    reference: ArtifactRef
    destination: Path


class _StagingCapacitySupervisor:
    """Process-local admission for job-owned staging disk reservations."""

    def __init__(self, capacity_bytes: int) -> None:
        if capacity_bytes <= 0:
            raise ValueError("staging capacity must be positive")
        self.capacity_bytes = capacity_bytes
        self._reserved_bytes = 0
        self._lock = asyncio.Lock()

    async def reserve(self, size_bytes: int) -> None:
        if size_bytes < 0:
            raise ValueError("staging reservation must be non-negative")
        async with self._lock:
            if self._reserved_bytes + size_bytes > self.capacity_bytes:
                raise TrustedWorkerWireError("trusted_worker_staging_capacity_exceeded")
            self._reserved_bytes += size_bytes

    async def release(self, size_bytes: int) -> None:
        async with self._lock:
            self._reserved_bytes = max(0, self._reserved_bytes - size_bytes)


class _StagingReservation:
    def __init__(self, supervisor: _StagingCapacitySupervisor, reserved_bytes: int) -> None:
        self._supervisor = supervisor
        self._reserved_bytes = reserved_bytes

    async def grow(self, size_bytes: int) -> None:
        await self._supervisor.reserve(size_bytes)
        self._reserved_bytes += size_bytes

    async def release(self) -> None:
        reserved_bytes = self._reserved_bytes
        self._reserved_bytes = 0
        if reserved_bytes:
            await self._supervisor.release(reserved_bytes)


class SupervisedStdioWireTransport:
    """Job-owned concrete wire transport with bounded output and cancellation."""

    def __init__(
        self,
        *,
        artifact_authority: TrustedWorkerArtifactAuthority,
        isolation_report: PlatformIsolationReport,
        allow_local_development_isolation_gap: bool = False,
        zero_timeout_seconds: float = 5.0,
        supervisor_factory: Callable[..., OperationProcessSupervisor] = OperationProcessSupervisor,
        staging_capacity_bytes: int = DEFAULT_STAGING_CAPACITY_BYTES,
    ) -> None:
        if zero_timeout_seconds <= 0:
            raise ValueError("zero timeout must be positive")
        self._artifact_authority = artifact_authority
        self._isolation_report = isolation_report
        self._allow_local_development_isolation_gap = allow_local_development_isolation_gap
        self._supervisor_factory = supervisor_factory
        self._zero_timeout_seconds = zero_timeout_seconds
        self._staging_capacity = _StagingCapacitySupervisor(staging_capacity_bytes)
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
        try:
            configured_output_limit = int(
                environment.get("PPT_WORKER_OUTPUT_LIMIT_BYTES", str(MAX_OUTPUT_ARTIFACT_BYTES))
            )
        except ValueError as error:
            raise TrustedWorkerWireError("trusted_worker_output_limit_invalid") from error
        if configured_output_limit <= 0:
            raise TrustedWorkerWireError("trusted_worker_output_limit_invalid")
        output_limit = min(MAX_OUTPUT_ARTIFACT_BYTES, configured_output_limit)
        try:
            output_reservation = int(environment.get("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(output_limit)))
        except ValueError as error:
            raise TrustedWorkerWireError("trusted_worker_cache_limits_invalid") from error
        if output_reservation <= 0:
            raise TrustedWorkerWireError("trusted_worker_cache_limits_invalid")
        spec = work_item.launch_spec
        if spec.filesystem is None:
            raise TrustedWorkerWireError("trusted_worker_filesystem_missing")
        reserved_bytes = job.input_artifact.size_bytes + output_reservation + spec.limits.temporary_bytes
        await self._staging_capacity.reserve(reserved_bytes)
        reservation = _StagingReservation(self._staging_capacity, reserved_bytes)
        try:
            return await self._execute_admitted(
                work_item=work_item,
                job=job,
                payload=payload,
                environment=environment,
                output_limit=output_limit,
                reservation=reservation,
            )
        finally:
            try:
                await asyncio.to_thread(_remove_operation_staging, spec.filesystem)
            finally:
                await reservation.release()

    async def _execute_admitted(
        self,
        *,
        work_item: TrustedExecutorWorkItem,
        job: IsolatedWorkerJob,
        payload: bytes,
        environment: Mapping[str, str],
        output_limit: int,
        reservation: _StagingReservation,
    ) -> TrustedWorkerWireResult:
        spec = work_item.launch_spec
        assert spec.filesystem is not None
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
        references = _input_bundle_references(job, primary_path)
        staging_plan = _physical_staging_plan(
            object_root=object_root,
            references=(job.input_artifact, *references),
        )
        if staging_plan[0].destination != primary_path:
            raise TrustedWorkerWireError("trusted_worker_stage_binding_mismatch")
        additional_input_bytes = sum(item.reference.size_bytes for item in staging_plan[1:])
        await reservation.grow(additional_input_bytes)
        for item in staging_plan[1:]:
            await self._artifact_authority.stage(
                reference=item.reference,
                destination=item.destination,
            )
        spec = replace(
            spec,
            filesystem=SandboxFilesystemSpec(
                sandbox_root=spec.filesystem.sandbox_root,
                input_root=object_root,
                temporary_root=spec.filesystem.temporary_root,
                staged_inputs=tuple(item.destination for item in staging_plan),
            ),
        )
        if self._allow_local_development_isolation_gap:
            if (
                os.name != "nt"
                or self._isolation_report.backend != "windows-job-object-only"
                or not self._isolation_report.resource_limits_supported
            ):
                raise TrustedWorkerWireError("trusted_worker_local_development_policy_unavailable")
        else:
            spec.require_os_enforcement(self._isolation_report)

        child_environment = dict(environment)
        child_environment.pop("PUBLIC_WORKER_OBJECT_STORE_ROOT", None)
        child_environment["PUBLIC_WORKER_INPUT_STORE_ROOT"] = str(object_root)
        child_environment["PUBLIC_WORKER_OUTPUT_STORE_ROOT"] = str(output_root)
        child_environment["TMPDIR"] = str(spec.filesystem.temporary_root)
        child_environment["TEMP"] = str(spec.filesystem.temporary_root)
        child_environment["TMP"] = str(spec.filesystem.temporary_root)
        if self._allow_local_development_isolation_gap:
            child_environment["PUBLIC_WORKER_LOCAL_DEVELOPMENT"] = "1"
        capability_key = child_environment.get("PUBLIC_WORKER_CAPABILITY_KEY_FILE", "").strip()
        if not capability_key or not Path(capability_key).is_absolute():
            raise TrustedWorkerWireError("trusted_worker_capability_key_unavailable")

        # Staging and scheduler waits cannot renew the admitted wall-clock budget.
        remaining_seconds = job.deadline_epoch_seconds - time.time()
        if remaining_seconds <= 0:
            raise TrustedWorkerWireError("deadline_exceeded")
        spec = replace(
            spec,
            limits=replace(
                spec.limits,
                deadline_seconds=min(spec.limits.deadline_seconds, remaining_seconds),
            ),
        )
        supervisor = self._supervisor_factory(spec.identity, spec.limits)
        resource_metrics: Mapping[str, object] = {}
        async with self._active_lock:
            if self._closed:
                await asyncio.to_thread(supervisor.close)
                raise TrustedWorkerWireError("trusted_worker_transport_closed")
            self._active.add(supervisor)

        def launch_observed():
            observation = begin_supervised_invocation((str(spec.executable), *spec.arguments))
            process = supervisor.launch(
                spec,
                cwd=spec.filesystem.sandbox_root,
                environment=child_environment,
                pipe_stdio=True,
            )
            return process, observation

        try:
            process, invocation_observation = await asyncio.to_thread(launch_observed)
            communication = asyncio.create_task(asyncio.to_thread(_communicate, process, payload))
            try:
                captured = await asyncio.shield(communication)
            except asyncio.CancelledError:
                await asyncio.to_thread(supervisor.cancel, zero_timeout=self._zero_timeout_seconds)
                try:
                    await communication
                except BaseException:
                    pass
                raise
            if not await asyncio.to_thread(supervisor.wait_for_zero, timeout=self._zero_timeout_seconds):
                await asyncio.to_thread(supervisor.cancel, zero_timeout=self._zero_timeout_seconds)
                raise TrustedWorkerWireError("trusted_worker_descendants_remained")
            tree_zero_at = datetime.now(timezone.utc)
            tree_zero_monotonic_ns = time.perf_counter_ns()
            for item in staging_plan:
                _verify_artifact_file(item.reference, item.destination)
            supervisor_metrics = await asyncio.to_thread(supervisor.telemetry)
            resource_metrics = {
                **supervisor_metrics,
                # This is derived from the exact platform report used by launch
                # admission.  Child environment markers are never accepted as
                # isolation evidence.
                "isolationAttestation": self._isolation_report.to_attestation(),
            }
        except asyncio.CancelledError:
            await asyncio.to_thread(supervisor.cancel, zero_timeout=self._zero_timeout_seconds)
            raise
        except ProcessDeadlineExceeded as error:
            metrics = await asyncio.to_thread(_supervisor_metrics_or_empty, supervisor)
            raise TrustedWorkerWireError("deadline_exceeded", resource_metrics=metrics) from error
        except ProcessMemoryLimitExceeded as error:
            metrics = await asyncio.to_thread(_supervisor_metrics_or_empty, supervisor)
            raise TrustedWorkerWireError("memory_limit_exceeded", resource_metrics=metrics) from error
        except ProcessSupervisorError as error:
            metrics = await asyncio.to_thread(_supervisor_metrics_or_empty, supervisor)
            raise TrustedWorkerWireError("trusted_worker_supervision_failed", resource_metrics=metrics) from error
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
            _verify_artifact_file(output_reference, output_path, max_bytes=output_limit)
            render_outputs, render_runtime = _render_output_bundle_references(
                job,
                output_reference=output_reference,
                path=output_path,
            )
            # Validate every child before publishing any part of a render bundle.
            # The child is untrusted and may bypass its own store/runtime checks.
            for render_output in render_outputs:
                render_output_path = output_root.joinpath(*render_output.storage_key.split("/"))
                _verify_artifact_file(render_output, render_output_path, max_bytes=output_limit)
            for render_output in render_outputs:
                render_output_path = output_root.joinpath(*render_output.storage_key.split("/"))
                await self._artifact_authority.publish(
                    reference=render_output,
                    source=render_output_path,
                )
            await self._artifact_authority.publish(
                reference=output_reference,
                source=output_path,
            )
            if render_runtime:
                resource_metrics = {**resource_metrics, **render_runtime}
            resource_metrics = {
                **resource_metrics,
                DISPATCH_EVIDENCE_KEY: seal_supervised_dispatch_evidence(
                    observation=invocation_observation,
                    tree_zero_at=tree_zero_at,
                    tree_zero_monotonic_ns=tree_zero_monotonic_ns,
                    job=job,
                    output=output_reference,
                    resource_metrics=resource_metrics,
                ),
            }
        return TrustedWorkerWireResult(
            stdout=captured.stdout,
            exit_code=captured.exit_code,
            resource_metrics=resource_metrics,
        )

    async def close(self) -> None:
        async with self._active_lock:
            self._closed = True
            active = tuple(self._active)
        for supervisor in active:
            await asyncio.to_thread(supervisor.cancel, zero_timeout=self._zero_timeout_seconds)


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

    stdout_thread = threading.Thread(target=drain, args=(process.stdout,), kwargs={"stdout": True}, daemon=True)
    stderr_thread = threading.Thread(target=drain, args=(process.stderr,), kwargs={"stdout": False}, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    try:
        process.stdin.write(payload)
        process.stdin.close()
        exit_code = process.wait()
    finally:
        stdout_thread.join(timeout=SUBPROCESS_DRAIN_JOIN_SECONDS)
        stderr_thread.join(timeout=SUBPROCESS_DRAIN_JOIN_SECONDS)
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


def _supervisor_metrics_or_empty(
    supervisor: OperationProcessSupervisor,
) -> Mapping[str, object]:
    try:
        return supervisor.telemetry()
    except ProcessSupervisorError:
        return {}


def _remove_operation_staging(filesystem: SandboxFilesystemSpec) -> None:
    """Remove only the validated job-owned sandbox tree."""

    root = filesystem.sandbox_root.resolve(strict=False)
    if root == Path(root.anchor):
        return
    children = (
        filesystem.input_root.resolve(strict=False),
        filesystem.temporary_root.resolve(strict=False),
        (filesystem.sandbox_root / "output").resolve(strict=False),
    )
    try:
        for child in children:
            child.relative_to(root)
    except ValueError:
        return
    shutil.rmtree(root, ignore_errors=True)


def _verify_artifact_file(reference: ArtifactRef, path: Path, *, max_bytes: int | None = None) -> None:
    if max_bytes is not None and reference.size_bytes > max_bytes:
        raise TrustedWorkerWireError("trusted_worker_output_limit_exceeded")
    try:
        size = path.stat().st_size
    except OSError as error:
        raise TrustedWorkerWireError("trusted_worker_artifact_unavailable") from error
    if max_bytes is not None and size > max_bytes:
        raise TrustedWorkerWireError("trusted_worker_output_limit_exceeded")
    if size != reference.size_bytes:
        raise TrustedWorkerWireError("trusted_worker_artifact_size_mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            digest.update(chunk)
    if digest.hexdigest() != reference.sha256:
        raise TrustedWorkerWireError("trusted_worker_artifact_digest_mismatch")


def _physical_staging_plan(*, object_root: Path, references: Sequence[ArtifactRef]) -> tuple[_StagedArtifact, ...]:
    plan: list[_StagedArtifact] = []
    by_destination: dict[Path, _StagedArtifact] = {}
    for reference in references:
        destination = object_root.joinpath(*reference.storage_key.split("/"))
        existing = by_destination.get(destination)
        if existing is None:
            item = _StagedArtifact(reference=reference, destination=destination)
            by_destination[destination] = item
            plan.append(item)
            continue
        if existing.reference.sha256 != reference.sha256 or existing.reference.size_bytes != reference.size_bytes:
            raise TrustedWorkerWireError("trusted_worker_stage_destination_collision")
    return tuple(plan)










__all__ = [
    "ArtifactObjectStoreWireAdapter",
    "SupervisedStdioWireTransport",
    "TrustedWorkerArtifactAuthority",
]
