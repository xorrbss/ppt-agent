from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from models.authored_vnext.semantic.editable_mvp_completion import (
    EditableMvpCompletionSealImportRequestV1,
)
from models.sql.authored_editable_hybrid_mvp_completion import (
    AuthoredEditableHybridMvpCompletion,
)
from models.sql.authored_vnext_repository import (
    AuthoredVNextDeckRevision,
    AuthoredVNextEntityHead,
    AuthoredVNextExportJob,
)
from services.authored_vnext.generation.sql_repository import SqlGenerationRepository
from services.authored_vnext.integration.sql_repository import AuthoredVNextSqlRepository
from services.authored_vnext.semantic.editable_mvp_completion import (
    GATE1E_MANIFEST_DIGEST,
    PROFILE_DIGEST,
    PROFILE_ID,
    REQUIRED_ARTIFACT_IDS,
    EditableMvpCompletionError,
    _digest,
    _sha256_bytes,
    _without,
    match_current_editable_mvp_completion,
    seal_import_editable_mvp_completion,
)


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _fixture(repository: SqlGenerationRepository, *, now: datetime) -> tuple[str, str, str, uuid.UUID]:
    deck_id = "deck:semantic-11111111111141118111111111111111"
    deck_digest = "sha256:" + "a" * 64
    scene_digest = "sha256:" + "b" * 64
    export_attempt_id = uuid.uuid4()
    base = repository._base
    with repository._sessions() as session:
        session.add(
            AuthoredVNextEntityHead(
                owner_id=base.owner_id,
                owner_scope=base.owner_scope,
                tenant_id=base.tenant_id,
                entity_kind="deck",
                entity_id=deck_id,
                revision=1,
                digest=deck_digest,
            )
        )
        session.add(
            AuthoredVNextDeckRevision(
                owner_id=base.owner_id,
                owner_scope=base.owner_scope,
                tenant_id=base.tenant_id,
                deck_id=deck_id,
                revision=1,
                digest=deck_digest,
                master_id="master:test",
                master_revision=1,
                master_digest="sha256:" + "c" * 64,
                payload={
                    "scene": {
                        "sceneDigest": scene_digest,
                        "hybridProfileId": PROFILE_ID,
                    }
                },
            )
        )
        session.add(
            AuthoredVNextExportJob(
                id=export_attempt_id,
                owner_id=base.owner_id,
                owner_scope=base.owner_scope,
                tenant_id=base.tenant_id,
                deck_id=deck_id,
                deck_revision=1,
                deck_digest=deck_digest,
                master_id="master:test",
                master_revision=1,
                master_digest="sha256:" + "c" * 64,
                spec_digest="sha256:" + "d" * 64,
                asset_manifest_digest="sha256:" + "e" * 64,
                font_manifest_digest="sha256:" + "f" * 64,
                request_payload={"exportMode": "hybrid"},
                request_hash="1" * 64,
                idempotency_key_hash="2" * 64,
                state="succeeded",
                completed_at=now,
            )
        )
        session.commit()
    return deck_id, deck_digest, scene_digest, export_attempt_id


