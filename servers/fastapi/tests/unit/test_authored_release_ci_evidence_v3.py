from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
from pathlib import Path
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.authored_vnext.semantic.readiness import CapabilityReceiptIssueRequestV2
from models.authored_vnext.semantic.release_evidence_v3 import (
    BitbucketRequiredChecksReceiptV3,
    CiEvidenceArtifactPublishRequestV3,
)
from models.sql.authored_semantic_bundle import AuthoredSemanticArtifact
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
)
from services.authored_vnext.semantic.readiness_v3 import (
    release_authority_target_v3_from_environment,
)
from services.authored_vnext.semantic.readiness_validators import (
    trusted_capability_feature_set_digest,
)
from services.authored_vnext.semantic.release_evidence_v3 import (
    publish_ci_evidence_artifact_v3,
    validate_ci_evidence_report_v3,
    validate_ci_required_checks_evidence_v3,
)


def _configure(monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    commit = "a" * 40
    key = "test-only-ci-evidence-key-32-bytes-minimum"
    monkeypatch.setenv("AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY", key)
    monkeypatch.setenv("AUTHORED_RELEASE_BACKEND_COMMIT_SHA", commit)
    monkeypatch.setenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "production")
    monkeypatch.setenv("AUTHORED_RELEASE_OS_BUILD", "windows-11-24h2")
    monkeypatch.setenv("AUTHORED_RELEASE_OFFICE_BUILD", "powerpoint-16.0.19029")
    for name, digit in (
        ("AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST", "1"),
        ("AUTHORED_RELEASE_FONT_INVENTORY_DIGEST", "2"),
        ("AUTHORED_RELEASE_BACKEND_BUILD_DIGEST", "3"),
    ):
        monkeypatch.setenv(name, "sha256:" + digit * 64)
    return commit, key


def _report(commit: str, key: str, now: datetime) -> BitbucketRequiredChecksReceiptV3:
    unsigned = {
        "schemaVersion": "authored-bitbucket-required-check-receipt/v1",
        "repository": "ibizsoftware/devax-ppt",
        "commit": commit,
        "pipelineUuid": "{pipeline-42}",
        "protectedBranches": ["main"],
        "requiredPipelineSteps": [
            "Authored release readiness configuration",
            "Run shared Next.js CI Node suite",
            "Workspace chat-first browser E2E",
        ],
        "successfulCommitStatusKeys": ["PIPELINE"],
        "verifiedAt": now.isoformat().replace("+00:00", "Z"),
    }
    digest = canonical_digest(unsigned)
    signature = "hmac-sha256:" + hmac.new(
        key.encode(), digest.encode(), sha256
    ).hexdigest()
    return BitbucketRequiredChecksReceiptV3.model_validate(
        {
            **unsigned,
            "verifiedAt": now,
            "receiptDigest": digest,
            "authoritySignature": signature,
        }
    )


def test_signed_ci_evidence_is_immutable_and_commit_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit, key = _configure(monkeypatch)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    report = _report(commit, key, now)
    validate_ci_evidence_report_v3(report, now=now)

    async def scenario() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ci.db'}")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.run_sync(
                SQLModel.metadata.create_all,
                tables=[AuthoredSemanticArtifact.__table__],
            )
        owner_id = uuid.uuid4()
        target = release_authority_target_v3_from_environment()
        async with maker() as session:
            artifact = await publish_ci_evidence_artifact_v3(
                session,
                request=CiEvidenceArtifactPublishRequestV3(receipt=report),
                publisher_principal_id=owner_id,
                publisher_is_admin=True,
                tenant_id="default",
            )
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
                expires_at=now + timedelta(hours=2),
                reason_code="ci_required_checks_verified",
            )
            await validate_ci_required_checks_evidence_v3(
                session, request, target, "default"
            )
        await engine.dispose()

    asyncio.run(scenario())
    monkeypatch.setenv("AUTHORED_RELEASE_BACKEND_COMMIT_SHA", "b" * 40)
    with pytest.raises(SemanticContractError, match="authored_ci_evidence_authority_invalid"):
        validate_ci_evidence_report_v3(report, now=now)
