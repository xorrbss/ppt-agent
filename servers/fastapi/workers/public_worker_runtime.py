"""Shared one-shot runtime and digest-verified scratch for public workers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import sys
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field

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
from services.public_operation_correlation import log_public_operation_correlation
from workers.public_worker_store import (
    MAX_ARTIFACT_BYTES,
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
)
from workers.public_svg_projection_error import SvgExportProjectionError, projection_failure_detail

# An execution evidence summary rides back with the artifact so the durable
# receipt can say which path produced the file. It is a bounded, sanitized
# summary: never paths, never session material, never raw worker output.
# A source-preserving SVG export carries per-page readback proofs the controller
# re-verifies (~25 KB/page; a 100-page deck measured 2.49 MB), so the bound is
# sized for the admitted page cap and stays well inside the 16 MiB result wire.
# Must equal services.isolated_worker_process.MAX_EXECUTION_EVIDENCE_BYTES.
MAX_EXECUTION_EVIDENCE_BYTES = 8 * 1024 * 1024
_REPLAY_GUARD = CapabilityReplayGuard()


@dataclass(frozen=True, slots=True)
class WorkerOutput:
    """One worker artifact plus the bounded evidence describing how it was made."""

    artifact: "ArtifactRef"
    evidence: Mapping[str, object] = field(default_factory=dict)


WorkerExecutor = Callable[
    [IsolatedWorkerJob, LocalContentAddressedStore],
    Awaitable["ArtifactRef | WorkerOutput"],
]


def _normalized_output(value: "ArtifactRef | WorkerOutput") -> WorkerOutput:
    if isinstance(value, WorkerOutput):
        return value
    return WorkerOutput(artifact=value)


def _validated_execution_evidence(evidence: Mapping[str, object]) -> dict[str, object]:
    """Keep the evidence a bounded JSON object the parent can trust to decode."""

    if not evidence:
        return {}
    try:
        encoded = json.dumps(
            dict(evidence), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
    except (TypeError, ValueError) as error:
        raise PublicWorkerExecutionError(
            "public_worker_execution_evidence_invalid"
        ) from error
    if len(encoded) > MAX_EXECUTION_EVIDENCE_BYTES:
        raise PublicWorkerExecutionError("public_worker_execution_evidence_too_large")
    return json.loads(encoded)


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


async def _execute_before_job_deadline(
    *,
    job: IsolatedWorkerJob,
    executor: WorkerExecutor,
    store: LocalContentAddressedStore,
) -> "ArtifactRef | WorkerOutput":
    """Run one worker executor without allowing it to outlive its sealed job."""

    remaining_seconds = job.deadline_epoch_seconds - time.time()
    if remaining_seconds <= 0:
        raise PublicWorkerExecutionError("public_worker_job_expired")
    task = asyncio.create_task(executor(job, store))
    try:
        done, _pending = await asyncio.wait((task,), timeout=remaining_seconds)
        if task not in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise PublicWorkerExecutionError("public_worker_job_expired")
        return task.result()
    except BaseException:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        raise


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
            if not (
                name == "TRUSTED_EXECUTOR_SESSIONLESS"
                and os.environ[name] == "1"
            )
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
        component = "render" if role == "render" else "worker"
        log_public_operation_correlation(
            operation_id=job.operation_id,
            component=component,
            action="started",
            outcome="succeeded",
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
        worker_output = _normalized_output(
            await _execute_before_job_deadline(
                job=job,
                executor=executor,
                store=LocalContentAddressedStore(
                    pathlib.Path(input_root_value),
                    pathlib.Path(output_root_value),
                    active_deadline_epoch_seconds=job.deadline_epoch_seconds,
                ),
            )
        )
        output = worker_output.artifact
        execution_evidence = _validated_execution_evidence(worker_output.evidence)
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
        log_public_operation_correlation(
            operation_id=job.operation_id,
            component=component,
            action="completed",
            outcome="succeeded",
        )
        _write_result(
            {
                **_base_result(job),
                "ok": True,
                "errorCode": None,
                "outputArtifact": output.to_wire(),
                "executionEvidence": execution_evidence,
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
            component = "render" if role == "render" else "worker"
            log_public_operation_correlation(
                operation_id=job.operation_id,
                component=component,
                action="completed",
                outcome="failed",
            )
            _write_result(
                {
                    **_base_result(job),
                    "ok": False,
                    "errorCode": code,
                    "outputArtifact": None,
                    "executionEvidence": {
                        "svgProjectionFailure": projection_failure_detail(error.detail),
                    } if isinstance(error, SvgExportProjectionError) else {},
                }
            )
        return 1


def main(role: WorkerRole, executor: WorkerExecutor) -> int:
    return asyncio.run(run_worker(role, executor))


__all__ = [
    "LocalContentAddressedStore",
    "MAX_EXECUTION_EVIDENCE_BYTES",
    "PublicWorkerExecutionError",
    "WorkerOutput",
    "main",
    "run_worker",
]
