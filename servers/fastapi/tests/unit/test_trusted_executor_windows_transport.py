from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import uuid

import pytest

from services.isolated_worker_contracts import (
    ISOLATED_RESULT_SCHEMA,
    ArtifactRef,
    IsolatedWorkerJob,
)
from services.isolated_worker_process import (
    NETWORK_NONE_ATTESTATION,
    WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION,
    IsolatedWorkerProcessError,
    execute_isolated_worker_job,
    worker_environment,
)
from services.trusted_executor.composition import (
    create_trusted_worker_executor_runtime,
)
from services.trusted_executor.isolation_policy import IsolationUnavailableError


pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows local transport requires Job Object supervision"
)


class _FilesystemAuthority:
    def __init__(self, source: Path, published: Path) -> None:
        self._source = source
        self._published = published

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(self._source.read_bytes())

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        payload = source.read_bytes()
        assert len(payload) == reference.size_bytes
        assert hashlib.sha256(payload).hexdigest() == reference.sha256
        self._published.parent.mkdir(parents=True, exist_ok=True)
        self._published.write_bytes(payload)
        assert self._published.read_bytes() == payload


def _storage_key(owner_id: uuid.UUID, tenant_id: str, digest: str) -> str:
    tenant = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
    return f"v1/tenant/{tenant}/owner/{owner_id}/sha256/{digest[:2]}/{digest}"


def test_worker_environment_separates_production_and_local_development_attestation() -> None:
    production = worker_environment(
        "render", {"PUBLIC_WORKER_OS_ISOLATION": NETWORK_NONE_ATTESTATION}
    )
    assert production["PUBLIC_WORKER_OS_ISOLATION"] == NETWORK_NONE_ATTESTATION
    assert "PUBLIC_WORKER_LOCAL_DEVELOPMENT" not in production

    local = worker_environment(
        "render",
        {"PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development"},
        trusted_dispatch=True,
    )
    assert (
        local["PUBLIC_WORKER_OS_ISOLATION"]
        == WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION
    )
    assert local["PUBLIC_WORKER_LOCAL_DEVELOPMENT"] == "1"

    with pytest.raises(IsolatedWorkerProcessError, match="network-none"):
        worker_environment("render", {}, trusted_dispatch=True)


def test_windows_local_composition_requires_explicit_development_policy(
    tmp_path: Path,
) -> None:
    authority = _FilesystemAuthority(tmp_path / "input", tmp_path / "published")
    with pytest.raises(IsolationUnavailableError, match="LOCAL_POLICY=development"):
        create_trusted_worker_executor_runtime(
            staging_root=tmp_path / "staging",
            node_id="node-local-policy",
            artifact_authority=authority,
            environment={"PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "windows-local"},
        )


def test_windows_local_transport_runs_bounded_stdio_and_publishes_exact_output(
    tmp_path: Path,
) -> None:
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    tenant_id = "tenant-windows-local-wire"
    input_payload = b"<html>trusted local input</html>"
    input_digest = hashlib.sha256(input_payload).hexdigest()
    input_path = tmp_path / "authority-input.html"
    input_path.write_bytes(input_payload)
    input_reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="slide-html",
        sha256=input_digest,
        size_bytes=len(input_payload),
        media_type="text/html",
        storage_key=_storage_key(owner_id, tenant_id, input_digest),
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    output_payload = b"trusted-windows-local-output"
    output_digest = hashlib.sha256(output_payload).hexdigest()
    output_reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="slide-png",
        sha256=output_digest,
        size_bytes=len(output_payload),
        media_type="image/png",
        storage_key=_storage_key(owner_id, tenant_id, output_digest),
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    attempt_token = "windows-local-wire-attempt"
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token=attempt_token,
        fencing_token=11,
        input_digest=input_digest,
        input_artifact=input_reference,
        capability="test.capability",
        deadline_epoch_seconds=int(time.time()) + 30,
    )
    result = {
        "schemaVersion": ISOLATED_RESULT_SCHEMA,
        "role": job.role,
        "operationId": str(job.operation_id),
        "attemptTokenSha256": hashlib.sha256(attempt_token.encode("utf-8")).hexdigest(),
        "fencingToken": job.fencing_token,
        "inputDigest": job.input_digest,
        "ok": True,
        "errorCode": None,
        "outputArtifact": output_reference.to_wire(),
    }
    worker_code = """
import json, os, pathlib, sys
job = json.loads(sys.stdin.buffer.read())
assert os.environ["PUBLIC_WORKER_OS_ISOLATION"] == "windows-job-object-local-development"
assert os.environ["PUBLIC_WORKER_LOCAL_DEVELOPMENT"] == "1"
input_path = pathlib.Path(os.environ["PUBLIC_WORKER_INPUT_STORE_ROOT"], *job["inputArtifact"]["storageKey"].split("/"))
assert input_path.read_bytes() == bytes.fromhex(sys.argv[1])
output = bytes.fromhex(sys.argv[2])
output_path = pathlib.Path(os.environ["PUBLIC_WORKER_OUTPUT_STORE_ROOT"], *sys.argv[3].split("/"))
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_bytes(output)
sys.stderr.buffer.write(b"x" * (1024 * 1024 + 4096))
sys.stdout.write(sys.argv[4])
"""
    capability_key = tmp_path / "capability.key"
    capability_key.write_bytes(b"k" * 32)
    published = tmp_path / "published" / "output.png"
    runtime = create_trusted_worker_executor_runtime(
        staging_root=tmp_path / "staging",
        node_id="node-windows-local",
        artifact_authority=_FilesystemAuthority(input_path, published),
        environment={
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "windows-local",
            "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development",
        },
    )

    async def run() -> None:
        try:
            decoded = await execute_isolated_worker_job(
                job,
                presentation_id=presentation_id,
                dispatcher=runtime.dispatcher,
                command=(
                    sys.executable,
                    "-c",
                    worker_code,
                    input_payload.hex(),
                    output_payload.hex(),
                    output_reference.storage_key,
                    json.dumps(result, separators=(",", ":")),
                ),
                source_environment={
                    "PATH": os.environ.get("PATH", ""),
                    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                    "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development",
                    "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(capability_key),
                },
            )
            assert decoded.output_artifact == output_reference.to_wire()
        finally:
            await runtime.close()

    asyncio.run(run())
    assert published.read_bytes() == output_payload
