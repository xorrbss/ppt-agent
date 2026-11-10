from __future__ import annotations

import asyncio
import base64
from hashlib import sha256
import io
import json
from pathlib import Path
import time
import uuid

from PIL import Image
import pytest

from services.public_export_operation import (PublicExportOperationError, public_render_runtime_binding, public_render_runtime_binding_digest, public_slide_renderer_version)
from services.isolated_worker_contracts import ArtifactRef, IsolatedWorkerJob
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
