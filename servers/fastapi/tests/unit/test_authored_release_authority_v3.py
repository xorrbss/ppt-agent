from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.authored_vnext.semantic.readiness import CapabilityReceiptIssueRequestV2
from models.authored_vnext.semantic.release_authority_v3 import (
    CapabilityReceiptIssueRequestV3,
    ComQualificationCreateRequestV3,
    ComQualificationTransitionRequestV3,
    DurableArtifactPutRequestV3,
    EvidenceIssuerEnvelopeV3,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_release_evidence_v3 import (
    AuthoredComQualificationJobV3,
    AuthoredReleaseArtifactV3,
    AuthoredReleaseNonceV3,
)
from models.sql.authored_semantic_bundle import AuthoredSemanticExportBinding
from services.authored_vnext.semantic.canonical import SemanticContractError, canonical_digest
from services.authored_vnext.semantic import release_authority_v3 as release_authority_service
from services.authored_vnext.semantic.qualification_v3 import (
    bind_durable_artifact_v3,
    claim_com_qualification_job_v3,
    create_com_qualification_job_v3,
    put_durable_artifact_v3,
    transition_com_qualification_job_v3,
)
from services.authored_vnext.semantic.readiness_v3 import (
    issue_capability_receipt_v3,
    release_authority_target_v3_from_environment,
)
from services.authored_vnext.semantic.readiness_validators import (
    trusted_capability_evidence_policy_id,
    trusted_capability_feature_set_digest,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    BoundEvidenceSubjectRejectionV3,
    actual_product_authority_digest,
    actual_product_canonical_digest_v2,
    build_bound_capability_subject_v3,
    issue_actual_product_capability_receipt_v3,
    validate_actual_product_record_v3,
    validate_server_readback_v3,
)


def _configure(monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    entries = []
    for evidence_class, purpose in (
        ("actual_product_capability", "actual-product-capability"),
        ("actual_product_com_qualification", "actual-product-com-qualification"),
    ):
        entries.append(
            {
                "authorityDomain": "actual_product",
                "issuerId": "release.worker",
                "signerKeyId": f"release.{evidence_class}.key-1",
                "environmentId": "release-test",
                "keyPurpose": purpose,
                "rotationEpoch": 1,
                "algorithm": "Ed25519",
                "revoked": False,
                "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
            }
        )
    monkeypatch.setenv("AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID", "release-test")
    monkeypatch.setenv("AUTHORED_RELEASE_SIGNER_TRUST_BUNDLE_JSON", json.dumps(entries))
    monkeypatch.setenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("AUTHORED_RELEASE_OS_BUILD", "windows-11-24h2")
    monkeypatch.setenv("AUTHORED_RELEASE_OFFICE_BUILD", "powerpoint-16")
    for name, digit in (
        ("AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST", "1"),
        ("AUTHORED_RELEASE_FONT_INVENTORY_DIGEST", "2"),
        ("AUTHORED_RELEASE_BACKEND_BUILD_DIGEST", "3"),
    ):
        monkeypatch.setenv(name, "sha256:" + digit * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_ENVIRONMENT_ATTESTATION_DIGEST", "sha256:" + "4" * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_WORKER_ATTESTATION_DIGEST", "sha256:" + "5" * 64)
    return private_key


def _authority(
    private_key: Ed25519PrivateKey,
    payload: dict,
    *,
    evidence_class: str,
    nonce: str,
    now: datetime,
) -> EvidenceIssuerEnvelopeV3:
    purpose = {
        "actual_product_capability": "actual-product-capability",
        "actual_product_com_qualification": "actual-product-com-qualification",
    }[evidence_class]
    envelope = EvidenceIssuerEnvelopeV3(
        authority_domain="actual_product",
        evidence_class=evidence_class,
        issuer_id="release.worker",
        signer_key_id=f"release.{evidence_class}.key-1",
        environment_id="release-test",
        key_purpose=purpose,
        rotation_epoch=1,
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
        nonce=nonce,
        signature_algorithm="Ed25519",
        signature_base64=base64.b64encode(b"0" * 64).decode("ascii"),
    )
    digest = actual_product_authority_digest(payload, envelope)
    return envelope.model_copy(
        update={"signature_base64": base64.b64encode(private_key.sign(digest.encode("ascii"))).decode("ascii")}
    )


def test_actual_product_authority_digest_matches_gate1_node_canonical_v2() -> None:
    now = datetime(2026, 8, 23, 19, 1, 27, tzinfo=timezone.utc)
    envelope = EvidenceIssuerEnvelopeV3(
        authority_domain="actual_product",
        evidence_class="actual_product_completion",
        issuer_id="completion.issuer",
        signer_key_id="completion.key-1",
        environment_id="release-test",
        key_purpose="actual-product-completion",
        rotation_epoch=1,
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
        nonce="cross_runtime_authority_nonce_0001",
        signature_algorithm="Ed25519",
        signature_base64=base64.b64encode(b"0" * 64).decode("ascii"),
    )
    receipt = {
        "schemaVersion": "authored-cross-runtime-authority-fixture/v1",
        "artifactCount": 70,
        "byteLength": 29_370_701,
        "presentationRevision": 3,
        "normalizedText": "분기별\r\n운영 지표",
        "orderedCounts": [1, 67, 29_370_701],
    }
    authority = envelope.model_dump(mode="json", by_alias=True)
    authority.pop("signatureBase64")
    signed_payload = {"receipt": receipt, "authority": authority}

    node_script = """
import fs from "node:fs";
import { canonicalSha256 } from "./scripts/authored-bounded-release-contracts.mjs";
const payload = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(canonicalSha256(payload));
"""
    repository_root = Path(__file__).resolve().parents[4]
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", node_script],
        cwd=repository_root,
        input=json.dumps(signed_payload, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
    )

    digest = actual_product_authority_digest(receipt, envelope)
    assert digest == completed.stdout
    assert digest != canonical_digest(signed_payload)


@pytest.mark.parametrize(
    ("evidence_class", "key_purpose"),
    [
        ("actual_product_ci", "actual-product-ci"),
        ("actual_product_completion", "actual-product-completion"),
    ],
)
def test_capability_request_rejects_non_capability_signer_namespace(evidence_class: str, key_purpose: str) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    receipt = CapabilityReceiptIssueRequestV2.model_construct()
    authority = EvidenceIssuerEnvelopeV3(
        authority_domain="actual_product",
        evidence_class=evidence_class,
        issuer_id="release.worker",
        signer_key_id="release.wrong-purpose.key-1",
        environment_id="release-test",
        key_purpose=key_purpose,
        rotation_epoch=1,
        issued_at=now,
        expires_at=now + timedelta(minutes=15),
        nonce="wrong_purpose_nonce_0001",
        signature_algorithm="Ed25519",
        signature_base64=base64.b64encode(b"0" * 64).decode("ascii"),
    )
    with pytest.raises(ValueError, match="authored_capability_receipt_authority_class_invalid"):
        CapabilityReceiptIssueRequestV3(
            receipt=receipt,
            export_attempt_id=uuid.uuid4(),
            binding_digest="sha256:" + "9" * 64,
            authority=authority,
        )


def test_actual_product_signer_namespace_nonce_and_structural_non_promotion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key = _configure(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'authority.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AuthoredCapabilityReceiptRecordV2.__table__,
                    AuthoredReleaseNonceV3.__table__,
                    AuthoredSemanticExportBinding.__table__,
                ],
            )
        target = release_authority_target_v3_from_environment()
        capability_id = "renderer.attestation"
        request = CapabilityReceiptIssueRequestV2(
            capability_id=capability_id,
            state="ready",
            scope=target.scope,
            environment=target.environment,
            serializer_id=target.scope.renderer,
            serializer_version=target.profile_definition.serializer_version,
            feature_set_digest=trusted_capability_feature_set_digest(capability_id, target),
            evidence_policy_id=trusted_capability_evidence_policy_id(capability_id),
            policy_digest=target.profile_definition_digest,
            evidence_bundle_id=uuid.uuid4(),
            evidence_digest="sha256:" + "4" * 64,
            expires_at=now + timedelta(minutes=10),
            reason_code="actual_product_qualified",
        )

        async def accept(*args, **kwargs) -> None:
            return None

        async with maker() as session:
            structural = await issue_capability_receipt_v3(
                session,
                request=request,
                issuer_principal_id=uuid.uuid4(),
                issuer_is_admin=True,
                tenant_id="structural",
                now=now,
                target=target,
                capability_validators={capability_id: accept},
            )
            structural_record = await session.scalar(
                select(AuthoredCapabilityReceiptRecordV2).where(
                    AuthoredCapabilityReceiptRecordV2.receipt_digest == structural.receipt_digest
                )
            )
            assert structural_record is not None
            assert structural_record.authority_domain == "structural_test"
            assert validate_actual_product_record_v3(structural_record, now=now) is False

        wire = request.model_dump(mode="json", by_alias=True)
        export_attempt_id = uuid.uuid4()
        binding_digest = "sha256:" + "9" * 64
        binding_record = AuthoredSemanticExportBinding(
            owner_id=uuid.uuid4(),
            tenant_id="actual",
            export_attempt_id=export_attempt_id,
            presentation_id=uuid.uuid4(),
            presentation_revision=1,
            canonical_digest="sha256:" + "1" * 64,
            renderer_source_digest="sha256:" + "2" * 64,
            mode="hybrid",
            serializer_id="pptxgenjs-powerpoint",
            serializer_version="v1",
            pptx_digest="sha256:" + "3" * 64,
            binding_digest=binding_digest,
            payload={},
        )
        envelope = _authority(
            private_key,
            {},
            evidence_class="actual_product_capability",
            nonce="actual_nonce_000000000001",
            now=now,
        )
        bound_subject = build_bound_capability_subject_v3(
            receipt_wire=wire,
            authority=envelope,
            binding=binding_record,
            target=target,
            tenant_id="actual",
        )
        envelope = _authority(
            private_key,
            bound_subject,
            evidence_class="actual_product_capability",
            nonce="actual_nonce_000000000001",
            now=now,
        )
        wrapped = CapabilityReceiptIssueRequestV3(
            receipt=request,
            export_attempt_id=export_attempt_id,
            binding_digest=binding_digest,
            authority=envelope,
        )
        validator_calls: list[dict] = []
        shared_validator = release_authority_service.validate_bound_evidence_subject_v3

        def observed_validator(candidate, **kwargs):
            validator_calls.append(dict(candidate))
            return shared_validator(candidate, **kwargs)

        monkeypatch.setattr(release_authority_service, "validate_bound_evidence_subject_v3", observed_validator)
        async with maker() as session:
            session.add(binding_record)
            await session.flush()
            receipt = await issue_actual_product_capability_receipt_v3(
                session,
                request=wrapped,
                issuer_principal_id=uuid.uuid4(),
                issuer_is_admin=True,
                tenant_id="actual",
                now=now,
                target=target,
                capability_validators={capability_id: accept},
            )
            replay = await issue_actual_product_capability_receipt_v3(
                session,
                request=wrapped,
                issuer_principal_id=uuid.uuid4(),
                issuer_is_admin=True,
                tenant_id="actual",
                now=now,
                target=target,
                capability_validators={capability_id: accept},
            )
            assert replay.receipt_digest == receipt.receipt_digest
            record = await session.scalar(
                select(AuthoredCapabilityReceiptRecordV2).where(
                    AuthoredCapabilityReceiptRecordV2.receipt_digest == receipt.receipt_digest
                )
            )
            assert record is not None
            assert validate_actual_product_record_v3(record, now=now) is True
            assert validator_calls[-1] == bound_subject
            with pytest.raises(BoundEvidenceSubjectRejectionV3, match="evidence-binding-digest-mismatch"):
                shared_validator(
                    {**bound_subject, "bindingDigest": "sha256:" + "8" * 64},
                    expected=bound_subject,
                    phase="payload-before-issue",
                    now=now,
                )
            representative_mutations = (
                ("authorityDomain", "structural_test", "evidence-authority-domain-not-allowed", "payload-before-issue"),
                ("tenantId", "other", "evidence-tenant-scope-mismatch", "payload-before-issue"),
                ("manifestDigest", "sha256:" + "6" * 64, "evidence-manifest-digest-mismatch", "payload-before-issue"),
                ("inputPptxDigest", "sha256:" + "7" * 64, "evidence-input-pptx-mismatch", "payload-before-issue"),
                ("allRequiredCapabilityIds", [], "capability-set-incomplete", "payload-before-issue"),
            )
            for field, value, code, phase in representative_mutations:
                candidate = deepcopy(bound_subject)
                candidate[field] = value
                with pytest.raises(BoundEvidenceSubjectRejectionV3, match=code):
                    shared_validator(candidate, expected=bound_subject, phase=phase, now=now)
            font_candidate = deepcopy(bound_subject)
            font_candidate["fontInventory"]["faces"][0]["sourceContainerSha256"] = "sha256:" + "8" * 64
            with pytest.raises(BoundEvidenceSubjectRejectionV3, match="font-source-byte-mismatch"):
                shared_validator(font_candidate, expected=bound_subject, phase="payload-before-issue", now=now)
            artifact_candidate = deepcopy(bound_subject)
            artifact_candidate["artifactReference"]["versionId"] = str(uuid.uuid4())
            with pytest.raises(BoundEvidenceSubjectRejectionV3, match="durable-artifact-version-mismatch"):
                shared_validator(artifact_candidate, expected=bound_subject, phase="post-issue", now=now)

            mutated = request.model_copy(update={"reason_code": "actual_product_changed"})
            mutated_wire = mutated.model_dump(mode="json", by_alias=True)
            mutated_subject = {**bound_subject, "receipt": mutated_wire}
            reused_nonce = _authority(
                private_key,
                mutated_subject,
                evidence_class="actual_product_capability",
                nonce="actual_nonce_000000000001",
                now=now,
            )
            with pytest.raises(SemanticContractError, match="authored_evidence_nonce_replay"):
                await issue_actual_product_capability_receipt_v3(
                    session,
                    request=CapabilityReceiptIssueRequestV3(
                        receipt=mutated,
                        export_attempt_id=wrapped.export_attempt_id,
                        binding_digest=wrapped.binding_digest,
                        authority=reused_nonce,
                    ),
                    issuer_principal_id=uuid.uuid4(),
                    issuer_is_admin=True,
                    tenant_id="actual",
                    now=now,
                    target=target,
                    capability_validators={capability_id: accept},
                )
        await engine.dispose()

    asyncio.run(scenario())


def test_server_readback_rejects_wrong_nonce_and_stale_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = Ed25519PrivateKey.generate()
    private_bytes = private_key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    monkeypatch.setenv("AUTHORED_RELEASE_GATE1_EXECUTION_NONCE", "sealed_gate1_nonce_20260823")
    monkeypatch.setenv(
        "AUTHORED_RELEASE_READBACK_ED25519_PRIVATE_KEY_BASE64",
        base64.b64encode(private_bytes).decode("ascii"),
    )
    monkeypatch.setattr(
        release_authority_service,
        "READBACK_PUBLIC_KEY_DIGEST_V3",
        "sha256:" + sha256(public_bytes).hexdigest(),
    )
    with pytest.raises(SemanticContractError, match="authored_release_readback_execution_nonce_invalid"):
        release_authority_service.sign_server_readback_v3(
            {"schemaVersion": "test/v1"}, execution_nonce="wrong_gate1_nonce_20260823"
        )
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stale = release_authority_service.sign_server_readback_v3(
        {"schemaVersion": "test/v1", "artifactCount": 70, "byteLength": 69_329},
        execution_nonce="sealed_gate1_nonce_20260823",
        now=now - timedelta(minutes=10),
    )
    unsigned = dict(stale)
    for field in ("serverReadbackDigest", "readbackSignerKeyId", "readbackSignatureBase64"):
        unsigned.pop(field)
    assert stale["serverReadbackDigest"] == actual_product_canonical_digest_v2(unsigned)
    assert stale["serverReadbackDigest"] != canonical_digest(unsigned)
    with pytest.raises(SemanticContractError, match="authored_release_readback_stale"):
        validate_server_readback_v3(
            stale,
            execution_nonce="sealed_gate1_nonce_20260823",
            public_key_bytes=public_bytes,
            now=now,
        )


def test_durable_artifact_cas_and_com_failure_closure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    private_key = _configure(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'qualification.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AuthoredReleaseArtifactV3.__table__,
                    AuthoredReleaseNonceV3.__table__,
                    AuthoredComQualificationJobV3.__table__,
                ],
            )
        content = b"immutable-pptx-evidence"
        digest = "sha256:" + sha256(content).hexdigest()
        export_attempt_id = uuid.uuid4()
        put = DurableArtifactPutRequestV3(
            export_attempt_id=export_attempt_id,
            artifact_id="pptx/input",
            sha256=digest,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            encryption_key_ref="kms://release/evidence",
            storage_generation=1,
            content_base64=base64.b64encode(content).decode("ascii"),
        )
        async with maker() as session:
            artifact = await put_durable_artifact_v3(session, request=put, tenant_id="t")
            replay = await put_durable_artifact_v3(session, request=put, tenant_id="t")
            assert replay.version_id == artifact.version_id
            conflict = put.model_copy(
                update={
                    "sha256": "sha256:" + sha256(b"changed").hexdigest(),
                    "content_base64": base64.b64encode(b"changed").decode("ascii"),
                }
            )
            with pytest.raises(SemanticContractError, match="authored_release_artifact_cas_conflict"):
                await put_durable_artifact_v3(session, request=conflict, tenant_id="t")
            with pytest.raises(SemanticContractError, match="authored_release_artifact_binding_mismatch"):
                await bind_durable_artifact_v3(
                    session,
                    artifact_id="pptx/input",
                    export_attempt_id=export_attempt_id,
                    expected_version_id=artifact.version_id,
                    expected_sha256="sha256:" + "0" * 64,
                    tenant_id="t",
                )
            assert (
                await bind_durable_artifact_v3(
                    session,
                    artifact_id="pptx/input",
                    export_attempt_id=export_attempt_id,
                    expected_version_id=artifact.version_id,
                    expected_sha256=digest,
                    tenant_id="t",
                )
            ).state == "bound"
            other_attempt = uuid.uuid4()
            other = await put_durable_artifact_v3(
                session,
                request=put.model_copy(update={"export_attempt_id": other_attempt}),
                tenant_id="t",
            )
            assert other.version_id != artifact.version_id
            with pytest.raises(
                SemanticContractError,
                match="authored_release_artifact_binding_mismatch",
            ):
                await bind_durable_artifact_v3(
                    session,
                    artifact_id="pptx/input",
                    export_attempt_id=other_attempt,
                    expected_version_id=artifact.version_id,
                    expected_sha256=digest,
                    tenant_id="t",
                )

            job = await create_com_qualification_job_v3(
                session,
                request=ComQualificationCreateRequestV3(
                    export_attempt_id=uuid.uuid4(), evidence_policy_digest="sha256:" + "9" * 64
                ),
                tenant_id="t",
            )
            claimed = await claim_com_qualification_job_v3(
                session, job_id=job.job_id, worker_id="release.worker", tenant_id="t", now=now
            )
            current = claimed
            for index, next_state in enumerate(
                (
                    "planned",
                    "assembled",
                    "inputOoxmlVerified",
                    "firstOpenObserved",
                    "savedCopyVerified",
                    "reopenObserved",
                    "comVerified",
                    "sealed",
                ),
                start=1,
            ):
                evidence = {}
                if next_state == "sealed":
                    evidence = {
                        "inputPptxDigest": digest,
                        "savedCopyPptxDigest": digest,
                        "reopenInputPptxDigest": digest,
                        "firstOpenSnapshotDigest": "sha256:" + "1" * 64,
                        "reopenSnapshotDigest": "sha256:" + "2" * 64,
                        "workerAttestationDigest": "sha256:" + "3" * 64,
                        "environmentAttestationDigest": "sha256:" + "4" * 64,
                        "repairDialogs": 0,
                        "powerPointSurvivorPids": [10576],
                        "desktopInterference": False,
                        "ooxmlRepairObserved": False,
                    }
                draft = {
                    "schemaVersion": "authored-com-qualification-transition/v3",
                    "workerId": "release.worker",
                    "leaseToken": claimed.lease_token,
                    "expectedState": current.state,
                    "expectedTransitionSequence": current.transition_sequence,
                    "nextState": next_state,
                    "evidence": evidence,
                    "failureCode": None,
                }
                authority = _authority(
                    private_key,
                    draft,
                    evidence_class="actual_product_com_qualification",
                    nonce=f"com_nonce_{index:020d}",
                    now=now,
                )
                current = await transition_com_qualification_job_v3(
                    session,
                    job_id=job.job_id,
                    request=ComQualificationTransitionRequestV3.model_validate(
                        {**draft, "authority": authority.model_dump(mode="json", by_alias=True)}
                    ),
                    tenant_id="t",
                    now=now,
                )
            assert current.state == "rejected"
            assert current.failure_code == "powerpoint_process_survived"
        await engine.dispose()

    asyncio.run(scenario())
