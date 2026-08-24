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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.readiness import CapabilityReceiptIssueRequestV2
from models.authored_vnext.semantic.release_evidence_v3 import (
    BitbucketRequiredChecksReceiptV3,
    CiEvidenceArtifactPublishRequestV3,
    CiEvidenceArtifactV3,
)
from models.sql.authored_semantic_bundle import AuthoredSemanticArtifact
from models.sql.authored_release_evidence_v3 import AuthoredCiEvidenceIdentityV3
from models.sql.authored_semantic_bundle import (
    AuthoredDeckEmissionActivePointer,
    AuthoredDeckEmissionManifestRecord,
    AuthoredSemanticExportBinding,
)
from models.sql.presentation_export_attempt import PresentationExportAttempt
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
)
from services.authored_vnext.semantic.release_authority_v3 import (
    actual_product_canonical_digest_v2,
    actual_product_canonical_json_v2,
    consume_authority_nonce_v3,
    validate_actual_product_authority_v3,
)
from utils.datetime_utils import get_current_utc_datetime


_CI_POLICY_ID = "authored-trusted-capability/ci-required-checks/v1"
_CI_RESOLVER_VERSION = "authored-ci-evidence/v3"
_MAX_CI_EVIDENCE_AGE = timedelta(seconds=1800)
_MAX_FUTURE_SKEW = timedelta(seconds=30)


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
    expected_digest = actual_product_canonical_digest_v2(_unsigned_report(report))
    expected_signature = "hmac-sha256:" + hmac.new(
        _ci_key(), expected_digest.encode("utf-8"), sha256
    ).hexdigest()
    observed_at = _utc(now or get_current_utc_datetime())
    verified_at = _utc(report.verified_at)
    expires_at = _utc(report.expires_at)
    if (
        report.commit != _expected_commit()
        or report.receipt_digest != expected_digest
        or not hmac.compare_digest(report.authority_signature, expected_signature)
        or verified_at > observed_at + _MAX_FUTURE_SKEW
        or observed_at - verified_at > _MAX_CI_EVIDENCE_AGE
        or expires_at <= observed_at
        or expires_at - verified_at > timedelta(minutes=15)
    ):
        raise SemanticContractError("authored_ci_evidence_authority_invalid")


def _persisted_ci_request(
    artifact: AuthoredSemanticArtifact,
) -> CiEvidenceArtifactPublishRequestV3:
    content = bytes(artifact.content_bytes)
    if (
        artifact.resolver_version != _CI_RESOLVER_VERSION
        or artifact.media_type != "application/json"
        or artifact.retention_class != "evidence"
        or artifact.byte_length != len(content)
        or artifact.artifact_ref
        != f"artifact://sha256/{artifact.content_digest.removeprefix('sha256:')}"
        or artifact.content_digest != "sha256:" + sha256(content).hexdigest()
    ):
        raise SemanticContractError("authored_ci_evidence_artifact_invalid")
    try:
        return CiEvidenceArtifactPublishRequestV3.model_validate_json(content, strict=False)
    except (UnicodeError, ValidationError, ValueError) as error:
        raise SemanticContractError("authored_ci_evidence_artifact_invalid") from error


def _artifact_response(
    artifact: AuthoredSemanticArtifact,
    published: CiEvidenceArtifactPublishRequestV3,
    *,
    authority_digest: str,
) -> CiEvidenceArtifactV3:
    report = published.receipt
    return CiEvidenceArtifactV3(
        evidence_bundle_id=artifact.id,
        artifact_ref=artifact.artifact_ref,
        artifact_digest=artifact.content_digest,
        receipt_digest=report.receipt_digest,
        authority_digest=authority_digest,
        byte_length=artifact.byte_length,
        repository=report.repository,
        commit=report.commit,
        pipeline_uuid=report.pipeline_uuid,
        verified_at=report.verified_at,
        publish_request_digest=artifact.content_digest,
        attempt_binding=report.attempt_binding,
    )


