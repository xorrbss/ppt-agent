from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import subprocess
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.authored_vnext.semantic.readiness import CapabilityReceiptIssueRequestV2
from models.authored_vnext.semantic.release_evidence_v3 import (
    BitbucketRequiredChecksReceiptV3,
    CiEvidenceArtifactPublishRequestV3,
)
from models.authored_vnext.semantic.release_authority_v3 import EvidenceIssuerEnvelopeV3
from models.sql.authored_release_evidence_v3 import (
    AuthoredCiEvidenceIdentityV3,
    AuthoredReleaseNonceV3,
)
from models.sql.authored_semantic_bundle import (
    AuthoredSemanticArtifact,
    AuthoredSemanticExportBinding,
)
from services.authored_vnext.semantic.canonical import SemanticContractError
from services.authored_vnext.semantic.readiness_v3 import (
    release_authority_target_v3_from_environment,
)
from services.authored_vnext.semantic.readiness_validators import (
    trusted_capability_feature_set_digest,
)
from services.authored_vnext.semantic.release_evidence_v3 import (
    get_ci_evidence_artifact_v3,
    publish_ci_evidence_artifact_v3,
    validate_ci_evidence_report_v3,
    validate_ci_required_checks_evidence_v3,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    actual_product_canonical_digest_v2,
    actual_product_canonical_json_v2,
)
import services.authored_vnext.semantic.release_evidence_v3 as release_evidence_service


