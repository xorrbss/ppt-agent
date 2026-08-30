"""Fail-closed subprocess boundary for sessionless render/export profiles."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
from pathlib import PurePosixPath
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any
import uuid

from services.isolated_worker_contracts import (
    ISOLATED_RESULT_SCHEMA,
    ArtifactRef,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    WorkerRole,
)
from services.trusted_executor.worker_dispatcher import (
    TrustedWorkerDispatcher,
    TrustedWorkerWireError,
)


NETWORK_NONE_ATTESTATION = "nonroot-readonly-tmpfs-network-none"
WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION = "windows-job-object-local-development"
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
        "PUBLIC_WORKER_INPUT_STORE_ROOT",
        "PUBLIC_WORKER_LOCAL_DEVELOPMENT",
        "PUBLIC_WORKER_OBJECT_STORE_ROOT",
        "PUBLIC_WORKER_OUTPUT_STORE_ROOT",
        "PPT_WORKER_NETWORK_MODE",
        "PPT_WORKER_ROOT_FILESYSTEM",
        "PPT_WORKER_SCRATCH",
        "PPT_WORKER_RUN_AS_NON_ROOT",
        "PPT_WORKER_SECCOMP_PROFILE",
        "PPT_WORKER_REPLICAS",
        "PPT_WORKER_MEMORY_LIMIT_BYTES",
        "PPT_WORKER_CPU_MILLIS",
        "PPT_WORKER_PID_LIMIT",
        "PPT_WORKER_OPEN_FILE_LIMIT",
        "PPT_WORKER_OUTPUT_LIMIT_BYTES",
        "PPT_WORKER_WALL_CLOCK_SECONDS",
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


_trusted_worker_dispatcher: TrustedWorkerDispatcher | None = None


def install_trusted_worker_dispatcher(
    dispatcher: TrustedWorkerDispatcher | None,
) -> None:
    """Install the composition-root trusted wire port; ``None`` fails closed."""

    global _trusted_worker_dispatcher
    _trusted_worker_dispatcher = dispatcher


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
    *,
    trusted_dispatch: bool = False,
) -> dict[str, str]:
    incoming = os.environ if source is None else source
    attestation = incoming.get("PUBLIC_WORKER_OS_ISOLATION", "").strip()
    local_development = (
        trusted_dispatch
        and incoming.get("PUBLIC_TRUSTED_WORKER_LOCAL_POLICY", "").strip().lower()
        == "development"
    )
    if not local_development and attestation != NETWORK_NONE_ATTESTATION:
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
    # The trusted transport injects separate, operation-scoped input/output
    # roots after staging. Never pass the legacy authority-shaped alias to the
    # child or require a shared cache before that trusted boundary runs.
    result.pop("PUBLIC_WORKER_OBJECT_STORE_ROOT", None)
    result.update(
        {
            "PUBLIC_WORKER_ROLE": role,
            "PUBLIC_WORKER_OS_ISOLATION": (
                WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION
                if local_development
                else NETWORK_NONE_ATTESTATION
            ),
            "PUBLIC_WORKER_SESSIONLESS": "1",
        }
    )
    if local_development:
        result["PUBLIC_WORKER_LOCAL_DEVELOPMENT"] = "1"
    if role == "render":
        # The public render process is already the one-shot isolation boundary;
        # this marker permits the low-level CDP entry without recursively
        # spawning the legacy renderer worker.
        result["AUTHORED_RENDERER_WORKER"] = "1"
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
    if not (
        pathlib.Path(command[0]).is_absolute()
        or PurePosixPath(command[0]).is_absolute()
    ):
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
    presentation_id: uuid.UUID,
    source_environment: Mapping[str, str] | None = None,
    command: Sequence[str] | None = None,
    dispatcher: TrustedWorkerDispatcher | None = None,
) -> IsolatedWorkerResult:
    """Dispatch one immutable job through the trusted-executor product port."""

    encoded = json.dumps(
        job.to_wire(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(encoded) > MAX_JOB_BYTES:
        raise IsolatedWorkerContractError("public worker job exceeds the wire limit")
    environment = worker_environment(
        job.role,
        source_environment,
        trusted_dispatch=True,
    )
    argv = list(command) if command is not None else worker_command(
        job.role, source_environment
    )
    if not argv or not (
        pathlib.Path(argv[0]).is_absolute()
        or PurePosixPath(argv[0]).is_absolute()
    ):
        raise IsolatedWorkerProcessError("public worker executable must be absolute")
    selected_dispatcher = dispatcher or _trusted_worker_dispatcher
    if selected_dispatcher is None:
        raise IsolatedWorkerProcessError("trusted worker dispatcher is unavailable")
    try:
        wire_result = await selected_dispatcher.execute(
            job=job,
            presentation_id=presentation_id,
            payload=encoded,
            command=argv,
            environment=environment,
        )
    except TrustedWorkerWireError as error:
        raise IsolatedWorkerProcessError(
            error.code,
            resource_metrics=error.resource_metrics,
        ) from error
    if len(wire_result.stdout) > MAX_RESULT_BYTES:
        raise IsolatedWorkerProcessError("public worker result exceeded the wire limit")
    result = _decode_result(wire_result.stdout, job)
    if wire_result.exit_code != 0 or not result.ok:
        raise IsolatedWorkerProcessError(
            result.error_code or "public_worker_execution_failed",
            resource_metrics=wire_result.resource_metrics,
        )
    return replace(result, resource_metrics=dict(wire_result.resource_metrics))


def development_worker_command(role: WorkerRole) -> list[str]:
    """Explicit local command helper; never selected implicitly in production."""

    module = f"workers.public_{role}_worker"
    return [sys.executable, "-m", module]


__all__ = [
    "IsolatedWorkerProcessError",
    "IsolatedWorkerResult",
    "NETWORK_NONE_ATTESTATION",
    "WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION",
    "development_worker_command",
    "execute_isolated_worker_job",
    "install_trusted_worker_dispatcher",
    "worker_command",
    "worker_environment",
]
