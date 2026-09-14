from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace
import uuid

import pytest

from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.trusted_executor.composition import (
    create_trusted_worker_executor_runtime,
)
from services.trusted_executor.isolation_policy import IsolationUnavailableError
from services.trusted_executor.isolation_policy import SandboxFilesystemSpec
from services.trusted_executor.wire_transport import (
    ArtifactObjectStoreWireAdapter,
    SupervisedStdioWireTransport,
)
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError
from workers.public_worker_store import (
    LocalContentAddressedStore,
    PublicWorkerExecutionError,
)


_DOCKER_TEST_IMAGE = (
    "alpine@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b"
)


class _FilesystemAuthority:
    def __init__(self, source: Path, published: Path) -> None:
        self.source = source
        self.published = published

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self.source.open("rb") as incoming, destination.open("xb") as output:
            while chunk := incoming.read(64 * 1024):
                output.write(chunk)

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        self.published.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as incoming, self.published.open("xb") as output:
            while chunk := incoming.read(64 * 1024):
                output.write(chunk)


def _storage_key(owner_id: uuid.UUID, tenant_id: str, digest: str) -> str:
    tenant = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
    return f"v1/tenant/{tenant}/owner/{owner_id}/sha256/{digest[:2]}/{digest}"


def test_object_store_wire_adapter_stages_in_one_bounded_verified_pass(
    tmp_path: Path,
) -> None:
    payload = (b"trusted-worker-input" * 131_072) + b"end"
    owner_id = uuid.uuid4()
    digest = hashlib.sha256(payload).hexdigest()
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-wire-stream",
        kind="slide-html",
        sha256=digest,
        size_bytes=len(payload),
        media_type="text/html",
        storage_key=_storage_key(owner_id, "tenant-wire-stream", digest),
        created_by_operation_id=uuid.uuid4(),
        retention_class="ephemeral",
    )

    class _Probe:
        def __init__(self) -> None:
            import io

            self._stream = io.BytesIO(payload)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            if size < 0:
                raise AssertionError("wire staging attempted a whole-file read")
            return self._stream.read(size)

        def close(self) -> None:
            self._stream.close()

    class _Store:
        def __init__(self) -> None:
            self.probe = _Probe()

        async def open_read_stream(self, artifact: object) -> object:
            from services.artifact_store import ArtifactReadStream

            return ArtifactReadStream(
                body=self.probe,
                content_length=len(payload),
                media_type="text/html",
                etag=None,
            )

        async def put_stream(self, artifact: object, source: object) -> None:
            raise AssertionError("stage must not publish")

        async def head(self, artifact: object) -> object:
            return None

    async def scenario() -> None:
        store = _Store()
        destination = (tmp_path / "sandbox" / "input.html").resolve()
        await ArtifactObjectStoreWireAdapter(store).stage(
            reference=reference,
            destination=destination,
        )
        assert destination.read_bytes() == payload
        assert store.probe.read_sizes
        assert max(store.probe.read_sizes) <= 1024 * 1024

    asyncio.run(scenario())


def test_job_owned_store_keeps_old_inputs_and_all_outputs_active_until_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "1")
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", "16")
    monkeypatch.setenv("PPT_WORKER_OUTPUT_LIMIT_BYTES", "16")
    input_root = (tmp_path / "input").resolve()
    output_root = (tmp_path / "output").resolve()
    input_root.mkdir()
    active_deadline = time.time() + 30
    store = LocalContentAddressedStore(
        input_root, output_root,
        active_deadline_epoch_seconds=active_deadline,
    )
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    payload = b"old-input"
    digest = hashlib.sha256(payload).hexdigest()
    source = ArtifactRef(
        artifact_id=uuid.uuid4(), owner_id=owner_id, tenant_id="tenant-active",
        kind="slide-html", sha256=digest, size_bytes=len(payload),
        media_type="text/html", storage_key=_storage_key(owner_id, "tenant-active", digest),
        created_by_operation_id=operation_id, retention_class="ephemeral",
    )
    input_path = input_root.joinpath(*source.storage_key.split("/"))
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(payload)
    old = time.time() - 3600
    os.utime(input_path, (old, old))

    assert store.verified_path(source) == input_path
    first_path = tmp_path / "first.bin"
    second_path = tmp_path / "second.bin"
    overflow_path = tmp_path / "overflow.bin"
    first_path.write_bytes(b"a" * 8)
    second_path.write_bytes(b"b" * 8)
    overflow_path.write_bytes(b"c")
    first = store.put_path(
        first_path, source=source, operation_id=operation_id,
        kind="slide-png", media_type="image/png",
    )
    second = store.put_path(
        second_path, source=source, operation_id=operation_id,
        kind="slide-png", media_type="image/png",
    )
    with pytest.raises(PublicWorkerExecutionError, match="cache_budget_exceeded"):
        store.put_path(
            overflow_path, source=source, operation_id=operation_id,
            kind="slide-png", media_type="image/png",
        )
    assert store.verified_path(first).is_file()
    assert store.verified_path(second).is_file()

    expired_store = LocalContentAddressedStore(
        input_root, output_root,
        active_deadline_epoch_seconds=time.time() - 1,
    )
    with pytest.raises(PublicWorkerExecutionError, match="artifact_expired"):
        expired_store.verified_path(source)
    with pytest.raises(PublicWorkerExecutionError, match="artifact_expired"):
        expired_store.put_path(
            overflow_path, source=source, operation_id=operation_id,
            kind="slide-png", media_type="image/png",
        )


