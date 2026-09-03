from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from hashlib import sha256
import io
import json
from pathlib import Path
import time
import uuid

from PIL import Image
import pytest
from sqlalchemy.pool import NullPool, QueuePool

from models.sql.public_artifact import (
    ArtifactRef as PublicArtifactRef,
    artifact_storage_key,
)
from services.public_export_operation import (
    MAX_PUBLIC_RENDER_BATCH_SLIDES,
    PublicExportOperationError,
    build_public_render_bundle,
    partition_public_render_references,
    public_render_runtime_binding,
    public_render_runtime_binding_digest,
    public_slide_renderer_version,
)
from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
from services.public_export_identity import (
    CanonicalSlideRenderIdentity,
    acquire_slide_render_authority,
    run_slide_render_singleflight,
)
from services.public_render_batching import (
    OperationRenderBatcher,
    PublicRenderBatchingError,
    admit_render_authority,
    gather_render_members,
    pooled_render_authority_admission,
)
from utils import slide_capture
from workers.public_render_worker import execute_render
from workers.public_worker_runtime import LocalContentAddressedStore


def test_shared_slide_checkpoint_identity_tracks_chrome_and_local_asset_drift(
    tmp_path: Path, monkeypatch
) -> None:
    asset = tmp_path / "brand-mark.svg"
    asset.write_bytes(b"<svg><rect fill='blue'/></svg>")
    html = f'<html><body><img src="{asset.as_uri()}"></body></html>'
    chrome_a = "sha256:" + sha256(b"chrome-a").hexdigest()
    chrome_b = "sha256:" + sha256(b"chrome-b").hexdigest()
    test_font_digest = "sha256:" + sha256(b"test-fonts").hexdigest()
    local_binding = public_render_runtime_binding(
        {
            "ENVIRONMENT": "test",
            "PUBLIC_TRUSTED_WORKER_SANDBOX_BACKEND": "windows-local",
            "PUBLIC_TRUSTED_WORKER_LOCAL_POLICY": "development",
            "PUBLIC_RENDER_TEST_FONT_INVENTORY_DIGEST": test_font_digest,
        }
    )
    assert local_binding["releaseEligible"] is False
    assert local_binding["fontInventoryDigest"] == test_font_digest
    assert local_binding["limitations"] == [
        "controller-worker-colocation-required"
    ]

    monkeypatch.setattr(slide_capture, "chrome_runtime_digest", lambda: chrome_a)
    initial = public_slide_renderer_version(html, runtime_binding=local_binding)
    assert initial == public_slide_renderer_version(
        html, runtime_binding=local_binding
    )

    asset.write_bytes(b"<svg><rect fill='yellow'/></svg>")
    asset_changed = public_slide_renderer_version(
        html, runtime_binding=local_binding
    )
    assert asset_changed != initial

    monkeypatch.setattr(slide_capture, "chrome_runtime_digest", lambda: chrome_b)
    chrome_changed = public_slide_renderer_version(
        html, runtime_binding=local_binding
    )
    assert chrome_changed not in {initial, asset_changed}
    assert chrome_changed.startswith("public-slide-renderer-identity/v1:")
    assert len(chrome_changed) <= 128

    with pytest.raises(
        PublicExportOperationError,
        match="public_render_worker_runtime_pins_required",
    ):
        public_render_runtime_binding({"ENVIRONMENT": "production"})

    image_digest = "sha256:" + sha256(b"image-a").hexdigest()
    font_digest = "sha256:" + sha256(b"fonts-a").hexdigest()
    external_binding = public_render_runtime_binding(
        {
            "ENVIRONMENT": "production",
            "PUBLIC_RENDER_WORKER_IMAGE_DIGEST": image_digest,
            "PUBLIC_RENDER_WORKER_FONT_INVENTORY_DIGEST": font_digest,
        }
    )
    assert external_binding["checkpointReuseEligible"] is False
    assert external_binding["releaseEligible"] is False
    external_binding_digest = public_render_runtime_binding_digest(
        external_binding
    )
    assert external_binding_digest.startswith("sha256:")
    externally_pinned = public_slide_renderer_version(
        html, runtime_binding=external_binding
    )
    changed_font_binding = {
        **external_binding,
        "fontInventoryDigest": "sha256:" + sha256(b"fonts-b").hexdigest(),
    }
    assert public_render_runtime_binding_digest(
        changed_font_binding
    ) != external_binding_digest
    assert public_slide_renderer_version(
        html, runtime_binding=changed_font_binding
    ) != externally_pinned


