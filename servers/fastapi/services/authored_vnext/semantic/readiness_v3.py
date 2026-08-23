"""Reachable, profile-scoped Authored bounded-Hybrid release authority v3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.readiness import (
    CapabilityEnvironmentV2,
    CapabilityProjectionEntryV2,
    CapabilityReceiptIssueRequestV2,
    CapabilityReceiptV2,
    CapabilityScopeV2,
    ExportAuthorityClaimV2,
    RendererAttestationProjectionV2,
)
from models.authored_vnext.semantic.release_profile_v3 import (
    BoundedHybridKillSwitchV3,
    BoundedHybridReleaseBlockerV3,
    BoundedHybridReleaseProfileDefinitionV3,
    BoundedHybridReleaseReadinessV3,
    WorkbenchCapabilityProjectionV3,
)
from models.sql.authored_capability_receipt import AuthoredCapabilityReceiptRecordV2
from models.sql.authored_semantic_bundle import AuthoredSemanticExportBinding
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
)
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.readiness import (
    CapabilityEvidenceValidatorV2,
    _active_records,
    _digest_model,
    _kill_switches_from_environment,
    _receipt_from_record,
    _records,
    _utc,
    issue_capability_receipt,
    validate_visual_evidence_authority,
)
from utils.datetime_utils import get_current_utc_datetime


_ZERO_DIGEST = "sha256:" + "0" * 64
_PROFILE_PATH = (
    Path(__file__).resolve().parents[5] / "contracts" / "authored_vnext" / "semantic" / "release-profile.v3.json"
)


@lru_cache(maxsize=1)
def load_release_profile_definition_v3() -> tuple[BoundedHybridReleaseProfileDefinitionV3, str]:
    try:
        payload = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        definition = BoundedHybridReleaseProfileDefinitionV3.model_validate(payload, strict=False)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise SemanticContractError("authored_release_v3_profile_definition_invalid") from error
    # Release authority binds the sealed JSON artifact, not Pydantic's model
    # projection.  The Gate 1 runner consumes this same sorted, compact JSON
    # projection, including every explicitly sealed field in the source file.
    artifact_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    artifact_digest = "sha256:" + sha256(artifact_json.encode("utf-8")).hexdigest()
    return definition, artifact_digest


@lru_cache(maxsize=1)
def load_release_profile_model_projection_digest_v3() -> str:
    """Return the non-authoritative Pydantic projection digest for diagnostics."""

    definition, _ = load_release_profile_definition_v3()
    return canonical_digest(definition)


_PROFILE, _PROFILE_DIGEST = load_release_profile_definition_v3()
REQUIRED_CAPABILITY_IDS_V3 = tuple(_PROFILE.all_required_capability_ids)


@dataclass(frozen=True)
class ReleaseAuthorityTargetV3:
    profile_id: str
    profile_definition: BoundedHybridReleaseProfileDefinitionV3
    profile_definition_digest: str
    scope: CapabilityScopeV2
    environment: CapabilityEnvironmentV2
    configured: bool


def release_authority_target_v3_from_environment() -> ReleaseAuthorityTargetV3:
    definition, definition_digest = load_release_profile_definition_v3()
    deployment = os.getenv("AUTHORED_RELEASE_DEPLOYMENT_ENVIRONMENT", "development")
    if deployment not in {"development", "staging", "production"}:
        deployment = "development"
    digest_names = (
        "AUTHORED_RELEASE_RENDERER_IMAGE_DIGEST",
        "AUTHORED_RELEASE_FONT_INVENTORY_DIGEST",
        "AUTHORED_RELEASE_BACKEND_BUILD_DIGEST",
    )
    digests = [os.getenv(name, _ZERO_DIGEST) for name in digest_names]
    configured = bool(
        deployment == "production"
        and os.getenv("AUTHORED_RELEASE_OS_BUILD")
        and os.getenv("AUTHORED_RELEASE_OFFICE_BUILD")
        and all(value != _ZERO_DIGEST for value in digests)
    )
    return ReleaseAuthorityTargetV3(
        profile_id=definition.profile_id,
        profile_definition=definition,
        profile_definition_digest=definition_digest,
        scope=CapabilityScopeV2(
            platform=definition.platform_scope.platform,
            renderer=definition.platform_scope.renderer,
            archetypes=definition.platform_scope.archetypes,
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


def _receipt_pins(capability_ids: list[str], ready_by_capability: Mapping[str, str]) -> tuple[bool, list[str]]:
    ready = all(capability_id in ready_by_capability for capability_id in capability_ids)
    return ready, ([ready_by_capability[capability_id] for capability_id in capability_ids] if ready else [])


def _block_capability_entry(
    entries: list[CapabilityProjectionEntryV2],
    *,
    capability_id: str,
    reason_code: str,
) -> None:
    for index, entry in enumerate(entries):
        if entry.capability_id != capability_id:
            continue
        entries[index] = entry.model_copy(
            update={
                "state": "blocked",
                "reason_code": reason_code,
                "receipt_digest": None,
                "expires_at": None,
            }
        )
        return


async def get_workbench_capability_projection_v3(
    session: AsyncSession,
    *,
    presentation_id: uuid.UUID,
    owner_id: uuid.UUID,
    tenant_id: str = "default",
    now: datetime | None = None,
    target: ReleaseAuthorityTargetV3 | None = None,
) -> WorkbenchCapabilityProjectionV3:
    active_emission = await get_active_authored_emission(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    evaluated_at = _utc(now or get_current_utc_datetime())
    target = target or release_authority_target_v3_from_environment()
    definition = target.profile_definition
    from services.authored_vnext.semantic.readiness_validators import (
        trusted_capability_evidence_policy_id,
        trusted_capability_feature_set_digest,
    )

    scope_digest = _digest_model(target.scope)
    environment_digest = _digest_model(target.environment)
    records = [
        item
        for item in await _records(session, tenant_id=tenant_id)
        if item.scope_digest == scope_digest
        and item.environment_digest == environment_digest
        and item.capability_id in definition.all_required_capability_ids
    ]
    active_records = _active_records(records)
    capabilities: list[CapabilityProjectionEntryV2] = []
    ready_by_capability: dict[str, str] = {}
    ready_receipts: dict[str, CapabilityReceiptV2] = {}
    ready_records: dict[str, AuthoredCapabilityReceiptRecordV2] = {}
    for capability_id in definition.all_required_capability_ids:
        matches = [item for item in active_records if item.capability_id == capability_id]
        if len(matches) != 1:
            capabilities.append(
                CapabilityProjectionEntryV2(
                    capability_id=capability_id,
                    state="blocked",
                    reason_code=("capability_receipt_missing" if not matches else "capability_receipt_ambiguous"),
                    scope=target.scope,
                    serializer_id=target.scope.renderer,
                    serializer_version=definition.platform_scope.serializer_version,
                )
            )
            continue
        receipt = _receipt_from_record(matches[0])
        receipt_wire = receipt.model_dump(mode="json", by_alias=True)
        receipt_wire.pop("receiptDigest", None)
        from services.authored_vnext.semantic.release_authority_v3 import (
            validate_actual_product_record_v3,
        )

        receipt_identity_mismatch = (
            receipt.receipt_digest != matches[0].receipt_digest
            or canonical_digest(receipt_wire) != receipt.receipt_digest
            or receipt.capability_id != capability_id
            or receipt.policy_digest != target.profile_definition_digest
            or receipt.evidence_policy_id != trusted_capability_evidence_policy_id(capability_id)
            or receipt.feature_set_digest != trusted_capability_feature_set_digest(capability_id, target)
            or not validate_actual_product_record_v3(matches[0], now=evaluated_at)
        )
        expired = _utc(receipt.expires_at) <= evaluated_at
        serializer_mismatch = (
            receipt.serializer_id != target.scope.renderer
            or receipt.serializer_version != definition.platform_scope.serializer_version
        )
        state = "blocked" if expired or serializer_mismatch or receipt_identity_mismatch else receipt.state
        reason_code = (
            "capability_receipt_expired"
            if expired
            else (
                "capability_receipt_serializer_mismatch"
                if serializer_mismatch
                else ("capability_receipt_profile_mismatch" if receipt_identity_mismatch else receipt.reason_code)
            )
        )
        digest = None if expired or serializer_mismatch or receipt_identity_mismatch else receipt.receipt_digest
        if state == "ready" and digest is not None:
            ready_by_capability[capability_id] = digest
            ready_receipts[capability_id] = receipt
            ready_records[capability_id] = matches[0]
        capabilities.append(
            CapabilityProjectionEntryV2(
                capability_id=capability_id,
                state=state,
                reason_code=reason_code,
                receipt_digest=digest,
                scope=receipt.scope,
                serializer_id=receipt.serializer_id,
                serializer_version=receipt.serializer_version,
                expires_at=receipt.expires_at,
            )
        )

    # Deck and serializer/native receipts are qualification evidence for this
    # exact immutable presentation revision, not a transferable environment
    # credential.  The signed visual authority identifies the one Hybrid
    # export attempt shared by every deck-scoped receipt.  CI remains a
    # separately pinned commit-scoped authority.
    visual_id = definition.visual_evidence_capability_id
    visual_receipt = ready_receipts.get(visual_id)
    visual_authority = visual_receipt.visual_evidence_authority if visual_receipt is not None else None
    visual_binding = visual_authority.binding if visual_authority is not None else None
    manifest_digest = active_emission.manifest.canonical_digest if active_emission.manifest is not None else None
    exact_visual_binding = bool(
        visual_binding is not None
        and visual_binding.presentation_id == presentation_id
        and visual_binding.presentation_revision == active_emission.presentation_revision
        and visual_binding.manifest_digest == active_emission.canonical_digest
        and visual_binding.manifest_digest == manifest_digest
    )
    bound_export_attempt_id = visual_binding.export_attempt_id if exact_visual_binding else None
    if visual_receipt is not None and not exact_visual_binding:
        ready_by_capability.pop(visual_id, None)
        ready_receipts.pop(visual_id, None)
        _block_capability_entry(
            capabilities,
            capability_id=visual_id,
            reason_code="capability_evidence_binding_mismatch",
        )
    deck_scoped_ids = set(definition.serializer_profile_capability_ids)
    for capability_id in sorted(deck_scoped_ids):
        receipt = ready_receipts.get(capability_id)
        if receipt is None:
            continue
        if bound_export_attempt_id is None or receipt.evidence_bundle_id != bound_export_attempt_id:
            ready_by_capability.pop(capability_id, None)
            ready_receipts.pop(capability_id, None)
            _block_capability_entry(
                capabilities,
                capability_id=capability_id,
                reason_code="capability_evidence_bundle_mismatch",
            )
    if bound_export_attempt_id is not None:
        bound_export_binding = await session.scalar(
            select(AuthoredSemanticExportBinding).where(
                AuthoredSemanticExportBinding.tenant_id == tenant_id,
                AuthoredSemanticExportBinding.export_attempt_id == bound_export_attempt_id,
            )
        )
        for capability_id in tuple(ready_by_capability):
            record = ready_records.get(capability_id)
            if (
                record is None
                or record.export_attempt_id != bound_export_attempt_id
                or bound_export_binding is None
                or record.binding_digest != bound_export_binding.binding_digest
            ):
                ready_by_capability.pop(capability_id, None)
                ready_receipts.pop(capability_id, None)
                _block_capability_entry(
                    capabilities,
                    capability_id=capability_id,
                    reason_code="capability_receipt_export_attempt_mismatch",
                )

    from services.authored_vnext.semantic.completion_v3 import (
        sealed_completion_matches_projection_v3,
    )

    completion_ready = await sealed_completion_matches_projection_v3(
        session,
        tenant_id=tenant_id,
        presentation_id=presentation_id,
        presentation_revision=active_emission.presentation_revision,
        active_manifest_digest=active_emission.canonical_digest,
        receipt_by_capability=dict(ready_by_capability),
    )
    if not completion_ready:
        for capability_id in tuple(ready_by_capability):
            _block_capability_entry(
                capabilities,
                capability_id=capability_id,
                reason_code="evidence_bundle_completion_unsealed",
            )
        ready_by_capability.clear()
        ready_receipts.clear()

    degraded_fidelity_only = active_emission.canonical_mode != "bundleGraph" or any(
        getattr(
            getattr(bundle, "semantic_component", None) or getattr(bundle, "compatibility_component", None),
            "failure_code",
            None,
        )
        for bundle in active_emission.bundles
    )
    deck_ready, deck_pins = _receipt_pins(definition.deck_evidence_capability_ids, ready_by_capability)
    serializer_ready, serializer_pins = _receipt_pins(definition.serializer_profile_capability_ids, ready_by_capability)
    # Bounded release v3 is Fidelity/raster-only until a future profile seals a
    # native subset.  Empty native capabilities are N/A, never an implicit
    # native-ready claim.
    native_ready, native_pins = False, []
    commit_ready, commit_pins = _receipt_pins(definition.release_commit_capability_ids, ready_by_capability)
    deck_ready = deck_ready and not degraded_fidelity_only
    if not deck_ready:
        deck_pins = []

    blockers: list[BoundedHybridReleaseBlockerV3] = []
    if not target.configured:
        blockers.append(
            BoundedHybridReleaseBlockerV3(
                blocker_id="release.environment",
                reason_code="release_environment_unconfigured",
            )
        )
    if degraded_fidelity_only:
        blockers.append(
            BoundedHybridReleaseBlockerV3(
                blocker_id="semantic.degradation",
                reason_code="semantic_fidelity_only_degraded",
            )
        )
    if not completion_ready:
        blockers.append(
            BoundedHybridReleaseBlockerV3(
                blocker_id="evidence.completion",
                reason_code="evidence_bundle_completion_unsealed",
            )
        )
    switches = _kill_switches_from_environment(evaluated_at)
    readiness_switches = [
        BoundedHybridKillSwitchV3(
            capability_id=item.capability_id,
            active=item.active,
            reason_code=(item.reason if item.active else None),
        )
        for item in switches
    ]
    promotion_ready = bool(
        deck_ready
        and serializer_ready
        and commit_ready
        and not blockers
        and not any(item.active for item in readiness_switches)
    )
    all_pins = (
        [ready_by_capability[capability_id] for capability_id in definition.all_required_capability_ids]
        if promotion_ready
        else []
    )
    expiries = [
        _utc(receipt.expires_at) for receipt in ready_receipts.values() if _utc(receipt.expires_at) > evaluated_at
    ]
    expires_at = min(expiries) if expiries else evaluated_at + timedelta(minutes=1)
    reason_code = (
        "readiness_incomplete"
        if not all((deck_ready, serializer_ready, commit_ready))
        else (
            "release_blocked"
            if blockers
            else (
                "release_kill_switch_active"
                if any(item.active for item in readiness_switches)
                else "promotion_formula_satisfied"
            )
        )
    )
    profile = BoundedHybridReleaseReadinessV3(
        profile_id=definition.profile_id,
        platform_scope=definition.platform_scope,
        deck_evidence_capability_ids=definition.deck_evidence_capability_ids,
        serializer_profile_capability_ids=(definition.serializer_profile_capability_ids),
        native_subset_status=definition.native_subset_status,
        native_subset_capability_ids=definition.native_subset_capability_ids,
        unsupported_native_capability_ids=definition.unsupported_native_capability_ids,
        release_commit_capability_ids=definition.release_commit_capability_ids,
        all_required_capability_ids=definition.all_required_capability_ids,
        profile_definition_digest=target.profile_definition_digest,
        evaluated_at=evaluated_at,
        expires_at=expires_at,
        deck_evidence_ready=deck_ready,
        serializer_profile_ready=serializer_ready,
        native_subset_ready=native_ready,
        release_commit_ready=commit_ready,
        deck_evidence_receipt_digests=deck_pins,
        serializer_profile_receipt_digests=serializer_pins,
        native_subset_receipt_digests=native_pins,
        release_commit_receipt_digests=commit_pins,
        promotion_receipt_digests=all_pins,
        blockers=blockers,
        kill_switches=readiness_switches,
        deployment_authorized=False,
        promotion_ready=promotion_ready,
        reason_code=reason_code,
    )
    renderer_id = definition.renderer_attestation_capability_id
    renderer_entry = next(item for item in capabilities if item.capability_id == renderer_id)
    renderer_receipt = ready_receipts.get(renderer_id)
    renderer_state = "attested" if renderer_receipt is not None else "unattested"
    draft = WorkbenchCapabilityProjectionV3(
        presentation_id=presentation_id,
        canonical_mode=active_emission.canonical_mode,
        presentation_revision=active_emission.presentation_revision,
        canonical_digest=active_emission.canonical_digest,
        manifest_digest=(active_emission.manifest.canonical_digest if active_emission.manifest is not None else None),
        degraded_fidelity_only=degraded_fidelity_only,
        release_profile=profile,
        capabilities=capabilities,
        renderer_attestation=RendererAttestationProjectionV2(
            state=renderer_state,
            reason_code=renderer_entry.reason_code,
            attestation_digest=renderer_entry.receipt_digest,
            environment=target.environment,
            expires_at=renderer_entry.expires_at,
        ),
        kill_switches=readiness_switches,
        projection_digest=_ZERO_DIGEST,
    )
    digest_value = draft.model_dump(mode="json", by_alias=True)
    for field in (
        "projectionDigest",
        "releaseAuthorityReceiptDigests",
        "releaseProjectionDigest",
        "exportAuthorityClaim",
    ):
        digest_value.pop(field, None)
    projection_digest = canonical_digest(digest_value)
    claim: ExportAuthorityClaimV2 | None = None
    claim_receipts: list[str] = []
    if promotion_ready:
        claim_receipts = all_pins
        claim = ExportAuthorityClaimV2(
            kind="promotion",
            profile_id=definition.profile_id,
            profile_definition_digest=target.profile_definition_digest,
            projection_digest=projection_digest,
            receipt_digests=claim_receipts,
        )
    elif native_ready:
        claim_receipts = native_pins
        claim = ExportAuthorityClaimV2(
            kind="boundedNative",
            profile_id=definition.profile_id,
            profile_definition_digest=target.profile_definition_digest,
            projection_digest=projection_digest,
            receipt_digests=claim_receipts,
        )
    return WorkbenchCapabilityProjectionV3.model_validate(
        draft.model_copy(
            update={
                "projection_digest": projection_digest,
                "release_authority_receipt_digests": claim_receipts,
                "release_projection_digest": (projection_digest if claim is not None else None),
                "export_authority_claim": claim,
            }
        ).model_dump(mode="python", by_alias=False)
    )


async def issue_capability_receipt_v3(
    session: AsyncSession,
    *,
    request: CapabilityReceiptIssueRequestV2,
    issuer_principal_id: uuid.UUID | None,
    issuer_is_admin: bool,
    tenant_id: str = "default",
    now: datetime | None = None,
    target: ReleaseAuthorityTargetV3 | None = None,
    capability_validators: Mapping[str, CapabilityEvidenceValidatorV2] | None = None,
    authority_metadata: Mapping[str, Any] | None = None,
) -> CapabilityReceiptV2:
    target = target or release_authority_target_v3_from_environment()
    if capability_validators is None:
        from services.authored_vnext.semantic.readiness_validators import (
            get_trusted_capability_validators,
        )

        capability_validators = get_trusted_capability_validators()
    return await issue_capability_receipt(
        session,
        request=request,
        issuer_principal_id=issuer_principal_id,
        issuer_is_admin=issuer_is_admin,
        tenant_id=tenant_id,
        now=now,
        release_target=target,  # shared exact scope/environment protocol
        visual_evidence_validator=validate_visual_evidence_authority,
        capability_validators=capability_validators,
        allowed_capability_ids=REQUIRED_CAPABILITY_IDS_V3,
        authority_metadata=authority_metadata,
    )


def validate_export_authority_claim_v3(
    projection: WorkbenchCapabilityProjectionV3, claim: ExportAuthorityClaimV2
) -> None:
    expected = projection.export_authority_claim
    if expected is None or claim != expected:
        raise SemanticContractError("authored_export_authority_claim_v3_invalid")


__all__ = [
    "REQUIRED_CAPABILITY_IDS_V3",
    "ReleaseAuthorityTargetV3",
    "get_workbench_capability_projection_v3",
    "issue_capability_receipt_v3",
    "load_release_profile_definition_v3",
    "load_release_profile_model_projection_digest_v3",
    "release_authority_target_v3_from_environment",
    "validate_export_authority_claim_v3",
]
