"""Linux development-only trusted-worker backend (``linux-local``)."""

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

from services.isolated_worker_contracts import ISOLATED_RESULT_SCHEMA, ArtifactRef, IsolatedWorkerJob
from services.isolated_worker_process import (
    LINUX_LOCAL_DEVELOPMENT_ATTESTATION,
    WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION,
    execute_isolated_worker_job,
    worker_environment,
)
from services.public_render_runtime_identity import public_render_runtime_binding
from services.trusted_executor.composition import create_trusted_worker_executor_runtime
from services.trusted_executor.contracts import (
    ExecutionIdentity,
    ResourceLimits,
    SandboxLaunchSpec,
    SandboxPolicy,
)
from services.trusted_executor.isolation_policy import (
    LINUX_LOCAL_DEVELOPMENT_ISOLATION_BACKEND,
    IsolationUnavailableError,
    linux_local_development_isolation_report,
)
from services.trusted_executor.linux_process_supervisor import (
    ENFORCEMENT_LAYER,
    LinuxSessionProcessSupervisor,
    ProcessCountLimitExceeded,
)
from services.trusted_executor.process_supervisor import (
    ProcessDeadlineExceeded,
    ProcessMemoryLimitExceeded,
)

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="linux-local supervises Linux process sessions"
)

_LOCAL = {
    "ENVIRONMENT": "development",
    "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "linux-local",
    "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development",
}


def _identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        operation_id=uuid.uuid4(), tenant_id="tenant-linux-local",
        owner_id=uuid.uuid4(), presentation_id=uuid.uuid4(),
    )


def _spec(identity, limits, code: str) -> SandboxLaunchSpec:
    return SandboxLaunchSpec(
        identity=identity, role="export", executable=Path(sys.executable),
        arguments=("-c", code), limits=limits, policy=SandboxPolicy(),
    )


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"


# A parent that starts a grandchild in a *new* session (escaping the session
# id) and prints its pid, then blocks.
_ESCAPING_TREE = """
import os, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
print(child.pid, flush=True)
time.sleep(60)
"""


def test_cancel_kills_descendants_including_one_that_left_the_session(tmp_path: Path) -> None:
    identity = _identity()
    limits = ResourceLimits(memory_bytes=2 * 1024**3, process_limit=16, deadline_seconds=30)
    supervisor = LinuxSessionProcessSupervisor(identity, limits)
    try:
        process = supervisor.launch(_spec(identity, limits, _ESCAPING_TREE), cwd=tmp_path, pipe_stdio=True)
        escaped = int(process.stdout.readline())
        assert _alive(escaped)
        assert supervisor.active_process_count == 2
        supervisor.cancel(zero_timeout=5)
        assert not _alive(escaped)
        assert supervisor.wait_for_zero(timeout=1)
        assert supervisor.descendant_zero_verified
        telemetry = supervisor.telemetry()
        assert telemetry["enforcementLayer"] == ENFORCEMENT_LAYER
        assert telemetry["peakProcessCount"] >= 2 and telemetry["finalProcessCount"] == 0
    finally:
        supervisor.close()


def test_normal_exit_reaches_zero_and_reports_the_exit_code(tmp_path: Path) -> None:
    identity = _identity()
    limits = ResourceLimits(memory_bytes=2 * 1024**3, process_limit=8, deadline_seconds=30)
    with LinuxSessionProcessSupervisor(identity, limits) as supervisor:
        process = supervisor.launch(
            _spec(identity, limits, "import sys; sys.stdout.write(sys.stdin.read().upper())"),
            cwd=tmp_path, pipe_stdio=True,
        )
        process.stdin.write(b"payload")
        process.stdin.close()
        assert process.stdout.read() == b"PAYLOAD"
        assert process.wait() == 0
        assert supervisor.wait_for_zero(timeout=5)
        assert supervisor.telemetry()["totalProcessCount"] >= 1