def test_public_render_controller_bounds_large_decks_and_keeps_five_slide_batch(
) -> None:
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    references: list[PublicArtifactRef] = []
    for index in range(100):
        payload = f"slide-{index}".encode("utf-8")
        digest = sha256(payload).hexdigest()
        references.append(
            PublicArtifactRef(
                artifact_id=uuid.uuid4(),
                owner_id=owner_id,
                tenant_id="tenant-a",
                kind="slide-html",
                sha256=digest,
                size_bytes=len(payload),
                media_type="text/html",
                storage_key=artifact_storage_key(
                    owner_id=owner_id,
                    tenant_id="tenant-a",
                    digest=digest,
                ),
                created_by_operation_id=operation_id,
                retention_class="ephemeral",
            )
        )

    five_slide_batches = partition_public_render_references(references[:5])
    assert len(five_slide_batches) == 1
    assert five_slide_batches[0] == references[:5]

    large_deck_batches = partition_public_render_references(references)
    assert [len(batch) for batch in large_deck_batches] == [
        MAX_PUBLIC_RENDER_BATCH_SLIDES
    ] * 5
    assert [
        reference for batch in large_deck_batches for reference in batch
    ] == references
    for batch in large_deck_batches:
        bundle = json.loads(build_public_render_bundle(batch))
        assert len(bundle["slides"]) == MAX_PUBLIC_RENDER_BATCH_SLIDES

    with pytest.raises(
        PublicExportOperationError,
        match="public_render_bundle_slide_count_invalid",
    ):
        build_public_render_bundle(references[: MAX_PUBLIC_RENDER_BATCH_SLIDES + 1])

    async def readiness_contract() -> None:
        calls: list[list[PublicArtifactRef]] = []

        async def render_batch(batch):
            calls.append(list(batch))
            return batch

        def batcher(count, render=render_batch):
            return OperationRenderBatcher(
                participant_count=count,
                render_batch=render,
                partition=partition_public_render_references,
            )

        unused = batcher(5)
        assert unused._drain is None  # runtime-binding preflight failure owns no task
        await unused.close()

        late = batcher(5)
        # Identical references still own five independent input-index slots.
        members = [asyncio.create_task(late.render(i, references[0])) for i in range(4)]
        await asyncio.sleep(0.06)  # exceeds the removed 50ms coalescing deadline
        assert calls == []
        members.append(asyncio.create_task(late.render(4, references[0])))
        assert await asyncio.gather(*members) == [references[0]] * 5
        assert calls == [[references[0]] * 5]
        await late.close()

        identity = CanonicalSlideRenderIdentity(
            owner_id=owner_id, tenant_id="tenant-a", presentation_id=uuid.uuid4(),
            source_revision=1, slide_index=0, html_digest=references[0].sha256,
            scene_digest="1" * 64, quality_digest="2" * 64,
            render_adapter="public-isolated-fidelity", renderer_version="test-v1",
        )
        calls.clear()
        joined_batcher = batcher(2)
        leader_started = asyncio.Event()

        async def leader():
            leader_started.set()
            return await joined_batcher.render(0, references[0])

        async def forbidden_follower_producer():
            raise AssertionError("follower started a second renderer")

        leader_task = asyncio.create_task(run_slide_render_singleflight(
            identity, leader, max_in_flight=64, wait_seconds=2,
        ))
        await leader_started.wait()
        follower_task = asyncio.create_task(run_slide_render_singleflight(
            identity, forbidden_follower_producer, max_in_flight=64, wait_seconds=2,
            on_follower_wait=lambda: joined_batcher.settle(1),
        ))
        assert await asyncio.gather(leader_task, follower_task) == [
            (references[0], False), (references[0], True),
        ]
        assert calls == [[references[0]]]
        await joined_batcher.close()

        class Bind:
            class dialect:
                name = "postgresql"

            def __init__(self, pool):
                self.pool = pool

        class AuthoritySession:
            def __init__(self, bound, outcomes):
                self.bound, self.outcomes = bound, iter(outcomes)
                self.transactions = 0

            def get_bind(self):
                return self.bound

            @asynccontextmanager
            async def begin(self):
                self.transactions += 1
                try:
                    yield
                finally:
                    self.transactions -= 1

            async def scalar(self, *_args):
                return next(self.outcomes)

        bound = Bind(QueuePool(lambda: None, pool_size=5, max_overflow=10))
        admission = pooled_render_authority_admission(bound)
        assert pooled_render_authority_admission(bound) is admission
        assert admission._slots._value == 13  # two real pool checkouts reserved
        for pool in (NullPool(lambda: None), QueuePool(lambda: None, pool_size=0),
                     QueuePool(lambda: None, pool_size=5, max_overflow=-1)):
            assert pooled_render_authority_admission(Bind(pool))._slots._value == 20
        with pytest.raises(PublicRenderBatchingError, match="pool_capacity_invalid"):
            pooled_render_authority_admission(Bind(QueuePool(
                lambda: None, pool_size=1, max_overflow=0,
            )))
        sqlite_bind = Bind(None)
        sqlite_bind.dialect = type("Dialect", (), {"name": "sqlite"})()
        async with admit_render_authority(
            AuthoritySession(sqlite_bind, []), on_wait=lambda: None, wait_seconds=2,
        ):
            pass  # StaticPool/NullPool development paths never need PG admission

        calls.clear()
        locked_batcher = batcher(2)
        authority = AuthoritySession(bound, [False, False, True])
        misses = []

        def waiting_elsewhere():
            assert authority.transactions == 0  # miss transaction already released
            misses.append(True)
            locked_batcher.settle(1)

        async def reuse_after_pg_wait():
            async with acquire_slide_render_authority(
                authority, identity=identity, wait_seconds=2,
                on_authority_wait=waiting_elsewhere,
            ):
                assert calls == [[references[0]]]
                locked_batcher.settle(1)  # authoritative checkpoint recheck hit
                return references[0]

        assert await gather_render_members([
            locked_batcher.render(0, references[0]), reuse_after_pg_wait(),
        ]) == [references[0]] * 2
        assert misses == [True] and authority.transactions == 0
        await locked_batcher.close()

        # Multiple operations share the engine budget; >pool-size participants
        # must discharge blocked readiness before awaiting a permit.
        held = peak = blocked = 0
        large_calls = []

        async def large_render(batch):
            large_calls.append(list(batch))
            await asyncio.sleep(0)
            return batch

        async def large_operation(offset):
            large = batcher(30, large_render)

            async def member(index):
                nonlocal held, peak, blocked

                def gate_wait():
                    nonlocal blocked
                    blocked += 1
                    large.settle(index)

                async with admit_render_authority(
                    AuthoritySession(bound, []), on_wait=gate_wait, wait_seconds=2,
                ):
                    held += 1
                    peak = max(peak, held)
                    try:
                        return await large.render(index, references[offset + index])
                    finally:
                        held -= 1

            try:
                return await gather_render_members([member(i) for i in range(30)])
            finally:
                await large.close()

        assert await asyncio.gather(large_operation(0), large_operation(30)) == [
            references[:30], references[30:60],
        ]
        assert peak == 13 and held == 0 and blocked > 0
        assert admission._slots._value == 13
        assert sum(map(len, large_calls)) == 60
        assert all(0 < len(batch) <= MAX_PUBLIC_RENDER_BATCH_SLIDES for batch in large_calls)

        # A failed worker chunk and operation cancellation drain all member,
        # batch and held-authority tasks; neither kind of failure waits forever.
        for cancel in (False, True):
            entered = asyncio.Event()
            worker_finished = asyncio.Event()
            holder_finished = asyncio.Event()

            # Every closure in this loop runs before the next iteration, so
            # the loop variables are bound as defaults to keep it that way.
            async def failing_render(
                _batch,
                *,
                entered=entered,
                cancel=cancel,
                worker_finished=worker_finished,
            ):
                entered.set()
                try:
                    if not cancel:
                        raise RuntimeError("fixture-render-failure")
                    await asyncio.Future()
                finally:
                    worker_finished.set()

            failed = batcher(2, failing_render)

            async def holder(*, failed=failed, holder_finished=holder_finished):
                try:
                    async with admission.acquire(on_wait=lambda: None, wait_seconds=2):
                        failed.settle(1)
                        await asyncio.Future()
                finally:
                    holder_finished.set()

            async def operation(*, failed=failed):
                try:
                    return await gather_render_members([
                        failed.render(0, references[0]), holder(),
                    ])
                finally:
                    await failed.close()

            operation_task = asyncio.create_task(operation())
            await entered.wait()
            if cancel:
                operation_task.cancel()
            with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
                await operation_task
            assert worker_finished.is_set() and holder_finished.is_set()
            assert admission._slots._value == 13
            assert failed._drain.done()

    asyncio.run(asyncio.wait_for(readiness_contract(), timeout=5))


