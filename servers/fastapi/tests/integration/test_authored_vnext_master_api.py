"""Focused HTTP vertical slice for Master Setup through body-only CAS publish."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v1.ppt.endpoints.authored_vnext.master.router import (
    AUTHORED_VNEXT_MASTER_ROUTER,
    get_authored_vnext_master_service,
)
from services.authored_vnext.master.repository import (
    InMemoryAuthoredVNextMasterRepository,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from tests.support.authored_vnext_master_fixture import (
    ZERO_DIGEST,
    long_title_slide_payload,
    master_contract_payload,
)


PREFIX = "/api/v1/ppt/authored-vnext/master"


def _client(monkeypatch):
    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "true")
    repository = InMemoryAuthoredVNextMasterRepository()
    service = AuthoredVNextMasterService(repository)
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_MASTER_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_authored_vnext_master_service] = lambda: service
    return TestClient(app), repository


def test_flag_off_returns_stable_404_before_store_dependency(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AUTHORED_VNEXT_MASTER_ENABLED", "false")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "must-not-exist"))
    calls = 0

    def forbidden_service():
        nonlocal calls
        calls += 1
        raise AssertionError("disabled route must not resolve its repository")

    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_MASTER_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_authored_vnext_master_service] = forbidden_service
    with TestClient(app) as client:
        response = client.post(f"{PREFIX}/drafts", json={})

    assert response.status_code == 404
    assert response.json() == {"detail": "authored_vnext_master_disabled"}
    assert calls == 0
    assert not (tmp_path / "must-not-exist").exists()


def test_draft_validate_publish_preview_deck_and_regenerate_conflict(monkeypatch) -> None:
    client, repository = _client(monkeypatch)
    create_response = client.post(
        f"{PREFIX}/drafts",
        json={
            "contract": master_contract_payload(),
            "visualDirection": {
                "tone": ["editorial", "calm"],
                "imagery": "documentary",
            },
        },
    )
    assert create_response.status_code == 201, create_response.text
    draft = create_response.json()
    assert draft["revision"] == 1
    assert draft["status"] == "draft"
    assert draft["digest"] != ZERO_DIGEST
    assert draft["contract"]["fontManifest"]["digest"] != ZERO_DIGEST

    stale_digest = client.post(
        f"{PREFIX}/drafts/{draft['masterId']}/validate",
        json={"expectedRevision": 1, "expectedDigest": ZERO_DIGEST},
    )
    assert stale_digest.status_code == 409
    assert stale_digest.json()["detail"]["code"] == (
        "authored_vnext_master_digest_conflict"
    )

    cas = {"expectedRevision": draft["revision"], "expectedDigest": draft["digest"]}
    validated = client.post(
        f"{PREFIX}/drafts/{draft['masterId']}/validate",
        json=cas,
    )
    assert validated.status_code == 200
    assert validated.json() == {
        "valid": True,
        "errors": [],
        "furnitureBodyDuplicates": 0,
        "writableOverlaps": 0,
        "titleFitFailures": 0,
    }

    publish_response = client.post(
        f"{PREFIX}/drafts/{draft['masterId']}/publish",
        json=cas,
    )
    assert publish_response.status_code == 200
    published = publish_response.json()
    assert published["revision"] == 2
    assert published["status"] == "published"

    stale_revision = client.post(
        f"{PREFIX}/drafts/{draft['masterId']}/publish",
        json=cas,
    )
    assert stale_revision.status_code == 409
    assert stale_revision.json()["detail"]["code"] == (
        "authored_vnext_master_revision_conflict"
    )

    slide = long_title_slide_payload()
    preview_request = {
        "masterId": published["masterId"],
        "masterRevision": published["revision"],
        "masterDigest": published["digest"],
        "slide": slide,
    }
    preview_response = client.post(f"{PREFIX}/preview", json=preview_request)
    assert preview_response.status_code == 200, preview_response.text
    preview = preview_response.json()
    assert preview["slide"]["titleFit"]["clipped"] is False
    assert preview["slide"]["titleFit"]["margins"] == {
        "leftPt": 7.2,
        "rightPt": 7.2,
        "topPt": 3.6,
        "bottomPt": 3.6,
    }
    assert preview["html"].count("data-master-furniture") == 5
    assert preview["exportPlan"]["compilerPort"] == "OoxmlMasterCompilerPort"
    assert preview["exportPlan"]["compilerVersion"] == "ooxml-master-port/v1"
    assert repository.candidates == {}

    repeated_preview = client.post(f"{PREFIX}/preview", json=preview_request)
    assert repeated_preview.status_code == 200
    assert repeated_preview.json() == preview

    deck_response = client.post(
        f"{PREFIX}/decks",
        json={
            "deckId": "deck:vertical-slice",
            "masterId": published["masterId"],
            "masterRevision": published["revision"],
            "masterDigest": published["digest"],
            "visualDirection": {"tone": ["editorial"]},
            "slides": [slide],
        },
    )
    assert deck_response.status_code == 201, deck_response.text
    deck = deck_response.json()
    original_title = deck["slides"][0]["title"]
    regenerate_request = {
        "scope": "bodyOnly",
        "expectedDeckRevision": deck["revision"],
        "expectedDeckDigest": deck["digest"],
        "expectedMasterDigest": deck["masterDigest"],
        "expectedSlideRevision": deck["slides"][0]["revision"],
        "layoutId": deck["slides"][0]["layoutId"],
        "body": "새 body candidate만 검증하고 CAS publish합니다.",
    }
    stale_deck_digest = client.post(
        f"{PREFIX}/decks/{deck['deckId']}/slides/slide:long-title/regenerate-body",
        json={**regenerate_request, "expectedDeckDigest": ZERO_DIGEST},
    )
    assert stale_deck_digest.status_code == 409
    assert stale_deck_digest.json()["detail"]["code"] == (
        "authored_vnext_deck_digest_conflict"
    )
    regenerated_response = client.post(
        f"{PREFIX}/decks/{deck['deckId']}/slides/slide:long-title/regenerate-body",
        json=regenerate_request,
    )
    assert regenerated_response.status_code == 200, regenerated_response.text
    regenerated = regenerated_response.json()
    assert regenerated["validation"]["valid"] is True
    assert regenerated["deck"]["revision"] == 2
    assert regenerated["deck"]["masterRevision"] == deck["masterRevision"]
    assert regenerated["deck"]["masterDigest"] == deck["masterDigest"]
    assert regenerated["deck"]["slides"][0]["layoutId"] == "layout:long-title"
    assert regenerated["deck"]["slides"][0]["title"] == original_title
    assert regenerated["deck"]["slides"][0]["body"] == regenerate_request["body"]
    assert len(repository.candidates) == 1
    immutable_parent = repository.get_deck(deck["deckId"], revision=1)
    assert immutable_parent.digest == deck["digest"]
    assert immutable_parent.slides[0].body == slide["body"]

    conflict = client.post(
        f"{PREFIX}/decks/{deck['deckId']}/slides/slide:long-title/regenerate-body",
        json=regenerate_request,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == (
        "authored_vnext_deck_revision_conflict"
    )
