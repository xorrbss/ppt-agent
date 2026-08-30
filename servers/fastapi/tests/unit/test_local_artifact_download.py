from __future__ import annotations

import asyncio
from hashlib import sha256
import io
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest

from api.v2.router import download_local_development_artifact
from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.artifact_store import (
    configured_local_development_artifact_store,
)


def _artifact(payload: bytes) -> ArtifactRef:
    owner_id = uuid.uuid4()
    digest = sha256(payload).hexdigest()
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="local-development",
        kind="presentation-pptx",
        sha256=digest,
        size_bytes=len(payload),
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        storage_key=artifact_storage_key(
            owner_id=owner_id,
            tenant_id="local-development",
            digest=digest,
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="published",
    )


def _response_body(response) -> bytes:
    async def collect() -> bytes:
        return b"".join([chunk async for chunk in response.body_iterator])

    return asyncio.run(collect())


def test_local_signed_download_is_loopback_bound_streamed_and_range_capable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("PUBLIC_ARTIFACT_STORE_BACKEND", "local-development")
    monkeypatch.setenv("PUBLIC_WORKER_OBJECT_STORE_ROOT", str(tmp_path / "objects"))
    monkeypatch.setenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_SIGNING_KEY", "k" * 32)
    monkeypatch.setenv("PUBLIC_LOCAL_ARTIFACT_DOWNLOAD_ORIGIN", "http://127.0.0.1:8010")
    payload = b"0123456789abcdef"
    reference = _artifact(payload)
    store = configured_local_development_artifact_store()
    asyncio.run(store.put_stream(reference, io.BytesIO(payload)))
    grant = asyncio.run(store.create_signed_download(reference, expires_seconds=120))
    parsed = urlsplit(grant.url)
    assert parsed.hostname == "127.0.0.1"
    token = parse_qs(parsed.query)["token"][0]

    full = asyncio.run(
        download_local_development_artifact(
            str(reference.artifact_id), token=token, range_header=None
        )
    )
    assert full.status_code == 200
    assert full.headers["content-length"] == str(len(payload))
    assert _response_body(full) == payload

    ranged = asyncio.run(
        download_local_development_artifact(
            str(reference.artifact_id), token=token, range_header="bytes=2-7"
        )
    )
    assert ranged.status_code == 206
    assert ranged.headers["content-range"] == "bytes 2-7/16"
    assert _response_body(ranged) == payload[2:8]

    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="local_download_development_only"):
        configured_local_development_artifact_store()
