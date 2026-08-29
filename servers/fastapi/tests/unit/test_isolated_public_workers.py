from __future__ import annotations

import asyncio
import hashlib
import io
import json
import time
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from services.isolated_worker_contracts import (
    ARTIFACT_REF_SCHEMA,
    ArtifactRef,
    CapabilityReplayGuard,
    IsolatedWorkerContractError,
    IsolatedWorkerJob,
    issue_worker_capability,
    verify_and_consume_worker_capability,
)
from services.isolated_worker_process import (
    NETWORK_NONE_ATTESTATION,
    worker_environment,
)
from workers.public_export_worker import EXPORT_BUNDLE_SCHEMA, execute_export
from workers.public_worker_runtime import LocalContentAddressedStore


def _artifact(
    *,
    operation_id: uuid.UUID,
    payload: bytes,
    storage_key: str,
    kind: str,
    media_type: str,
) -> ArtifactRef:
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=uuid.UUID("10000000-0000-4000-8000-000000000001"),
        tenant_id="tenant-a",
        kind=kind,
        sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type=media_type,
        storage_key=storage_key,
        created_by_operation_id=operation_id,
        retention_class="staged",
    )


def _store_payload(root: Path, reference: ArtifactRef, payload: bytes) -> None:
    path = root / reference.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_capability_is_attempt_digest_scoped_and_one_shot() -> None:
    now = int(time.time())
    operation_id = uuid.uuid4()
    payload = b"sealed input"
    artifact = _artifact(
        operation_id=operation_id,
        payload=payload,
        storage_key="tenant/owner/aa/input",
        kind="render-html",
        media_type="text/html",
    )
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token="attempt-token-1234567890",
        fencing_token=3,
        input_digest=artifact.sha256,
        input_artifact=artifact,
        capability="pending",
        deadline_epoch_seconds=now + 300,
    )
    signing_key = b"k" * 32
    job = replace(
        job,
        capability=issue_worker_capability(
            job,
            signing_key=signing_key,
            ttl_seconds=120,
            now=now,
            nonce="a" * 32,
        ),
    )
    parsed = IsolatedWorkerJob.from_wire(job.to_wire())
    guard = CapabilityReplayGuard()

    claims = verify_and_consume_worker_capability(
        parsed,
        signing_key=signing_key,
        replay_guard=guard,
        now=now,
    )
    assert claims.operation_id == operation_id
    assert claims.input_digest == artifact.sha256
    with pytest.raises(IsolatedWorkerContractError, match="already consumed"):
        verify_and_consume_worker_capability(
            parsed,
            signing_key=signing_key,
            replay_guard=guard,
            now=now,
        )


def test_worker_environment_is_allowlisted_and_requires_network_none() -> None:
    source = {
        "PATH": "C:/runtime",
        "PUBLIC_WORKER_OS_ISOLATION": NETWORK_NONE_ATTESTATION,
        "PUBLIC_WORKER_CAPABILITY_KEY_FILE": "C:/run/secrets/capability.key",
        "PUBLIC_WORKER_OBJECT_STORE_ROOT": "C:/object-store",
        "OPENAI_API_KEY": "must-not-cross",
        "DATABASE_URL": "must-not-cross",
        "COOKIE": "must-not-cross",
    }
    environment = worker_environment("export", source)
    assert environment["PUBLIC_WORKER_SESSIONLESS"] == "1"
    assert environment["PUBLIC_WORKER_ROLE"] == "export"
    assert "OPENAI_API_KEY" not in environment
    assert "DATABASE_URL" not in environment
    assert "COOKIE" not in environment

    render_environment = worker_environment("render", source)
    assert render_environment["AUTHORED_RENDERER_WORKER"] == "1"
    assert "OPENAI_API_KEY" not in render_environment


def test_export_worker_builds_digest_verified_pptx_from_artifact_refs(
    tmp_path: Path,
) -> None:
    operation_id = uuid.uuid4()
    image = Image.new("RGB", (1280, 720), "#1265E3")
    png_stream = io.BytesIO()
    image.save(png_stream, format="PNG")
    image.close()
    png = png_stream.getvalue()
    slide = _artifact(
        operation_id=operation_id,
        payload=png,
        storage_key="tenant/owner/slide.png",
        kind="slide-png",
        media_type="image/png",
    )
    _store_payload(tmp_path, slide, png)
    bundle_payload = json.dumps(
        {
            "schemaVersion": EXPORT_BUNDLE_SCHEMA,
            "exportFormat": "pptx",
            "slides": [slide.to_wire()],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle = _artifact(
        operation_id=operation_id,
        payload=bundle_payload,
        storage_key="tenant/owner/export-bundle.json",
        kind="export-bundle",
        media_type="application/vnd.presenton.export-bundle+json",
    )
    _store_payload(tmp_path, bundle, bundle_payload)
    job = IsolatedWorkerJob(
        role="export",
        operation_id=operation_id,
        attempt_token="attempt-token-1234567890",
        fencing_token=1,
        input_digest=bundle.sha256,
        input_artifact=bundle,
        capability="validated-by-runtime",
        deadline_epoch_seconds=int(time.time()) + 300,
    )
    store = LocalContentAddressedStore(tmp_path)

    output = asyncio.run(execute_export(job, store))

    assert output.kind == "presentation-pptx"
    assert output.owner_id == bundle.owner_id
    assert output.created_by_operation_id == operation_id
    payload = store.read(output)
    assert hashlib.sha256(payload).hexdigest() == output.sha256
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert "ppt/presentation.xml" in archive.namelist()


def test_artifact_ref_rejects_paths_and_session_fields() -> None:
    operation_id = uuid.uuid4()
    payload = b"x"
    base = _artifact(
        operation_id=operation_id,
        payload=payload,
        storage_key="tenant/owner/object",
        kind="slide-png",
        media_type="image/png",
    ).to_wire()
    assert base["schemaVersion"] == ARTIFACT_REF_SCHEMA
    with pytest.raises(IsolatedWorkerContractError, match="relative"):
        ArtifactRef.from_wire({**base, "storageKey": "C:/private/input.png"})
    with pytest.raises(IsolatedWorkerContractError, match="credential"):
        ArtifactRef.from_wire({**base, "cookie": "session=secret"})


def test_next_export_route_is_admission_only() -> None:
    route = (
        Path(__file__).resolve().parents[3]
        / "nextjs"
        / "app"
        / "api"
        / "export-presentation"
        / "route.ts"
    ).read_text(encoding="utf-8")
    assert "/api/v1/ppt/workspace/presentations/" in route
    assert "runBundledPresentationExport" not in route
    assert "runAuthoredHybridPresentationExport" not in route
    assert "child_process" not in route
    assert "export_task.json" not in route
