from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.ppt.endpoints.authored_vnext.master.asset_router import (
    AUTHORED_VNEXT_MASTER_ASSET_ROUTER,
    get_master_asset_store,
)
from services import authored_source_images
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from services.source_images.models import SourceImageCandidate


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
PNG_PATH = REPOSITORY_ROOT / "servers" / "nextjs" / "public" / "Logo.png"


def test_body_image_upload_and_readback_fail_closed_on_changed_bytes(
    tmp_path: Path,
) -> None:
    store = OwnerMasterAssetStore(tmp_path / "owner")
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_MASTER_ASSET_ROUTER)
    app.dependency_overrides[get_master_asset_store] = lambda: store
    client = TestClient(app)
    value = PNG_PATH.read_bytes()

    uploaded = client.post(
        "/assets/body-image",
        files={"file": ("body.png", value, "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    receipt = uploaded.json()
    fetched = client.get(
        f"/assets/{receipt['assetId']}",
        params={"digest": receipt["digest"]},
    )
    assert fetched.status_code == 200
    assert fetched.content == value

    stored = next(store.root.rglob("*.png"))
    stored.write_bytes(value + b"changed")
    rejected = client.get(
        f"/assets/{receipt['assetId']}",
        params={"digest": receipt["digest"]},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == (
        "authored_vnext_master_asset_readback_failed"
    )


def test_source_candidate_is_not_offered_after_bytes_change(tmp_path: Path) -> None:
    path = tmp_path / "source.png"
    original = b"original-source-image"
    path.write_bytes(original)
    candidate = SourceImageCandidate(
        asset_id="asset:source",
        path=path,
        mime_type="image/png",
        width=800,
        height=600,
        digest=hashlib.sha256(original).hexdigest(),
    )
    path.write_bytes(b"different-bytes")

    assert authored_source_images.plan_slide_source_images(
        ("제목\n본문",),
        (candidate,),
    ) is None