def _sealed_bytes(
    *,
    deck_id: str,
    deck_digest: str,
    scene_digest: str,
    export_attempt_id: uuid.UUID,
    now: datetime,
    private_key: Ed25519PrivateKey,
) -> tuple[bytes, bytes, bytes]:
    presentation_id = str(uuid.UUID(hex=deck_id.removeprefix("deck:semantic-")))
    inventory = [
        {
            "artifactId": artifact_id,
            "versionId": f"version-{index}",
            "sha256": "sha256:" + f"{index:064x}",
            "byteLength": index,
            "mediaType": "application/json",
        }
        for index, artifact_id in enumerate(sorted(REQUIRED_ARTIFACT_IDS), start=1)
    ]
    inventory_by_id = {item["artifactId"]: item for item in inventory}
    attempt = {
        "presentationId": presentation_id,
        "presentationRevision": 1,
        "generationAttemptId": "generation-attempt-test",
        "hybridExportAttemptId": str(export_attempt_id),
        "sceneDigest": scene_digest,
        "activeManifestDigest": deck_digest,
        "gateMinus1eResultSetDigest": "sha256:" + "3" * 64,
        "gate0eResultSetDigest": "sha256:" + "4" * 64,
        "sourceHeadCommit": "5" * 40,
        "sourceCensusDigest": "sha256:" + "6" * 64,
        "sourceDirtyFileCount": 3,
        "expectedQids": [f"qid-{index}" for index in range(1, 6)],
    }
    result = {
        "schemaVersion": "authored-editable-hybrid-gate1e-result-set/v1",
        "profileId": PROFILE_ID,
        "authorityDomain": "actual_product",
        "manifestDigest": GATE1E_MANIFEST_DIGEST,
        "profileDigest": PROFILE_DIGEST,
        "attempt": attempt,
        "suiteStatus": "accepted",
        "executedCount": 35,
        "skippedCount": 0,
        "orderedResults": [
            {
                "ordinal": index,
                "artifactId": artifact_id,
                "state": "accepted",
                "artifactDigest": inventory_by_id[artifact_id]["sha256"],
            }
            for index, artifact_id in enumerate(REQUIRED_ARTIFACT_IDS, start=1)
        ],
        "artifactInventory": inventory,
        "artifactInventoryDigest": _digest(inventory),
        "semanticValidationReceiptRef": None,
        "resultCoreDigest": "",
        "resultSetDigest": "",
    }
    result["resultCoreDigest"] = _digest(
        _without(
            result,
            "semanticValidationReceiptRef",
            "resultCoreDigest",
            "resultSetDigest",
        )
    )
    public_der = private_key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    trust = {
        "schemaVersion": "authored-editable-hybrid-verifier-trust/v1",
        "issuerId": "external-editable-validator",
        "signerKeyId": "external-editable-key-1",
        "keyPurpose": "editable-hybrid-mvp-gate1e",
        "algorithm": "Ed25519",
        "authorityScope": "local-shadow",
        "promotionAuthorized": False,
        "deploymentAuthorized": False,
        "revoked": False,
        "notBefore": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "publicKeySpkiBase64": base64.b64encode(public_der).decode(),
    }
    receipt = {
        "schemaVersion": "authored-editable-hybrid-validation-receipt/v1",
        "issuerId": trust["issuerId"],
        "signerKeyId": trust["signerKeyId"],
        "authorityScope": trust["authorityScope"],
        "promotionAuthorized": False,
        "deploymentAuthorized": False,
        "nonce": "nonce-editable-mvp-0001",
        "issuedAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),
        "subjectManifestDigest": result["manifestDigest"],
        "subjectProfileDigest": result["profileDigest"],
        "subjectResultCoreDigest": result["resultCoreDigest"],
        "subjectArtifactInventoryDigest": result["artifactInventoryDigest"],
        "presentationId": presentation_id,
        "presentationRevision": 1,
        "generationAttemptId": attempt["generationAttemptId"],
        "hybridExportAttemptId": attempt["hybridExportAttemptId"],
    }
    receipt["receiptDigest"] = _digest(receipt)
    receipt["signatureBase64"] = base64.b64encode(
        private_key.sign(receipt["receiptDigest"].encode("ascii"))
    ).decode()
    receipt_bytes = _json_bytes(receipt)
    result["semanticValidationReceiptRef"] = {
        "artifactId": "gate1e-semantic-validation-receipt",
        "versionId": "receipt-version-1",
        "sha256": _sha256_bytes(receipt_bytes),
        "byteLength": len(receipt_bytes),
        "mediaType": "application/json",
        "receiptDigest": receipt["receiptDigest"],
        "issuerId": receipt["issuerId"],
        "signerKeyId": receipt["signerKeyId"],
        "authorityScope": receipt["authorityScope"],
        "promotionAuthorized": False,
        "deploymentAuthorized": False,
    }
    result["resultSetDigest"] = _digest(_without(result, "resultSetDigest"))
    return _json_bytes(result), receipt_bytes, _json_bytes(trust)


