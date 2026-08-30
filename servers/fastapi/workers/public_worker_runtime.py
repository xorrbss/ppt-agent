"""Shared one-shot runtime and digest-verified scratch for public workers."""

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
from services.public_worker_profiles import (
    get_public_worker_profile,
    validate_public_worker_environment,
)


MAX_ARTIFACT_BYTES = 512 * 1024 * 1024
_REPLAY_GUARD = CapabilityReplayGuard()


class PublicWorkerExecutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LocalContentAddressedStore:
    """Non-authoritative transport cache shared with one isolated worker."""

    def __init__(
        self,
        input_root: pathlib.Path,
        output_root: pathlib.Path | None = None,
    ) -> None:
        resolved_output = input_root if output_root is None else output_root
        if not input_root.is_absolute() or not resolved_output.is_absolute():
            raise PublicWorkerExecutionError("public_worker_store_root_not_absolute")
        try:
            self.max_cache_bytes = int(
                os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", "")
            )
            self.ttl_seconds = int(
                os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "")
            )
        except ValueError as error:
            raise PublicWorkerExecutionError(
                "public_worker_cache_limits_invalid"
            ) from error
        if self.max_cache_bytes <= 0 or self.ttl_seconds <= 0:
            raise PublicWorkerExecutionError("public_worker_cache_limits_invalid")
        self.input_root = input_root.resolve(strict=False)
        self.output_root = resolved_output.resolve(strict=False)
        self.output_root.mkdir(parents=True, exist_ok=True)

    def _entries(self) -> list[tuple[pathlib.Path, float, int]]:
        entries: list[tuple[pathlib.Path, float, int]] = []
        for path in self.output_root.rglob("*"):
            try:
                if path.is_symlink() or not path.is_file() or path.name.startswith("."):
                    continue
                stat = path.stat()
            except OSError:
                continue
            entries.append((path, stat.st_mtime, stat.st_size))
        return entries

    def _evict(self, *, protected: pathlib.Path | None = None) -> None:
        now = time.time()
        survivors: list[tuple[pathlib.Path, float, int]] = []
        for path, modified, size in self._entries():
            if path != protected and now - modified > self.ttl_seconds:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    survivors.append((path, modified, size))
            else:
                survivors.append((path, modified, size))
        total = sum(size for _, _, size in survivors)
        for path, _, size in sorted(survivors, key=lambda item: item[1]):
            if total <= self.max_cache_bytes:
                break
            if path == protected:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total -= size

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
        is_output = self.output_root == self.input_root or path.is_relative_to(
            self.output_root
        )
        try:
            stat = path.stat()
        except OSError as error:
            raise PublicWorkerExecutionError("public_worker_artifact_unavailable") from error
        if time.time() - stat.st_mtime > self.ttl_seconds:
            if is_output:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise PublicWorkerExecutionError("public_worker_artifact_expired")
        size = stat.st_size
        if size != reference.size_bytes or size > MAX_ARTIFACT_BYTES:
            raise PublicWorkerExecutionError("public_worker_artifact_size_mismatch")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != reference.sha256:
            raise PublicWorkerExecutionError("public_worker_artifact_digest_mismatch")
        if is_output:
            os.utime(path, None)
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
        try:
            configured_limit = int(
                os.getenv("PPT_WORKER_OUTPUT_LIMIT_BYTES", str(MAX_ARTIFACT_BYTES))
            )
        except ValueError as error:
            raise PublicWorkerExecutionError("public_worker_output_limit_invalid") from error
        output_limit = min(MAX_ARTIFACT_BYTES, configured_limit)
        if configured_limit <= 0:
            raise PublicWorkerExecutionError("public_worker_output_limit_invalid")
        if not payload or len(payload) > output_limit:
            raise PublicWorkerExecutionError("public_worker_output_size_invalid")
        if len(payload) > self.max_cache_bytes:
            raise PublicWorkerExecutionError("public_worker_cache_budget_exceeded")
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
        if size_bytes > self.max_cache_bytes:
            raise PublicWorkerExecutionError("public_worker_cache_budget_exceeded")
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
                if path.stat().st_size != size_bytes:
                    raise PublicWorkerExecutionError("public_worker_cas_conflict")
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
        os.utime(path, None)
        self._evict(protected=path)
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
        if not local_development:
            try:
                validate_public_worker_environment(
                    get_public_worker_profile(role),
                    os.environ,
                )
            except RuntimeError as error:
                raise PublicWorkerExecutionError(str(error)) from error
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
        cache_root = os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_ROOT", "").strip()
        input_root_value = os.getenv("PUBLIC_WORKER_INPUT_STORE_ROOT", "").strip()
        output_root_value = os.getenv("PUBLIC_WORKER_OUTPUT_STORE_ROOT", "").strip()
        input_root_value = input_root_value or cache_root
        output_root_value = output_root_value or cache_root
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
        try:
            output_limit = int(os.getenv("PPT_WORKER_OUTPUT_LIMIT_BYTES", ""))
        except ValueError as error:
            raise PublicWorkerExecutionError("public_worker_output_limit_invalid") from error
        if output_limit <= 0:
            raise PublicWorkerExecutionError("public_worker_output_limit_invalid")
        # LocalContentAddressedStore.put rejects before writing a CAS object.
        # Recheck the returned ref so a future injected store cannot bypass the
        # same one-shot worker contract.
        if output.size_bytes > min(MAX_ARTIFACT_BYTES, output_limit):
            raise PublicWorkerExecutionError("public_worker_output_size_invalid")
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
