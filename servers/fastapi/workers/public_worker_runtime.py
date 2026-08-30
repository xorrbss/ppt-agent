"""Shared one-shot runtime and local CAS adapter for isolated public workers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import sys
import time
import shutil
import uuid
from collections.abc import Awaitable, Callable, Mapping

from services.isolated_worker_contracts import (
    ISOLATED_RESULT_SCHEMA,
    ArtifactRef,
    CapabilityReplayGuard,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    WorkerRole,
    verify_and_consume_worker_capability,
)
from services.isolated_worker_process import (
    MAX_JOB_BYTES,
    NETWORK_NONE_ATTESTATION,
    WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION,
)


MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_REPLAY_GUARD = CapabilityReplayGuard()


class PublicWorkerExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LocalContentAddressedStore:
    """Development adapter; production injects the object-store mount/sidecar."""

    def __init__(
        self,
        input_root: pathlib.Path,
        output_root: pathlib.Path | None = None,
    ) -> None:
        resolved_output = input_root if output_root is None else output_root
        if not input_root.is_absolute() or not resolved_output.is_absolute():
            raise PublicWorkerExecutionError("public_worker_store_root_not_absolute")
        self.input_root = input_root.resolve(strict=False)
        self.output_root = resolved_output.resolve(strict=False)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def _path(self, storage_key: str, *, output: bool = False) -> pathlib.Path:
        root = self.output_root if output else self.input_root
        candidate = (root / storage_key).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise PublicWorkerExecutionError(
                "public_worker_storage_key_escape"
            ) from error
        return candidate

    def verified_path(self, reference: ArtifactRef) -> pathlib.Path:
        path = self._path(reference.storage_key)
        if not path.is_file() and self.output_root != self.input_root:
            path = self._path(reference.storage_key, output=True)
        try:
            size = path.stat().st_size
        except OSError as error:
            raise PublicWorkerExecutionError("public_worker_artifact_unavailable") from error
        if size != reference.size_bytes or size > MAX_ARTIFACT_BYTES:
            raise PublicWorkerExecutionError("public_worker_artifact_size_mismatch")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != reference.sha256:
            raise PublicWorkerExecutionError("public_worker_artifact_digest_mismatch")
        return path

    def read(self, reference: ArtifactRef) -> bytes:
        return self.verified_path(reference).read_bytes()

    def put(
        self,
        payload: bytes,
        *,
        source: ArtifactRef,
        operation_id: uuid.UUID,
        kind: str,
        media_type: str,
    ) -> ArtifactRef:
        if not payload or len(payload) > MAX_ARTIFACT_BYTES:
            raise PublicWorkerExecutionError("public_worker_output_size_invalid")
        temporary_source = self.output_root / f".wire-output-{uuid.uuid4().hex}.tmp"
        try:
            temporary_source.write_bytes(payload)
            return self.put_path(
                temporary_source,
                source=source,
                operation_id=operation_id,
                kind=kind,
                media_type=media_type,
            )
        finally:
            temporary_source.unlink(missing_ok=True)

    def put_path(
        self,
        source_path: pathlib.Path,
        *,
        source: ArtifactRef,
        operation_id: uuid.UUID,
        kind: str,
        media_type: str,
    ) -> ArtifactRef:
        size_bytes = source_path.stat().st_size
        if size_bytes <= 0 or size_bytes > MAX_ARTIFACT_BYTES:
            raise PublicWorkerExecutionError("public_worker_output_size_invalid")
        digest_state = hashlib.sha256()
        with source_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest_state.update(chunk)
        digest = digest_state.hexdigest()
        tenant_scope = hashlib.sha256(source.tenant_id.encode("utf-8")).hexdigest()
        storage_key = (
            f"v1/tenant/{tenant_scope}/owner/{source.owner_id}/"
            f"sha256/{digest[:2]}/{digest}"
        )
        path = self._path(storage_key, output=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            if path.exists():
                existing_digest = hashlib.sha256()
                with path.open("rb") as existing:
                    while chunk := existing.read(1024 * 1024):
                        existing_digest.update(chunk)
                if existing_digest.hexdigest() != digest:
                    raise PublicWorkerExecutionError("public_worker_cas_conflict")
            else:
                with temporary.open("xb") as stream:
                    with source_path.open("rb") as source_stream:
                        shutil.copyfileobj(source_stream, stream, 1024 * 1024)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return ArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=source.owner_id,
            tenant_id=source.tenant_id,
            kind=kind,
            sha256=digest,
            size_bytes=size_bytes,
            media_type=media_type,
            storage_key=storage_key,
            created_by_operation_id=operation_id,
            retention_class="ephemeral",
        )


WorkerExecutor = Callable[
    [IsolatedWorkerJob, LocalContentAddressedStore],
    Awaitable[ArtifactRef],
]


def _read_signing_key() -> bytes:
    path_value = os.getenv("PUBLIC_WORKER_CAPABILITY_KEY_FILE", "").strip()
    if not path_value or not pathlib.Path(path_value).is_absolute():
        raise PublicWorkerExecutionError("public_worker_capability_key_file_required")
    try:
        key = pathlib.Path(path_value).read_bytes()
    except OSError as error:
        raise PublicWorkerExecutionError(
            "public_worker_capability_key_unavailable"
        ) from error
    if len(key) < 32 or len(key) > 4096:
        raise PublicWorkerExecutionError("public_worker_capability_key_invalid")
    return key


def _read_job() -> IsolatedWorkerJob:
    payload = sys.stdin.buffer.read(MAX_JOB_BYTES + 1)
    if not payload or len(payload) > MAX_JOB_BYTES:
        raise PublicWorkerExecutionError("public_worker_job_wire_limit")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PublicWorkerExecutionError("public_worker_job_invalid_json") from error
    if not isinstance(value, Mapping):
        raise PublicWorkerExecutionError("public_worker_job_invalid_shape")
    return IsolatedWorkerJob.from_wire(value)


def _base_result(job: IsolatedWorkerJob) -> dict[str, object]:
    return {
        "schemaVersion": ISOLATED_RESULT_SCHEMA,
        "role": job.role,
        "operationId": str(job.operation_id),
        "attemptTokenSha256": hashlib.sha256(
            job.attempt_token.encode("utf-8")
        ).hexdigest(),
        "fencingToken": job.fencing_token,
        "inputDigest": job.input_digest,
    }


def _write_result(value: Mapping[str, object]) -> None:
    sys.stdout.buffer.write(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )
    sys.stdout.buffer.flush()


async def run_worker(role: WorkerRole, executor: WorkerExecutor) -> int:
    job: IsolatedWorkerJob | None = None
    try:
        if os.getenv("PUBLIC_WORKER_SESSIONLESS") != "1":
            raise PublicWorkerExecutionError("public_worker_sessionless_marker_missing")
        if os.getenv("PUBLIC_WORKER_ROLE") != role:
            raise PublicWorkerExecutionError("public_worker_role_mismatch")
        isolation_attestation = os.getenv("PUBLIC_WORKER_OS_ISOLATION", "")
        local_development = os.getenv("PUBLIC_WORKER_LOCAL_DEVELOPMENT") == "1"
        isolation_is_valid = (
            isolation_attestation == NETWORK_NONE_ATTESTATION
            and not local_development
        ) or (
            isolation_attestation == WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION
            and local_development
        )
        if not isolation_is_valid:
            raise PublicWorkerExecutionError("public_worker_network_none_required")
        forbidden = [
            name
            for name in os.environ
            if name != "PUBLIC_WORKER_SESSIONLESS"
            if any(
                marker in name.upper()
                for marker in (
                    "API_KEY",
                    "AUTH_TOKEN",
                    "BEARER",
                    "COOKIE",
                    "DATABASE_URL",
                    "PASSWORD",
                    "PROVIDER",
                    "SESSION",
                )
            )
        ]
        if forbidden:
            raise PublicWorkerExecutionError("public_worker_environment_not_sessionless")
        job = _read_job()
        if job.role != role:
            raise PublicWorkerExecutionError("public_worker_role_mismatch")
        if job.deadline_epoch_seconds < int(time.time()):
            raise PublicWorkerExecutionError("public_worker_job_expired")
        verify_and_consume_worker_capability(
            job,
            signing_key=_read_signing_key(),
            replay_guard=_REPLAY_GUARD,
        )
        legacy_root = os.getenv("PUBLIC_WORKER_OBJECT_STORE_ROOT", "").strip()
        input_root_value = os.getenv("PUBLIC_WORKER_INPUT_STORE_ROOT", "").strip()
        output_root_value = os.getenv("PUBLIC_WORKER_OUTPUT_STORE_ROOT", "").strip()
        input_root_value = input_root_value or legacy_root
        output_root_value = output_root_value or legacy_root
        if not input_root_value or not output_root_value:
            raise PublicWorkerExecutionError("public_worker_store_root_required")
        output = await executor(
            job,
            LocalContentAddressedStore(
                pathlib.Path(input_root_value),
                pathlib.Path(output_root_value),
            ),
        )
        if (
            output.owner_id != job.input_artifact.owner_id
            or output.tenant_id != job.input_artifact.tenant_id
            or output.created_by_operation_id != job.operation_id
        ):
            raise PublicWorkerExecutionError("public_worker_output_binding_mismatch")
        _write_result(
            {
                **_base_result(job),
                "ok": True,
                "errorCode": None,
                "outputArtifact": output.to_wire(),
            }
        )
        return 0
    except Exception as error:
        code = (
            error.code
            if isinstance(error, PublicWorkerExecutionError)
            else "public_worker_contract_invalid"
            if isinstance(error, IsolatedWorkerContractError)
            else "public_worker_unexpected_failure"
        )
        if job is not None:
            _write_result(
                {
                    **_base_result(job),
                    "ok": False,
                    "errorCode": code,
                    "outputArtifact": None,
                }
            )
        return 1


def main(role: WorkerRole, executor: WorkerExecutor) -> int:
    return asyncio.run(run_worker(role, executor))


__all__ = [
    "LocalContentAddressedStore",
    "PublicWorkerExecutionError",
    "main",
    "run_worker",
]
