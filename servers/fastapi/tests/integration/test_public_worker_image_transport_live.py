from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import zipfile

import pytest

pytestmark = pytest.mark.skip(
    reason="Docker image transport is retired; use Windows local product-path validation"
)
from PIL import Image

from services.isolated_worker_contracts import (
    ArtifactRef,
    IsolatedWorkerJob,
    issue_worker_capability,
)
from services.isolated_worker_process import execute_isolated_worker_job
from services.trusted_executor.composition import (
    create_trusted_worker_executor_runtime,
)
from services.trusted_executor.worker_dispatcher import TrustedWorkerWireError


_PRODUCT_IMAGE = (
    "presenton-public-worker@sha256:"
    "c90152497f44073a0f91d09359de16f11f8f92df33ada252c880d9683fa570ce"
)
_EXPORT_COMMAND = (
    "/opt/venv/bin/python",
    "-m",
    "workers.public_export_worker",
)


def _storage_key(owner_id: uuid.UUID, tenant_id: str, digest: str) -> str:
    tenant_digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()
    return (
        f"v1/tenant/{tenant_digest}/owner/{owner_id}/"
        f"sha256/{digest[:2]}/{digest}"
    )


class _LocalStreamingAuthority:
    def __init__(self, *, sources: dict[str, Path], publication_root: Path) -> None:
        self._sources = sources
        self._publication_root = publication_root
        self.published: dict[str, Path] = {}

    async def stage(self, *, reference: ArtifactRef, destination: Path) -> None:
        source = self._sources.get(reference.storage_key)
        if source is None:
            raise AssertionError(f"unexpected stage ref: {reference.storage_key}")
        await asyncio.to_thread(_copy_verified, reference, source, destination)

    async def publish(self, *, reference: ArtifactRef, source: Path) -> None:
        destination = self._publication_root.joinpath(*reference.storage_key.split("/"))
        await asyncio.to_thread(_copy_verified, reference, source, destination)
        await asyncio.to_thread(_verify_file, reference, destination)
        self.published[reference.storage_key] = destination