def test_transport_cancel_releases_staging_reservation_and_removes_attempt_tree(
    tmp_path: Path,
) -> None:
    payload = b"0123456789"
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    digest = hashlib.sha256(payload).hexdigest()
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(), owner_id=owner_id, tenant_id="tenant-capacity",
        kind="slide-html", sha256=digest, size_bytes=len(payload),
        media_type="text/html", storage_key=_storage_key(owner_id, "tenant-capacity", digest),
        created_by_operation_id=operation_id, retention_class="ephemeral",
    )
    job = IsolatedWorkerJob(
        role="render", operation_id=operation_id, attempt_token="capacity-attempt",
        fencing_token=1, input_digest=digest, input_artifact=reference,
        capability="test.capability", deadline_epoch_seconds=int(time.time()) + 30,
    )

    class _CancellingAuthority:
        def __init__(self) -> None:
            self.calls = 0

        async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
            self.calls += 1
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            raise asyncio.CancelledError

        async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
            raise AssertionError("cancelled staging must not publish")

    authority = _CancellingAuthority()
    transport = SupervisedStdioWireTransport(
        artifact_authority=authority,
        isolation_report=SimpleNamespace(),
        staging_capacity_bytes=100,
    )

    async def scenario() -> None:
        for index in range(2):
            sandbox = (tmp_path / f"attempt-{index}").resolve()
            filesystem = SandboxFilesystemSpec(
                sandbox_root=sandbox,
                input_root=sandbox / "input",
                temporary_root=sandbox / "tmp",
                staged_inputs=(sandbox / "input" / Path(reference.storage_key),),
            )
            work_item = SimpleNamespace(
                launch_spec=SimpleNamespace(
                    filesystem=filesystem,
                    limits=SimpleNamespace(temporary_bytes=0),
                )
            )
            with pytest.raises(asyncio.CancelledError):
                await transport.execute(
                    work_item=work_item, job=job, payload=b"{}",
                    environment={
                        "PPT_WORKER_OUTPUT_LIMIT_BYTES": "90",
                        "PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES": "90",
                    },
                )
            assert not sandbox.exists()
        assert authority.calls == 2

    asyncio.run(scenario())


def test_runtime_factory_fails_closed_without_complete_platform_isolation(
    tmp_path: Path,
) -> None:
    authority = _FilesystemAuthority(tmp_path / "input", tmp_path / "published")
    with pytest.raises(IsolationUnavailableError, match="SANDBOX_BACKEND must be container or windows-local"):
        create_trusted_worker_executor_runtime(
            staging_root=tmp_path / "staging",
            node_id="node-test",
            artifact_authority=authority,
        )


