"""Focused owner-scoped master catalog and server registration contract."""

from __future__ import annotations

import base64
from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.ppt.endpoints.authored_vnext.master.router import (
    AUTHORED_VNEXT_MASTER_ROUTER,
    get_authored_vnext_master_service,
)
from api.v1.ppt.endpoints.authored_vnext.master.asset_router import (
    get_master_asset_store,
)
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from services.authored_vnext.master.catalog_seed import (
    IBIZ_MASTER_ID,
    SAMPLE_MASTER_IDS,
    seed_catalog,
)
from services.authored_vnext.master.repository import InMemoryAuthoredVNextMasterRepository
from services.authored_vnext.master.service import AuthoredVNextMasterService
from tests.support.authored_vnext_master_fixture import master_contract_payload


PREFIX = "/api/v1/ppt/authored-vnext/master"
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
LOGO_PATH = REPOSITORY_ROOT / "servers" / "nextjs" / "public" / "Logo.png"


def test_register_catalog_and_latest_published_pin(monkeypatch) -> None:
    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "true")
    repository = InMemoryAuthoredVNextMasterRepository()
    service = AuthoredVNextMasterService(repository)
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_MASTER_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_authored_vnext_master_service] = lambda: service

    with TestClient(app) as client:
        assert client.get(f"{PREFIX}/masters").json() == {
            "items": [],
            "nextCursor": None,
        }

        supplied = master_contract_payload()
        supplied["masterId"] = "master:client-choice"
        registered_response = client.post(
            f"{PREFIX}/masters/register",
            json={"contract": supplied},
        )
        assert registered_response.status_code == 201, registered_response.text
        draft = registered_response.json()
        assert draft["masterId"] != "master:client-choice"
        assert draft["masterId"].startswith("master:")
        assert draft["revision"] == 1
        assert draft["status"] == "draft"

        draft_catalog = client.get(f"{PREFIX}/masters").json()
        assert draft_catalog["nextCursor"] is None
        assert draft_catalog["items"] == [
            {
                "masterId": draft["masterId"],
                "name": draft["contract"]["name"],
                "semanticVersion": draft["contract"]["semanticVersion"],
                "publishedRevision": None,
                "publishedDigest": None,
                "hasDraft": True,
                "logo": draft["contract"]["brandAssets"]["logo"],
                "primaryFont": draft["contract"]["fontManifest"]["primary"],
                "layoutCount": 4,
                "updatedTime": draft_catalog["items"][0]["updatedTime"],
            }
        ]
        published_response = client.post(
            f"{PREFIX}/drafts/{draft['masterId']}/publish",
            json={
                "expectedRevision": draft["revision"],
                "expectedDigest": draft["digest"],
            },
        )
        assert published_response.status_code == 200, published_response.text
        published = published_response.json()

        next_contract = deepcopy(published["contract"])
        next_contract["semanticVersion"] = "1.0.1"
        next_contract["digest"] = None
        next_draft_response = client.post(
            f"{PREFIX}/drafts",
            json={
                "contract": next_contract,
                "expectedRevision": published["revision"],
                "expectedDigest": published["digest"],
            },
        )
        assert next_draft_response.status_code == 201, next_draft_response.text

        catalog_response = client.get(f"{PREFIX}/masters?limit=1")
        assert catalog_response.status_code == 200, catalog_response.text
        catalog = catalog_response.json()
        assert catalog["nextCursor"] is None
        assert catalog["items"] == [
            {
                "masterId": published["masterId"],
                "name": published["contract"]["name"],
                "semanticVersion": "1.0.0",
                "publishedRevision": published["revision"],
                "publishedDigest": published["digest"],
                "hasDraft": True,
                "logo": published["contract"]["brandAssets"]["logo"],
                "primaryFont": published["contract"]["fontManifest"]["primary"],
                "layoutCount": 4,
                "updatedTime": catalog["items"][0]["updatedTime"],
            }
        ]

        resolved = client.get(
            f"{PREFIX}/masters/{published['masterId']}/published"
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json() == published

        second_contract = master_contract_payload()
        second_contract["name"] = "Second catalog master"
        second_draft_response = client.post(
            f"{PREFIX}/masters/register",
            json={"contract": second_contract},
        )
        assert second_draft_response.status_code == 201, second_draft_response.text
        second_draft = second_draft_response.json()
        second_publish_response = client.post(
            f"{PREFIX}/drafts/{second_draft['masterId']}/publish",
            json={
                "expectedRevision": second_draft["revision"],
                "expectedDigest": second_draft["digest"],
            },
        )
        assert second_publish_response.status_code == 200, second_publish_response.text

        first_page = client.get(f"{PREFIX}/masters?limit=1").json()
        assert len(first_page["items"]) == 1
        assert first_page["nextCursor"] is not None
        second_page = client.get(
            f"{PREFIX}/masters?limit=1&cursor={first_page['nextCursor']}"
        ).json()
        assert len(second_page["items"]) == 1
        assert second_page["nextCursor"] is None
        assert {
            first_page["items"][0]["masterId"],
            second_page["items"][0]["masterId"],
        } == {published["masterId"], second_draft["masterId"]}


def test_owner_scoped_asset_get_returns_digest_pinned_png(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "true")
    store = OwnerMasterAssetStore(tmp_path / "owner-a")
    receipt = store.publish_png(LOGO_PATH.read_bytes())
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_MASTER_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_master_asset_store] = lambda: store

    with TestClient(app) as client:
        response = client.get(
            f"{PREFIX}/assets/{receipt['assetId']}",
            params={"digest": receipt["digest"]},
        )
        assert response.status_code == 200, response.text
        assert response.content == LOGO_PATH.read_bytes()
        assert response.headers["content-type"] == "image/png"
        assert response.headers["etag"] == f'"{receipt["digest"]}"'
        assert response.headers["cache-control"].endswith("immutable")

        missing = client.get(
            f"{PREFIX}/assets/asset:{'f' * 32}",
            params={"digest": f"sha256:{'f' * 64}"},
        )
        assert missing.status_code == 404