def _copy_verified(reference: ArtifactRef, source: Path, destination: Path) -> None:
    _verify_file(reference, source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with source.open("rb") as incoming, temporary.open("xb") as output:
            while chunk := incoming.read(1024 * 1024):
                output.write(chunk)
            output.flush()
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_file(reference: ArtifactRef, path: Path) -> None:
    assert path.stat().st_size == reference.size_bytes
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    assert digest.hexdigest() == reference.sha256


def _make_export_fixture(
    tmp_path: Path,
    *,
    operation_id: uuid.UUID,
    deadline_seconds: int,
) -> tuple[IsolatedWorkerJob, _LocalStreamingAuthority, Path]:
    owner_id = uuid.uuid4()
    tenant_id = "tenant-product-worker-live"
    slide_path = tmp_path / f"{operation_id}-slide.png"
    Image.new("RGB", (32, 18), (42, 103, 188)).save(slide_path, format="PNG")
    slide_digest = hashlib.sha256(slide_path.read_bytes()).hexdigest()
    slide_ref = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="slide-png",
        sha256=slide_digest,
        size_bytes=slide_path.stat().st_size,
        media_type="image/png",
        storage_key=_storage_key(owner_id, tenant_id, slide_digest),
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    bundle_payload = json.dumps(
        {
            "schemaVersion": "public-export-bundle/v1",
            "exportFormat": "pptx",
            "slides": [slide_ref.to_wire()],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle_path = tmp_path / f"{operation_id}-bundle.json"
    bundle_path.write_bytes(bundle_payload)
    bundle_digest = hashlib.sha256(bundle_payload).hexdigest()
    bundle_ref = ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id=tenant_id,
        kind="export-bundle",
        sha256=bundle_digest,
        size_bytes=len(bundle_payload),
        media_type="application/vnd.presenton.export-bundle+json",
        storage_key=_storage_key(owner_id, tenant_id, bundle_digest),
        created_by_operation_id=operation_id,
        retention_class="ephemeral",
    )
    issued_at = int(time.time())
    job = IsolatedWorkerJob(
        role="export",
        operation_id=operation_id,
        attempt_token=f"attempt-{uuid.uuid4().hex}",
        fencing_token=11,
        input_digest=bundle_digest,
        input_artifact=bundle_ref,
        capability="pending.signature",
        deadline_epoch_seconds=issued_at + deadline_seconds,
    )
    signing_key = hashlib.sha256(f"live-{operation_id}".encode()).digest()
    capability = issue_worker_capability(
        job,
        signing_key=signing_key,
        ttl_seconds=max(1, deadline_seconds - 1),
        now=issued_at,
    )
    authority = _LocalStreamingAuthority(
        sources={
            bundle_ref.storage_key: bundle_path,
            slide_ref.storage_key: slide_path,
        },
        publication_root=tmp_path / "published",
    )
    key_path = tmp_path / f"{operation_id}-capability.key"
    key_path.write_bytes(signing_key)
    return replace(job, capability=capability), authority, key_path


def _docker_runtime(
    tmp_path: Path,
    authority: _LocalStreamingAuthority,
):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI unavailable")
    return create_trusted_worker_executor_runtime(
        staging_root=tmp_path / "staging",
        node_id="node-product-worker-live",
        artifact_authority=authority,
        environment={
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "docker",
            "PUBLIC_TRUSTED_WORKER_DOCKER_IMAGE": _PRODUCT_IMAGE,
            "PUBLIC_TRUSTED_WORKER_DOCKER_EXECUTABLE": docker,
        },
        zero_timeout_seconds=10.0,
    )


def _worker_environment(key_path: Path) -> dict[str, str]:
    return {"PUBLIC_WORKER_CAPABILITY_KEY_FILE": str(key_path)}


def _remaining_containers(operation_id: uuid.UUID) -> str:
    docker = shutil.which("docker")
    assert docker is not None
    prefix = f"presenton-worker-{str(operation_id)[:12]}"
    result = subprocess.run(
        [docker, "ps", "-a", "--filter", f"name={prefix}", "--format", "{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_product_worker_image_executes_real_export_and_publishes_pptx(
    tmp_path: Path,
) -> None:
    operation_id = uuid.uuid4()
    job, authority, key_path = _make_export_fixture(
        tmp_path,
        operation_id=operation_id,
        deadline_seconds=90,
    )
    runtime = _docker_runtime(tmp_path, authority)

    async def run():
        try:
            return await execute_isolated_worker_job(
                job,
                presentation_id=uuid.uuid4(),
                source_environment=_worker_environment(key_path),
                command=_EXPORT_COMMAND,
                dispatcher=runtime.dispatcher,
            )
        finally:
            await runtime.close()

    result = asyncio.run(run())
    assert result.ok is True
    output = ArtifactRef.from_wire(dict(result.output_artifact or {}))
    published = authority.published[output.storage_key]
    _verify_file(output, published)
    with zipfile.ZipFile(published) as archive:
        names = archive.namelist()
        assert "ppt/presentation.xml" in names
        assert len(
            [
                name
                for name in names
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ]
        ) == 1
    assert _remaining_containers(operation_id) == ""


@pytest.mark.parametrize("termination", ["deadline", "cancel"])
def test_product_worker_image_termination_leaves_no_container_descendants(
    tmp_path: Path,
    termination: str,
) -> None:
    operation_id = uuid.uuid4()
    job, authority, key_path = _make_export_fixture(
        tmp_path,
        operation_id=operation_id,
        deadline_seconds=3 if termination == "deadline" else 60,
    )
    runtime = _docker_runtime(tmp_path, authority)
    delayed_command = (
        "/opt/venv/bin/python",
        "-c",
        (
            "import runpy,time;time.sleep(60);"
            "runpy.run_module('workers.public_export_worker',run_name='__main__')"
        ),
    )

    async def run() -> None:
        task = asyncio.create_task(
            execute_isolated_worker_job(
                job,
                presentation_id=uuid.uuid4(),
                source_environment=_worker_environment(key_path),
                command=delayed_command,
                dispatcher=runtime.dispatcher,
            )
        )
        try:
            if termination == "cancel":
                expires = time.monotonic() + 10
                while time.monotonic() < expires and not _remaining_containers(
                    operation_id
                ):
                    await asyncio.sleep(0.05)
                assert _remaining_containers(operation_id)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(TrustedWorkerWireError, match="deadline_exceeded"):
                    await task
        finally:
            await runtime.close()

    asyncio.run(run())
    assert _remaining_containers(operation_id) == ""