def test_five_slide_public_render_batch_reuses_one_chromium_pipe(
    tmp_path: Path, monkeypatch
) -> None:
    operation_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    output_root.mkdir()
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_MAX_BYTES", str(64 * 1024 * 1024))
    monkeypatch.setenv("PUBLIC_WORKER_ARTIFACT_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("PPT_WORKER_OUTPUT_LIMIT_BYTES", str(64 * 1024 * 1024))

    def reference(*, storage_key: str, payload: bytes, kind: str, media_type: str) -> ArtifactRef:
        path = input_root / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return ArtifactRef(
            artifact_id=uuid.uuid4(),
            owner_id=owner_id,
            tenant_id="tenant-a",
            kind=kind,
            sha256=sha256(payload).hexdigest(),
            size_bytes=len(payload),
            media_type=media_type,
            storage_key=storage_key,
            created_by_operation_id=operation_id,
            retention_class="ephemeral",
        )

    html_refs = [
        reference(
            storage_key=f"slides/{index}.html",
            payload=(
                "<!doctype html><html><head><meta charset='utf-8'></head>"
                f"<body><div data-qid='title-{index}'>Slide {index}</div></body></html>"
            ).encode("utf-8"),
            kind="slide-html",
            media_type="text/html",
        )
        for index in range(5)
    ]
    bundle_payload = json.dumps(
        {
            "schemaVersion": "public-render-bundle/v1",
            "slides": [
                {"slideIndex": index, "artifactRef": item.to_wire()}
                for index, item in enumerate(html_refs)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    bundle_ref = reference(
        storage_key="bundles/render.json",
        payload=bundle_payload,
        kind="export-bundle",
        media_type="application/vnd.presenton.export-bundle+json",
    )
    job = IsolatedWorkerJob(
        role="render",
        operation_id=operation_id,
        attempt_token="attempt-token",
        fencing_token=7,
        input_digest=bundle_ref.sha256,
        input_artifact=bundle_ref,
        capability="test-capability",
        deadline_epoch_seconds=int(time.time()) + 120,
    )

    png_buffer = io.BytesIO()
    Image.new("RGB", (1280, 720), "white").save(png_buffer, format="PNG")
    png_base64 = base64.b64encode(png_buffer.getvalue()).decode("ascii")

    class FakeChromePipe:
        launches = 0
        closes = 0
        created_contexts = 0
        disposed_contexts = 0
        download_denials = 0
        created_targets = 0
        closed_targets = 0

        def __init__(self, _chrome: str, _profile: Path) -> None:
            type(self).launches += 1

        def command(self, _command_id, method, _params=None, *, session_id=None):
            if method == "Target.createBrowserContext":
                type(self).created_contexts += 1
                return {"browserContextId": f"context-{self.created_contexts}"}
            if method == "Browser.setDownloadBehavior":
                assert _params == {
                    "behavior": "deny",
                    "browserContextId": f"context-{self.created_contexts}",
                }
                type(self).download_denials += 1
            if method == "Target.createTarget":
                type(self).created_targets += 1
                assert _params == {
                    "url": "data:,",
                    "browserContextId": f"context-{self.created_contexts}",
                }
                return {"targetId": f"target-{self.created_targets}"}
            if method == "Target.attachToTarget":
                return {"sessionId": f"session-{self.created_targets}"}
            if method == "Page.getFrameTree":
                return {"frameTree": {"frame": {"id": "frame-1"}}}
            if method == "Runtime.evaluate":
                return {"result": {"value": None}}
            if method == "Page.captureScreenshot":
                return {"data": png_base64}
            if method == "Target.closeTarget":
                type(self).closed_targets += 1
            if method == "Target.disposeBrowserContext":
                type(self).disposed_contexts += 1
            return {}

        def close(self) -> None:
            type(self).closes += 1

    monkeypatch.setattr(slide_capture, "_ChromePipe", FakeChromePipe)
    monkeypatch.setattr(slide_capture, "find_chrome", lambda: "synthetic-chrome")
    monkeypatch.setattr(slide_capture, "_runtime_value", lambda _payload: None)

    store = LocalContentAddressedStore(input_root, output_root)
    result_ref = asyncio.run(execute_render(job, store))
    manifest = json.loads(store.read(result_ref))

    assert manifest["runtime"] == {
        "chromiumProcessLaunchCount": 1,
        "browserReuseCount": 4,
        "pageSessionCount": 5,
    }
    assert len(manifest["slides"]) == 5
    assert FakeChromePipe.launches == 1
    assert FakeChromePipe.closes == 1
    assert FakeChromePipe.created_contexts == 5
    assert FakeChromePipe.disposed_contexts == 5
    assert FakeChromePipe.download_denials == 5
    assert FakeChromePipe.created_targets == 5
    assert FakeChromePipe.closed_targets == 5
