"""Fail-closed subprocess boundary for sessionless render/export profiles."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import pathlib
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from services.isolated_worker_contracts import (
    ISOLATED_RESULT_SCHEMA,
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    WorkerRole,
)
from services.public_worker_resource_boundary import (
    LIMIT_ENV,
    SUPERVISOR_POLL_INTERVAL_MILLIS,
    WorkerResourceBoundaryError,
    WorkerResourceLimits,
    create_worker_resource_boundary,
)
from services.public_worker_profiles import (
    get_public_worker_profile,
    validate_public_worker_environment,
)


NETWORK_NONE_ATTESTATION = "nonroot-readonly-tmpfs-network-none-seccomp"
MAX_JOB_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 16 * 1024 * 1024
_ENV_ALLOWLIST = frozenset(
    {
        "CHROME_PATH",
        "CHROMIUM_PATH",
        "COMSPEC",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LOCALAPPDATA",
        "PATH",
        "PATHEXT",
        "PUPPETEER_EXECUTABLE_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "VIRTUAL_ENV",
        "WINDIR",
        "PUBLIC_WORKER_CAPABILITY_KEY_FILE",
        "PUBLIC_WORKER_ARTIFACT_CACHE_ROOT",
        "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES",
        "PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS",
        "PUBLIC_WORKER_OBJECT_STORE_ROOT",
        "PPT_WORKER_NETWORK_MODE",
        "PPT_WORKER_ROOT_FILESYSTEM",
        "PPT_WORKER_SCRATCH",
        "PPT_WORKER_RUN_AS_NON_ROOT",
        "PPT_WORKER_SECCOMP_PROFILE",
        "PPT_WORKER_REPLICAS",
        *LIMIT_ENV.values(),
    }
)
_FORBIDDEN_ENV_MARKERS = (
    "API_KEY",
    "APIKEY",
    "AUTHORIZATION",
    "AUTH_TOKEN",
    "BEARER",
    "CLIENT_SECRET",
    "COOKIE",
    "DATABASE_URL",
    "PASSWORD",
    "PRIVATE_KEY",
    "PROVIDER",
    "SESSION",
)


class IsolatedWorkerProcessError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        resource_metrics: Mapping[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.resource_metrics = dict(resource_metrics or {})
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class IsolatedWorkerResult:
    role: WorkerRole
    operation_id: str
    attempt_token_sha256: str
    fencing_token: int
    input_digest: str
    ok: bool
    error_code: str | None
    output_artifact: Mapping[str, Any] | None
    resource_metrics: Mapping[str, Any] | None = None


def worker_environment(
    role: WorkerRole,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    incoming = os.environ if source is None else source
    attestation = incoming.get("PUBLIC_WORKER_OS_ISOLATION", "").strip()
    if attestation != NETWORK_NONE_ATTESTATION:
        raise IsolatedWorkerProcessError(
            "public worker requires network-none OS isolation attestation"
        )
    result = {
        key: value
        for key, value in incoming.items()
        if key.upper() in _ENV_ALLOWLIST
        and not any(marker in key.upper() for marker in _FORBIDDEN_ENV_MARKERS)
    }
    leaked = [
        key
        for key in result
        if any(marker in key.upper() for marker in _FORBIDDEN_ENV_MARKERS)
    ]
    if leaked:
        raise IsolatedWorkerProcessError("public worker environment contains secrets")
    cache_root = incoming.get("PUBLIC_WORKER_ARTIFACT_CACHE_ROOT", "").strip()
    if not cache_root:
        environment = incoming.get("ENVIRONMENT", "production").strip().lower()
        backend = incoming.get("PUBLIC_ARTIFACT_STORE_BACKEND", "").strip().lower()
        legacy_root = incoming.get("PUBLIC_WORKER_OBJECT_STORE_ROOT", "").strip()
        if (
            legacy_root
            and backend == "local-development"
            and environment in {"development", "dev", "local", "test"}
        ):
            cache_root = legacy_root
    if not cache_root or not pathlib.Path(cache_root).is_absolute():
        raise IsolatedWorkerProcessError("public worker artifact cache root is required")
    result["PUBLIC_WORKER_ARTIFACT_CACHE_ROOT"] = cache_root
    # The legacy variable named this scratch directory as if it were durable
    # object storage. Never pass that authority-shaped alias to the child.
    result.pop("PUBLIC_WORKER_OBJECT_STORE_ROOT", None)
    result.update(
        {
            "PUBLIC_WORKER_ROLE": role,
            "PUBLIC_WORKER_OS_ISOLATION": NETWORK_NONE_ATTESTATION,
            "PUBLIC_WORKER_SESSIONLESS": "1",
        }
    )
    if role == "render":
        # The public render process is already the one-shot isolation boundary;
        # this marker permits the low-level CDP entry without recursively
        # spawning the legacy renderer worker.
        result["AUTHORED_RENDERER_WORKER"] = "1"
    try:
        validate_public_worker_environment(
            get_public_worker_profile(role),
            result,
        )
    except RuntimeError as error:
        raise IsolatedWorkerProcessError(str(error)) from error
    return result


def worker_command(
    role: WorkerRole,
    source: Mapping[str, str] | None = None,
) -> list[str]:
    incoming = os.environ if source is None else source
    variable = f"PUBLIC_{role.upper()}_WORKER_COMMAND_JSON"
    configured = incoming.get(variable, "").strip()
    if not configured:
        raise IsolatedWorkerProcessError(f"{variable} is required")
    try:
        command = json.loads(configured)
    except json.JSONDecodeError as error:
        raise IsolatedWorkerProcessError(f"{variable} must be a JSON argv array") from error
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item or "\x00" in item for item in command)
    ):
        raise IsolatedWorkerProcessError(f"{variable} must be a JSON argv array")
    if not pathlib.Path(command[0]).is_absolute():
        raise IsolatedWorkerProcessError("public worker executable must be absolute")
    return command


def _decode_result(payload: bytes, job: IsolatedWorkerJob) -> IsolatedWorkerResult:
    if not payload or len(payload) > MAX_RESULT_BYTES:
        raise IsolatedWorkerProcessError("public worker result exceeded the wire limit")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IsolatedWorkerProcessError("public worker returned invalid JSON") from error
    if not isinstance(value, Mapping):
        raise IsolatedWorkerProcessError("public worker returned a non-object result")
    expected_keys = {
        "schemaVersion",
        "role",
        "operationId",
        "attemptTokenSha256",
        "fencingToken",
        "inputDigest",
        "ok",
        "errorCode",
        "outputArtifact",
    }
    if set(value) != expected_keys or value.get("schemaVersion") != ISOLATED_RESULT_SCHEMA:
        raise IsolatedWorkerProcessError("public worker result shape is invalid")
    if (
        value.get("role") != job.role
        or value.get("operationId") != str(job.operation_id)
        or value.get("fencingToken") != job.fencing_token
        or value.get("inputDigest") != job.input_digest
    ):
        raise IsolatedWorkerProcessError("public worker result binding mismatch")
    ok = value.get("ok")
    error_code = value.get("errorCode")
    output_artifact = value.get("outputArtifact")
    if not isinstance(ok, bool):
        raise IsolatedWorkerProcessError("public worker result status is invalid")
    if ok:
        if error_code is not None or not isinstance(output_artifact, Mapping):
            raise IsolatedWorkerProcessError("public worker success result is incomplete")
    elif not isinstance(error_code, str) or output_artifact is not None:
        raise IsolatedWorkerProcessError("public worker failure result is incomplete")
    attempt_token_sha256 = value.get("attemptTokenSha256")
    expected_attempt_token_sha256 = hashlib.sha256(
        job.attempt_token.encode("utf-8")
    ).hexdigest()
    if (
        not isinstance(attempt_token_sha256, str)
        or attempt_token_sha256 != expected_attempt_token_sha256
    ):
        raise IsolatedWorkerProcessError("public worker attempt binding is invalid")
    if isinstance(output_artifact, Mapping):
        try:
            parsed_output = ArtifactRef.from_wire(output_artifact)
        except IsolatedWorkerContractError as error:
            raise IsolatedWorkerProcessError(
                "public worker output artifact is invalid"
            ) from error
        if (
            parsed_output.owner_id != job.input_artifact.owner_id
            or parsed_output.tenant_id != job.input_artifact.tenant_id
            or parsed_output.created_by_operation_id != job.operation_id
        ):
            raise IsolatedWorkerProcessError(
                "public worker output artifact binding mismatch"
            )
    return IsolatedWorkerResult(
        role=job.role,
        operation_id=str(job.operation_id),
        attempt_token_sha256=attempt_token_sha256,
        fencing_token=job.fencing_token,
        input_digest=job.input_digest,
        ok=ok,
        error_code=error_code if isinstance(error_code, str) else None,
        output_artifact=output_artifact if isinstance(output_artifact, Mapping) else None,
    )


async def execute_isolated_worker_job(
    job: IsolatedWorkerJob,
    *,
    source_environment: Mapping[str, str] | None = None,
    command: Sequence[str] | None = None,
) -> IsolatedWorkerResult:
    """Execute exactly one stdin/stdout job without a task file or inherited env."""

    encoded = json.dumps(
        job.to_wire(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_JOB_BYTES:
        raise IsolatedWorkerContractError("public worker job exceeds the wire limit")
    incoming_environment = os.environ if source_environment is None else source_environment
    limits = WorkerResourceLimits.from_environment(incoming_environment)
    boundary = create_worker_resource_boundary(limits)
    environment = worker_environment(job.role, incoming_environment)
    argv = list(command) if command is not None else worker_command(
        job.role, source_environment
    )
    if not argv or not pathlib.Path(argv[0]).is_absolute():
        raise IsolatedWorkerProcessError("public worker executable must be absolute")
    kwargs: dict[str, Any] = {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.DEVNULL,
        "env": environment,
    }
    kwargs.update(boundary.creation_kwargs)
    try:
        process = await asyncio.create_subprocess_exec(*argv, **kwargs)
    except BaseException:
        boundary.close()
        raise
    try:
        boundary.attach(process.pid)
        # Windows children are born suspended and cannot execute any worker
        # code until the kernel Job Object owns them. POSIX boundaries resume
        # implicitly after preexec rlimit/process-group setup.
        boundary.resume()
    except BaseException as error:
        # Assignment failed while the process was still suspended, so it has
        # executed no worker instruction and cannot have spawned descendants.
        if process.returncode is None:
            process.kill()
        await process.wait()
        metrics = boundary.terminate_and_verify()
        boundary.close()
        raise IsolatedWorkerProcessError(
            getattr(error, "code", str(error)),
            resource_metrics=_durable_failure_metrics(metrics, limit_triggered=False),
        ) from error

    async def terminate_and_collect(*, limit_triggered: bool) -> dict[str, Any]:
        try:
            boundary.sample()
        except WorkerResourceBoundaryError:
            limit_triggered = True
        metrics = boundary.terminate_and_verify()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
        # Query after the OS process handle has signalled as well as after the
        # boundary reports zero descendants. A non-zero count is a hard
        # recycle failure and is preserved in the durable error projection.
        metrics = boundary.telemetry()
        return _durable_failure_metrics(metrics, limit_triggered=limit_triggered)

    timeout = min(
        max(1, job.deadline_epoch_seconds - int(time.time())),
        limits.wall_clock_seconds,
    )
    communication = asyncio.create_task(process.communicate(encoded))
    try:
        deadline = time.monotonic() + min(timeout, 600)
        while not communication.done():
            if time.monotonic() >= deadline:
                raise asyncio.TimeoutError
            boundary.sample()
            await asyncio.sleep(SUPERVISOR_POLL_INTERVAL_MILLIS / 1000)
        stdout, _ = await communication
        boundary.sample()
    except asyncio.CancelledError:
        await terminate_and_collect(limit_triggered=False)
        boundary.close()
        raise
    except (asyncio.TimeoutError, WorkerResourceBoundaryError) as error:
        failure_metrics = await terminate_and_collect(
            limit_triggered=isinstance(error, WorkerResourceBoundaryError)
        )
        code = (
            str(error)
            if isinstance(error, WorkerResourceBoundaryError)
            else "public_worker_wall_clock_limit_exceeded"
        )
        boundary.close()
        raise IsolatedWorkerProcessError(
            code, resource_metrics=failure_metrics
        ) from error
    finally:
        if not communication.done():
            communication.cancel()
            await asyncio.gather(communication, return_exceptions=True)
    try:
        if len(stdout) > MAX_RESULT_BYTES:
            raise IsolatedWorkerProcessError("public worker result exceeded the wire limit")
        result = _decode_result(stdout, job)
        if process.returncode != 0 or not result.ok:
            raise IsolatedWorkerProcessError(
                result.error_code or "public_worker_execution_failed"
            )
        output_size = int(result.output_artifact.get("sizeBytes", 0)) if result.output_artifact else 0
        if output_size > limits.output_bytes:
            raise IsolatedWorkerProcessError("public_worker_output_limit_exceeded")
        metrics = boundary.telemetry()
        if metrics["resourceLimitsEnforced"] is not True or metrics["recycledWithinBaseline"] is not True:
            raise IsolatedWorkerProcessError("public_worker_resource_recycle_failed")
        return replace(result, resource_metrics=metrics)
    except IsolatedWorkerProcessError as error:
        failure_metrics = await terminate_and_collect(
            limit_triggered=("limit" in error.code)
        )
        raise IsolatedWorkerProcessError(
            error.code, resource_metrics=failure_metrics
        ) from error
    except BaseException as error:
        failure_metrics = await terminate_and_collect(limit_triggered=False)
        raise IsolatedWorkerProcessError(
            "public_worker_execution_failed", resource_metrics=failure_metrics
        ) from error
    finally:
        boundary.close()


def _durable_failure_metrics(
    metrics: Mapping[str, Any], *, limit_triggered: bool
) -> dict[str, Any]:
    limits = metrics.get("limits") if isinstance(metrics.get("limits"), Mapping) else {}
    recycled = metrics.get("recycledWithinBaseline") is True and int(
        metrics.get("finalProcessCount", 1)
    ) == 0
    return {
        "worker_peak_memory_bytes": int(metrics.get("peakPrivateBytes", 0)),
        "worker_peak_handle_count": int(metrics.get("peakHandleCount", 0)),
        "worker_peak_process_count": int(metrics.get("peakProcessCount", 0)),
        "worker_peak_cpu_millis": int(metrics.get("peakCpuMillis", 0)),
        "worker_recycle_failure_count": 0 if recycled else 1,
        "worker_final_process_count": int(metrics.get("finalProcessCount", 0)),
        "resource_limits_enforced": metrics.get("resourceLimitsEnforced") is True,
        "worker_enforcement_layer": str(metrics.get("enforcementLayer", "unavailable")),
        "worker_execution_count": 1,
        "worker_limit_triggered": bool(limit_triggered),
        "worker_limit_memory_bytes": int(limits.get("memoryBytes", 0)),
        "worker_limit_cpu_millis": int(limits.get("cpuMillis", 0)),
        "worker_limit_process_count": int(limits.get("pidCount", 0)),
        "worker_limit_handle_count": int(limits.get("openFiles", 0)),
        "worker_limit_output_bytes": int(limits.get("outputBytes", 0)),
        "worker_limit_wall_clock_seconds": int(limits.get("wallClockSeconds", 0)),
    }


def development_worker_command(role: WorkerRole) -> list[str]:
    """Explicit local command helper; never selected implicitly in production."""

    module = f"workers.public_{role}_worker"
    return [sys.executable, "-m", module]


__all__ = [
    "IsolatedWorkerProcessError",
    "IsolatedWorkerResult",
    "NETWORK_NONE_ATTESTATION",
    "development_worker_command",
    "execute_isolated_worker_job",
    "worker_command",
    "worker_environment",
]