def test_catalog_seed_is_published_and_idempotent(tmp_path: Path) -> None:
    repository = InMemoryAuthoredVNextMasterRepository()
    service = AuthoredVNextMasterService(repository)
    asset_store = OwnerMasterAssetStore(tmp_path / "owner-a")
    png = LOGO_PATH.read_bytes()
    svg = tmp_path / "Ibiz_logo.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg"><image href="data:image/png;base64,'
        + base64.b64encode(png).decode("ascii")
        + '"/></svg>',
        encoding="utf-8",
    )

    first = seed_catalog(service, asset_store, ibiz_logo_svg=svg)
    assert [item["action"] for item in first] == ["published"] * 4
    second = seed_catalog(service, asset_store, ibiz_logo_svg=svg)
    assert [item["action"] for item in second] == ["unchanged"] * 4
    assert [item["revision"] for item in first] == [2, 2, 2, 2]
    assert [item["revision"] for item in second] == [2, 2, 2, 2]

    catalog = service.list_masters(cursor=None, limit=20)
    assert {item.master_id for item in catalog.items} == {
        IBIZ_MASTER_ID,
        *SAMPLE_MASTER_IDS,
    }
    ibiz = service.get_published_revision(IBIZ_MASTER_ID)
    assert ibiz.contract.name == "IBIZ 제안서 블루"
    assert ibiz.contract.layouts.cover.name == "표지"
    assert ibiz.contract.layouts.section.name == "섹션"
    assert ibiz.contract.layouts.standard.name == "본문"
    assert ibiz.contract.layouts.long_title.name == "긴 제목"
    assert ibiz.contract.furniture.page_number.bounds.x == 610
    assert ibiz.contract.furniture.logo.bounds.x == 1134
    logo = ibiz.contract.brand_assets.logo
    assert logo is not None
    assert asset_store.open_verified(logo.asset_id, logo.digest) == png