async def _reject_persisted_ci_replay(
    session: AsyncSession,
    *,
    report: BitbucketRequiredChecksReceiptV3,
    tenant_id: str,
) -> None:
    identities = (
        await session.scalars(
            select(AuthoredCiEvidenceIdentityV3)
            .where(
                AuthoredCiEvidenceIdentityV3.tenant_id == tenant_id,
            )
            .with_for_update()
        )
    ).all()
    subject_digest = canonical_digest(
        report.attempt_binding.model_dump(mode="json", by_alias=True)
    )
    for identity in identities:
        if identity.receipt_digest == report.receipt_digest:
            raise SemanticContractError("authored_ci_evidence_receipt_replay")
        if identity.source_head_commit == report.commit:
            raise SemanticContractError("authored_ci_evidence_same_head_relabel")
        if identity.pipeline_uuid == report.pipeline_uuid:
            raise SemanticContractError("authored_ci_evidence_pipeline_relabel")
        if identity.attempt_subject_digest == subject_digest:
            raise SemanticContractError("authored_ci_evidence_attempt_relabel")


async def _validate_ci_attempt_binding_v3(
    session: AsyncSession,
    *,
    report: BitbucketRequiredChecksReceiptV3,
    publisher_principal_id: uuid.UUID,
    tenant_id: str,
) -> None:
    binding = report.attempt_binding
    expected_runtime = {
        "source_tree_census_digest": os.getenv(
            "AUTHORED_RELEASE_SOURCE_TREE_CENSUS_DIGEST", ""
        ).strip(),
        "evidence_policy_digest": os.getenv(
            "AUTHORED_RELEASE_EVIDENCE_POLICY_DIGEST", ""
        ).strip(),
        "execution_nonce": os.getenv("AUTHORED_RELEASE_GATE1_EXECUTION_NONCE", "").strip(),
    }
    if (
        binding.source_head_commit != report.commit
        or binding.source_head_commit != _expected_commit()
        or any(not value for value in expected_runtime.values())
        or any(getattr(binding, field) != value for field, value in expected_runtime.items())
    ):
        raise SemanticContractError("authored_ci_evidence_attempt_binding_invalid")
    pointer = await session.scalar(
        select(AuthoredDeckEmissionActivePointer).where(
            AuthoredDeckEmissionActivePointer.owner_id == publisher_principal_id,
            AuthoredDeckEmissionActivePointer.tenant_id == tenant_id,
            AuthoredDeckEmissionActivePointer.presentation_id == binding.presentation_id,
        )
    )
    manifest = (
        await session.get(AuthoredDeckEmissionManifestRecord, pointer.manifest_id)
        if pointer is not None and pointer.manifest_id is not None
        else None
    )
    if (
        pointer is None
        or manifest is None
        or pointer.canonical_mode != "bundleGraph"
        or pointer.presentation_revision != binding.presentation_revision
        or manifest.presentation_revision != binding.presentation_revision
        or manifest.generation_attempt_id != binding.generation_attempt_id
        or manifest.canonical_digest != binding.active_manifest_digest
    ):
        raise SemanticContractError("authored_ci_evidence_attempt_binding_invalid")
    for attempt_id, expected_mode in (
        (binding.fidelity_export_attempt_id, "fidelity"),
        (binding.hybrid_export_attempt_id, "hybrid"),
    ):
        attempt = await session.get(PresentationExportAttempt, attempt_id)
        export_binding = await session.scalar(
            select(AuthoredSemanticExportBinding).where(
                AuthoredSemanticExportBinding.export_attempt_id == attempt_id,
            )
        )
        if (
            attempt is None
            or export_binding is None
            or attempt.owner_id != publisher_principal_id
            or attempt.presentation_id != binding.presentation_id
            or attempt.source_revision != binding.presentation_revision
            or attempt.state != "succeeded"
            or export_binding.owner_id != publisher_principal_id
            or export_binding.tenant_id != tenant_id
            or export_binding.presentation_id != binding.presentation_id
            or export_binding.presentation_revision != binding.presentation_revision
            or export_binding.mode != expected_mode
            or export_binding.canonical_digest != binding.active_manifest_digest
        ):
            raise SemanticContractError("authored_ci_evidence_attempt_binding_invalid")


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
    await _validate_ci_attempt_binding_v3(
        session,
        report=request.receipt,
        publisher_principal_id=publisher_principal_id,
        tenant_id=tenant_id,
    )
    receipt_wire = request.receipt.model_dump(mode="json", by_alias=True)
    authority_digest = validate_actual_product_authority_v3(
        receipt_wire, request.authority
    )
    content = actual_product_canonical_json_v2(request).encode("utf-8")
    artifact_digest = "sha256:" + sha256(content).hexdigest()
    artifact_ref = f"artifact://sha256/{artifact_digest.removeprefix('sha256:')}"
    artifact = await session.scalar(
        select(AuthoredSemanticArtifact).where(
            AuthoredSemanticArtifact.owner_id == publisher_principal_id,
            AuthoredSemanticArtifact.tenant_id == tenant_id,
            AuthoredSemanticArtifact.content_digest == artifact_digest,
        )
    )
    if artifact is not None:
        persisted = _persisted_ci_request(artifact)
        identity = await session.scalar(
            select(AuthoredCiEvidenceIdentityV3).where(
                AuthoredCiEvidenceIdentityV3.evidence_bundle_id == artifact.id,
                AuthoredCiEvidenceIdentityV3.tenant_id == tenant_id,
            )
        )
        if (
            bytes(artifact.content_bytes) != content
            or identity is None
            or identity.artifact_digest != artifact.content_digest
            or identity.receipt_digest != request.receipt.receipt_digest
        ):
            raise SemanticContractError("authored_ci_evidence_artifact_collision")
        return _artifact_response(
            artifact,
            persisted,
            authority_digest=authority_digest,
        )
    await consume_authority_nonce_v3(
        session, authority=request.authority, subject_digest=authority_digest
    )
    await _reject_persisted_ci_replay(
        session,
        report=request.receipt,
        tenant_id=tenant_id,
    )
    artifact = AuthoredSemanticArtifact(
        owner_id=publisher_principal_id,
        tenant_id=tenant_id,
        artifact_ref=artifact_ref,
        content_digest=artifact_digest,
        byte_length=len(content),
        media_type="application/json",
        resolver_version=_CI_RESOLVER_VERSION,
        retention_class="evidence",
        content_bytes=content,
    )
    session.add(artifact)
    session.add(
        AuthoredCiEvidenceIdentityV3(
            tenant_id=tenant_id,
            owner_id=publisher_principal_id,
            evidence_bundle_id=artifact.id,
            receipt_digest=request.receipt.receipt_digest,
            source_head_commit=request.receipt.commit,
            pipeline_uuid=request.receipt.pipeline_uuid,
            attempt_subject_digest=canonical_digest(
                request.receipt.attempt_binding.model_dump(mode="json", by_alias=True)
            ),
            artifact_digest=artifact_digest,
        )
    )
    try:
        await session.flush()
    except IntegrityError as error:
        raise SemanticContractError("authored_ci_evidence_identity_conflict") from error
    return _artifact_response(
        artifact,
        request,
        authority_digest=authority_digest,
    )