def _configure(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str, Ed25519PrivateKey]:
    commit = "a" * 40
    key = "test-only-ci-evidence-key-32-bytes-minimum"
    monkeypatch.setenv("AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY", key)
    monkeypatch.setenv("AUTHORED_RELEASE_BACKEND_COMMIT_SHA", commit)
    monkeypatch.setenv("AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST", "sha256:" + "4" * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST", "sha256:" + "5" * 64)
    monkeypatch.setenv("AUTHORED_RELEASE_GATE1_EXECUTION_NONCE", "gate1_execution_nonce_test_v3")
    monkeypatch.setenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("AUTHORED_RELEASE_OS_BUILD", "windows-11-24h2")
    monkeypatch.setenv("AUTHORED_RELEASE_OFFICE_BUILD", "powerpoint-16.0.19029")
    for name, digit in (
        ("AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST", "1"),
        ("AUTHORED_RELEASE_FONT_INVENTORY_DIGEST", "2"),
        ("AUTHORED_RELEASE_BACKEND_BUILD_DIGEST", "3"),
    ):
        monkeypatch.setenv(name, "sha256:" + digit * 64)
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    monkeypatch.setenv("AUTHORED_RELEASE_AUTHORITY_ENVIRONMENT_ID", "release-test")
    monkeypatch.setenv(
        "AUTHORED_RELEASE_SIGNER_TRUST_BUNDLE_JSON",
        json.dumps(
            [
                {
                    "authorityDomain": "actual_product",
                    "issuerId": "ci.verifier",
                    "signerKeyId": "ci.release.key-1",
                    "environmentId": "release-test",
                    "keyPurpose": "actual-product-ci",
                    "rotationEpoch": 1,
                    "algorithm": "Ed25519",
                    "revoked": False,
                    "publicKeyBase64": base64.b64encode(public_key).decode("ascii"),
                }
            ]
        ),
    )
    return commit, key, private_key


def _ci_authority(
    report: BitbucketRequiredChecksReceiptV3,
    private_key: Ed25519PrivateKey,
    now: datetime,
    *,
    nonce: str = "ci_nonce_0000000000000001",
) -> EvidenceIssuerEnvelopeV3:
    unsigned = {
        "schemaVersion": "authored-evidence-issuer-envelope/v3",
        "authorityDomain": "actual_product",
        "evidenceClass": "actual_product_ci",
        "issuerId": "ci.verifier",
        "signerKeyId": "ci.release.key-1",
        "environmentId": "release-test",
        "keyPurpose": "actual-product-ci",
        "rotationEpoch": 1,
        "issuedAt": now,
        "expiresAt": now + timedelta(minutes=15),
        "nonce": nonce,
        "signatureAlgorithm": "Ed25519",
    }
    digest = actual_product_canonical_digest_v2(
        {
            "receipt": report.model_dump(mode="json", by_alias=True),
            "authority": EvidenceIssuerEnvelopeV3.model_validate(
                {**unsigned, "signatureBase64": base64.b64encode(b"0" * 64).decode("ascii")}
            ).model_dump(mode="json", by_alias=True, exclude={"signature_base64"}),
        }
    )
    return EvidenceIssuerEnvelopeV3.model_validate(
        {
            **unsigned,
            "signatureBase64": base64.b64encode(private_key.sign(digest.encode("ascii"))).decode("ascii"),
        }
    )


def _report(
    commit: str,
    key: str,
    now: datetime,
    *,
    pipeline_uuid: str = "{pipeline-42}",
    commit_status_key: str = "PIPELINE",
) -> BitbucketRequiredChecksReceiptV3:
    unsigned = {
        "schemaVersion": "authored-bitbucket-required-check-receipt/v1",
        "repository": "ibizsoftware/devax-ppt",
        "commit": commit,
        "pipelineUuid": pipeline_uuid,
        "protectedBranches": ["main"],
        "requiredPipelineSteps": [
            "Authored release readiness configuration",
            "Dashboard component gate",
            "Run shared Next.js CI Node suite",
            "Upstream byte-parity verification",
            "Workspace chat-first browser E2E",
        ],
        "successfulStepNames": [
            "Authored release readiness configuration",
            "Dashboard component gate",
            "Run shared Next.js CI Node suite",
            "Upstream byte-parity verification",
            "Workspace chat-first browser E2E",
        ],
        "paginationComplete": True,
        "latestStatePerStepSuccessful": True,
        "successfulCommitStatusKeys": [commit_status_key],
        "status": "verified",
        "verifiedAt": now.isoformat().replace("+00:00", "Z"),
        "expiresAt": (now + timedelta(minutes=15)).isoformat().replace("+00:00", "Z"),
        "attemptBinding": {
            "schemaVersion": "authored-ci-attempt-binding/v3",
            "presentationId": "00000000-0000-4000-8000-000000000101",
            "presentationRevision": 3,
            "generationAttemptId": "00000000-0000-4000-8000-000000000102",
            "fidelityExportAttemptId": "00000000-0000-4000-8000-000000000103",
            "hybridExportAttemptId": "00000000-0000-4000-8000-000000000104",
            "activeManifestDigest": "sha256:" + "6" * 64,
            "sourceTreeCensusDigest": "sha256:" + "4" * 64,
            "sourceHeadCommit": commit,
            "evidencePolicyDigest": "sha256:" + "5" * 64,
            "executionNonce": "gate1_execution_nonce_test_v3",
        },
    }
    digest = actual_product_canonical_digest_v2(unsigned)
    signature = "hmac-sha256:" + hmac.new(
        key.encode(), digest.encode(), sha256
    ).hexdigest()
    return BitbucketRequiredChecksReceiptV3.model_validate(
        {
            **unsigned,
            "verifiedAt": now,
            "receiptDigest": digest,
            "authoritySignature": signature,
        },
        strict=False,
    )


def test_signed_ci_evidence_is_immutable_and_commit_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit, key, private_key = _configure(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _report(commit, key, now)
    validate_ci_evidence_report_v3(report, now=now)
    js_receipt_wire = report.model_dump(mode="json", by_alias=True)
    assert js_receipt_wire["verifiedAt"] == now.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert js_receipt_wire["expiresAt"] == (now + timedelta(minutes=15)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    roundtrip_receipt = BitbucketRequiredChecksReceiptV3.model_validate(
        js_receipt_wire, strict=False
    )
    assert roundtrip_receipt.model_dump(mode="json", by_alias=True) == js_receipt_wire
    js_publish_wire = {
        "schemaVersion": "authored-ci-evidence-artifact-publish/v3",
        "receipt": js_receipt_wire,
        "authority": _ci_authority(report, private_key, now).model_dump(
            mode="json", by_alias=True
        ),
    }
    roundtrip_publish = CiEvidenceArtifactPublishRequestV3.model_validate(
        js_publish_wire, strict=False
    ).model_dump(mode="json", by_alias=True)
    assert actual_product_canonical_json_v2(roundtrip_publish) == (
        actual_product_canonical_json_v2(js_publish_wire)
    )
    assert actual_product_canonical_digest_v2(roundtrip_publish) == (
        actual_product_canonical_digest_v2(js_publish_wire)
    )
    unsigned = report.model_dump(mode="json", by_alias=True)
    unsigned.pop("receiptDigest")
    unsigned.pop("authoritySignature")
    millisecond_unsigned = {
        **unsigned,
        "verifiedAt": unsigned["verifiedAt"].replace("Z", ".000Z"),
        "expiresAt": unsigned["expiresAt"].replace("Z", ".000Z"),
    }
    millisecond_digest = actual_product_canonical_digest_v2(millisecond_unsigned)
    millisecond_report = BitbucketRequiredChecksReceiptV3.model_validate(
        {
            **millisecond_unsigned,
            "receiptDigest": millisecond_digest,
            "authoritySignature": "hmac-sha256:"
            + hmac.new(
                key.encode(), millisecond_digest.encode(), sha256
            ).hexdigest(),
        },
        strict=False,
    )
    with pytest.raises(
        SemanticContractError, match="authored_ci_evidence_authority_invalid"
    ):
        validate_ci_evidence_report_v3(millisecond_report, now=now)
    with pytest.raises(ValueError, match="authored_ci_timestamp_not_canonical_second"):
        BitbucketRequiredChecksReceiptV3.model_validate(
            {
                **js_receipt_wire,
                "verifiedAt": unsigned["verifiedAt"].replace("Z", ".123Z"),
            },
            strict=False,
        )
    node = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                'import fs from "node:fs"; import { canonicalSha256 } from '
                '"./scripts/authored-bounded-release-contracts.mjs"; '
                'process.stdout.write(canonicalSha256(JSON.parse(fs.readFileSync(0,"utf8"))));'
            ),
        ],
        cwd=Path(__file__).resolve().parents[4],
        input=json.dumps(unsigned),
        text=True,
        capture_output=True,
        check=True,
    )
    assert node.stdout == actual_product_canonical_digest_v2(unsigned)
    unicode_report = _report(
        commit,
        key,
        now,
        pipeline_uuid="{pipeline-unicode}",
        commit_status_key="PIPELINE-e\u0301\r\nline",
    )
    unicode_unsigned = unicode_report.model_dump(mode="json", by_alias=True)
    unicode_unsigned.pop("receiptDigest")
    unicode_unsigned.pop("authoritySignature")
    unicode_node = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                'import fs from "node:fs"; import { canonicalSha256 } from '
                '"./scripts/authored-bounded-release-contracts.mjs"; '
                'process.stdout.write(canonicalSha256(JSON.parse(fs.readFileSync(0,"utf8"))));'
            ),
        ],
        cwd=Path(__file__).resolve().parents[4],
        input=json.dumps(unicode_unsigned),
        text=True,
        capture_output=True,
        check=True,
    )
    assert unicode_node.stdout == unicode_report.receipt_digest
    validate_ci_evidence_report_v3(unicode_report, now=now)
    report = unicode_report
    publish_request = CiEvidenceArtifactPublishRequestV3(
        receipt=report,
        authority=_ci_authority(report, private_key, now),
    )
    unicode_publish_wire = publish_request.model_dump(mode="json", by_alias=True)
    python_content = actual_product_canonical_json_v2(unicode_publish_wire)
    full_publish_node = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            (
                'import fs from "node:fs"; import { canonicalJson, canonicalSha256 } from '
                '"./scripts/authored-bounded-release-contracts.mjs"; '
                'const value=JSON.parse(fs.readFileSync(0,"utf8")); const content=canonicalJson(value); '
                'process.stdout.write(JSON.stringify({content,digest:canonicalSha256(value),'
                'byteLength:Buffer.byteLength(content,"utf8")}));'
            ),
        ],
        cwd=Path(__file__).resolve().parents[4],
        input=json.dumps(unicode_publish_wire),
        text=True,
        capture_output=True,
        check=True,
    )
    node_publish = json.loads(full_publish_node.stdout)
    assert node_publish == {
        "content": python_content,
        "digest": "sha256:" + sha256(python_content.encode("utf-8")).hexdigest(),
        "byteLength": len(python_content.encode("utf-8")),
    }
    short_authority = _ci_authority(report, private_key, now).model_copy(
        update={"expires_at": now + timedelta(minutes=14)}
    )
    with pytest.raises(ValueError, match="authored_ci_evidence_authority_class_invalid"):
        CiEvidenceArtifactPublishRequestV3(
            receipt=report,
            authority=short_authority,
        )

    async def accept_binding(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(release_evidence_service, "_validate_ci_attempt_binding_v3", accept_binding)

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ci.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AuthoredSemanticArtifact.__table__,
                    AuthoredReleaseNonceV3.__table__,
                    AuthoredCiEvidenceIdentityV3.__table__,
                    AuthoredSemanticExportBinding.__table__,
                ],
            )
        owner_id = uuid.uuid4()
        target = release_authority_target_v3_from_environment()
        async with maker() as session:
            binding_digest = "sha256:" + "7" * 64
            session.add(
                AuthoredSemanticExportBinding(
                    owner_id=owner_id,
                    tenant_id="default",
                    export_attempt_id=report.attempt_binding.hybrid_export_attempt_id,
                    presentation_id=report.attempt_binding.presentation_id,
                    presentation_revision=report.attempt_binding.presentation_revision,
                    canonical_digest=report.attempt_binding.active_manifest_digest,
                    renderer_source_digest="sha256:" + "8" * 64,
                    mode="hybrid",
                    serializer_id="authored-hybrid",
                    serializer_version="v3",
                    pptx_digest="sha256:" + "9" * 64,
                    binding_digest=binding_digest,
                    payload={},
                )
            )
            artifact = await publish_ci_evidence_artifact_v3(
                session,
                request=publish_request,
                publisher_principal_id=owner_id,
                publisher_is_admin=True,
                tenant_id="default",
            )
            same = await publish_ci_evidence_artifact_v3(
                session,
                request=publish_request,
                publisher_principal_id=owner_id,
                publisher_is_admin=True,
                tenant_id="default",
            )
            assert same.evidence_bundle_id == artifact.evidence_bundle_id
            assert artifact.commit == commit
            assert artifact.pipeline_uuid == report.pipeline_uuid
            assert artifact.state == "sealed"
            assert artifact.publish_request_digest == artifact.artifact_digest
            assert artifact.artifact_digest == node_publish["digest"]
            assert artifact.byte_length == node_publish["byteLength"]
            readback = await get_ci_evidence_artifact_v3(
                session,
                evidence_bundle_id=artifact.evidence_bundle_id,
                publisher_principal_id=owner_id,
                tenant_id="default",
                now=now,
            )
            assert readback == artifact
            request = CapabilityReceiptIssueRequestV2(
                capability_id="ci.requiredChecks",
                state="ready",
                scope=target.scope,
                environment=target.environment,
                serializer_id=target.scope.renderer,
                serializer_version=target.profile_definition.serializer_version,
                feature_set_digest=trusted_capability_feature_set_digest(
                    "ci.requiredChecks", target
                ),
                evidence_policy_id=(
                    "authored-trusted-capability/ci-required-checks/v1"
                ),
                policy_digest=target.profile_definition_digest,
                evidence_bundle_id=artifact.evidence_bundle_id,
                evidence_digest=report.receipt_digest,
                expires_at=now + timedelta(minutes=10),
                reason_code="ci_required_checks_verified",
            )
            await validate_ci_required_checks_evidence_v3(
                session,
                request,
                target,
                "default",
                export_attempt_id=report.attempt_binding.hybrid_export_attempt_id,
                binding_digest=binding_digest,
            )
        await engine.dispose()

    asyncio.run(scenario())
    monkeypatch.setenv("AUTHORED_RELEASE_BACKEND_COMMIT_SHA", "b" * 40)
    with pytest.raises(SemanticContractError, match="authored_ci_evidence_authority_invalid"):
        validate_ci_evidence_report_v3(report, now=now)


