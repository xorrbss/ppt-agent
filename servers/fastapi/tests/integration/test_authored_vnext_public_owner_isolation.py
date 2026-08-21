"""Public authored generation/export routes keep owner data undiscoverable."""

from __future__ import annotations

import json
from pathlib import Path
import uuid

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from api.v1.ppt.endpoints.authored_vnext.export import router as export_router
from api.v1.ppt.endpoints.authored_vnext.generation import router as generation_router
from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import CreateDraftRequest, MasterCasRequest
from models.sql.authored_vnext_generation_operation import (
    AuthoredVNextGenerationOperation,
)
from models.sql.user import User
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.integration.artifact_store import BufferedArtifactStore
from services.authored_vnext.integration.export_service import AuthoredVNextExportService
from services.authored_vnext.integration.sql_repository import AuthoredVNextSqlRepository
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.master.service import AuthoredVNextMasterService
from tests.integration.test_authored_vnext_export_integration import (
    DeterministicWorkerTransport,
    _published_sql_deck,
    _worker_success,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload
from utils.owner_context import reset_current_owner_id, set_current_owner_id


OWNER_A = uuid.uuid5(uuid.NAMESPACE_URL, "public-authored-owner-a")
OWNER_B = uuid.uuid5(uuid.NAMESPACE_URL, "public-authored-owner-b")
OWNER_HEADER = "x-test-authored-owner"
GENERATION_PREFIX = "/api/v1/ppt/authored-vnext/generation"
EXPORT_PREFIX = "/api/v1/ppt/authored-vnext/export"
OUTLINE = (
    Path(__file__).resolve().parents[4]
    / "docs/experiments/authored-vnext/06-master-aware-e2e/fixture-10-slides.json"
)


def _seed_owners(engine) -> None:
    User.__table__.create(engine, checkfirst=True)
    with Session(engine) as session:
        session.add_all(
            [
                User(id=OWNER_A, username="public-authored-a", hashed_password="test"),
                User(id=OWNER_B, username="public-authored-b", hashed_password="test"),
            ]
        )
        session.commit()


def _owner_app(router) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def bind_test_owner(request: Request, call_next):
        raw_owner = request.headers.get(OWNER_HEADER)
        owner_id = uuid.UUID(raw_owner) if raw_owner else None
        token = set_current_owner_id(owner_id)
        try:
            return await call_next(request)
        finally:
            reset_current_owner_id(token)

    app.include_router(router, prefix="/api/v1/ppt")
    return app


def _headers(owner_id: uuid.UUID) -> dict[str, str]:
    return {OWNER_HEADER: str(owner_id)}


def _generation_request(master) -> dict[str, object]:
    pin = {
        "masterId": master.master_id,
        "masterRevision": master.revision,
        "masterDigest": master.digest,
        "layoutRegistryDigest": canonical_digest(
            master.contract.layouts.model_dump(mode="json", by_alias=True)
        ),
        "fontManifestDigest": master.contract.font_manifest.digest,
    }
    return {
        "idempotencyKey": "owner-a-generate",
        "command": {
            "operationId": "operation:owner-a-generate",
            "deckId": "deck:owner-a-only",
            "master": pin,
            "outline": json.loads(OUTLINE.read_text(encoding="utf-8")),
            "qualityPolicyVersion": "authored-vnext-quality/v1",
            "publicationFence": {
                "expectedDeckRevision": 0,
                "expectedActiveRevisionDigest": None,
            },
        },
    }


def test_public_generation_http_hides_owner_a_deck_and_operation_from_owner_b(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AUTHORED_VNEXT_GENERATION_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_VNEXT_INTERNAL_COMPATIBILITY_ENABLED", "true")
    monkeypatch.setenv("AUTHORED_VNEXT_NEW_GENERATION_ENABLED", "true")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'generation-owner-isolation.db'}",
        connect_args={"check_same_thread": False},
    )
    try:
        _seed_owners(engine)
        AuthoredVNextSqlRepository.create_schema(engine)
        AuthoredVNextGenerationOperation.__table__.create(engine, checkfirst=True)
        bases = {
            str(owner): AuthoredVNextSqlRepository(
                engine,
                owner_scope=str(owner),
                tenant_id="default",
                owner_id=owner,
            )
            for owner in (OWNER_A, OWNER_B)
        }
        services = {owner: SqlGenerationRepository(base) for owner, base in bases.items()}
        master_service = AuthoredVNextMasterService(bases[str(OWNER_A)])
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
        monkeypatch.setattr(
            generation_router,
            "_repository_for_owner",
            lambda owner_id: services[owner_id],
        )
        app = _owner_app(generation_router.AUTHORED_VNEXT_GENERATION_ROUTER)

        with TestClient(app) as client:
            created = client.post(
                f"{GENERATION_PREFIX}/decks",
                json=_generation_request(master),
                headers=_headers(OWNER_A),
            )
            assert created.status_code == 200, created.text
            assert client.get(
                f"{GENERATION_PREFIX}/decks/deck:owner-a-only",
                headers=_headers(OWNER_A),
            ).status_code == 200

            hidden_deck = client.get(
                f"{GENERATION_PREFIX}/decks/deck:owner-a-only",
                headers=_headers(OWNER_B),
            )
            hidden_operation = client.get(
                f"{GENERATION_PREFIX}/operations/operation:owner-a-generate",
                headers=_headers(OWNER_B),
            )

        assert hidden_deck.status_code == 404
        assert hidden_operation.status_code == 404
        assert hidden_deck.json()["detail"]["code"] == (
            "authored_vnext_generation_not_found"
        )
        assert hidden_operation.json()["detail"]["code"] == (
            "authored_vnext_generation_not_found"
        )
    finally:
        engine.dispose()


