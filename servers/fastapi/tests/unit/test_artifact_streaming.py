from __future__ import annotations

import asyncio
from hashlib import sha256
import io
from pathlib import Path
import uuid

from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services.artifact_store import (
    DurableArtifactService,
    LocalDevelopmentArtifactObjectStore,
    WorkerArtifactTransportCache,
)


class _BoundedReadProbe(io.RawIOBase):
    def __init__(self, source: io.BufferedIOBase) -> None:
        self._source = source
        self.read_sizes: list[int] = []

    def readable(self) -> bool:
        return True

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("artifact handoff attempted a whole-file read")
        return self._source.read(size)

    def close(self) -> None:
        try:
            self._source.close()
        finally:
            super().close()


def _reference(payload: bytes) -> ArtifactRef:
    owner_id = uuid.uuid4()
    digest = sha256(payload).hexdigest()
    return ArtifactRef(
        artifact_id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-streaming",
        kind="slide-html",
        sha256=digest,
        size_bytes=len(payload),
        media_type="text/html",
        storage_key=artifact_storage_key(
            owner_id=owner_id,
            tenant_id="tenant-streaming",
            digest=digest,
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="ephemeral",
    )


def test_authority_to_worker_cache_transfer_is_bounded_and_digest_bound(
    tmp_path: Path,
) -> None:
    payload = (b"<section>public export artifact</section>" * 131_072) + b"end"
    reference = _reference(payload)

    class _SourceStore(LocalDevelopmentArtifactObjectStore):
        probe: _BoundedReadProbe | None = None

        async def open_read_stream(self, artifact: ArtifactRef, **kwargs):  # type: ignore[no-untyped-def]
            opened = await super().open_read_stream(artifact, **kwargs)
            self.probe = _BoundedReadProbe(opened.body)
            from services.artifact_store import ArtifactReadStream

            return ArtifactReadStream(
                body=self.probe,
                content_length=artifact.size_bytes,
                media_type=artifact.media_type,
                etag=None,
            )

    async def scenario() -> None:
        source = _SourceStore(tmp_path / "authority")
        cache = WorkerArtifactTransportCache(
            tmp_path / "worker-cache",
            max_cache_bytes=reference.size_bytes + 1024,
            ttl_seconds=60,
        )
        await source.put_stream(reference, io.BytesIO(payload))
        await DurableArtifactService(source).stream_ref_to_object_store(reference, cache)
        assert source.probe is not None
        assert source.probe.read_sizes
        assert max(source.probe.read_sizes) <= 1024 * 1024

        opened = await cache.open_read_stream(reference)
        try:
            assert b"".join(opened.iter_chunks(chunk_size=64 * 1024)) == payload
        finally:
            opened.close()

    asyncio.run(scenario())