def test_deadline_kills_the_tree_and_wait_raises(tmp_path: Path) -> None:
    identity = _identity()
    limits = ResourceLimits(memory_bytes=2 * 1024**3, process_limit=8, deadline_seconds=0.5)
    with LinuxSessionProcessSupervisor(identity, limits) as supervisor:
        process = supervisor.launch(_spec(identity, limits, "import time; time.sleep(60)"), cwd=tmp_path)
        started = time.monotonic()
        with pytest.raises(ProcessDeadlineExceeded):
            process.wait(timeout=10)
        assert time.monotonic() - started < 5
        assert supervisor.termination_reason == "deadline_exceeded"


def test_memory_ceiling_kills_the_tree(tmp_path: Path) -> None:
    identity = _identity()
    limits = ResourceLimits(memory_bytes=64 * 1024**2, process_limit=8, deadline_seconds=30,
                            temporary_bytes=16 * 1024**2)
    code = "import time; block = bytearray(256 * 1024 * 1024); time.sleep(60)"
    with LinuxSessionProcessSupervisor(identity, limits) as supervisor:
        process = supervisor.launch(_spec(identity, limits, code), cwd=tmp_path)
        with pytest.raises(ProcessMemoryLimitExceeded):
            process.wait(timeout=20)
        assert supervisor.memory_limit_exceeded


def test_process_count_ceiling_kills_the_tree(tmp_path: Path) -> None:
    identity = _identity()
    limits = ResourceLimits(memory_bytes=2 * 1024**3, process_limit=2, deadline_seconds=30)
    code = (
        "import subprocess, sys, time\n"
        "children = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']) for _ in range(4)]\n"
        "time.sleep(60)\n"
    )
    with LinuxSessionProcessSupervisor(identity, limits) as supervisor:
        process = supervisor.launch(_spec(identity, limits, code), cwd=tmp_path)
        with pytest.raises(ProcessCountLimitExceeded):
            process.wait(timeout=20)
        assert supervisor.wait_for_zero(timeout=5)


def test_isolation_report_is_never_production_eligible() -> None:
    report = linux_local_development_isolation_report()
    assert report.backend == LINUX_LOCAL_DEVELOPMENT_ISOLATION_BACKEND
    assert report.production_eligible is False
    attestation = report.to_attestation()
    assert all(item["status"] == "unsupported" for item in attestation["controls"].values())


class _Authority:
    def __init__(self, source: Path, published: Path) -> None:
        self._source, self._published = source, published

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
        await asyncio.to_thread(self._copy, self._source, destination)

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        payload = await asyncio.to_thread(source.read_bytes)
        assert hashlib.sha256(payload).hexdigest() == reference.sha256
        await asyncio.to_thread(self._copy, source, self._published)

    @staticmethod
    def _copy(source: Path, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())