def test_public_export_http_hides_owner_a_job_and_artifact_from_owner_b(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AUTHORED_VNEXT_EXPORT_ENABLED", "true")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'export-owner-isolation.db'}",
        connect_args={"check_same_thread": False},
    )
    try:
        _seed_owners(engine)
        AuthoredVNextSqlRepository.create_schema(engine)
        repositories = {
            str(owner): AuthoredVNextSqlRepository(
                engine,
                owner_scope=str(owner),
                tenant_id="tenant-a",
                owner_id=owner,
            )
            for owner in (OWNER_A, OWNER_B)
        }
        _master, deck, digests = _published_sql_deck(
            repositories[str(OWNER_A)], "deck:owner-a-export"
        )
        artifact = b"PK\x03\x04owner-a-export"
        workers = {
            str(OWNER_A): DeterministicWorkerTransport(
                lambda payload: _worker_success(payload, artifact)
            ),
            str(OWNER_B): DeterministicWorkerTransport(),
        }
        services = {
            owner: AuthoredVNextExportService(
                repositories[owner],
                workers[owner],
                BufferedArtifactStore(tmp_path / "artifacts" / owner),
            )
            for owner in repositories
        }
        monkeypatch.setattr(
            export_router,
            "_service_for_scope",
            lambda owner_scope, _owner_id: services[owner_scope],
        )
        app = _owner_app(export_router.AUTHORED_VNEXT_EXPORT_ROUTER)
        request = {
            "deckId": deck.deck_id,
            "expectedDeckRevision": deck.revision,
            "expectedDeckDigest": deck.digest,
            "expectedMasterRevision": deck.master_revision,
            "expectedMasterDigest": deck.master_digest,
            "expectedActiveArtifactRevision": 0,
            "expectedActiveArtifactDigest": None,
            "expectedSourcePackageDigest": None,
            "expectedAssetDigests": [],
            "expectedFontDigest": _master.contract.font_manifest.digest,
            "idempotencyKey": "owner-a-export",
            "timeoutMs": 1_000,
            "maxAttempts": 3,
        }

        with TestClient(app) as client:
            created = client.post(
                f"{EXPORT_PREFIX}/jobs",
                json=request,
                headers=_headers(OWNER_A),
            )
            assert created.status_code == 202, created.text
            job_id = created.json()["jobId"]
            completed = client.post(
                f"{EXPORT_PREFIX}/jobs/{job_id}/run",
                headers=_headers(OWNER_A),
            )
            assert completed.status_code == 200, completed.text

            hidden_job = client.get(
                f"{EXPORT_PREFIX}/jobs/{job_id}", headers=_headers(OWNER_B)
            )
            hidden_artifact = client.get(
                f"{EXPORT_PREFIX}/decks/{deck.deck_id}/artifact",
                headers=_headers(OWNER_B),
            )
            hidden_create = client.post(
                f"{EXPORT_PREFIX}/jobs", json=request, headers=_headers(OWNER_B)
            )

        assert hidden_job.status_code == 404
        assert hidden_artifact.status_code == 404
        assert hidden_create.status_code == 404, hidden_create.text
        assert workers[str(OWNER_B)].calls == []
        assert len(workers[str(OWNER_A)].calls) == 1
    finally:
        engine.dispose()