async def get_ci_evidence_artifact_v3(
    session: AsyncSession,
    *,
    evidence_bundle_id: uuid.UUID,
    publisher_principal_id: uuid.UUID | None,
    tenant_id: str,
    now: datetime | None = None,
) -> CiEvidenceArtifactV3:
    if publisher_principal_id is None:
        raise SemanticContractError("authored_capability_receipt_admin_required")
    artifact = await session.get(AuthoredSemanticArtifact, evidence_bundle_id)
    if (
        artifact is None
        or artifact.owner_id != publisher_principal_id
        or artifact.tenant_id != tenant_id
    ):
        raise SemanticContractError("authored_ci_evidence_artifact_not_found")
    published = _persisted_ci_request(artifact)
    validate_ci_evidence_report_v3(published.receipt, now=now)
    await _validate_ci_attempt_binding_v3(
        session,
        report=published.receipt,
        publisher_principal_id=publisher_principal_id,
        tenant_id=tenant_id,
    )
    identity = await session.scalar(
        select(AuthoredCiEvidenceIdentityV3).where(
            AuthoredCiEvidenceIdentityV3.evidence_bundle_id == artifact.id,
            AuthoredCiEvidenceIdentityV3.tenant_id == tenant_id,
        )
    )
    if (
        identity is None
        or identity.owner_id != publisher_principal_id
        or identity.receipt_digest != published.receipt.receipt_digest
        or identity.source_head_commit != published.receipt.commit
        or identity.pipeline_uuid != published.receipt.pipeline_uuid
        or identity.attempt_subject_digest
        != canonical_digest(
            published.receipt.attempt_binding.model_dump(mode="json", by_alias=True)
        )
        or identity.artifact_digest != artifact.content_digest
    ):
        raise SemanticContractError("authored_ci_evidence_identity_invalid")
    authority_digest = validate_actual_product_authority_v3(
        published.receipt.model_dump(mode="json", by_alias=True),
        published.authority,
        now=now,
    )
    return _artifact_response(
        artifact,
        published,
        authority_digest=authority_digest,
    )


