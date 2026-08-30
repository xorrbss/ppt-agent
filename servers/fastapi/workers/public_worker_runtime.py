"""Shared one-shot runtime and digest-verified scratch for public workers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import sys
import time
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

    def __init__(self, root: pathlib.Path) -> None:
        if not root.is_absolute():
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
        self.root = root.resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)

    def _entries(self) -> list[tuple[pathlib.Path, float, int]]:
        entries: list[tuple[pathlib.Path, float, int]] = []
        for path in self.root.rglob("*"):
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

    def _path(self, storage_key: str) -> pathlib.Path:
        candidate = (self.root / storage_key).resolve(strict=False)
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise PublicWorkerExecutionError(
                "public_worker_storage_key_escape"
            ) from error
        return candidate

    def read(self, reference: ArtifactRef) -> bytes:
        path = self._path(reference.storage_key)
        try:
            stat = path.stat()
        except OSError as error:
            raise PublicWorkerExecutionError("public_worker_artifact_unavailable") from error
        if time.time() - stat.st_mtime > self.ttl_seconds:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise PublicWorkerExecutionError("public_worker_artifact_expired")
        size = stat.st_size
        if size != reference.size_bytes or size > MAX_ARTIFACT_BYTES:
            raise PublicWorkerExecutionError("public_worker_artifact_size_mismatch")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != reference.sha256:
            raise PublicWorkerExecutionError("public_worker_artifact_digest_mismatch")
        os.utime(path, None)
        return payload

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
        digest = hashlib.sha256(payload).hexdigest()
        tenant_scope = hashlib.sha256(source.tenant_id.encode("utf-8")).hexdigest()
        storage_key = (
            f"v1/tenant/{tenant_scope}/owner/{source.owner_id}/"
            f"sha256/{digest[:2]}/{digest}"
        )
        path = self._path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            if path.exists():
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise PublicWorkerExecutionError("public_worker_cas_conflict")
            else:
                with temporary.open("xb") as stream:
                    stream.write(payload)
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
            size_bytes=len(payload),
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
        if os.getenv("PUBLIC_WORKER_OS_ISOLATION") != NETWORK_NONE_ATTESTATION:
            raise PublicWorkerExecutionError("public_worker_network_none_required")
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
        root_value = os.getenv("PUBLIC_WORKER_ARTIFACT_CACHE_ROOT", "").strip()
        if not root_value:
            raise PublicWorkerExecutionError("public_worker_artifact_cache_root_required")
        output = await executor(
            job,
            LocalContentAddressedStore(pathlib.Path(root_value)),
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