def test_editable_mvp_completion_is_single_cas_idempotent_and_current(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    for table in (
        AuthoredVNextDeckRevision.__table__,
        AuthoredVNextEntityHead.__table__,
        AuthoredVNextExportJob.__table__,
        AuthoredEditableHybridMvpCompletion.__table__,
    ):
        table.create(engine)
    owner_id = uuid.uuid4()
    repository = SqlGenerationRepository(
        AuthoredVNextSqlRepository(
            engine,
            owner_scope=str(owner_id),
            tenant_id="default",
            owner_id=owner_id,
        )
    )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    deck_id, deck_digest, scene_digest, export_attempt_id = _fixture(repository, now=now)
    result_bytes, receipt_bytes, trust_bytes = _sealed_bytes(
        deck_id=deck_id,
        deck_digest=deck_digest,
        scene_digest=scene_digest,
        export_attempt_id=export_attempt_id,
        now=now,
        private_key=Ed25519PrivateKey.generate(),
    )
    monkeypatch.setenv("AUTHORED_EDITABLE_MVP_TRUST_SHA256", _sha256_bytes(trust_bytes))
    request = EditableMvpCompletionSealImportRequestV1(
        expected_deck_revision=1,
        expected_deck_digest=deck_digest,
        expected_scene_digest=scene_digest,
        expected_hybrid_export_attempt_id=str(export_attempt_id),
        sealed_result_set_base64=base64.b64encode(result_bytes).decode(),
        validation_receipt_base64=base64.b64encode(receipt_bytes).decode(),
        verifier_trust_base64=base64.b64encode(trust_bytes).decode(),
    )
    first = seal_import_editable_mvp_completion(
        repository, deck_id=deck_id, request=request, now=now
    )
    assert first.idempotent is False
    assert first.native_editable_ready is True
    assert first.promotion_ready is False
    assert first.deployment_authorized is False
    retry = seal_import_editable_mvp_completion(
        repository, deck_id=deck_id, request=request, now=now
    )
    assert retry.idempotent is True
    assert retry.completion_id == first.completion_id

    current, reason = match_current_editable_mvp_completion(
        repository,
        deck=SimpleNamespace(
            deck_id=deck_id,
            revision=1,
            digest=deck_digest,
            scene=SimpleNamespace(
                scene_digest=scene_digest,
                hybrid_profile_id=PROFILE_ID,
            ),
        ),
        now=now,
    )
    assert current is not None
    assert reason == "editable_hybrid_mvp_completion_current"

    different_bytes = result_bytes + b"\n"
    with pytest.raises(
        EditableMvpCompletionError, match="editable_mvp_completion_already_sealed"
    ):
        seal_import_editable_mvp_completion(
            repository,
            deck_id=deck_id,
            request=request.model_copy(
                update={
                    "sealed_result_set_base64": base64.b64encode(different_bytes).decode()
                }
            ),
            now=now,
        )

    with engine.begin() as connection:
        connection.execute(
            update(AuthoredVNextDeckRevision)
            .where(AuthoredVNextDeckRevision.deck_id == deck_id)
            .values(
                payload={
                    "scene": {
                        "sceneDigest": "sha256:" + "9" * 64,
                        "hybridProfileId": PROFILE_ID,
                    }
                }
            )
        )
    with pytest.raises(
        EditableMvpCompletionError, match="editable_mvp_stale_scene_cas"
    ):
        seal_import_editable_mvp_completion(
            repository, deck_id=deck_id, request=request, now=now
        )

    with Session(engine) as session:
        stored = session.get(AuthoredEditableHybridMvpCompletion, first.completion_id)
        assert stored is not None
        stored.cas_version = 2
        with pytest.raises(ValueError, match="append_only"):
            session.commit()
