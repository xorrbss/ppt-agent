from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from services.public_export_operation import (PublicExportOperationError, public_render_runtime_binding, public_render_runtime_binding_digest, public_slide_renderer_version)
from utils import slide_capture


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
