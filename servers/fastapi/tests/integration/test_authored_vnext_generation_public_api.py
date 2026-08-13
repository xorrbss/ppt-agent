"""Public SQL-backed authored-vNext lifecycle acceptance test."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.v1.ppt.endpoints.authored_vnext.generation.router import (
    AUTHORED_VNEXT_GENERATION_ROUTER,
    get_generation_repository,
)
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import CreateDraftRequest, MasterCasRequest
from models.sql.authored_vnext_generation_operation import AuthoredVNextGenerationOperation
from models.sql.user import User
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.integration.sql_repository import AuthoredVNextSqlRepository
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.master.service import AuthoredVNextMasterService
from tests.support.authored_vnext_master_fixture import master_contract_payload


PREFIX = "/api/v1/ppt/authored-vnext/generation"
FIXTURE = (
    Path(__file__).resolve().parents[4]
    / "docs/experiments/authored-vnext/06-master-aware-e2e/fixture-10-slides.json"
)


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTHORED_VNEXT_GENERATION_ENABLED", "true")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'public-flow.db'}",
        connect_args={"check_same_thread": False},
    )
    User.__table__.create(engine)
    owner_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(User(id=owner_id, username="public-flow", hashed_password="test"))
        session.commit()
    base = AuthoredVNextSqlRepository(
        engine,
        owner_scope=str(owner_id),
        tenant_id="default",
        owner_id=owner_id,
    )
    base.create_schema(engine)
    AuthoredVNextGenerationOperation.__table__.create(engine)
    master_service = AuthoredVNextMasterService(base)
    draft = master_service.create_draft(
        CreateDraftRequest(
            contract=DeckMasterContract.model_validate(master_contract_payload())
        )
    )
    master = master_service.publish(
        draft.master_id,
        MasterCasRequest(
            expected_revision=draft.revision,
            expected_digest=draft.digest,
        ),
    )
    repository = SqlGenerationRepository(base)
    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_GENERATION_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_generation_repository] = lambda: repository
    return TestClient(app), repository, master


def _master_pin(master) -> dict[str, object]:
    return {
        "masterId": master.master_id,
        "masterRevision": master.revision,
        "masterDigest": master.digest,
        "layoutRegistryDigest": canonical_digest(
            master.contract.layouts.model_dump(mode="json", by_alias=True)
        ),
        "fontManifestDigest": master.contract.font_manifest.digest,
    }


def _generate_body(master) -> dict[str, object]:
    return {
        "idempotencyKey": "public-generate-key",
        "command": {
            "operationId": "operation:public-generate",
            "deckId": "deck:public-flow",
            "master": _master_pin(master),
            "outline": json.loads(FIXTURE.read_text(encoding="utf-8")),
            "qualityPolicyVersion": "authored-vnext-quality/v1",
            "publicationFence": {
                "expectedDeckRevision": 0,
                "expectedActiveRevisionDigest": None,
            },
        },
    }


def test_public_generate_resume_detached_regenerate_publish_and_rollback(
    monkeypatch,
    tmp_path,
) -> None:
    client, repository, master = _client(monkeypatch, tmp_path)
    generated_response = client.post(f"{PREFIX}/decks", json=_generate_body(master))
    assert generated_response.status_code == 200, generated_response.text
    generated = generated_response.json()
    assert generated["checkpoint"]["stage"] == "published"
    assert len(generated["deck"]["slides"]) == 10
    assert generated["exportArtifactDigest"].startswith("sha256:")

    replay = client.post(f"{PREFIX}/decks", json=_generate_body(master))
    assert replay.status_code == 200
    assert replay.json() == generated

    checkpoint = generated["checkpoint"]
    resumed = client.post(
        f"{PREFIX}/resume",
        json={
            "idempotencyKey": "public-resume-key",
            "command": {
                "operationId": checkpoint["operationId"],
                "expectedCheckpointDigest": checkpoint["checkpointDigest"],
                "expectedMasterDigest": master.digest,
                "checkpoint": checkpoint,
            },
        },
    )
    assert resumed.status_code == 200
    assert resumed.json() == generated

    target = generated["deck"]["slides"][3]["slide"]
    candidate_request = {
        "idempotencyKey": "public-regenerate-prepare-key",
        "command": {
            "operationId": "operation:public-regenerate-prepare",
            "deckId": generated["deck"]["deckId"],
            "slideId": target["slideId"],
            "expectedSlideRevision": target["revision"],
            "expectedSlideSpecDigest": target["specDigest"],
            "master": _master_pin(master),
            "sourceDigest": generated["deck"]["sourceDigest"],
            "body": "승인 전에는 head를 바꾸지 않는 분리 후보입니다.",
            "publicationFence": {
                "expectedDeckRevision": generated["deck"]["revision"],
                "expectedActiveRevisionDigest": generated["deck"]["digest"],
            },
        },
    }
    candidate_response = client.post(
        f"{PREFIX}/decks/deck:public-flow/slides/{target['slideId']}/regeneration-candidates",
        json=candidate_request,
    )
    assert candidate_response.status_code == 200, candidate_response.text
    candidate = candidate_response.json()["candidate"]
    assert candidate["lifecycle"] == "detached"
    assert repository.get_deck("deck:public-flow").digest == generated["deck"]["digest"]

    published_response = client.post(
        f"{PREFIX}/decks/deck:public-flow/regeneration-candidates/{candidate['candidateDigest']}/publish",
        json={
            "idempotencyKey": "public-regenerate-publish-key",
            "operationId": "operation:public-regenerate-publish",
        },
    )
    assert published_response.status_code == 200, published_response.text
    published = published_response.json()
    assert published["deck"]["revision"] == 2
    assert published["deck"]["slides"][3]["slide"]["body"] == candidate_request["command"]["body"]

    stale_publish = client.post(
        f"{PREFIX}/decks/deck:public-flow/regeneration-candidates/{candidate['candidateDigest']}/publish",
        json={
            "idempotencyKey": "different-publish-key",
            "operationId": "operation:public-stale-publish",
        },
    )
    assert stale_publish.status_code == 409

    rollback = client.post(
        f"{PREFIX}/decks/deck:public-flow/rollback",
        json={
            "idempotencyKey": "public-rollback-key",
            "command": {
                "operationId": "operation:public-rollback",
                "deckId": "deck:public-flow",
                "targetRevision": 1,
                "targetRevisionDigest": generated["deck"]["digest"],
                "masterDigest": master.digest,
                "publicationFence": {
                    "expectedDeckRevision": 2,
                    "expectedActiveRevisionDigest": published["deck"]["digest"],
                },
                "reason": "editorial rejection",
            },
        },
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["deck"]["revision"] == 3
    assert rollback.json()["deck"]["slides"][3]["slide"]["body"] == target["body"]

    restored = rollback.json()["deck"]
    original_order = [item["slide"]["slideId"] for item in restored["slides"]]
    reordered = client.post(
        f"{PREFIX}/decks/deck:public-flow/reorder",
        json={
            "idempotencyKey": "public-reorder-key",
            "command": {
                "operationId": "operation:public-reorder",
                "deckId": "deck:public-flow",
                "orderedSlideIds": list(reversed(original_order)),
                "masterDigest": master.digest,
                "publicationFence": {
                    "expectedDeckRevision": restored["revision"],
                    "expectedActiveRevisionDigest": restored["digest"],
                },
            },
        },
    )
    assert reordered.status_code == 200, reordered.text
    assert [
        item["slide"]["slideId"] for item in reordered.json()["deck"]["slides"]
    ] == list(reversed(original_order))


def test_generation_flag_off_short_circuits_dependency(monkeypatch) -> None:
    monkeypatch.delenv("AUTHORED_VNEXT_GENERATION_ENABLED", raising=False)
    calls = 0

    def forbidden_repository():
        nonlocal calls
        calls += 1
        raise AssertionError("disabled route resolved repository")

    app = FastAPI()
    app.include_router(AUTHORED_VNEXT_GENERATION_ROUTER, prefix="/api/v1/ppt")
    app.dependency_overrides[get_generation_repository] = forbidden_repository
    with TestClient(app) as client:
        response = client.post(f"{PREFIX}/decks", json={})
    assert response.status_code == 404
    assert calls == 0
