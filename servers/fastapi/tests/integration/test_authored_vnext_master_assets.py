from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.v1.ppt.endpoints.authored_vnext.master.asset_router import (
    get_master_asset_store,
)
from api.v1.ppt.endpoints.authored_vnext.master.router import (
    AUTHORED_VNEXT_MASTER_ROUTER,
)
from services.authored_vnext.master.assets import (
    MasterAssetError,
    OwnerMasterAssetStore,
    quarantine_owner_master_assets,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LOGO_PATH = REPOSITORY_ROOT / "servers" / "nextjs" / "public" / "Logo.png"


def test_owner_scoped_logo_upload_is_immutable_and_digest_verified(
    tmp_path: Path, monkeypatch
) -> None:
    owner_a = OwnerMasterAssetStore(tmp_path / "owner-a")
    owner_b = OwnerMasterAssetStore(tmp_path / "owner-b")
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_MASTER_ROUTER)
    app.dependency_overrides[get_master_asset_store] = lambda: owner_a
    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "true")
    client = TestClient(app)
    value = LOGO_PATH.read_bytes()

    response = client.post(
        "/authored-vnext/master/assets/logo",
        files={"file": ("corporate.png", value, "image/png")},
    )
    assert response.status_code == 201, response.text
    receipt = response.json()
    assert receipt["mediaType"] == "image/png"
    assert receipt["byteLength"] == len(value)
    assert receipt["width"] > 0 and receipt["height"] > 0
    assert owner_a.open_verified(receipt["assetId"], receipt["digest"]) == value
    with pytest.raises(MasterAssetError, match="asset_not_found"):
        owner_b.open_verified(receipt["assetId"], receipt["digest"])

    repeated = client.post(
        "/authored-vnext/master/assets/logo",
        files={"file": ("corporate.png", value, "image/png")},
    )
    assert repeated.status_code == 201
    assert repeated.json() == receipt

    rejected = client.post(
        "/authored-vnext/master/assets/logo",
        files={"file": ("fake.png", b"not-a-png", "image/png")},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == (
        "authored_vnext_master_asset_png_required"
    )


def test_account_deletion_fences_and_quarantines_owner_logo_assets(
    tmp_path: Path,
) -> None:
    owner_id = "6c00a71f-8ab3-4459-8b09-74201f519be2"
    root = tmp_path / "app-data"
    store = OwnerMasterAssetStore(
        root / "authored_vnext" / "master-assets" / "users" / owner_id
    )
    value = LOGO_PATH.read_bytes()
    receipt = store.publish_png(value)
    result = quarantine_owner_master_assets(
        root,
        owner_id=owner_id,
        expected_generation=3,
    )
    assert result == {
        "ownerId": owner_id,
        "generation": 3,
        "quarantined": True,
        "assetCount": 1,
    }
    quarantine = (
        root
        / "authored_vnext"
        / "master-assets"
        / "quarantine"
        / owner_id
        / "generation-3"
    )
    assert list(quarantine.rglob("*.png"))
    assert quarantine_owner_master_assets(
        root,
        owner_id=owner_id,
        expected_generation=3,
    ) == result
    with pytest.raises(MasterAssetError, match="owner_fenced"):
        store.publish_png(value)
    with pytest.raises(MasterAssetError, match="owner_fenced"):
        store.open_verified(str(receipt["assetId"]), str(receipt["digest"]))