@pytest.mark.parametrize("environment, match", [
    ({"PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "linux-local"}, "LOCAL_POLICY=development"),
    ({**_LOCAL, "ENVIRONMENT": "production"}, "explicit local development tier"),
    ({"PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "bubblewrap"}, "container, windows-local or linux-local"),
])
def test_linux_local_composition_fails_closed_outside_development(tmp_path, environment, match) -> None:
    with pytest.raises(IsolationUnavailableError, match=match):
        create_trusted_worker_executor_runtime(
            staging_root=tmp_path / "staging", node_id="node-linux-local",
            artifact_authority=_Authority(tmp_path / "in", tmp_path / "out"), environment=environment,
        )


def test_worker_attestation_and_render_binding_follow_the_linux_backend() -> None:
    local = worker_environment("export", dict(_LOCAL), trusted_dispatch=True)
    assert local["PUBLIC_WORKER_OS_ISOLATION"] == LINUX_LOCAL_DEVELOPMENT_ATTESTATION
    windows = worker_environment(
        "export", {**_LOCAL, "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "windows-local"},
        trusted_dispatch=True,
    )
    assert windows["PUBLIC_WORKER_OS_ISOLATION"] == WINDOWS_LOCAL_DEVELOPMENT_ATTESTATION
    binding = public_render_runtime_binding({**_LOCAL, "HOME": os.environ.get("HOME", "/root")})
    assert binding["mode"] == "owned-local-controller-observed"
    assert binding["fontInventoryDigest"].startswith("sha256:")


def test_linux_local_transport_runs_a_worker_and_publishes_its_output(tmp_path: Path) -> None:
    owner_id, operation_id, tenant_id = uuid.uuid4(), uuid.uuid4(), "tenant-linux-local-wire"

    def key(digest: str) -> str:
        tenant = hashlib.sha256(tenant_id.encode()).hexdigest()
        return f"v1/tenant/{tenant}/owner/{owner_id}/sha256/{digest[:2]}/{digest}"

    def ref(kind, media, payload):
        digest = hashlib.sha256(payload).hexdigest()
        return ArtifactRef(
            artifact_id=uuid.uuid4(), owner_id=owner_id, tenant_id=tenant_id, kind=kind,
            sha256=digest, size_bytes=len(payload), media_type=media, storage_key=key(digest),
            created_by_operation_id=operation_id, retention_class="ephemeral",
        )

    # An export bundle whose schema stages no further inputs.
    input_payload = b'{"schemaVersion":"linux-local-wire-test/v1","slides":[]}'
    output_payload = b"linux-local-output"
    input_path = tmp_path / "input.json"
    input_path.write_bytes(input_payload)
    input_ref = ref("export-bundle", "application/vnd.presenton.export-bundle+json", input_payload)
    output_ref = ref("slide-png", "image/png", output_payload)
    attempt = "linux-local-attempt"
    job = IsolatedWorkerJob(
        role="export", operation_id=operation_id, attempt_token=attempt, fencing_token=3,
        input_digest=input_ref.sha256, input_artifact=input_ref, capability="test.capability",
        deadline_epoch_seconds=int(time.time()) + 30,
    )
    result = {
        "schemaVersion": ISOLATED_RESULT_SCHEMA, "role": job.role, "operationId": str(operation_id),
        "attemptTokenSha256": hashlib.sha256(attempt.encode()).hexdigest(),
        "fencingToken": job.fencing_token, "inputDigest": job.input_digest, "ok": True,
        "errorCode": None, "outputArtifact": output_ref.to_wire(), "executionEvidence": {},
    }
    worker_code = """
import json, os, pathlib, sys
job = json.loads(sys.stdin.buffer.read())
assert os.environ["PUBLIC_WORKER_OS_ISOLATION"] == "linux-process-session-local-development"
assert os.environ["PUBLIC_WORKER_LOCAL_DEVELOPMENT"] == "1"
inp = pathlib.Path(os.environ["PUBLIC_WORKER_INPUT_STORE_ROOT"], *job["inputArtifact"]["storageKey"].split("/"))
assert inp.read_bytes() == bytes.fromhex(sys.argv[1])
out = pathlib.Path(os.environ["PUBLIC_WORKER_OUTPUT_STORE_ROOT"], *sys.argv[3].split("/"))
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(bytes.fromhex(sys.argv[2]))
sys.stdout.write(sys.argv[4])
"""
    capability_key = tmp_path / "capability.key"
    capability_key.write_bytes(b"k" * 32)
    published = tmp_path / "published" / "output.png"
    runtime = create_trusted_worker_executor_runtime(
        staging_root=tmp_path / "staging", node_id="node-linux-local",
        artifact_authority=_Authority(input_path, published), environment=_LOCAL,
    )

    async def run():
        try:
            return await execute_isolated_worker_job(
                job, presentation_id=uuid.uuid4(), dispatcher=runtime.dispatcher,
                command=(sys.executable, "-c", worker_code, input_payload.hex(), output_payload.hex(),
                         output_ref.storage_key, json.dumps(result, separators=(",", ":"))),
                source_environment={
                    "PATH": os.environ.get("PATH", ""),
                    **_LOCAL,
                    "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(capability_key),
                    "PPT_WORKER_OUTPUT_LIMIT_BYTES": str(len(output_payload)),
                },
            )
        finally:
            await runtime.close()

    decoded = asyncio.run(run())
    assert decoded.output_artifact == output_ref.to_wire()
    assert published.read_bytes() == output_payload
    attestation = decoded.resource_metrics["isolationAttestation"]
    assert attestation["backend"] == LINUX_LOCAL_DEVELOPMENT_ISOLATION_BACKEND
    assert attestation["productionEligible"] is False
    assert decoded.resource_metrics["enforcementLayer"] == ENFORCEMENT_LAYER
    assert decoded.resource_metrics["finalProcessCount"] == 0