async def validate_ci_required_checks_evidence_v3(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: Any,
    tenant_id: str,
    *,
    export_attempt_id: uuid.UUID,
    binding_digest: str,
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
        or artifact.resolver_version != _CI_RESOLVER_VERSION
        or artifact.media_type != "application/json"
        or artifact.content_digest
        != "sha256:" + sha256(artifact.content_bytes).hexdigest()
    ):
        raise SemanticContractError("authored_ci_evidence_artifact_unresolvable")
    published = _persisted_ci_request(artifact)
    report = published.receipt
    validate_ci_evidence_report_v3(report)
    if export_attempt_id != report.attempt_binding.hybrid_export_attempt_id:
        raise SemanticContractError("authored_ci_evidence_receipt_claim_invalid")
    await _validate_ci_attempt_binding_v3(
        session,
        report=report,
        publisher_principal_id=artifact.owner_id,
        tenant_id=tenant_id,
    )
    outer_binding = await session.scalar(
        select(AuthoredSemanticExportBinding).where(
            AuthoredSemanticExportBinding.tenant_id == tenant_id,
            AuthoredSemanticExportBinding.export_attempt_id == export_attempt_id,
            AuthoredSemanticExportBinding.binding_digest == binding_digest,
        )
    )
    if (
        outer_binding is None
        or outer_binding.mode != "hybrid"
        or outer_binding.canonical_digest != report.attempt_binding.active_manifest_digest
    ):
        raise SemanticContractError("authored_ci_evidence_receipt_claim_invalid")
    identity = await session.scalar(
        select(AuthoredCiEvidenceIdentityV3).where(
            AuthoredCiEvidenceIdentityV3.evidence_bundle_id == artifact.id,
            AuthoredCiEvidenceIdentityV3.tenant_id == tenant_id,
        )
    )
    if (
        identity is None
        or identity.owner_id != artifact.owner_id
        or identity.receipt_digest != report.receipt_digest
        or identity.source_head_commit != report.commit
        or identity.pipeline_uuid != report.pipeline_uuid
        or identity.attempt_subject_digest
        != canonical_digest(report.attempt_binding.model_dump(mode="json", by_alias=True))
        or identity.artifact_digest != artifact.content_digest
    ):
        raise SemanticContractError("authored_ci_evidence_identity_invalid")
    receipt_wire = report.model_dump(mode="json", by_alias=True)
    validate_actual_product_authority_v3(receipt_wire, published.authority)
    if (
        request.evidence_digest != report.receipt_digest
        or _utc(request.expires_at) > _utc(report.expires_at)
    ):
        raise SemanticContractError("authored_ci_evidence_receipt_claim_invalid")


__all__ = [
    "publish_ci_evidence_artifact_v3",
    "get_ci_evidence_artifact_v3",
    "validate_ci_evidence_report_v3",
    "validate_ci_required_checks_evidence_v3",
]
