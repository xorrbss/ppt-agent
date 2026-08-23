"""Immutable, signed CI evidence for bounded-Hybrid release qualification."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
import os
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.readiness import CapabilityReceiptIssueRequestV2
from models.authored_vnext.semantic.release_evidence_v3 import (
    BitbucketRequiredChecksReceiptV3,
    CiEvidenceArtifactPublishRequestV3,
    CiEvidenceArtifactV3,
)
from models.sql.authored_semantic_bundle import AuthoredSemanticArtifact
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
    canonical_json,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    consume_authority_nonce_v3,
    validate_actual_product_authority_v3,
)
from utils.datetime_utils import get_current_utc_datetime


_CI_POLICY_ID = "authored-trusted-capability/ci-required-checks/v1"
_MAX_CI_EVIDENCE_AGE = timedelta(hours=72)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _ci_key() -> bytes:
    value = os.getenv("AUTHORED_RELEASE_CI_EVIDENCE_HMAC_KEY", "")
    if len(value.encode("utf-8")) < 32:
        raise SemanticContractError("authored_ci_evidence_signing_key_unconfigured")
    return value.encode("utf-8")


def _expected_commit() -> str:
    value = os.getenv("AUTHORED_RELEASE_BACKEND_COMMIT_SHA", "").strip().lower()
    if len(value) not in {40, 64} or any(character not in "0123456789abcdef" for character in value):
        raise SemanticContractError("authored_ci_evidence_backend_commit_unconfigured")
    return value


def _unsigned_report(report: BitbucketRequiredChecksReceiptV3) -> dict[str, Any]:
    value = report.model_dump(mode="json", by_alias=True)
    value.pop("receiptDigest", None)
    value.pop("authoritySignature", None)
    return value


def validate_ci_evidence_report_v3(
    report: BitbucketRequiredChecksReceiptV3,
    *,
    now: datetime | None = None,
) -> None:
    expected_digest = canonical_digest(_unsigned_report(report))
    expected_signature = "hmac-sha256:" + hmac.new(
        _ci_key(), expected_digest.encode("utf-8"), sha256
    ).hexdigest()
    observed_at = _utc(now or get_current_utc_datetime())
    verified_at = _utc(report.verified_at)
    if (
        report.commit != _expected_commit()
        or report.receipt_digest != expected_digest
        or not hmac.compare_digest(report.authority_signature, expected_signature)
        or verified_at > observed_at + timedelta(minutes=5)
        or observed_at - verified_at > _MAX_CI_EVIDENCE_AGE
    ):
        raise SemanticContractError("authored_ci_evidence_authority_invalid")


async def publish_ci_evidence_artifact_v3(
    session: AsyncSession,
    *,
    request: CiEvidenceArtifactPublishRequestV3,
    publisher_principal_id: uuid.UUID | None,
    publisher_is_admin: bool,
    tenant_id: str,
) -> CiEvidenceArtifactV3:
    if publisher_principal_id is None or not publisher_is_admin:
        raise SemanticContractError("authored_capability_receipt_admin_required")
    validate_ci_evidence_report_v3(request.receipt)
    receipt_wire = request.receipt.model_dump(mode="json", by_alias=True)
    authority_digest = validate_actual_product_authority_v3(
        receipt_wire, request.authority
    )
    await consume_authority_nonce_v3(
        session, authority=request.authority, subject_digest=authority_digest
    )
    content = canonical_json(request).encode("utf-8")
    artifact_digest = "sha256:" + sha256(content).hexdigest()
    artifact_ref = f"artifact://sha256/{artifact_digest.removeprefix('sha256:')}"
    artifact = await session.scalar(
        select(AuthoredSemanticArtifact).where(
            AuthoredSemanticArtifact.owner_id == publisher_principal_id,
            AuthoredSemanticArtifact.tenant_id == tenant_id,
            AuthoredSemanticArtifact.content_digest == artifact_digest,
        )
    )
    if artifact is None:
        artifact = AuthoredSemanticArtifact(
            owner_id=publisher_principal_id,
            tenant_id=tenant_id,
            artifact_ref=artifact_ref,
            content_digest=artifact_digest,
            byte_length=len(content),
            media_type="application/json",
            resolver_version="authored-ci-evidence/v3",
            retention_class="evidence",
            content_bytes=content,
        )
        session.add(artifact)
        await session.flush()
    elif (
        artifact.artifact_ref != artifact_ref
        or bytes(artifact.content_bytes) != content
        or artifact.byte_length != len(content)
        or artifact.resolver_version != "authored-ci-evidence/v3"
    ):
        raise SemanticContractError("authored_ci_evidence_artifact_collision")
    return CiEvidenceArtifactV3(
        evidence_bundle_id=artifact.id,
        artifact_ref=artifact_ref,
        artifact_digest=artifact_digest,
        receipt_digest=request.receipt.receipt_digest,
        authority_digest=authority_digest,
        byte_length=len(content),
    )


async def validate_ci_required_checks_evidence_v3(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: Any,
    tenant_id: str,
) -> None:
    from services.authored_vnext.semantic.readiness_validators import (
        trusted_capability_feature_set_digest,
    )

    if (
        not target.configured
        or request.capability_id != "ci.requiredChecks"
        or request.scope != target.scope
        or request.environment != target.environment
        or request.serializer_id != target.scope.renderer
        or request.serializer_version != target.profile_definition.serializer_version
        or request.policy_digest != target.profile_definition_digest
        or request.evidence_policy_id != _CI_POLICY_ID
        or request.feature_set_digest
        != trusted_capability_feature_set_digest(request.capability_id, target)
    ):
        raise SemanticContractError("authored_ci_evidence_authority_scope_mismatch")
    artifact = await session.get(AuthoredSemanticArtifact, request.evidence_bundle_id)
    if (
        artifact is None
        or artifact.tenant_id != tenant_id
        or artifact.resolver_version != "authored-ci-evidence/v3"
        or artifact.media_type != "application/json"
        or artifact.content_digest
        != "sha256:" + sha256(artifact.content_bytes).hexdigest()
    ):
        raise SemanticContractError("authored_ci_evidence_artifact_unresolvable")
    try:
        published = CiEvidenceArtifactPublishRequestV3.model_validate_json(
            bytes(artifact.content_bytes), strict=False
        )
    except (UnicodeError, ValidationError, ValueError) as error:
        raise SemanticContractError("authored_ci_evidence_artifact_invalid") from error
    report = published.receipt
    validate_ci_evidence_report_v3(report)
    receipt_wire = report.model_dump(mode="json", by_alias=True)
    validate_actual_product_authority_v3(receipt_wire, published.authority)
    if (
        request.evidence_digest != report.receipt_digest
        or _utc(request.expires_at) > _utc(report.verified_at) + _MAX_CI_EVIDENCE_AGE
    ):
        raise SemanticContractError("authored_ci_evidence_receipt_claim_invalid")


__all__ = [
    "publish_ci_evidence_artifact_v3",
    "validate_ci_evidence_report_v3",
    "validate_ci_required_checks_evidence_v3",
]