@pytest.mark.skip(reason="Docker transport is retired from current development validation")
def test_docker_transport_stages_runs_drains_publishes_and_removes_container(
    tmp_path: Path,
) -> None:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker CLI unavailable")
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    tenant_id = "tenant-wire-test"
    input_payload = b"<html>staged input</html>"
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
    output_payload = b"trusted-docker-output"
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
    attempt_token = "attempt-wire-test"
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token=attempt_token,
        fencing_token=7,
        input_digest=input_digest,
        input_artifact=input_reference,
        capability="test.capability",
        deadline_epoch_seconds=int(time.time()) + 30,
    )
    result = {
        "schemaVersion": "ppt-isolated-worker-result/v1",
        "role": job.role,
        "operationId": str(job.operation_id),
        "attemptTokenSha256": hashlib.sha256(attempt_token.encode()).hexdigest(),
        "fencingToken": job.fencing_token,
        "inputDigest": job.input_digest,
        "ok": True,
        "errorCode": None,
        "outputArtifact": output_reference.to_wire(),
    }
    worker_code = (
        "cat >/tmp/job.json; key=\"$1\"; data=\"$2\"; result=\"$3\"; "
        "mkdir -p \"/workspace/output/$(dirname \"$key\")\"; "
        "printf %s \"$data\" > \"/workspace/output/$key\"; "
        "dd if=/dev/zero bs=1024 count=128 1>&2 2>/dev/null; "
        "printf %s \"$result\""
    )
    capability_key = tmp_path / "capability.key"
    capability_key.write_bytes(b"k" * 32)
    published = tmp_path / "published" / "output.png"
    authority = _FilesystemAuthority(input_path, published)
    runtime = create_trusted_worker_executor_runtime(
        staging_root=tmp_path / "staging",
        node_id="node-test",
        artifact_authority=authority,
        environment={
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "docker",
            "PUBLIC_TRUSTED_WORKER_DOCKER_IMAGE": _DOCKER_TEST_IMAGE,
            "PUBLIC_TRUSTED_WORKER_DOCKER_EXECUTABLE": docker,
        },
    )

    async def run() -> bytes:
        try:
            return await runtime.dispatcher.execute(
                job=job,
                presentation_id=presentation_id,
                payload=json.dumps(job.to_wire(), separators=(",", ":")).encode(),
                command=(
                    "/bin/sh",
                    "-c",
                    worker_code,
                    "trusted-worker",
                    output_reference.storage_key,
                    output_payload.decode("ascii"),
                    json.dumps(result, separators=(",", ":")),
                ),
                environment={
                    "PATH": os.environ.get("PATH", ""),
                    "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                    "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(capability_key),
                    "PUBLIC_WORKER_ROLE": "render",
                    "PUBLIC_WORKER_OS_ISOLATION": "nonroot-readonly-tmpfs-network-none",
                    "PUBLIC_WORKER_SESSIONLESS": "1",
                },
            )
        finally:
            await runtime.close()

    stdout = asyncio.run(run())
    assert json.loads(stdout)["outputArtifact"] == output_reference.to_wire()
    assert published.read_bytes() == output_payload


@pytest.mark.parametrize("termination", ["deadline", "cancel"])
@pytest.mark.skip(reason="Docker transport is retired from current development validation")
def test_docker_transport_termination_removes_container(
    tmp_path: Path,
    termination: str,
) -> None:
    docker = shutil.which("docker")
    if not docker:
        pytest.skip("docker CLI unavailable")
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    tenant_id = "tenant-docker-termination"
    payload = b"termination-input"
    digest = hashlib.sha256(payload).hexdigest()
    source = tmp_path / "input.bin"
    source.write_bytes(payload)
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="slide-html",
        sha256=digest,
        size_bytes=len(payload),
        media_type="text/html",
        storage_key=_storage_key(owner_id, tenant_id, digest),
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token="termination-attempt",
        fencing_token=3,
        input_digest=digest,
        input_artifact=reference,
        capability="test.capability",
        deadline_epoch_seconds=int(time.time()) + (2 if termination == "deadline" else 30),
    )
    key = tmp_path / "capability.key"
    key.write_bytes(b"k" * 32)
    runtime = create_trusted_worker_executor_runtime(
        staging_root=tmp_path / "staging",
        node_id="node-termination",
        artifact_authority=_FilesystemAuthority(source, tmp_path / "unused"),
        environment={
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "docker",
            "PUBLIC_TRUSTED_WORKER_DOCKER_IMAGE": _DOCKER_TEST_IMAGE,
            "PUBLIC_TRUSTED_WORKER_DOCKER_EXECUTABLE": docker,
        },
    )
    prefix = f"presenton-worker-{str(operation_id)[:12]}"

    async def run() -> None:
        task = asyncio.create_task(
            runtime.dispatcher.execute(
                job=job,
                presentation_id=uuid.uuid4(),
                payload=json.dumps(job.to_wire(), separators=(",", ":")).encode(),
                command=("/bin/sh", "-c", "cat >/tmp/job.json; sleep 60"),
                environment={
                    "PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(key),
                    "PUBLIC_WORKER_ROLE": "render",
                    "PUBLIC_WORKER_OS_ISOLATION": "nonroot-readonly-tmpfs-network-none",
                    "PUBLIC_WORKER_SESSIONLESS": "1",
                },
            )
        )
        try:
            if termination == "cancel":
                expires = time.monotonic() + 5
                while time.monotonic() < expires:
                    visible = await asyncio.to_thread(
                        subprocess.run,
                        [docker, "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
                        capture_output=True,
                        text=True,
                    )
                    if visible.stdout.strip():
                        break
                    await asyncio.sleep(0.05)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(TrustedWorkerWireError, match="deadline_exceeded"):
                    await task
        finally:
            await runtime.close()

    asyncio.run(run())
    remaining = subprocess.run(
        [docker, "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert remaining.stdout.strip() == ""
