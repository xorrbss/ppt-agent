"""Durable capability authority and fail-closed Authored readiness v2."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import KillSwitchProjectionV1
from models.authored_vnext.semantic.readiness import (
    CapabilityEnvironmentV2,
    CapabilityProjectionEntryV2,
    CapabilityReceiptIssueRequestV2,
    CapabilityReceiptRevokeRequestV2,
    CapabilityReceiptV2,
    CapabilityScopeV2,
    ExportAuthorityClaimV2,
    ProductReadinessProjectionV2,
    ReleaseProfileDefinitionV2,
    RendererAttestationProjectionV2,
    WorkbenchCapabilityProjectionV2,
    VisualEvidenceArtifactPublishRequestV2,
    VisualEvidenceArtifactV2,
    VisualParityGateReportV1,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_semantic_bundle import AuthoredSemanticArtifact
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
    canonical_json,
)
from services.authored_vnext.semantic.query import get_active_authored_emission
from utils.datetime_utils import get_current_utc_datetime


_MAX_RECEIPT_TTL = timedelta(days=31)
_ZERO_DIGEST = "sha256:" + "0" * 64
_RELEASE_PROFILE_PATH = (
    Path(__file__).resolve().parents[5]
    / "contracts"
    / "authored_vnext"
    / "semantic"
    / "release-profile.v2.json"
)


@lru_cache(maxsize=1)
def load_release_profile_definition() -> tuple[ReleaseProfileDefinitionV2, str]:
    try:
        payload = json.loads(_RELEASE_PROFILE_PATH.read_text(encoding="utf-8"))
        definition = ReleaseProfileDefinitionV2.model_validate(payload, strict=False)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SemanticContractError("authored_release_profile_definition_invalid") from error
    return definition, canonical_digest(definition)


_PROFILE_DEFINITION, _PROFILE_DEFINITION_DIGEST = load_release_profile_definition()
REQUIRED_CAPABILITY_IDS_V2 = tuple(
    _PROFILE_DEFINITION.bounded_native_capability_ids
)
PROMOTION_REQUIRED_CAPABILITY_IDS_V2 = tuple(
    _PROFILE_DEFINITION.promotion_capability_ids
)
_RENDERER_ATTESTATION_ID = _PROFILE_DEFINITION.renderer_attestation_capability_id
_VISUAL_EVIDENCE_ID = _PROFILE_DEFINITION.visual_evidence_capability_id
_VISUAL_GATE_ISSUER = "authored-visual-parity-gate/v1"
_VISUAL_GATE_SIGNING_KEY_ENV = "AUTHORED_RELEASE_VISUAL_GATE_HMAC_KEY"


@dataclass(frozen=True)
class ReleaseAuthorityTargetV2:
    profile_id: str
    profile_definition: ReleaseProfileDefinitionV2
    profile_definition_digest: str
    scope: CapabilityScopeV2
    environment: CapabilityEnvironmentV2
    configured: bool


VisualEvidenceValidatorV2 = Callable[
    [AsyncSession, CapabilityReceiptIssueRequestV2, ReleaseAuthorityTargetV2, str],
    Awaitable[None],
]
CapabilityEvidenceValidatorV2 = Callable[
    [AsyncSession, CapabilityReceiptIssueRequestV2, ReleaseAuthorityTargetV2, str],
    Awaitable[None],
]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _digest_model(value: object) -> str:
    return canonical_digest(value)


def _validate_visual_gate_signature(report: VisualParityGateReportV1) -> None:
    """Require a server-held authority key; admin-uploaded JSON is not authority."""

    signing_key = os.getenv(_VISUAL_GATE_SIGNING_KEY_ENV, "").encode("utf-8")
    if len(signing_key) < 32 or report.trusted_issuer != _VISUAL_GATE_ISSUER:
        raise SemanticContractError("authored_visual_evidence_trusted_issuer_unconfigured")
    supplied = report.authority_signature or ""
    payload = report.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload.pop("authoritySignature", None)
    expected = "hmac-sha256:" + hmac.new(
        signing_key,
        canonical_json(payload).encode("utf-8"),
        sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied, expected):
        raise SemanticContractError("authored_visual_evidence_signature_invalid")


def release_authority_target_from_environment() -> ReleaseAuthorityTargetV2:
    definition, definition_digest = load_release_profile_definition()
    deployment = os.getenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "development")
    if deployment not in {"development", "staging", "production"}:
        deployment = "development"
    digest_names = (
        "AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST",
        "AUTHORED_RELEASE_FONT_INVENTORY_DIGEST",
        "AUTHORED_RELEASE_BACKEND_BUILD_DIGEST",
    )
    digests = [os.getenv(name, _ZERO_DIGEST) for name in digest_names]
    configured = all(
        (
            bool(os.getenv("AUTHORED_RELEASE_OS_BUILD")),
            bool(os.getenv("AUTHORED_RELEASE_OFFICE_BUILD")),
            all(value != _ZERO_DIGEST for value in digests),
        )
    )
    return ReleaseAuthorityTargetV2(
        profile_id=definition.profile_id,
        profile_definition=definition,
        profile_definition_digest=definition_digest,
        scope=definition.platform_scope,
        environment=CapabilityEnvironmentV2(
            deployment_environment=deployment,
            os_build=os.getenv("AUTHORED_RELEASE_OS_BUILD", "unconfigured"),
            office_build=os.getenv("AUTHORED_RELEASE_OFFICE_BUILD", "unconfigured"),
            renderer_image_digest=digests[0],
            font_inventory_digest=digests[1],
            backend_build_digest=digests[2],
        ),
        configured=configured,
    )


async def publish_visual_evidence_artifact(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    request: VisualEvidenceArtifactPublishRequestV2,
    publisher_principal_id: uuid.UUID | None,
    publisher_is_admin: bool,
    tenant_id: str = "default",
) -> VisualEvidenceArtifactV2:
    """Persist one owner-scoped canonical gate report as immutable evidence."""

    owner_id = _require_issuer_authority(
        issuer_principal_id=publisher_principal_id,
        issuer_is_admin=publisher_is_admin,
    )
    presentation = await session.get(PresentationModel, presentation_id)
    if presentation is None or presentation.owner_id != owner_id:
        raise SemanticContractError("authored_visual_evidence_presentation_not_found")
    if request.report.identity.presentation_id != str(presentation_id):
        raise SemanticContractError("authored_visual_evidence_presentation_mismatch")
    content = canonical_json(request.report).encode("utf-8")
    digest = "sha256:" + sha256(content).hexdigest()
    artifact_ref = "artifact://sha256/" + digest.removeprefix("sha256:")
    existing = await session.scalar(
        select(AuthoredSemanticArtifact).where(
            AuthoredSemanticArtifact.owner_id == owner_id,
            AuthoredSemanticArtifact.tenant_id == tenant_id,
            AuthoredSemanticArtifact.content_digest == digest,
        )
    )
    if existing is not None:
        if (
            existing.artifact_ref != artifact_ref
            or existing.content_digest != digest
            or bytes(existing.content_bytes) != content
            or existing.byte_length != len(content)
            or existing.media_type != "application/json"
        ):
            raise SemanticContractError("authored_semantic_artifact_digest_collision")
    else:
        session.add(
            AuthoredSemanticArtifact(
                owner_id=owner_id,
                tenant_id=tenant_id,
                artifact_ref=artifact_ref,
                content_digest=digest,
                byte_length=len(content),
                media_type="application/json",
                resolver_version="authored-visual-evidence/v2",
                retention_class="evidence",
                content_bytes=content,
            )
        )
        await session.flush()
    return VisualEvidenceArtifactV2(
        presentation_id=presentation_id,
        report_artifact_ref=artifact_ref,
        report_artifact_digest=digest,
        byte_length=len(content),
    )


async def validate_visual_evidence_authority(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
    tenant_id: str,
) -> None:
    """Resolve and validate the immutable visual gate report against live authority."""

    authority = request.visual_evidence_authority
    if authority is None:
        raise SemanticContractError("authored_visual_evidence_authority_required")
    binding = authority.binding
    presentation = await session.get(PresentationModel, binding.presentation_id)
    if presentation is None:
        raise SemanticContractError("authored_visual_evidence_presentation_not_found")
    artifact = await session.scalar(
        select(AuthoredSemanticArtifact).where(
            AuthoredSemanticArtifact.tenant_id == tenant_id,
            AuthoredSemanticArtifact.owner_id == presentation.owner_id,
            AuthoredSemanticArtifact.artifact_ref == binding.report_artifact_ref,
            AuthoredSemanticArtifact.content_digest == binding.report_artifact_digest,
        )
    )
    if artifact is None or (
        "sha256:" + sha256(artifact.content_bytes).hexdigest()
        != binding.report_artifact_digest
    ):
        raise SemanticContractError("authored_visual_evidence_report_unresolvable")
    try:
        report_payload = json.loads(artifact.content_bytes.decode("utf-8-sig"))
        report = VisualParityGateReportV1.model_validate(report_payload, strict=False)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SemanticContractError("authored_visual_evidence_report_invalid") from error
    definition = target.profile_definition
    policy = definition.visual_evidence_policy
    identity = report.identity
    _validate_visual_gate_signature(report)
    if report.schema_version != policy.report_schema_version:
        raise SemanticContractError("authored_visual_evidence_report_schema_mismatch")
    if (
        report.status != policy.required_status
        or report.visual_parity != policy.required_visual_parity
        or report.promotion_ready is not False
        or report.languages != policy.required_languages
        or report.archetypes != policy.required_archetypes
        or (
            str(getattr(definition, "schema_version", "")).endswith("/v3")
            and identity.release_profile_id != target.profile_id
        )
        or canonical_digest(identity) != report.evidence_digest
        or request.evidence_digest != report.evidence_digest
        or request.evidence_policy_id != policy.policy_id
        or request.policy_digest != target.profile_definition_digest
    ):
        raise SemanticContractError("authored_visual_evidence_report_claim_invalid")
    freshness_time = identity.freshness.get("generatedAt")
    try:
        generated_at = (
            datetime.fromisoformat(freshness_time.replace("Z", "+00:00"))
            if isinstance(freshness_time, str)
            else None
        )
    except ValueError:
        generated_at = None
    expected_expiry = (
        _utc(generated_at) + timedelta(seconds=policy.receipt_ttl_seconds)
        if generated_at is not None
        else None
    )
    observed_at = _utc(get_current_utc_datetime())
    if (
        not target.configured
        or request.scope != target.scope
        or request.environment != target.environment
        or expected_expiry is None
        or _utc(request.expires_at) != expected_expiry
        or generated_at is None
        or _utc(generated_at) > observed_at + timedelta(minutes=5)
        or expected_expiry <= observed_at
    ):
        raise SemanticContractError("authored_visual_evidence_authority_invalid")
    active = await get_active_authored_emission(
        session,
        presentation_id=binding.presentation_id,
        owner_id=presentation.owner_id,
        tenant_id=artifact.tenant_id,
    )
    manifest = active.manifest
    if (
        active.canonical_mode != "bundleGraph"
        or manifest is None
        or active.presentation_revision != binding.presentation_revision
        or active.canonical_digest != binding.manifest_digest
        or manifest.canonical_digest != binding.manifest_digest
        or manifest.generation_attempt_id != binding.generation_attempt_id
        or str(binding.presentation_id) != identity.presentation_id
        or binding.presentation_revision != identity.presentation_revision
        or str(manifest.operation_id) != identity.operation_id
        or str(binding.generation_attempt_id) != identity.generation_attempt_id
    ):
        raise SemanticContractError("authored_visual_evidence_manifest_identity_mismatch")
    mode_names: set[str] = set()
    attempt_ids: set[uuid.UUID] = set()
    for mode in identity.modes:
        mode_name = mode.get("mode")
        attempt_id_value = mode.get("exportAttemptId")
        try:
            attempt_id = uuid.UUID(attempt_id_value)
        except (TypeError, ValueError, AttributeError) as error:
            raise SemanticContractError(
                "authored_visual_evidence_export_attempt_mismatch"
            ) from error
        if mode_name not in {"authored", "fidelity", "hybrid"}:
            raise SemanticContractError("authored_visual_evidence_mode_set_invalid")
        if mode_name in mode_names or attempt_id in attempt_ids:
            raise SemanticContractError("authored_visual_evidence_mode_set_invalid")
        mode_names.add(mode_name)
        attempt_ids.add(attempt_id)
        attempt = await session.get(PresentationExportAttempt, attempt_id)
        if (
            attempt is None
            or attempt.presentation_id != binding.presentation_id
            or attempt.source_revision != binding.presentation_revision
            or attempt.state != "succeeded"
            or attempt.artifact_sha256 is None
            or mode.get("pptxSha256") != "sha256:" + attempt.artifact_sha256
        ):
            raise SemanticContractError(
                "authored_visual_evidence_export_attempt_mismatch"
            )
    if mode_names != {"authored", "fidelity", "hybrid"} or (
        binding.export_attempt_id not in attempt_ids
    ):
        raise SemanticContractError("authored_visual_evidence_mode_set_invalid")


def _receipt_from_record(record: AuthoredCapabilityReceiptRecordV2) -> CapabilityReceiptV2:
    # JSON columns return ISO datetime strings. Parse those wire values while
    # retaining the model's strict field/extra-value contract.
    return CapabilityReceiptV2.model_validate(record.payload, strict=False)


async def _records(
    session: AsyncSession, *, tenant_id: str
) -> list[AuthoredCapabilityReceiptRecordV2]:
    return list(
        await session.scalars(
            select(AuthoredCapabilityReceiptRecordV2).where(
                AuthoredCapabilityReceiptRecordV2.tenant_id == tenant_id
            )
        )
    )


def _active_records(
    records: list[AuthoredCapabilityReceiptRecordV2],
) -> list[AuthoredCapabilityReceiptRecordV2]:
    transitioned = {
        item.transition_target_digest
        for item in records
        if item.transition_target_digest is not None
    }
    return [item for item in records if item.receipt_digest not in transitioned]


def _require_issuer_authority(
    *, issuer_principal_id: uuid.UUID | None, issuer_is_admin: bool
) -> uuid.UUID:
    if issuer_principal_id is None or not issuer_is_admin:
        raise SemanticContractError("authored_capability_receipt_admin_required")
    return issuer_principal_id


async def issue_capability_receipt(
    session: AsyncSession,
    *,
    request: CapabilityReceiptIssueRequestV2,
    issuer_principal_id: uuid.UUID | None,
    issuer_is_admin: bool,
    tenant_id: str = "default",
    now: datetime | None = None,
    release_target: ReleaseAuthorityTargetV2 | None = None,
    visual_evidence_validator: VisualEvidenceValidatorV2 = validate_visual_evidence_authority,
    capability_validators: Mapping[str, CapabilityEvidenceValidatorV2] | None = None,
    allowed_capability_ids: Collection[str] | None = None,
    authority_metadata: Mapping[str, Any] | None = None,
) -> CapabilityReceiptV2:
    issuer_id = _require_issuer_authority(
        issuer_principal_id=issuer_principal_id,
        issuer_is_admin=issuer_is_admin,
    )
    allowed_ids = (
        REQUIRED_CAPABILITY_IDS_V2
        if allowed_capability_ids is None
        else tuple(allowed_capability_ids)
    )
    if request.capability_id not in allowed_ids:
        raise SemanticContractError("authored_capability_receipt_capability_unknown")
    evaluated_at = _utc(now or get_current_utc_datetime())
    expires_at = _utc(request.expires_at)
    if expires_at <= evaluated_at or expires_at - evaluated_at > _MAX_RECEIPT_TTL:
        raise SemanticContractError("authored_capability_receipt_expiry_invalid")
    target = release_target or release_authority_target_from_environment()
    if request.state == "ready" and (
        not target.configured
        or request.scope != target.scope
        or request.environment != target.environment
        or request.serializer_id != target.scope.renderer
        or request.serializer_version != target.profile_definition.serializer_version
    ):
        raise SemanticContractError("authored_capability_receipt_authority_scope_mismatch")
    if request.capability_id == _VISUAL_EVIDENCE_ID and request.state == "ready":
        await visual_evidence_validator(session, request, target, tenant_id)
    elif request.visual_evidence_authority is not None:
        raise SemanticContractError("authored_visual_evidence_authority_unexpected")
    elif request.state == "ready":
        validator = (capability_validators or {}).get(request.capability_id)
        if validator is None:
            raise SemanticContractError(
                "authored_capability_receipt_trusted_validator_unavailable"
            )
        await validator(session, request, target, tenant_id)

    records = await _records(session, tenant_id=tenant_id)
    active = _active_records(records)
    scope_digest = _digest_model(request.scope)
    environment_digest = _digest_model(request.environment)
    matching_active = [
        item
        for item in active
        if item.capability_id == request.capability_id
        and item.scope_digest == scope_digest
        and item.environment_digest == environment_digest
        and _utc(item.expires_at) > evaluated_at
    ]
    transition_target: AuthoredCapabilityReceiptRecordV2 | None = None
    if request.supersedes_receipt_digest is not None:
        transition_target = next(
            (
                item
                for item in matching_active
                if item.receipt_digest == request.supersedes_receipt_digest
            ),
            None,
        )
        if transition_target is None:
            raise SemanticContractError("authored_capability_receipt_supersede_target_invalid")
    elif matching_active:
        raise SemanticContractError("authored_capability_receipt_active_conflict")

    receipt = CapabilityReceiptV2(
        capability_id=request.capability_id,
        state=request.state,
        scope=request.scope,
        environment=request.environment,
        serializer_id=request.serializer_id,
        serializer_version=request.serializer_version,
        feature_set_digest=request.feature_set_digest,
        evidence_policy_id=request.evidence_policy_id,
        policy_digest=request.policy_digest,
        evidence_bundle_id=request.evidence_bundle_id,
        evidence_digest=request.evidence_digest,
        issuer_principal_id=issuer_id,
        issuer_authority="authenticated-admin",
        evaluated_at=evaluated_at,
        expires_at=expires_at,
        reason_code=request.reason_code,
        transition_kind="supersede" if transition_target else "issue",
        supersedes_receipt_digest=(
            transition_target.receipt_digest if transition_target else None
        ),
        visual_evidence_authority=request.visual_evidence_authority,
    )
    value = receipt.model_dump(mode="json", by_alias=True)
    value.pop("receiptDigest", None)
    receipt = receipt.model_copy(update={"receipt_digest": canonical_digest(value)})
    session.add(
        AuthoredCapabilityReceiptRecordV2(
            tenant_id=tenant_id,
            capability_id=receipt.capability_id,
            state=receipt.state,
            scope_digest=scope_digest,
            environment_digest=environment_digest,
            serializer_id=receipt.serializer_id,
            serializer_version=receipt.serializer_version,
            issuer_principal_id=issuer_id,
            issuer_authority=receipt.issuer_authority,
            authority_domain=(
                str(authority_metadata["authority_domain"])
                if authority_metadata is not None
                else "structural_test"
            ),
            evidence_class=(
                str(authority_metadata["evidence_class"])
                if authority_metadata is not None
                else "synthetic_structural_only"
            ),
            issuer_id=(
                str(authority_metadata["issuer_id"])
                if authority_metadata is not None
                else None
            ),
            signer_key_id=(
                str(authority_metadata["signer_key_id"])
                if authority_metadata is not None
                else None
            ),
            environment_id=(
                str(authority_metadata["environment_id"])
                if authority_metadata is not None
                else None
            ),
            rotation_epoch=(
                int(authority_metadata["rotation_epoch"])
                if authority_metadata is not None
                else None
            ),
            nonce=(
                str(authority_metadata["nonce"])
                if authority_metadata is not None
                else None
            ),
            signature_algorithm=(
                str(authority_metadata["signature_algorithm"])
                if authority_metadata is not None
                else None
            ),
            signature_base64=(
                str(authority_metadata["signature_base64"])
                if authority_metadata is not None
                else None
            ),
            authority_digest=(
                str(authority_metadata["authority_digest"])
                if authority_metadata is not None
                else None
            ),
            authority_payload=(
                dict(authority_metadata["authority_payload"])
                if authority_metadata is not None
                else None
            ),
            evaluated_at=evaluated_at,
            expires_at=expires_at,
            transition_kind=receipt.transition_kind,
            transition_target_digest=(
                transition_target.receipt_digest if transition_target else None
            ),
            receipt_digest=receipt.receipt_digest,
            payload=receipt.model_dump(mode="json", by_alias=True),
        )
    )
    await session.flush()
    return receipt


async def revoke_capability_receipt(
    session: AsyncSession,
    *,
    receipt_digest: str,
    request: CapabilityReceiptRevokeRequestV2,
    issuer_principal_id: uuid.UUID | None,
    issuer_is_admin: bool,
    tenant_id: str = "default",
    now: datetime | None = None,
) -> CapabilityReceiptV2:
    issuer_id = _require_issuer_authority(
        issuer_principal_id=issuer_principal_id,
        issuer_is_admin=issuer_is_admin,
    )
    evaluated_at = _utc(now or get_current_utc_datetime())
    records = await _records(session, tenant_id=tenant_id)
    target_record = next(
        (item for item in _active_records(records) if item.receipt_digest == receipt_digest),
        None,
    )
    if target_record is None:
        raise SemanticContractError("authored_capability_receipt_revoke_target_invalid")
    target = _receipt_from_record(target_record)
    if _utc(target.expires_at) <= evaluated_at:
        raise SemanticContractError("authored_capability_receipt_revoke_target_expired")
    receipt = CapabilityReceiptV2(
        capability_id=target.capability_id,
        state="blocked",
        scope=target.scope,
        environment=target.environment,
        serializer_id=target.serializer_id,
        serializer_version=target.serializer_version,
        feature_set_digest=target.feature_set_digest,
        evidence_policy_id=target.evidence_policy_id,
        policy_digest=target.policy_digest,
        evidence_bundle_id=target.evidence_bundle_id,
        evidence_digest=canonical_digest(
            {
                "revokedReceiptDigest": receipt_digest,
                "reasonCode": request.reason_code,
            }
        ),
        issuer_principal_id=issuer_id,
        issuer_authority="authenticated-admin",
        evaluated_at=evaluated_at,
        expires_at=_utc(target.expires_at),
        reason_code=request.reason_code,
        transition_kind="revoke",
        revokes_receipt_digest=receipt_digest,
        visual_evidence_authority=target.visual_evidence_authority,
    )
    value = receipt.model_dump(mode="json", by_alias=True)
    value.pop("receiptDigest", None)
    receipt = receipt.model_copy(update={"receipt_digest": canonical_digest(value)})
    session.add(
        AuthoredCapabilityReceiptRecordV2(
            tenant_id=tenant_id,
            capability_id=receipt.capability_id,
            state=receipt.state,
            scope_digest=target_record.scope_digest,
            environment_digest=target_record.environment_digest,
            serializer_id=receipt.serializer_id,
            serializer_version=receipt.serializer_version,
            issuer_principal_id=issuer_id,
            issuer_authority=receipt.issuer_authority,
            evaluated_at=evaluated_at,
            expires_at=_utc(target.expires_at),
            transition_kind="revoke",
            transition_target_digest=receipt_digest,
            receipt_digest=receipt.receipt_digest,
            payload=receipt.model_dump(mode="json", by_alias=True),
        )
    )
    await session.flush()
    return receipt


def _kill_switches_from_environment(now: datetime) -> list[KillSwitchProjectionV1]:
    return [
        KillSwitchProjectionV1(
            capability_id=capability_id,
            active=True,
            reason="release_kill_switch_active",
            expires_at=None,
        )
        for capability_id in sorted(
            {
                item.strip()
                for item in os.getenv("AUTHORED_RELEASE_KILL_SWITCHES", "").split(",")
                if item.strip()
            }
        )
    ]


async def get_workbench_capability_projection_v2(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str = "default",
    target: ReleaseAuthorityTargetV2 | None = None,
    now: datetime | None = None,
    kill_switches: list[KillSwitchProjectionV1] | None = None,
) -> WorkbenchCapabilityProjectionV2:
    active_emission = await get_active_authored_emission(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    evaluated_at = _utc(now or get_current_utc_datetime())
    target = target or release_authority_target_from_environment()
    definition = target.profile_definition
    promotion_capability_ids = tuple(definition.promotion_capability_ids)
    bounded_capability_ids = tuple(definition.bounded_native_capability_ids)
    scope_digest = _digest_model(target.scope)
    environment_digest = _digest_model(target.environment)
    records = [
        item
        for item in await _records(session, tenant_id=tenant_id)
        if item.scope_digest == scope_digest
        and item.environment_digest == environment_digest
        and item.capability_id in bounded_capability_ids
    ]
    active_records = _active_records(records)
    capabilities: list[CapabilityProjectionEntryV2] = []
    ready_receipts: list[str] = []
    receipts_by_capability: dict[str, CapabilityReceiptV2] = {}
    for capability_id in bounded_capability_ids:
        matches = [item for item in active_records if item.capability_id == capability_id]
        if len(matches) != 1:
            reason = (
                "capability_receipt_missing"
                if not matches
                else "capability_receipt_ambiguous"
            )
            capabilities.append(
                CapabilityProjectionEntryV2(
                    capability_id=capability_id,
                    state="blocked",
                    reason_code=reason,
                    scope=target.scope,
                    serializer_id=target.scope.renderer,
                    serializer_version="unavailable",
                )
            )
            continue
        receipt = _receipt_from_record(matches[0])
        expired = _utc(receipt.expires_at) <= evaluated_at
        serializer_mismatch = (
            receipt.serializer_id != definition.platform_scope.renderer
            or receipt.serializer_version != definition.serializer_version
        )
        if not expired and not serializer_mismatch:
            receipts_by_capability[capability_id] = receipt
        state = "blocked" if expired or serializer_mismatch else receipt.state
        reason = (
            "capability_receipt_expired"
            if expired
            else (
                "capability_receipt_serializer_mismatch"
                if serializer_mismatch
                else receipt.reason_code
            )
        )
        digest = None if expired or serializer_mismatch else receipt.receipt_digest
        if state == "ready" and digest:
            ready_receipts.append(digest)
        capabilities.append(
            CapabilityProjectionEntryV2(
                capability_id=capability_id,
                state=state,
                reason_code=reason,
                receipt_digest=digest,
                scope=receipt.scope,
                serializer_id=receipt.serializer_id,
                serializer_version=receipt.serializer_version,
                expires_at=receipt.expires_at,
            )
        )
    switches = kill_switches if kill_switches is not None else _kill_switches_from_environment(evaluated_at)
    ready_capability_ids = {
        item.capability_id for item in capabilities if item.state == "ready"
    }
    promotion_complete = set(promotion_capability_ids).issubset(ready_capability_ids)
    bounded_complete = set(bounded_capability_ids).issubset(ready_capability_ids)
    receipt_by_capability = {
        item.capability_id: item.receipt_digest
        for item in capabilities
        if item.state == "ready" and item.receipt_digest is not None
    }
    promotion_receipts = (
        [receipt_by_capability[item] for item in promotion_capability_ids]
        if promotion_complete
        else []
    )
    bounded_receipts = (
        [receipt_by_capability[item] for item in bounded_capability_ids]
        if bounded_complete
        else []
    )
    degraded_fidelity_only = active_emission.canonical_mode != "bundleGraph" or any(
        getattr(
            getattr(bundle, "semantic_component", None)
            or getattr(bundle, "compatibility_component", None),
            "failure_code",
            None,
        )
        for bundle in active_emission.bundles
    )
    promotion_ready = (
        target.configured
        and promotion_complete
        and not degraded_fidelity_only
        and not any(item.active for item in switches)
    )
    bounded_native_parity_ready = promotion_ready and bounded_complete
    if not target.configured:
        reason_code = "release_environment_unconfigured"
    elif any(item.active for item in switches):
        reason_code = "release_kill_switch_active"
    elif degraded_fidelity_only:
        reason_code = "semantic_fidelity_only_degraded"
    elif not promotion_complete:
        reason_code = "required_capability_receipts_incomplete"
    else:
        reason_code = "promotion_formula_satisfied"
    expiries = [
        _utc(receipt.expires_at)
        for receipt in receipts_by_capability.values()
        if _utc(receipt.expires_at) > evaluated_at
    ]
    profile_expires_at = min(expiries) if promotion_ready and expiries else evaluated_at
    renderer = next(
        item for item in capabilities if item.capability_id == _RENDERER_ATTESTATION_ID
    )
    renderer_receipt = receipts_by_capability.get(_RENDERER_ATTESTATION_ID)
    if renderer_receipt is None:
        renderer_state = "unattested"
    elif _utc(renderer_receipt.expires_at) <= evaluated_at:
        renderer_state = "expired"
    elif renderer_receipt.state == "ready":
        renderer_state = "attested"
    else:
        renderer_state = "blocked"
    projection = WorkbenchCapabilityProjectionV2(
        presentation_id=presentation_id,
        canonical_mode=active_emission.canonical_mode,
        presentation_revision=active_emission.presentation_revision,
        canonical_digest=active_emission.canonical_digest,
        manifest_digest=(
            active_emission.manifest.canonical_digest
            if active_emission.manifest is not None
            else None
        ),
        degraded_fidelity_only=degraded_fidelity_only,
        release_profile=ProductReadinessProjectionV2(
            profile_id=target.profile_id,
            platform_scope=target.scope,
            evaluated_at=evaluated_at,
            expires_at=profile_expires_at,
            promotion_ready=promotion_ready,
            bounded_native_parity_ready=bounded_native_parity_ready,
            profile_definition_digest=target.profile_definition_digest,
            promotion_capability_ids=list(promotion_capability_ids),
            required_capability_ids=list(promotion_capability_ids),
            bounded_native_capability_ids=list(bounded_capability_ids),
            ready_receipt_digests=ready_receipts,
            promotion_receipt_digests=(
                promotion_receipts if promotion_ready else []
            ),
            bounded_native_receipt_digests=(
                bounded_receipts if bounded_native_parity_ready else []
            ),
            reason_code=reason_code,
        ),
        capabilities=capabilities,
        renderer_attestation=RendererAttestationProjectionV2(
            state=renderer_state,
            reason_code=renderer.reason_code,
            attestation_digest=renderer.receipt_digest,
            environment=target.environment,
            expires_at=renderer.expires_at,
        ),
        kill_switches=switches,
    )
    value = projection.model_dump(mode="json", by_alias=True)
    for field in (
        "projectionDigest",
        "releaseAuthorityReceiptDigests",
        "releaseProjectionDigest",
        "exportAuthorityClaim",
    ):
        value.pop(field, None)
    projection_digest = canonical_digest(value)
    projection = projection.model_copy(
        update={
            "projection_digest": projection_digest,
            "release_projection_digest": projection_digest,
        }
    )
    claim: ExportAuthorityClaimV2 | None = None
    if bounded_native_parity_ready:
        claim = build_export_authority_claim(projection, kind="boundedNative")
    elif promotion_ready:
        claim = build_export_authority_claim(projection, kind="promotion")
    return WorkbenchCapabilityProjectionV2.model_validate(
        projection.model_copy(
            update={
                "release_authority_receipt_digests": (
                    list(claim.receipt_digests) if claim is not None else []
                ),
                "export_authority_claim": claim,
            }
        ).model_dump(mode="python", by_alias=False)
    )


def build_export_authority_claim(
    projection: WorkbenchCapabilityProjectionV2,
    *,
    kind: str,
) -> ExportAuthorityClaimV2:
    """Build the only release claim accepted by both projection and binding."""

    definition, definition_digest = load_release_profile_definition()
    profile = projection.release_profile
    if (
        projection.projection_digest is None
        or profile.profile_id != definition.profile_id
        or profile.profile_definition_digest != definition_digest
        or profile.promotion_capability_ids != definition.promotion_capability_ids
        or profile.required_capability_ids != definition.promotion_capability_ids
        or profile.bounded_native_capability_ids
        != definition.bounded_native_capability_ids
    ):
        raise SemanticContractError("authored_export_authority_profile_mismatch")
    if kind == "promotion":
        ready = profile.promotion_ready
        receipt_digests = profile.promotion_receipt_digests
        capability_ids = definition.promotion_capability_ids
    elif kind == "boundedNative":
        ready = profile.bounded_native_parity_ready
        receipt_digests = profile.bounded_native_receipt_digests
        capability_ids = definition.bounded_native_capability_ids
    else:
        raise SemanticContractError("authored_export_authority_claim_kind_invalid")
    receipt_by_capability = {
        item.capability_id: item.receipt_digest
        for item in projection.capabilities
        if item.state == "ready" and item.receipt_digest is not None
    }
    expected = (
        [receipt_by_capability[item] for item in capability_ids]
        if set(capability_ids).issubset(receipt_by_capability)
        else []
    )
    if not ready or not receipt_digests or receipt_digests != expected:
        raise SemanticContractError("authored_export_authority_claim_not_ready")
    return ExportAuthorityClaimV2(
        kind=kind,
        profile_id=definition.profile_id,
        profile_definition_digest=definition_digest,
        projection_digest=projection.projection_digest,
        receipt_digests=receipt_digests,
    )


def validate_export_authority_claim(
    projection: WorkbenchCapabilityProjectionV2,
    claim: ExportAuthorityClaimV2,
) -> None:
    expected = build_export_authority_claim(projection, kind=claim.kind)
    if expected != claim:
        raise SemanticContractError("authored_export_authority_claim_stale")


async def validate_attempt_receipt_pins(
    session: AsyncSession,
    *,
    tenant_id: str,
    receipt_digests: list[str],
    claim: ExportAuthorityClaimV2,
    renderer_source_digest: str,
    now: datetime | None = None,
) -> None:
    """Validate exact active receipt authority before an attempt binding is appended."""

    if not receipt_digests:
        raise SemanticContractError("authored_export_release_authority_pins_required")
    if len(receipt_digests) != len(set(receipt_digests)):
        raise SemanticContractError("authored_export_receipt_pins_duplicate")
    evaluated_at = _utc(now or get_current_utc_datetime())
    records = await _records(session, tenant_id=tenant_id)
    by_digest = {item.receipt_digest: item for item in records}
    pinned = [by_digest.get(digest) for digest in receipt_digests]
    if any(item is None for item in pinned):
        raise SemanticContractError("authored_export_receipt_pin_unresolvable")
    pinned_records = [item for item in pinned if item is not None]
    active_digests = {item.receipt_digest for item in _active_records(records)}
    receipts = [_receipt_from_record(item) for item in pinned_records]
    definition, definition_digest = load_release_profile_definition()
    required_ids = (
        definition.promotion_capability_ids
        if claim.kind == "promotion"
        else definition.bounded_native_capability_ids
    )
    if (
        claim.profile_id != definition.profile_id
        or claim.profile_definition_digest != definition_digest
        or claim.receipt_digests != receipt_digests
        or {item.capability_id for item in receipts} != set(required_ids)
        or len(receipts) != len(required_ids)
    ):
        raise SemanticContractError("authored_export_receipt_pins_incomplete")
    if len({item.scope_digest for item in pinned_records}) != 1 or len(
        {item.environment_digest for item in pinned_records}
    ) != 1:
        raise SemanticContractError("authored_export_receipt_pin_scope_mismatch")
    if any(
        item.receipt_digest not in active_digests
        or item.state != "ready"
        or _utc(item.expires_at) <= evaluated_at
        or item.serializer_id != definition.platform_scope.renderer
        or item.serializer_version != definition.serializer_version
        for item in receipts
    ):
        raise SemanticContractError("authored_export_receipt_pin_inactive")
    if any(
        item.environment.renderer_image_digest != renderer_source_digest
        for item in receipts
    ):
        raise SemanticContractError("authored_export_receipt_pin_renderer_mismatch")


__all__ = [
    "REQUIRED_CAPABILITY_IDS_V2",
    "PROMOTION_REQUIRED_CAPABILITY_IDS_V2",
    "ReleaseAuthorityTargetV2",
    "get_workbench_capability_projection_v2",
    "issue_capability_receipt",
    "load_release_profile_definition",
    "publish_visual_evidence_artifact",
    "release_authority_target_from_environment",
    "revoke_capability_receipt",
    "validate_attempt_receipt_pins",
    "validate_export_authority_claim",
    "validate_visual_evidence_authority",
]
