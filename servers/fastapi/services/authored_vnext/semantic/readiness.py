"""Durable capability authority and fail-closed Authored readiness v2."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import os
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
    ProductReadinessProjectionV2,
    RendererAttestationProjectionV2,
    WorkbenchCapabilityProjectionV2,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from services.authored_vnext.semantic.canonical import SemanticContractError, canonical_digest
from services.authored_vnext.semantic.query import get_active_authored_emission
from utils.datetime_utils import get_current_utc_datetime


REQUIRED_CAPABILITY_IDS_V2 = (
    "shape.gradient",
    "shape.shadow",
    "layout.transform",
    "layout.flex",
    "layout.grid",
    "shape.borderRadius",
    "shape.opacity",
    "shape.clip",
    "receipt.cjk",
    "receipt.chart",
    "receipt.zBand",
    "receipt.degradation",
    "renderer.attestation",
    "evidence.visual",
)
PROMOTION_REQUIRED_CAPABILITY_IDS_V2 = (
    "receipt.cjk",
    "receipt.chart",
    "receipt.zBand",
    "receipt.degradation",
    "renderer.attestation",
    "evidence.visual",
)
_RENDERER_ATTESTATION_ID = "renderer.attestation"
_VISUAL_EVIDENCE_ID = "evidence.visual"
_MAX_RECEIPT_TTL = timedelta(days=31)
_ZERO_DIGEST = "sha256:" + "0" * 64


@dataclass(frozen=True)
class ReleaseAuthorityTargetV2:
    profile_id: str
    scope: CapabilityScopeV2
    environment: CapabilityEnvironmentV2
    configured: bool


VisualEvidenceValidatorV2 = Callable[
    [CapabilityReceiptIssueRequestV2, ReleaseAuthorityTargetV2], None
]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _digest_model(value: object) -> str:
    return canonical_digest(value)


def release_authority_target_from_environment() -> ReleaseAuthorityTargetV2:
    profile_id = os.getenv("AUTHORED_RELEASE_PROFILE_ID", "unconfigured")
    platform = os.getenv("AUTHORED_RELEASE_PLATFORM", "unconfigured")
    renderer = os.getenv("AUTHORED_RELEASE_RENDERER", "unconfigured")
    archetypes = sorted(
        {
            item.strip()
            for item in os.getenv("AUTHORED_RELEASE_ARCHETYPES", "unconfigured").split(",")
            if item.strip()
        }
    )
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
            profile_id != "unconfigured",
            platform != "unconfigured",
            renderer != "unconfigured",
            archetypes and archetypes != ["unconfigured"],
            bool(os.getenv("AUTHORED_RELEASE_OS_BUILD")),
            bool(os.getenv("AUTHORED_RELEASE_OFFICE_BUILD")),
            all(value != _ZERO_DIGEST for value in digests),
        )
    )
    return ReleaseAuthorityTargetV2(
        profile_id=profile_id,
        scope=CapabilityScopeV2(
            platform=platform,
            renderer=renderer,
            archetypes=archetypes or ["unconfigured"],
        ),
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


def validate_visual_evidence_authority(
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
) -> None:
    """Validate stable visual-gate authority instead of trusting request status."""

    expected_expiry = os.getenv("AUTHORED_RELEASE_VISUAL_EVIDENCE_EXPIRES_AT")
    try:
        expiry = datetime.fromisoformat(expected_expiry.replace("Z", "+00:00")) if expected_expiry else None
    except ValueError:
        expiry = None
    if (
        not target.configured
        or request.scope != target.scope
        or request.environment != target.environment
        or request.evidence_policy_id
        != os.getenv("AUTHORED_RELEASE_VISUAL_POLICY_ID")
        or request.policy_digest
        != os.getenv("AUTHORED_RELEASE_VISUAL_POLICY_DIGEST")
        or request.evidence_digest
        != os.getenv("AUTHORED_RELEASE_VISUAL_EVIDENCE_DIGEST")
        or expiry is None
        or _utc(request.expires_at) != _utc(expiry)
    ):
        raise SemanticContractError("authored_visual_evidence_authority_invalid")


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
) -> CapabilityReceiptV2:
    issuer_id = _require_issuer_authority(
        issuer_principal_id=issuer_principal_id,
        issuer_is_admin=issuer_is_admin,
    )
    if request.capability_id not in REQUIRED_CAPABILITY_IDS_V2:
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
    ):
        raise SemanticContractError("authored_capability_receipt_authority_scope_mismatch")
    if request.capability_id == _VISUAL_EVIDENCE_ID and request.state == "ready":
        visual_evidence_validator(request, target)

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
    scope_digest = _digest_model(target.scope)
    environment_digest = _digest_model(target.environment)
    records = [
        item
        for item in await _records(session, tenant_id=tenant_id)
        if item.scope_digest == scope_digest
        and item.environment_digest == environment_digest
        and item.capability_id in REQUIRED_CAPABILITY_IDS_V2
    ]
    active_records = _active_records(records)
    capabilities: list[CapabilityProjectionEntryV2] = []
    ready_receipts: list[str] = []
    receipts_by_capability: dict[str, CapabilityReceiptV2] = {}
    for capability_id in REQUIRED_CAPABILITY_IDS_V2:
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
        receipts_by_capability[capability_id] = receipt
        expired = _utc(receipt.expires_at) <= evaluated_at
        state = "blocked" if expired else receipt.state
        reason = "capability_receipt_expired" if expired else receipt.reason_code
        digest = None if expired else receipt.receipt_digest
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
    promotion_complete = set(PROMOTION_REQUIRED_CAPABILITY_IDS_V2).issubset(
        ready_capability_ids
    )
    bounded_complete = set(REQUIRED_CAPABILITY_IDS_V2).issubset(ready_capability_ids)
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
            required_capability_ids=list(PROMOTION_REQUIRED_CAPABILITY_IDS_V2),
            bounded_native_capability_ids=list(REQUIRED_CAPABILITY_IDS_V2),
            ready_receipt_digests=ready_receipts,
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
    value.pop("projectionDigest", None)
    return projection.model_copy(update={"projection_digest": canonical_digest(value)})


async def validate_attempt_receipt_pins(
    session: AsyncSession,
    *,
    tenant_id: str,
    receipt_digests: list[str],
    serializer_id: str,
    serializer_version: str,
    renderer_source_digest: str,
    now: datetime | None = None,
) -> None:
    """Validate exact active receipt authority before an attempt binding is appended."""

    if not receipt_digests:
        return  # v1 compatibility: legacy bindings did not carry v2 authority pins.
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
    if (
        {item.capability_id for item in receipts} != set(REQUIRED_CAPABILITY_IDS_V2)
        or len(receipts) != len(REQUIRED_CAPABILITY_IDS_V2)
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
        or item.serializer_id != serializer_id
        or item.serializer_version != serializer_version
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
    "release_authority_target_from_environment",
    "revoke_capability_receipt",
    "validate_attempt_receipt_pins",
    "validate_visual_evidence_authority",
]
