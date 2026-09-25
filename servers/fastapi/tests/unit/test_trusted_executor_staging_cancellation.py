from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.isolated_worker_contracts import ArtifactRef
from services.trusted_executor.wire_transport import ArtifactObjectStoreWireAdapter


@pytest.mark.parametrize("read_fails", [False, True])
@pytest.mark.parametrize("cancel_twice", [False, True])
def test_staging_cancellation_drains_copy_and_removes_temporary_file(
    tmp_path: Path, read_fails: bool, cancel_twice: bool,
) -> None:
    payload = b"cancelled staged input"
    digest = hashlib.sha256(payload).hexdigest()
    owner = uuid.uuid4()
    tenant = "staging-cancellation"
    tenant_digest = hashlib.sha256(tenant.encode()).hexdigest()
    reference = ArtifactRef(
        artifact_id=uuid.uuid4(), owner_id=owner, tenant_id=tenant,
        kind="slide-html", sha256=digest, size_bytes=len(payload),
        media_type="text/html",
        storage_key=f"v1/tenant/{tenant_digest}/owner/{owner}/sha256/{digest[:2]}/{digest}",
        created_by_operation_id=uuid.uuid4(), retention_class="ephemeral",
    )
    release = threading.Event()
    closed = threading.Event()
    destination = tmp_path / "input.html"

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        reading = asyncio.Event()

        class Stream:
            def read(self, size: int) -> bytes:
                loop.call_soon_threadsafe(reading.set)
                if not release.wait(5):
                    raise TimeoutError("test did not release staged read")
                if read_fails:
                    raise OSError("simulated read failure")
                return payload

        class Store:
            async def open_read_stream(self, artifact: object) -> object:
                return SimpleNamespace(body=Stream(), close=closed.set)

            async def put_stream(self, *args: object) -> None:
                raise AssertionError("staging must not publish")

            async def head(self, *args: object) -> None:
                raise AssertionError("staging must not publish")

        staging = asyncio.create_task(ArtifactObjectStoreWireAdapter(Store()).stage(
            reference=reference, destination=destination,
        ))
        try:
            await asyncio.wait_for(reading.wait(), 2)
            staging.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not staging.done(), "cancellation returned with a live file writer"
            if cancel_twice:
                staging.cancel()
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                assert not staging.done(), "repeated cancellation abandoned cleanup"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(staging, 2)
            assert closed.is_set()
            assert not destination.exists()
            assert not list(tmp_path.glob(".*.stage-*.tmp"))
        finally:
            release.set()
            if not staging.done():
                staging.cancel()
            await asyncio.gather(staging, return_exceptions=True)

    asyncio.run(scenario())
