"""Gate 0 reachability contract for the production Authored release projection.

This test deliberately enters through the real semantic FastAPI router and its
database dependency. It does not replace a capability or visual-evidence
validator. The v3 projection fields are an explicit interface expectation;
until the product exposes them, the test proves the API remains fail-closed.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import uuid

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints.authored_vnext.semantic.router import (
    AUTHORED_SEMANTIC_ROUTER,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_release_evidence_v3 import (
    AuthoredComQualificationJobV3,
    AuthoredEvidenceBundleCompletionV3,
    AuthoredEvidenceGraphRootV3,
    AuthoredGate0StructuralCompletionV3,
    AuthoredGate0StructuralEventV3,
    AuthoredGate0StructuralSubjectV3,
    AuthoredGate0StructuralTraceV3,
    AuthoredReleaseArtifactV3,
    AuthoredReleaseNonceV3,
)
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredEmissionBundleRecord,
    AuthoredSemanticArtifact,
    AuthoredSemanticExportBinding,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import (
    ExportPowerPointQaEvidence,
    PresentationExportAttempt,
    PresentationExportAttemptProvenance,
    PresentationExportCoverageManifest,
)
from models.sql.slide import SlideModel
from models.authored_vnext.semantic.readiness import (
    CapabilityReceiptIssueRequestV2,
    VisualEvidenceAuthorityV2,
    VisualParityGateReportV1,
)
from models.authored_vnext.semantic.release_evidence_v3 import (
    BitbucketRequiredChecksReceiptV3,
)
from models.authored_vnext.semantic.release_authority_v3 import (
    EvidenceIssuerEnvelopeV3,
    GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3,
    GATE1_GRAPH_BASE_ARTIFACT_IDS_V3,
    GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3,
)
from models.authored_vnext.semantic.typography import (
    NativeTypographyReadbackV1,
    NativeTypographyReleaseEvidenceV1,
    NativeTypographyRunReadbackV1,
)
from services.authored_vnext.semantic.canonical import (
    bytes_digest,
    canonical_digest,
    canonical_json,
    seal_export_binding,
)
from services.authored_vnext.semantic.publication import (
    publish_authored_semantic_bundle_graph,
)
from services.authored_vnext.semantic.readiness_v3 import (
    ReleaseAuthorityTargetV3,
    release_authority_target_v3_from_environment,
)
from services.authored_vnext.semantic.readiness_validators import (
    trusted_capability_evidence_digest,
    trusted_capability_evidence_policy_id,
    trusted_capability_feature_set_digest,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    actual_product_authority_digest,
    build_bound_capability_subject_v3,
)
from services.authored_vnext.semantic import release_authority_v3 as release_authority_service
from services.authored_vnext.semantic.typography import (
    admit_native_typography,
    load_bounded_windows_cjk_registry,
    native_typography_com_reopen_digest,
)
from services.authored_vnext.semantic import readiness_validators
from models.authored_vnext.semantic.contracts import (
    ArtifactPointerV1,
    ExportAttemptDigestBindingV1,
    FontManifestV1,
    ResolvedFontV1,
    TextPayloadV1,
)
from services import database
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_owner_is_admin,
    set_current_owner_id,
    set_current_owner_is_admin,
)


_RELEASE_V3_GRAPH_AND_GATE0_TABLES = [
    AuthoredReleaseArtifactV3.__table__,
    AuthoredComQualificationJobV3.__table__,
    AuthoredEvidenceGraphRootV3.__table__,
    AuthoredEvidenceBundleCompletionV3.__table__,
    AuthoredGate0StructuralSubjectV3.__table__,
    AuthoredGate0StructuralEventV3.__table__,
    AuthoredGate0StructuralCompletionV3.__table__,
    AuthoredGate0StructuralTraceV3.__table__,
]


V3_PROJECTION_FIELDS = (
    "deckEvidenceReady",
    "serializerProfileReady",
    "nativeSubsetReady",
    "releaseCommitReady",
    "promotionReady",
)
V3_PROJECTION_PATH = "/api/v1/ppt/presentation/{presentation_id}/authored-emission/capability-projection-v3"
V3_RECEIPT_PATH = "/api/v1/ppt/authored-emission/capability-receipts-v3"
_FONT_DIR = Path(r"C:\Windows\Fonts")
_ACTUAL_CJK_FONTS_AVAILABLE = all((_FONT_DIR / name).is_file() for name in ("malgun.ttf", "batang.ttc", "consola.ttf"))


def evaluate_gate0_projection(payload: dict) -> dict[str, object]:
    """Evaluate only the stable v3 projection seam; never infer readiness."""

    release_profile = payload.get("releaseProfile")
    if not isinstance(release_profile, dict):
        return {"ready": False, "reason": "v3_release_profile_missing", "missing": list(V3_PROJECTION_FIELDS)}
    missing = [field for field in V3_PROJECTION_FIELDS if field not in release_profile]
    if missing:
        return {"ready": False, "reason": "v3_projection_fields_missing", "missing": missing}
    if not all(release_profile[field] is True for field in V3_PROJECTION_FIELDS):
        return {"ready": False, "reason": "v3_projection_not_ready", "missing": []}
    return {"ready": True, "reason": "v3_projection_ready", "missing": []}


def _configure_exact_release_environment(monkeypatch, *, font_inventory_digest: str | None = None) -> None:
    monkeypatch.setenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("AUTHORED_RELEASE_OS_BUILD", "windows-11-24h2")
    monkeypatch.setenv("AUTHORED_RELEASE_OFFICE_BUILD", "powerpoint-16.0.19029")
    for name, digit in (
        ("AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST", "1"),
        ("AUTHORED_RELEASE_BACKEND_BUILD_DIGEST", "3"),
    ):
        monkeypatch.setenv(name, "sha256:" + digit * 64)
    monkeypatch.setenv(
        "AUTHORED_RELEASE_FONT_INVENTORY_DIGEST",
        font_inventory_digest or "sha256:" + "2" * 64,
    )
    monkeypatch.setenv("AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST", _sha("source-tree-census"))
    monkeypatch.setenv("AUTHORED_RELEASE_SOURCE_HEAD_COMMIT", "a" * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST", _sha("evidence-policy"))
    monkeypatch.setenv("AUTHORED_RELEASE_FEATURE_SET_DIGEST", _sha("feature-set"))
    monkeypatch.setenv("AUTHORED_RELEASE_GATE_MINUS1_MANIFEST_DIGEST", _sha("gate-minus1-manifest"))
    monkeypatch.setenv("AUTHORED_RELEASE_GATE_MINUS1_RESULT_SET_DIGEST", _sha("gate-minus1-result-set"))
    monkeypatch.setenv(
        "AUTHORED_RELEASE_GATE_MINUS1_RECEIPT_ARTIFACT_DIGEST",
        _sha("gate-minus1-receipt-artifact"),
    )
    monkeypatch.setenv(
        "AUTHORED_RELEASE_GATE_MINUS1_VALIDATOR_CONTRACT_DIGEST",
        _sha("gate-minus1-validator-contract"),
    )
    monkeypatch.setenv("AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST", _sha("environment-attestation"))
    monkeypatch.setenv("AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST", _sha("worker-attestation"))


def _configure_actual_product_signers(monkeypatch) -> Ed25519PrivateKey:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    entries = []
    for evidence_class, purpose in (
        ("actual_product_capability", "actual-product-capability"),
        ("actual_product_ci", "actual-product-ci"),
        ("actual_product_com_qualification", "actual-product-com-qualification"),
        ("actual_product_completion", "actual-product-completion"),
    ):
        issuer_id = "gate0.com.worker" if evidence_class == "actual_product_com_qualification" else "gate0.verifier"
        entries.append(
            {
                "authorityDomain": "actual_product",
                "issuerId": issuer_id,
                "signerKeyId": f"gate0.{evidence_class}.key-1",
                "environmentId": "gate0-product",
                "keyPurpose": purpose,
                "rotationEpoch": 1,
                "algorithm": "Ed25519",
                "revoked": False,
                "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
            }
        )
    monkeypatch.setenv("AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID", "gate0-product")
    monkeypatch.setenv("AUTHORED_RELEASE_GATE1_EXECUTION_NONCE", "gate1_execution_nonce_20260823")
    monkeypatch.setenv("AUTHORED_RELEASE_SIGNER_TRUST_BUNDLE_JSON", json.dumps(entries))
    monkeypatch.setenv("AUTHORED_RELEASE_READBACK_SIGNER_KEY_ID", "gate0.readback.key-1")
    monkeypatch.setenv(
        "AUTHORED_RELEASE_READBACK_ED25519_PRIVATE_KEY_BASE64",
        base64.b64encode(
            private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
        ).decode("ascii"),
    )
    monkeypatch.setattr(
        release_authority_service,
        "READBACK_PUBLIC_KEY_DIGEST_V3",
        "sha256:" + hashlib.sha256(public_key).hexdigest(),
    )
    return private_key


def _actual_product_authority(
    private_key: Ed25519PrivateKey,
    payload: dict,
    *,
    evidence_class: str,
    nonce: str,
    now: datetime,
) -> EvidenceIssuerEnvelopeV3:
    purpose = {
        "actual_product_capability": "actual-product-capability",
        "actual_product_ci": "actual-product-ci",
        "actual_product_com_qualification": "actual-product-com-qualification",
        "actual_product_completion": "actual-product-completion",
    }[evidence_class]
    issuer_id = "gate0.com.worker" if evidence_class == "actual_product_com_qualification" else "gate0.verifier"
    envelope = EvidenceIssuerEnvelopeV3(
        authority_domain="actual_product",
        evidence_class=evidence_class,
        issuer_id=issuer_id,
        signer_key_id=f"gate0.{evidence_class}.key-1",
        environment_id="gate0-product",
        key_purpose=purpose,
        rotation_epoch=1,
        issued_at=now,
        expires_at=now
        + timedelta(
            minutes=30
            if evidence_class
            in {"actual_product_capability", "actual_product_com_qualification", "actual_product_completion"}
            else 15
        ),
        nonce=nonce,
        signature_algorithm="Ed25519",
        signature_base64=base64.b64encode(b"0" * 64).decode("ascii"),
    )
    digest = actual_product_authority_digest(payload, envelope)
    return envelope.model_copy(
        update={"signature_base64": base64.b64encode(private_key.sign(digest.encode("ascii"))).decode("ascii")}
    )


def _build_app(owner_id: uuid.UUID, maker: async_sessionmaker) -> FastAPI:
    app = FastAPI()

    async def session_override():
        async with maker() as session:
            yield session

    app.dependency_overrides[database.get_async_session] = session_override

    @app.middleware("http")
    async def owner_context(request: Request, call_next):
        owner_token = set_current_owner_id(owner_id)
        admin_token = set_current_owner_is_admin(True)
        try:
            return await call_next(request)
        finally:
            reset_current_owner_is_admin(admin_token)
            reset_current_owner_id(owner_token)

    app.include_router(AUTHORED_SEMANTIC_ROUTER, prefix="/api/v1/ppt")
    return app


async def _seed_legacy_presentation(maker: async_sessionmaker, presentation_id: uuid.UUID, owner_id: uuid.UUID) -> None:
    async with maker() as session:
        session.add(
            PresentationModel(
                id=presentation_id,
                owner_id=owner_id,
                content="Gate 0 reachability fixture",
                n_slides=1,
                language="ko",
                title="Gate 0",
                mode="authored",
                revision=1,
            )
        )
        await session.commit()


def _sha(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


async def _seal_actual_product_completion_chain(
    client: httpx.AsyncClient,
    *,
    private_key: Ed25519PrivateKey,
    presentation_id: uuid.UUID,
    presentation_revision: int,
    export_attempt_id: uuid.UUID,
    manifest_digest: str,
    release_profile_digest: str,
    required_capability_ids: list[str],
    receipt_digests: list[str],
    issued_at: datetime,
) -> None:
    """Build the acyclic 67 + graph + phase + completion product chain."""

    def verify_readback(payload: dict) -> None:
        unsigned = dict(payload)
        digest = unsigned.pop("serverReadbackDigest")
        unsigned.pop("readbackSignerKeyId")
        signature = base64.b64decode(unsigned.pop("readbackSignatureBase64"), validate=True)
        assert canonical_digest(unsigned) == digest
        private_key.public_key().verify(signature, digest.encode("ascii"))

    async def publish_bound(artifact_id: str, content: bytes) -> dict:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        created = await client.post(
            "/api/v1/ppt/authored-emission/release-artifacts-v3",
            json={
                "schemaVersion": "authored-durable-artifact-put/v3",
                "exportAttemptId": str(export_attempt_id),
                "artifactId": artifact_id,
                "sha256": digest,
                "mediaType": "application/json",
                "encryptionKeyRef": "local-test/evidence-key-v3",
                "storageGeneration": 1,
                "contentBase64": base64.b64encode(content).decode("ascii"),
            },
        )
        assert created.status_code == 201, f"{artifact_id}: {created.text}"
        durable = created.json()
        bound = await client.post(
            f"/api/v1/ppt/authored-emission/release-artifacts-v3/{artifact_id}/bind",
            json={
                "schemaVersion": "authored-durable-artifact-bind/v3",
                "exportAttemptId": str(export_attempt_id),
                "expectedVersionId": durable["versionId"],
                "expectedSha256": digest,
            },
        )
        assert bound.status_code == 200, f"{artifact_id}: {bound.text}"
        return bound.json()

    inventory = []
    for artifact_id in GATE1_GRAPH_BASE_ARTIFACT_IDS_V3:
        inventory.append(
            await publish_bound(
                artifact_id,
                canonical_json(
                    {
                        "schemaVersion": "authored-gate1-fixture-artifact/v1",
                        "artifactId": artifact_id,
                        "exportAttemptId": str(export_attempt_id),
                    }
                ).encode("utf-8"),
            )
        )

    com_created = await client.post(
        "/api/v1/ppt/authored-emission/com-qualification-jobs-v3",
        json={
            "schemaVersion": "authored-com-qualification-create/v3",
            "exportAttemptId": str(export_attempt_id),
            "evidencePolicyDigest": _sha("evidence-policy"),
        },
    )
    assert com_created.status_code == 201, com_created.text
    com_job = com_created.json()
    claimed = await client.post(
        f"/api/v1/ppt/authored-emission/com-qualification-jobs-v3/{com_job['jobId']}/claim",
        json={"schemaVersion": "authored-com-qualification-claim/v3", "workerId": "gate0.com.worker"},
    )
    assert claimed.status_code == 200, claimed.text
    lease_token = claimed.json()["leaseToken"]
    artifact_digests = {item["artifactId"]: item["sha256"] for item in inventory}
    final_evidence = {
        "schemaVersion": "authored-com-qualification-evidence/v3",
        "repairDialogs": 0,
        "powerPointSurvivorPids": [],
        "desktopInterference": False,
        "ooxmlRepairObserved": False,
        "inputPptxDigest": artifact_digests["hybrid-input-pptx"],
        "savedCopyPptxDigest": artifact_digests["hybrid-saved-pptx"],
        "reopenInputPptxDigest": artifact_digests["hybrid-saved-pptx"],
        "firstOpenSnapshotDigest": artifact_digests["hybrid-first-open-slide-01-png"],
        "reopenSnapshotDigest": artifact_digests["hybrid-reopen-slide-01-png"],
        "comSnapshotDigest": artifact_digests["hybrid-reopen-com-json"],
        "ooxmlReadbackDigest": artifact_digests["hybrid-saved-ooxml-json"],
        "workerAttestationDigest": _sha("worker-attestation"),
        "environmentAttestationDigest": _sha("environment-attestation"),
        "artifactDigests": artifact_digests,
        "comState": {
            "firstOpen": "observed",
            "savedCopy": "verified",
            "reopen": "observed",
            "readback": "verified",
        },
    }
    states = [
        "captured",
        "planned",
        "assembled",
        "inputOoxmlVerified",
        "firstOpenObserved",
        "savedCopyVerified",
        "reopenObserved",
        "comVerified",
        "sealed",
    ]
    for sequence, (expected_state, next_state) in enumerate(zip(states[:-1], states[1:], strict=True)):
        transition_payload = {
            "schemaVersion": "authored-com-qualification-transition/v3",
            "workerId": "gate0.com.worker",
            "leaseToken": lease_token,
            "expectedState": expected_state,
            "expectedTransitionSequence": sequence,
            "nextState": next_state,
            "evidence": final_evidence if next_state == "sealed" else {},
            "failureCode": None,
        }
        transition = await client.post(
            f"/api/v1/ppt/authored-emission/com-qualification-jobs-v3/{com_job['jobId']}/transition",
            json={
                **transition_payload,
                "authority": _actual_product_authority(
                    private_key,
                    transition_payload,
                    evidence_class="actual_product_com_qualification",
                    nonce=f"gate0_com_transition_nonce_{sequence:02d}",
                    now=issued_at,
                ).model_dump(mode="json", by_alias=True),
            },
        )
        assert transition.status_code == 200, transition.text
        com_job = transition.json()
    assert com_job["state"] == "sealed"
    com_readback = await client.get(
        f"/api/v1/ppt/authored-emission/com-qualification-jobs-v3/{com_job['jobId']}",
        params={"executionNonce": "gate1_execution_nonce_20260823"},
    )
    assert com_readback.status_code == 200, com_readback.text
    verify_readback(com_readback.json())

    graph_payload = {
        "schemaVersion": "authored-evidence-graph-root-create/v3",
        "presentationId": str(presentation_id),
        "presentationRevision": presentation_revision,
        "exportAttemptId": str(export_attempt_id),
        "evidencePolicyDigest": _sha("evidence-policy"),
        "activeManifestDigest": manifest_digest,
        "sourceTreeCensusDigest": _sha("source-tree-census"),
        "sourceHeadCommit": "a" * 64,
        "releaseProfileDigest": release_profile_digest,
        "featureSetDigest": _sha("feature-set"),
        "gateMinus1ManifestDigest": _sha("gate-minus1-manifest"),
        "gateMinus1ResultSetDigest": _sha("gate-minus1-result-set"),
        "gateMinus1ReceiptArtifactDigest": _sha("gate-minus1-receipt-artifact"),
        "gateMinus1ValidatorContractDigest": _sha("gate-minus1-validator-contract"),
        "environmentAttestationDigest": _sha("environment-attestation"),
        "workerAttestationDigest": _sha("worker-attestation"),
        "comQualificationJobId": com_job["jobId"],
        "comQualificationResultDigest": com_job["resultDigest"],
        "baseArtifactIds": list(GATE1_GRAPH_BASE_ARTIFACT_IDS_V3),
        "baseArtifactIdsDigest": GATE1_GRAPH_BASE_ARTIFACT_IDS_DIGEST_V3,
        "finalRequiredArtifactIdsDigest": GATE1_REQUIRED_ARTIFACT_IDS_DIGEST_V3,
        "artifactInventory": inventory,
        "artifactInventoryDigest": canonical_digest(inventory),
    }
    graph_payload["rootDigest"] = canonical_digest(graph_payload)
    graph_payload["authority"] = _actual_product_authority(
        private_key,
        graph_payload,
        evidence_class="actual_product_completion",
        nonce="gate0_completion_graph_nonce_0001",
        now=issued_at,
    ).model_dump(mode="json", by_alias=True)
    graph_created = await client.post(
        "/api/v1/ppt/authored-emission/evidence-graph-roots-v3",
        json=graph_payload,
    )
    assert graph_created.status_code == 201, graph_created.text
    graph = graph_created.json()
    graph_readback = await client.get(
        f"/api/v1/ppt/authored-emission/evidence-graph-roots-v3/{graph['graphRootId']}",
        params={"executionNonce": "gate1_execution_nonce_20260823"},
    )
    assert graph_readback.status_code == 200, graph_readback.text
    verify_readback(graph_readback.json())
    assert graph_readback.json()["persistedRecordId"] == graph["graphRootId"]

    await publish_bound(
        "phase-result-set-json",
        canonical_json(
            {
                "schemaVersion": "authored-gate1-phase-result-set/v3",
                "phaseCount": 37,
                "passed": 37,
                "graphRootDigest": graph["rootDigest"],
            }
        ).encode("utf-8"),
    )
    await publish_bound(
        "evidence-graph-root-json",
        canonical_json(graph).encode("utf-8"),
    )

    completion_created = await client.post(
        "/api/v1/ppt/authored-emission/evidence-bundle-completions-v3",
        json={
            "schemaVersion": "authored-evidence-bundle-completion-create/v3",
            "graphRootId": graph["graphRootId"],
            "requiredCapabilityIds": required_capability_ids,
        },
    )
    assert completion_created.status_code == 201, completion_created.text
    completion = completion_created.json()
    signed_seal = {
        "schemaVersion": "authored-evidence-bundle-completion-seal/v3",
        "expectedCasVersion": completion["casVersion"],
        "receiptDigests": receipt_digests,
    }
    seal_authority = _actual_product_authority(
        private_key,
        signed_seal,
        evidence_class="actual_product_completion",
        nonce="gate0_completion_seal_nonce_0001",
        now=issued_at,
    )
    authority_digest = actual_product_authority_digest(signed_seal, seal_authority)
    completion_digest = canonical_digest(
        {
            "graphRootDigest": graph["rootDigest"],
            "requiredCapabilityIds": required_capability_ids,
            "receiptDigests": receipt_digests,
            "authorityDigest": authority_digest,
            "state": "sealed",
        }
    )
    sealed = await client.post(
        f"/api/v1/ppt/authored-emission/evidence-bundle-completions-v3/{completion['completionId']}/seal",
        json={
            **signed_seal,
            "completionDigest": completion_digest,
            "authority": seal_authority.model_dump(mode="json", by_alias=True),
        },
    )
    assert sealed.status_code == 200, sealed.text
    assert sealed.json()["state"] == "sealed"
    completion_readback = await client.get(
        f"/api/v1/ppt/authored-emission/evidence-bundle-completions-v3/{completion['completionId']}",
        params={"executionNonce": "gate1_execution_nonce_20260823"},
    )
    assert completion_readback.status_code == 200, completion_readback.text
    verify_readback(completion_readback.json())
    assert len(completion_readback.json()["receiptIdentities"]) == len(required_capability_ids)
    trust_readback = await client.get(
        "/api/v1/ppt/authored-emission/release-trust-readback-v3",
        params={"executionNonce": "gate1_execution_nonce_20260823"},
    )
    assert trust_readback.status_code == 200, trust_readback.text
    verify_readback(trust_readback.json())
    wrong_nonce = await client.get(
        "/api/v1/ppt/authored-emission/release-trust-readback-v3",
        params={"executionNonce": "wrong_execution_nonce_20260823"},
    )
    assert wrong_nonce.status_code == 422
    await publish_bound(
        "completion-seal-json",
        canonical_json(sealed.json()).encode("utf-8"),
    )


def _sidecar_sha(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def _bounded_typography_runs() -> list[dict]:
    return [
        {
            "text": "한국 AI 2026",
            "language": "ko-KR",
            "script": "cjk",
            "fontFamily": "Malgun Gothic",
            "fontFace": "Regular",
            "fontSizePt": 24,
            "weight": 400,
            "color": "#111827",
        },
        {
            "text": " 전략과 실행",
            "language": "ko-KR",
            "script": "cjk",
            "fontFamily": "Batang",
            "fontFace": "Regular",
            "fontSizePt": 24,
            "weight": 400,
            "color": "#111827",
        },
        {
            "text": " 코드 123",
            "language": "ko-KR",
            "script": "cjk",
            "fontFamily": "BatangChe",
            "fontFace": "Regular",
            "fontSizePt": 24,
            "weight": 400,
            "color": "#111827",
        },
        {
            "text": " CODE 2026",
            "language": "en-US",
            "script": "latin",
            "fontFamily": "Consolas",
            "fontFace": "Regular",
            "fontSizePt": 24,
            "weight": 400,
            "color": "#111827",
        },
    ]


def _native_semantic_draft(html: str, qid: str, feature_ids: list[str], kind: str) -> dict:
    if kind == "text":
        payload = {"paragraphs": [{"runs": _bounded_typography_runs()}]}
    elif kind == "table":
        payload = {
            "schemaVersion": "authored-native-table/v1",
            "headerRows": 1,
            "rows": [[{"text": "분기", "bold": True}, {"text": "값", "bold": True}], [{"text": "Q1"}, {"text": "96%"}]],
            "columnWidths": [1.0, 1.0],
        }
    elif kind == "chart":
        payload = {
            "chartType": "column",
            "categories": ["Q1", "Q2"],
            "series": [{"name": "정확도", "values": [92, 97]}],
        }
    else:
        payload = {"preset": "roundedRectangle", "fill": "#2563EB", "stroke": "#1E3A8A", "radiusPt": 8}
    element = {
        "elementId": qid,
        "kind": kind,
        "semanticRole": "native-content",
        "intendedOrder": 0,
        "requiredFeatures": feature_ids,
        "payload": payload,
    }
    return {
        "semanticSource": {
            "authoredHtmlDigest": bytes_digest(html.encode("utf-8")),
            "elements": [element],
            "identityRegistry": {"activeQids": [qid], "retiredQids": []},
        }
    }


def _bounded_cjk_admissions():
    registry = load_bounded_windows_cjk_registry()
    payload = TextPayloadV1.model_validate({"paragraphs": [{"runs": _bounded_typography_runs()}]}, strict=False)
    admissions = []
    for element_id in ("s02-content", "s06-content"):
        readback = NativeTypographyReadbackV1(
            element_id=element_id,
            runs=[
                NativeTypographyRunReadbackV1(
                    run_index=index,
                    text_digest=canonical_digest({"text": run.text}),
                    face=run.font_family,
                    east_asian_typeface=run.font_family,
                )
                for index, run in enumerate(payload.paragraphs[0].runs)
            ],
            line_rect_digests=[canonical_digest({"elementId": element_id, "line": 0})],
            overflow=False,
        )
        admissions.append(
            admit_native_typography(
                element_id=element_id,
                payload=payload,
                registry=registry,
                readback=readback,
            )
        )
    return registry, admissions


def _quality_and_coverage(
    *,
    attempt_id: uuid.UUID,
    presentation_id: uuid.UUID,
    revision: int,
    canonical_source: str,
    renderer_source: str,
    pptx: str,
    slide_count: int,
    actual_cjk_evidence: bool = False,
) -> tuple[dict, dict]:
    registry = admissions = None
    if actual_cjk_evidence:
        registry, admissions = _bounded_cjk_admissions()
        com_readback = native_typography_com_reopen_digest(
            font_registry_digest=registry.digest,
            admission_receipts=admissions,
        ).removeprefix("sha256:")
    else:
        com_readback = hashlib.sha256(b"readback").hexdigest()
    chart_readback = {
        "slideNumber": 4,
        "elementId": "s04-content",
        "chart": {
            "chartType": 51,
            "title": {"present": False, "text": "", "typeface": "", "sizePt": None},
            "series": [
                {
                    "name": "정확도",
                    "categories": ["Q1", "Q2"],
                    "values": [92, 97],
                }
            ],
            "axes": {
                "category": {
                    "present": True,
                    "hasTitle": False,
                    "title": "",
                    "tickLabelTypeface": "Arial",
                    "tickLabelSizePt": 12,
                },
                "value": {
                    "present": True,
                    "hasTitle": False,
                    "title": "",
                    "tickLabelTypeface": "Arial",
                    "tickLabelSizePt": 12,
                },
            },
            "legend": {
                "present": True,
                "position": -4107,
                "typeface": "Arial",
                "sizePt": 12,
            },
        },
    }
    qa = {
        "status": "passed",
        "platform": "windows",
        "supported": True,
        "automation": "PowerPoint.COM",
        "reopened": True,
        "rendered": True,
        "readback": True,
        "verificationScope": "powerpoint-first-open-save-reopen-stability",
        "semanticPayloadBinding": "passed",
        "repairDialogs": 0,
        "repairObservation": "interactive-observation",
        "renderedPngSha256": [hashlib.sha256(b"fresh-render").hexdigest()],
        "firstOpenRenderedPngSha256": [hashlib.sha256(b"fresh-render").hexdigest()],
        "reopenRenderedPngSha256": [hashlib.sha256(b"fresh-render").hexdigest()],
        "renderHashesMatch": True,
        "firstOpenReadbackSha256": com_readback,
        "reopenReadbackSha256": com_readback,
        "firstOpenReadbackArtifact": "artifact://com/first-open-readback",
        "reopenReadbackArtifact": "artifact://com/reopen-readback",
        "readbackMatch": True,
        "powerpointVersion": "PowerPoint-16.0.19029",
        "slideCount": slide_count,
        "nativeText": 2,
        "nativeImage": 2,
        "nativeShape": 2,
        "nativeTable": 1,
        "nativeChart": 1,
        "nativeImageCrop": 2,
        "nativeQidMissing": 0,
        "nativeQidGhosting": 0,
        "backplateCollisions": 0,
        "firstOpenChartReadback": [chart_readback],
        "reopenChartReadback": [chart_readback],
    }
    atomic = {
        "presentationId": str(presentation_id),
        "exportAttemptId": str(attempt_id),
        "presentationRevision": revision,
        "canonicalSourceDigest": canonical_source,
        "rendererSourceDigest": renderer_source,
        "pptxSha256": pptx,
    }
    hybrid_plan_digest = _sha("hybrid-plan-slide-5")
    closure_digest = _sha("paint-closure-slide-5")
    downgrade_payload = {
        "schemaVersion": "hybrid-layer-downgrade-receipt/browser-v1",
        "state": "closed",
        "reason": None,
        "demotedElementIds": [],
    }
    downgrade_receipt = {
        **downgrade_payload,
        "receiptDigest": canonical_digest(downgrade_payload),
    }
    qid_slides = []
    for index in range(1, slide_count + 1):
        qid = f"s{index:02d}-content"
        quality_slide = {
            "slideNumber": index,
            "backplateCount": 1 if index == 5 else 0,
            "qidCoverage": [{"elementId": qid, "logical": "native", "ooxml": "present"}],
            "fallbackElements": [],
            "rasterFallbackElements": 0,
            "semanticReceipts": {"observationReceiptDigest": _sha(f"obs-{index}")},
            "nativeElementReadback": [],
        }
        if index == 4:
            quality_slide["nativeElementReadback"] = [
                {
                    "slideNumber": 4,
                    "elementId": "s04-content",
                    "kind": "chart",
                    "chart": {
                        "chartType": "column",
                        "title": None,
                        "titleLatinTypefaces": [],
                        "titleEastAsianTypefaces": [],
                        "categories": ["Q1", "Q2"],
                        "series": [{"name": "정확도", "values": [92, 97]}],
                        "categoryAxis": {
                            "present": True,
                            "title": None,
                            "latinTypefaces": ["Arial"],
                            "eastAsianTypefaces": ["Arial"],
                        },
                        "valueAxis": {
                            "present": True,
                            "title": None,
                            "latinTypefaces": ["Arial"],
                            "eastAsianTypefaces": ["Arial"],
                        },
                        "legend": {
                            "present": True,
                            "title": None,
                            "position": "bottom",
                            "latinTypefaces": ["Arial"],
                            "eastAsianTypefaces": ["Arial"],
                        },
                    },
                }
            ]
        if index == 5:
            quality_slide["paintPlan"] = {
                "schemaVersion": "hybrid-paint-plan/browser-v1",
                "strategy": "single-backplate-fixed-point",
                "fixedPointReached": True,
                "closureIterations": 2,
                "closureDigest": closure_digest,
                "hybridPlanDigest": hybrid_plan_digest,
                "slideDemotedToRaster": False,
                "downgradeReceipt": downgrade_receipt,
                "demotions": [],
            }
        qid_slides.append(quality_slide)
    quality = {
        "schemaVersion": "presenton.export-quality/v1",
        "exportAttemptId": str(attempt_id),
        "atomicBinding": atomic,
        "powerPointQa": qa,
        "slides": qid_slides,
        "fontEmbeddingStatus": {
            "requested": True,
            "applied": True,
            "nativeTextUsage": "used",
            "faces": [
                {
                    "face": "Noto Serif CJK KR",
                    "licenseDecision": "allowed-editable",
                    "sourceSha256": hashlib.sha256(b"font").hexdigest(),
                    "sourceBytes": 2048,
                }
            ],
        },
    }
    if actual_cjk_evidence:
        assert registry is not None and admissions is not None
        authority = NativeTypographyReleaseEvidenceV1(
            font_registry_digest=registry.digest,
            font_faces=list(registry.receipts),
            admission_receipts=admissions,
            readback_authority="PowerPoint.COM-reopen",
            powerpoint_qa_digest=canonical_digest(ExportPowerPointQaEvidence.model_validate(qa)),
            first_open_readback_digest=f"sha256:{com_readback}",
            reopen_readback_digest=f"sha256:{com_readback}",
        )
        quality["nativeTypographyEvidence"] = authority.model_dump(mode="json", by_alias=True)
        quality["fontEmbeddingStatus"] = {
            "requested": True,
            "applied": True,
            "nativeTextUsage": "used",
            "nativeTextElementsUsingEmbeddedTypefaces": 2,
            "nativeTypefaceFamilies": [face.family for face in registry.receipts],
            "faces": [
                {
                    "typeface": face.family,
                    "face": face.face.casefold(),
                    "sourceSha256": face.font_byte_digest.removeprefix("sha256:"),
                    "sourceBytes": face.size_bytes,
                    "embeddedBytes": face.size_bytes,
                    "fsType": face.fs_type,
                    "licenseDecision": "allowed-editable",
                    "subset": False,
                }
                for face in registry.receipts
            ],
            "failures": [],
        }
    counts = {
        "nativeText": 2,
        "nativeImage": 2,
        "nativeShape": 2,
        "nativeTable": 1,
        "nativeChart": 1,
        "nativeImageCrop": 2,
        "rasterRemaining": 0,
    }
    slides = []
    for index in range(1, slide_count + 1):
        row = dict(counts)
        row["slideNumber"] = index
        for key in counts:
            row[key] = 0
        if index in {2, 6}:
            row["nativeText"] = 1
        if index in {5, 7}:
            row["nativeImage"] = 1
        if index in {1, 8}:
            row["nativeShape"] = 1
        if index == 3:
            row["nativeTable"] = 1
        if index == 4:
            row["nativeChart"] = 1
        if index in {5, 7}:
            row["nativeImageCrop"] = 1
        row["logicalCoverage"] = {key: row[key] for key in counts}
        row["ooxmlReadback"] = {"status": "passed", **{key: row[key] for key in counts}}
        row["backplateCount"] = 1 if index == 5 else 0
        row["typographyIssues"] = []
        row["semanticReceipts"] = {
            "observationReceiptDigest": _sha(f"obs-{index}"),
            **({"hybridPlanDigest": hybrid_plan_digest} if index == 5 else {}),
        }
        row["fallbackReasons"] = []
        row["visualParity"] = "passed"
        row["nativeBackplateDeDuplication"] = (
            "applied" if sum(row[key] for key in counts if key != "rasterRemaining") else "not-applicable"
        )
        slides.append(row)
    coverage = {
        "schemaVersion": "presenton.authored-hybrid-coverage/v2",
        "mode": "hybrid",
        "source": {
            "exportAttemptId": str(attempt_id),
            "presentationRevision": revision,
            "canonicalSourceDigest": canonical_source,
            "sourceDigest": renderer_source,
        },
        "totalSlides": slide_count,
        **counts,
        "logicalCoverage": counts,
        "ooxmlReadback": {"status": "passed", **counts},
        "backplateCount": 1,
        "typographyIssues": [],
        "fullyEditable": True,
        "visualParity": "passed",
        "slides": slides,
    }
    return quality, coverage


def _target_from_environment() -> ReleaseAuthorityTargetV3:
    return release_authority_target_v3_from_environment()


async def _seed_fresh_authoritative_bundle(
    maker: async_sessionmaker,
    *,
    owner_id: uuid.UUID,
    presentation_id: uuid.UUID,
    actual_cjk_evidence: bool = False,
) -> dict[str, object]:
    source_revision = 2
    operation_id = uuid.uuid4()
    generation_attempt_id = uuid.uuid4()
    source_bare, renderer_bare, pptx_bare = "b" * 64, "c" * 64, "d" * 64
    renderer_digest = "sha256:" + renderer_bare
    pptx_digest = "sha256:" + pptx_bare
    slide_kinds = ["shape", "text", "table", "chart", "shape", "text", "shape", "shape"]
    feature_sets = [
        ["gradient", "shadow", "transform", "flex", "grid", "border-radius", "opacity", "clip"],
        ["cjk"],
        ["table"],
        ["chart"],
        ["image-crop"],
        ["cjk"],
        ["image-crop"],
        ["gradient"],
    ]
    htmls = [f'<div data-qid="s{index:02d}-content">slide {index}</div>' for index in range(1, 9)]
    if actual_cjk_evidence:
        font_sources = {
            "malgun": (_FONT_DIR / "malgun.ttf").read_bytes(),
            "batang": (_FONT_DIR / "batang.ttc").read_bytes(),
            "consolas": (_FONT_DIR / "consola.ttf").read_bytes(),
        }
        registry = load_bounded_windows_cjk_registry()
        resolved_fonts = []
        for face in registry.receipts:
            source_key = (
                "batang" if face.family in {"Batang", "BatangChe"} else face.family.casefold().replace(" gothic", "")
            )
            content = font_sources[source_key]
            pointer = ArtifactPointerV1(
                artifact_ref=(f"artifact://sha256/{face.font_byte_digest.removeprefix('sha256:')}"),
                digest=face.font_byte_digest,
                byte_length=len(content),
                media_type="font/ttf",
            )
            resolved_fonts.append(
                ResolvedFontV1(
                    face_id=f"font:{face.family.casefold().replace(' ', '-')}",
                    family=face.family,
                    face=face.face,
                    artifact=pointer,
                    locator_digest=face.font_byte_digest,
                    resolver_version="windows-byte-registry/v1",
                    embedding_permitted=face.embedding_permitted,
                    subsetting_permitted=face.subsetting_permitted,
                    retention_class="active",
                )
            )
        slide_fonts = [FontManifestV1(fonts=resolved_fonts) for _ in range(8)]
        resolved_artifact_bytes = {bytes_digest(content): content for content in font_sources.values()}
    else:
        font_bytes = b"synthetic-noto-serif-cjk-font"
        font_digest = bytes_digest(font_bytes)
        font_pointer = ArtifactPointerV1(
            artifact_ref=f"artifact://sha256/{font_digest.removeprefix('sha256:')}",
            digest=font_digest,
            byte_length=len(font_bytes),
            media_type="font/otf",
        )
        synthetic_fonts = [
            ResolvedFontV1(
                face_id=f"font:synthetic-{family.casefold().replace(' ', '-')}",
                family=family,
                face="Regular",
                artifact=font_pointer,
                locator_digest=font_digest,
                resolver_version="fixture/v1",
                embedding_permitted=True,
                subsetting_permitted=True,
                retention_class="active",
            )
            for family in ("Malgun Gothic", "Batang", "BatangChe", "Consolas")
        ]
        slide_fonts = [FontManifestV1(fonts=synthetic_fonts) for _ in range(8)]
        resolved_artifact_bytes = {font_digest: font_bytes}
    slides = [
        SlideModel(
            id=uuid.uuid4(),
            owner_id=owner_id,
            presentation=presentation_id,
            layout_group="authored",
            layout="authored:content",
            index=index - 1,
            content={"__authored__": True},
            html_content=html,
        )
        for index, html in enumerate(htmls, 1)
    ]
    drafts = [
        _native_semantic_draft(html, f"s{index:02d}-content", feature_sets[index - 1], slide_kinds[index - 1])
        for index, html in enumerate(htmls, 1)
    ]
    manifest = None
    attempts: list[uuid.UUID] = []
    async with maker() as session:
        presentation = PresentationModel(
            id=presentation_id,
            owner_id=owner_id,
            content="fresh gate 0 semantic deck",
            n_slides=8,
            language="ko",
            title="Fresh Gate 0",
            mode="authored",
            revision=1,
        )
        session.add(presentation)
        session.add_all(slides)
        await session.flush()
        manifest = await publish_authored_semantic_bundle_graph(
            session,
            presentation_id=presentation_id,
            owner_id=owner_id,
            expected_presentation_revision=1,
            operation_id=operation_id,
            generation_attempt_id=generation_attempt_id,
            slides=slides,
            htmls=htmls,
            semantic_drafts=drafts,
            slide_font_manifests=slide_fonts,
            resolved_artifact_bytes=resolved_artifact_bytes,
            master_contract_digest=_sha("master"),
            composition_digest=_sha("composition"),
        )
        presentation.revision = source_revision
        session.add(presentation)
        await session.flush()
        source_bare = manifest.canonical_digest.removeprefix("sha256:")

        for mode in ("authored", "fidelity", "hybrid"):
            attempt_id = uuid.uuid4()
            attempts.append(attempt_id)
            quality, coverage = _quality_and_coverage(
                attempt_id=attempt_id,
                presentation_id=presentation_id,
                revision=source_revision,
                canonical_source=source_bare,
                renderer_source=renderer_bare,
                pptx=pptx_bare,
                slide_count=8,
                actual_cjk_evidence=actual_cjk_evidence,
            )
            quality_digest = _sidecar_sha(quality)
            coverage_digest = _sidecar_sha(
                PresentationExportCoverageManifest.model_validate(coverage).model_dump(mode="json", by_alias=True)
            )
            atomic_model = {
                "presentation_id": str(presentation_id),
                "export_attempt_id": str(attempt_id),
                "presentation_revision": source_revision,
                "canonical_source_sha256": source_bare,
                "renderer_source_sha256": renderer_bare,
                "pptx_sha256": pptx_bare,
            }
            qa = quality["powerPointQa"]
            provenance = PresentationExportAttemptProvenance.model_validate(
                {
                    "schema_version": 1,
                    "adapter_chain": ["general"],
                    "capability_evidence": {"export_mode": "hybrid", "mode": mode},
                    "asset_digests": {},
                    "compiler_runtime": {
                        "compiler": "authored-hybrid",
                        "compiler_version": "2",
                        "runtime": "node",
                        "runtime_version": "22",
                    },
                    "freshness": {
                        "source_revision": source_revision,
                        "source_snapshot_sha256": source_bare,
                        "renderer_source_sha256": renderer_bare,
                        "package_sha256": pptx_bare,
                        "asset_digests_match": True,
                        "package_digest_match": True,
                    },
                    "coverage_manifest": coverage,
                    "quality_sidecar": quality,
                    "atomic_binding": atomic_model,
                    "powerpoint_qa": qa,
                    "visual_parity": {
                        "status": "passed",
                        "binding": atomic_model,
                        "quality_sidecar_sha256": quality_digest,
                        "coverage_sidecar_sha256": coverage_digest,
                        "powerpoint_qa": qa,
                    },
                }
            )
            attempt = PresentationExportAttempt(
                id=attempt_id,
                owner_id=owner_id,
                owner_scope=str(owner_id),
                presentation_id=presentation_id,
                format="pptx",
                state="succeeded",
                source_revision=source_revision,
                source_snapshot_sha256=source_bare,
                request_hash=hashlib.sha256(f"request-{mode}".encode()).hexdigest(),
                idempotency_key_hash=hashlib.sha256(f"idempotency-{mode}".encode()).hexdigest(),
                requested_adapter="general",
                selected_adapter="general",
                artifact_sha256=pptx_bare,
                artifact_size_bytes=1024,
                provenance_schema_version=1,
                provenance=provenance.model_dump(mode="json"),
                started_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
            )
            session.add(attempt)
            if mode == "hybrid":
                binding = seal_export_binding(
                    ExportAttemptDigestBindingV1(
                        export_attempt_id=attempt_id,
                        presentation_id=presentation_id,
                        presentation_revision=source_revision,
                        operation_id=operation_id,
                        generation_attempt_id=generation_attempt_id,
                        slide_origin_generation_attempt_ids=[generation_attempt_id] * 8,
                        canonical_digest=manifest.canonical_digest,
                        manifest_digest=manifest.canonical_digest,
                        semantic_digests=[_sha(f"semantic-{index}") for index in range(8)],
                        master_contract_digest=_sha("master"),
                        composition_digest=_sha("composition"),
                        font_manifest_digest=_sha("font"),
                        asset_manifest_digest=_sha("asset"),
                        quality_policy_digest=_sha("quality"),
                        mode="hybrid",
                        renderer_source_digest=renderer_digest,
                        serializer_id="authored-hybrid",
                        serializer_version="2",
                        receipt_digests=[_sha("receipt")],
                        pptx_digest=pptx_digest,
                    )
                )
                session.add(
                    AuthoredSemanticExportBinding(
                        owner_id=owner_id,
                        tenant_id="default",
                        export_attempt_id=attempt_id,
                        presentation_id=presentation_id,
                        presentation_revision=source_revision,
                        canonical_digest=manifest.canonical_digest,
                        renderer_source_digest=renderer_digest,
                        mode="hybrid",
                        serializer_id="authored-hybrid",
                        serializer_version="2",
                        pptx_digest=pptx_digest,
                        binding_digest=binding.binding_digest,
                        payload=binding.model_dump(mode="json", by_alias=True),
                    )
                )
            await session.flush()
        await session.commit()
    return {
        "manifest": manifest,
        "operation_id": operation_id,
        "generation_attempt_id": generation_attempt_id,
        "source_revision": source_revision,
        "attempt_ids": attempts,
        "hybrid_attempt_id": attempts[2],
    }


def test_gate0_actual_router_fails_closed_until_v3_projection_exists(tmp_path: Path, monkeypatch) -> None:
    """Exact environment alone must not synthesize v3 release readiness."""

    async def scenario() -> None:
        _configure_exact_release_environment(monkeypatch)
        owner_id = uuid.uuid4()
        presentation_id = uuid.uuid4()
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate0.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    PresentationModel.__table__,
                    AuthoredCapabilityReceiptRecordV2.__table__,
                    AuthoredDeckEmissionActivePointer.__table__,
                    *_RELEASE_V3_GRAPH_AND_GATE0_TABLES,
                ],
            )
        await _seed_legacy_presentation(maker, presentation_id, owner_id)
        app = _build_app(owner_id, maker)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/ppt/presentation/{presentation_id}/authored-emission/capability-projection-v2"
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        gate0 = evaluate_gate0_projection(payload)
        assert gate0 == {
            "ready": False,
            "reason": "v3_projection_fields_missing",
            "missing": list(V3_PROJECTION_FIELDS[:-1]),
        }
        assert payload["releaseProfile"]["promotionReady"] is False
        assert payload["releaseProfile"]["boundedNativeParityReady"] is False
        await engine.dispose()

    asyncio.run(scenario())


def test_gate0_actual_router_requires_owner_before_projection(tmp_path: Path, monkeypatch) -> None:
    """The same router must not expose projection data without owner context."""

    async def scenario() -> None:
        _configure_exact_release_environment(monkeypatch)
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate0-owner.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    PresentationModel.__table__,
                    AuthoredCapabilityReceiptRecordV2.__table__,
                    AuthoredDeckEmissionActivePointer.__table__,
                    *_RELEASE_V3_GRAPH_AND_GATE0_TABLES,
                ],
            )
        app = FastAPI()

        async def session_override():
            async with maker() as session:
                yield session

        app.dependency_overrides[database.get_async_session] = session_override
        app.include_router(AUTHORED_SEMANTIC_ROUTER, prefix="/api/v1/ppt")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(
                f"/api/v1/ppt/presentation/{uuid.uuid4()}/authored-emission/capability-projection-v2"
            )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "authored_semantic_owner_required"
        await engine.dispose()

    asyncio.run(scenario())


def test_gate0_v3_http_and_receipt_post_seams_fail_closed_until_wired(tmp_path: Path, monkeypatch) -> None:
    """Pin the expected v3 HTTP seams without inventing a positive authority."""

    async def scenario() -> None:
        _configure_exact_release_environment(monkeypatch)
        owner_id = uuid.uuid4()
        presentation_id = uuid.uuid4()
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate0-v3.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    PresentationModel.__table__,
                    AuthoredCapabilityReceiptRecordV2.__table__,
                    AuthoredDeckEmissionActivePointer.__table__,
                    *_RELEASE_V3_GRAPH_AND_GATE0_TABLES,
                ],
            )
        await _seed_legacy_presentation(maker, presentation_id, owner_id)
        app = _build_app(owner_id, maker)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            projection = await client.get(V3_PROJECTION_PATH.format(presentation_id=presentation_id))
            receipt = await client.post(V3_RECEIPT_PATH, json={})

        # The v3 projection route is now reachable, but no authoritative
        # receipt POST has been supplied, so the server must remain blocked.
        assert projection.status_code == 200, projection.text
        gate0 = evaluate_gate0_projection(projection.json())
        assert gate0 == {"ready": False, "reason": "v3_projection_not_ready", "missing": []}
        assert projection.json()["releaseProfile"]["promotionReady"] is False
        assert receipt.status_code == 422, receipt.text
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "actual_cjk_evidence",
    [
        pytest.param(False, id="synthetic-noto-rejected"),
        pytest.param(
            True,
            id="actual-windows-font-bytes-promoted",
            marks=pytest.mark.skipif(
                not _ACTUAL_CJK_FONTS_AVAILABLE,
                reason="actual Windows bounded font bytes are unavailable",
            ),
        ),
    ],
)
def test_gate0_v3_http_cjk_release_authority_boundary(tmp_path: Path, monkeypatch, actual_cjk_evidence: bool) -> None:
    """Reject synthetic font claims and admit exact installed-byte evidence."""

    async def scenario() -> None:
        registry_digest = load_bounded_windows_cjk_registry().digest if actual_cjk_evidence else None
        _configure_exact_release_environment(monkeypatch, font_inventory_digest=registry_digest)
        signer_private_key = _configure_actual_product_signers(monkeypatch)
        monkeypatch.setenv("AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY", "visual-gate-test-key-01234567890123456789")
        monkeypatch.setenv("AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY", "ci-evidence-test-key-01234567890123456789")
        monkeypatch.setenv("AUTHORED_RELEASE_BACKEND_COMMIT_SHA", "a" * 64)
        owner_id, presentation_id = uuid.uuid4(), uuid.uuid4()
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate0-positive.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    PresentationModel.__table__,
                    SlideModel.__table__,
                    AuthoredSemanticArtifact.__table__,
                    AuthoredEmissionBundleRecord.__table__,
                    AuthoredDeckEmissionManifestRecord.__table__,
                    AuthoredDeckEmissionActivePointer.__table__,
                    PresentationExportAttempt.__table__,
                    AuthoredSemanticExportBinding.__table__,
                    AuthoredCapabilityReceiptRecordV2.__table__,
                    AuthoredReleaseNonceV3.__table__,
                    *_RELEASE_V3_GRAPH_AND_GATE0_TABLES,
                ],
            )
        seeded = await _seed_fresh_authoritative_bundle(
            maker,
            owner_id=owner_id,
            presentation_id=presentation_id,
            actual_cjk_evidence=actual_cjk_evidence,
        )
        target = _target_from_environment()
        manifest = seeded["manifest"]
        attempt_ids = seeded["attempt_ids"]
        hybrid_attempt_id = seeded["hybrid_attempt_id"]
        generation_attempt_id = seeded["generation_attempt_id"]
        operation_id = seeded["operation_id"]
        revision = int(seeded["source_revision"])
        generated_at = datetime.now(timezone.utc).replace(microsecond=0)
        identity = {
            "gateId": "fresh-gate0-v3",
            "releaseProfileId": target.profile_id,
            "presentationId": str(presentation_id),
            "presentationRevision": revision,
            "operationId": str(operation_id),
            "generationAttemptId": str(generation_attempt_id),
            "registryDigest": _sha("registry"),
            "semanticProof": {"source": "persisted-bundleGraph", "slides": 8},
            "slideCount": 8,
            "modes": [
                {"mode": mode, "exportAttemptId": str(attempt_id), "pptxSha256": "sha256:" + "d" * 64}
                for mode, attempt_id in zip(("authored", "fidelity", "hybrid"), attempt_ids, strict=True)
            ],
            "diffs": {"status": "passed", "perSlide": [{"slide": i, "status": "passed"} for i in range(1, 9)]},
            "ledger": [
                {
                    "slide": i,
                    "title": "reviewed",
                    "linebreak": 0,
                    "clipping": 0,
                    "overlap": 0,
                    "ghosting": 0,
                    "collision": 0,
                    "reviewer": "qa",
                    "reviewedAt": generated_at.isoformat(),
                }
                for i in range(1, 9)
            ],
            "releaseAuthority": {"profileId": target.profile_id, "status": "accepted"},
            "dbAttemptResolution": {"attemptIds": [str(item) for item in attempt_ids], "status": "resolved"},
            "freshness": {"generatedAt": generated_at.isoformat().replace("+00:00", "Z")},
            "atomicBindingDigest": _sha("atomic-report"),
        }
        report = VisualParityGateReportV1.model_validate(
            {
                "schemaVersion": "authored-vnext-visual-parity-gate-report/v1",
                "status": "passed",
                "promotionReady": False,
                "visualParity": "passed",
                "languages": ["ja", "ko", "zh"],
                "archetypes": [
                    "boundary",
                    "chart",
                    "diagram",
                    "image-crop",
                    "long-text",
                    "mixed-css",
                    "multi-column",
                    "table",
                ],
                "identity": identity,
                "evidenceDigest": canonical_digest(identity),
            }
        )
        unsigned_report = report.model_dump(mode="json", by_alias=True, exclude_none=True)
        unsigned_report.pop("authoritySignature", None)
        report = report.model_copy(
            update={
                "authority_signature": "hmac-sha256:"
                + hmac.new(
                    b"visual-gate-test-key-01234567890123456789",
                    canonical_json(unsigned_report).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest()
            }
        )
        app = _build_app(owner_id, maker)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            visual_publish = await client.post(
                f"/api/v1/ppt/presentation/{presentation_id}/authored-emission/visual-evidence-artifacts-v2",
                json={
                    "schemaVersion": "authored-visual-evidence-artifact-publish/v2",
                    "report": report.model_dump(mode="json", by_alias=True),
                },
            )
            assert visual_publish.status_code == 201, visual_publish.text
            visual_artifact = visual_publish.json()
            visual_authority = {
                "schemaVersion": "authored-visual-evidence-authority/v2",
                "binding": {
                    "presentationId": str(presentation_id),
                    "presentationRevision": revision,
                    "manifestDigest": manifest.canonical_digest,
                    "generationAttemptId": str(generation_attempt_id),
                    "exportAttemptId": str(hybrid_attempt_id),
                    "reportArtifactRef": visual_artifact["reportArtifactRef"],
                    "reportArtifactDigest": visual_artifact["reportArtifactDigest"],
                },
            }

            async with maker() as session:
                attempt = await session.get(PresentationExportAttempt, hybrid_attempt_id)
                binding_record = await session.scalar(
                    select(AuthoredSemanticExportBinding).where(
                        AuthoredSemanticExportBinding.export_attempt_id == hybrid_attempt_id
                    )
                )
                assert attempt is not None and binding_record is not None
                evidence = readiness_validators._AuthoritativeExportEvidence(
                    attempt=attempt,
                    binding_record=binding_record,
                    binding=readiness_validators._binding_model(binding_record),
                    provenance=PresentationExportAttemptProvenance.model_validate(attempt.provenance),
                    required_features_by_element={},
                )
                export_evidence_digests = {
                    capability: trusted_capability_evidence_digest(capability, target, evidence)
                    for capability in target.profile_definition.all_required_capability_ids
                    if capability != "ci.requiredChecks" and capability != "evidence.visual"
                }

            ci_verified_at = datetime.now(timezone.utc).replace(microsecond=0)
            ci_base = BitbucketRequiredChecksReceiptV3.model_validate(
                {
                    "schemaVersion": "authored-bitbucket-required-check-receipt/v1",
                    "repository": "ibizsoftware/devax-ppt",
                    "commit": "a" * 64,
                    "pipelineUuid": "{fresh-gate0-pipeline}",
                    "protectedBranches": ["main"],
                    "requiredPipelineSteps": [
                        "Authored release readiness configuration",
                        "Run shared Next.js CI Node suite",
                        "Workspace chat-first browser E2E",
                    ],
                    "successfulCommitStatusKeys": ["authored-release-readiness", "next-ci", "workspace-chat-e2e"],
                    "verifiedAt": ci_verified_at,
                    "receiptDigest": _sha("placeholder"),
                    "authoritySignature": "hmac-sha256:" + "0" * 64,
                }
            )
            unsigned_ci = ci_base.model_dump(mode="json", by_alias=True)
            unsigned_ci.pop("receiptDigest", None)
            unsigned_ci.pop("authoritySignature", None)
            ci_digest = canonical_digest(unsigned_ci)
            ci_report = ci_base.model_copy(
                update={
                    "receipt_digest": ci_digest,
                    "authority_signature": "hmac-sha256:"
                    + hmac.new(
                        b"ci-evidence-test-key-01234567890123456789", ci_digest.encode("utf-8"), hashlib.sha256
                    ).hexdigest(),
                }
            )
            ci_publish = await client.post(
                "/api/v1/ppt/authored-emission/ci-evidence-artifacts-v3",
                json={
                    "schemaVersion": "authored-ci-evidence-artifact-publish/v3",
                    "receipt": ci_report.model_dump(mode="json", by_alias=True),
                    "authority": _actual_product_authority(
                        signer_private_key,
                        ci_report.model_dump(mode="json", by_alias=True),
                        evidence_class="actual_product_ci",
                        nonce="gate0_ci_nonce_000000000001",
                        now=ci_verified_at,
                    ).model_dump(mode="json", by_alias=True),
                },
            )
            assert ci_publish.status_code == 201, ci_publish.text
            ci_artifact = ci_publish.json()

            receipt_digests: list[str] = []
            for capability in target.profile_definition.all_required_capability_ids:
                if capability == "ci.requiredChecks":
                    evidence_bundle_id = ci_artifact["evidenceBundleId"]
                    evidence_digest = ci_report.receipt_digest
                elif capability == "evidence.visual":
                    evidence_bundle_id = str(hybrid_attempt_id)
                    evidence_digest = report.evidence_digest
                else:
                    evidence_bundle_id = str(hybrid_attempt_id)
                    evidence_digest = export_evidence_digests[capability]
                expires_at = (
                    generated_at
                    + timedelta(seconds=target.profile_definition.visual_evidence_policy.receipt_ttl_seconds)
                    if capability == "evidence.visual"
                    else ci_verified_at + timedelta(minutes=10)
                )
                request = CapabilityReceiptIssueRequestV2(
                    capability_id=capability,
                    state="ready",
                    scope=target.scope,
                    environment=target.environment,
                    serializer_id=target.scope.renderer,
                    serializer_version=target.profile_definition.serializer_version,
                    feature_set_digest=trusted_capability_feature_set_digest(capability, target),
                    evidence_policy_id=trusted_capability_evidence_policy_id(capability),
                    policy_digest=target.profile_definition_digest,
                    evidence_bundle_id=evidence_bundle_id,
                    evidence_digest=evidence_digest,
                    expires_at=expires_at,
                    reason_code="verified_authoritative_evidence",
                    visual_evidence_authority=(
                        VisualEvidenceAuthorityV2.model_validate(visual_authority)
                        if capability == "evidence.visual"
                        else None
                    ),
                )
                receipt_wire = request.model_dump(mode="json", by_alias=True)
                capability_nonce = (
                    "gate0_capability_nonce_" + hashlib.sha256(capability.encode("utf-8")).hexdigest()[:24]
                )
                capability_authority = _actual_product_authority(
                    signer_private_key,
                    {},
                    evidence_class="actual_product_capability",
                    nonce=capability_nonce,
                    now=ci_verified_at,
                )
                signed_receipt_subject = build_bound_capability_subject_v3(
                    receipt_wire=receipt_wire,
                    authority=capability_authority,
                    binding=binding_record,
                    target=target,
                    tenant_id="default",
                )
                capability_digest = actual_product_authority_digest(signed_receipt_subject, capability_authority)
                capability_authority = capability_authority.model_copy(
                    update={
                        "signature_base64": base64.b64encode(
                            signer_private_key.sign(capability_digest.encode("ascii"))
                        ).decode("ascii")
                    }
                )
                receipt_response = await client.post(
                    V3_RECEIPT_PATH,
                    json={
                        "schemaVersion": "authored-capability-receipt-issue/v3",
                        "receipt": receipt_wire,
                        "exportAttemptId": str(hybrid_attempt_id),
                        "bindingDigest": binding_record.binding_digest,
                        "authority": capability_authority.model_dump(mode="json", by_alias=True),
                    },
                )
                if capability == "receipt.cjk" and not actual_cjk_evidence:
                    assert receipt_response.status_code == 422, receipt_response.text
                    assert receipt_response.json()["detail"]["code"] == ("authored_trusted_cjk_native_evidence_invalid")
                else:
                    assert receipt_response.status_code == 201, f"{capability}: {receipt_response.text}"
                    receipt_digests.append(receipt_response.json()["receiptDigest"])

            if actual_cjk_evidence:
                assert len(receipt_digests) == len(target.profile_definition.all_required_capability_ids)
                await _seal_actual_product_completion_chain(
                    client,
                    private_key=signer_private_key,
                    presentation_id=presentation_id,
                    presentation_revision=revision,
                    export_attempt_id=hybrid_attempt_id,
                    manifest_digest=manifest.canonical_digest,
                    release_profile_digest=target.profile_definition_digest,
                    required_capability_ids=target.profile_definition.all_required_capability_ids,
                    receipt_digests=receipt_digests,
                    issued_at=ci_verified_at,
                )
                async with maker() as session:
                    artifacts = list(
                        await session.scalars(
                            select(AuthoredReleaseArtifactV3).where(
                                AuthoredReleaseArtifactV3.export_attempt_id == hybrid_attempt_id
                            )
                        )
                    )
                    assert len(artifacts) == 70
                    assert len({artifact.artifact_id for artifact in artifacts}) == 70
                    assert all(artifact.state == "bound" for artifact in artifacts)

            projection = await client.get(V3_PROJECTION_PATH.format(presentation_id=presentation_id))
            assert projection.status_code == 200, projection.text
            result = projection.json()
            assert evaluate_gate0_projection(result)["ready"] is actual_cjk_evidence, result
            assert result["releaseProfile"]["promotionReady"] is actual_cjk_evidence, result
            await engine.dispose()

    asyncio.run(scenario())