def test_ci_evidence_stale_and_replay_or_relabel_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit, key, private_key = _configure(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stale = _report(commit, key, now - timedelta(seconds=1801))
    with pytest.raises(SemanticContractError, match="authored_ci_evidence_authority_invalid"):
        validate_ci_evidence_report_v3(stale, now=now)

    async def accept_binding(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(release_evidence_service, "_validate_ci_attempt_binding_v3", accept_binding)

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'replay.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[
                    AuthoredSemanticArtifact.__table__,
                    AuthoredReleaseNonceV3.__table__,
                    AuthoredCiEvidenceIdentityV3.__table__,
                ],
            )
        owner_id = uuid.uuid4()
        report = _report(commit, key, now)
        async with maker() as session:
            await publish_ci_evidence_artifact_v3(
                session,
                request=CiEvidenceArtifactPublishRequestV3(
                    receipt=report,
                    authority=_ci_authority(report, private_key, now),
                ),
                publisher_principal_id=owner_id,
                publisher_is_admin=True,
                tenant_id="default",
            )
            await session.commit()

        async with maker() as session:
            with pytest.raises(SemanticContractError, match="authored_ci_evidence_receipt_replay"):
                await publish_ci_evidence_artifact_v3(
                    session,
                    request=CiEvidenceArtifactPublishRequestV3(
                        receipt=report,
                        authority=_ci_authority(
                            report,
                            private_key,
                            now,
                            nonce="ci_nonce_0000000000000002",
                        ),
                    ),
                    publisher_principal_id=owner_id,
                    publisher_is_admin=True,
                    tenant_id="default",
                )
            await session.rollback()

        relabeled = _report(commit, key, now, pipeline_uuid="{pipeline-43}")
        async with maker() as session:
            with pytest.raises(SemanticContractError, match="authored_ci_evidence_same_head_relabel"):
                await publish_ci_evidence_artifact_v3(
                    session,
                    request=CiEvidenceArtifactPublishRequestV3(
                        receipt=relabeled,
                        authority=_ci_authority(
                            relabeled,
                            private_key,
                            now,
                            nonce="ci_nonce_0000000000000003",
                        ),
                    ),
                    publisher_principal_id=owner_id,
                    publisher_is_admin=True,
                    tenant_id="default",
                )
            await session.rollback()
        await engine.dispose()

    asyncio.run(scenario())
