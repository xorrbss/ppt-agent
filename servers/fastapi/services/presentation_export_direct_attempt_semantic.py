"""Semantic sealing helpers for direct presentation export attempts."""

from __future__ import annotations

import os
from typing import Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    AuthoredEmissionBundleV1,
    ExportAttemptDigestBindingV1,
    SemanticUnavailableReceiptV1,
)
from models.authored_vnext.semantic.readiness import (
    ExportAuthorityClaimV2,
    ReleasePinnedExportAttemptDigestBindingV2,
    WorkbenchCapabilityProjectionV2,
)
from models.authored_vnext.semantic.release_profile_v3 import (
    WorkbenchCapabilityProjectionV3,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.slide import SlideModel
from services.authored_vnext.semantic.canonical import (
    canonical_digest as semantic_canonical_digest,
)
from services.authored_vnext.semantic.export_binding import (
    bind_existing_export_attempt,
)
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.readiness import (
    build_export_authority_claim,
    get_workbench_capability_projection_v2,
    validate_export_authority_claim,
)
from services.authored_vnext.semantic.readiness_v3 import (
    get_workbench_capability_projection_v3,
    validate_export_authority_claim_v3,
)
from services.presentation_export_attempt_service import build_export_source_snapshot_sha256
from services.presentation_export_capabilities import (
    PresentationExportContractError,
    PresentationExportFreshnessError,
)
from utils.owner_context import get_current_owner_id


def prefixed_digest(value: str) -> str:
    return value if value.startswith("sha256:") else f"sha256:{value}"


def release_authority_pin_from_projection(
    projection: WorkbenchCapabilityProjectionV2 | WorkbenchCapabilityProjectionV3,
    *,
    export_mode: str,
) -> dict[str, Any]:
    if isinstance(projection, WorkbenchCapabilityProjectionV3):
        profile = projection.release_profile
        claim_kind = (
            "boundedNative"
            if export_mode == "hybrid" and profile.native_subset_status != "notApplicable"
            else "promotion"
        )
        claim_ready = (
            profile.native_subset_ready
            if claim_kind == "boundedNative"
            else profile.promotion_ready
        )
        pin: dict[str, Any] = {
            "schemaVersion": "authored-export-release-authority-pin/v3",
            "state": "ready" if claim_ready else "nonPromotable",
            "kind": claim_kind,
            "profileId": profile.profile_id,
            "profileDefinitionDigest": profile.profile_definition_digest,
            "projectionDigest": projection.projection_digest,
            "reasonCode": profile.reason_code,
        }
        if claim_ready:
            receipt_digests = (
                profile.native_subset_receipt_digests
                if claim_kind == "boundedNative"
                else profile.promotion_receipt_digests
            )
            pin["claim"] = ExportAuthorityClaimV2(
                kind=claim_kind,
                profile_id=profile.profile_id,
                profile_definition_digest=profile.profile_definition_digest,
                projection_digest=projection.projection_digest,
                receipt_digests=receipt_digests,
            ).model_dump(mode="json", by_alias=True)
        return pin
    profile = projection.release_profile
    claim_kind = (
        "boundedNative"
        if export_mode == "hybrid" and profile.bounded_native_parity_ready
        else "promotion"
    )
    claim_ready = (
        profile.bounded_native_parity_ready
        if claim_kind == "boundedNative"
        else profile.promotion_ready
    )
    pin: dict[str, Any] = {
        "schemaVersion": "authored-export-release-authority-pin/v2",
        "state": "ready" if claim_ready else "nonPromotable",
        "kind": claim_kind,
        "profileId": projection.release_profile.profile_id,
        "profileDefinitionDigest": projection.release_profile.profile_definition_digest,
        "projectionDigest": projection.projection_digest,
        "reasonCode": projection.release_profile.reason_code,
    }
    if claim_ready:
        pin["claim"] = build_export_authority_claim(
            projection, kind=claim_kind
        ).model_dump(mode="json", by_alias=True)
    return pin


def validate_v3_release_authority_claim(
    projection: WorkbenchCapabilityProjectionV3,
    claim: ExportAuthorityClaimV2,
    *,
    export_mode: str,
) -> None:
    """Validate the mode-scoped v3 claim without making deployment a gate."""

    expected_pin = release_authority_pin_from_projection(
        projection, export_mode=export_mode
    )
    expected_payload = expected_pin.get("claim")
    if expected_payload is None:
        raise PresentationExportContractError(
            "presentation_export_release_authority_claim_unavailable"
        )
    try:
        expected_claim = ExportAuthorityClaimV2.model_validate(expected_payload)
    except ValueError as error:
        raise PresentationExportContractError(
            "presentation_export_release_authority_pin_invalid"
        ) from error
    if claim != expected_claim:
        raise PresentationExportContractError(
            "presentation_export_release_authority_claim_invalid"
        )
    try:
        pinned_projection = WorkbenchCapabilityProjectionV3.model_validate(
            projection.model_copy(
                update={
                    "release_authority_receipt_digests": claim.receipt_digests,
                    "release_projection_digest": claim.projection_digest,
                    "export_authority_claim": claim,
                }
            ).model_dump(mode="python", by_alias=False)
        )
        validate_export_authority_claim_v3(pinned_projection, claim)
    except ValueError as error:
        raise PresentationExportContractError(
            "presentation_export_release_authority_claim_invalid"
        ) from error


def completed_export_binding(
    binding_values: dict[str, Any],
    claim: ExportAuthorityClaimV2 | None,
) -> ExportAttemptDigestBindingV1 | ReleasePinnedExportAttemptDigestBindingV2:
    if claim is None:
        return ExportAttemptDigestBindingV1(**binding_values)
    return ReleasePinnedExportAttemptDigestBindingV2(
        **binding_values,
        release_authority_receipt_digests=claim.receipt_digests,
        release_projection_digest=claim.projection_digest,
        export_authority_claim=claim,
    )


async def active_semantic_emission(
    session: AsyncSession,
    presentation_id: uuid.UUID,
):
    owner_id = get_current_owner_id()
    if owner_id is None:
        return None
    return await get_active_authored_emission(
        session,
        presentation_id=presentation_id,
        owner_id=owner_id,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
    )


async def current_export_canonical_digest(
    session: AsyncSession,
    *,
    presentation: PresentationModel,
    slides: list[SlideModel],
) -> str:
    """Resolve the same canonical source selected by the begin boundary."""

    active = await active_semantic_emission(session, presentation.id)
    if active is not None and active.canonical_mode == "bundleGraph":
        if (
            active.presentation_revision != presentation.revision
            or active.canonical_digest is None
        ):
            raise PresentationExportFreshnessError(
                "authored_semantic_export_stale_manifest"
            )
        return active.canonical_digest.removeprefix("sha256:")
    return build_export_source_snapshot_sha256(presentation, slides)


async def bind_completed_semantic_export_attempt(
    session: AsyncSession,
    *,
    attempt: PresentationExportAttempt,
    mode: str,
    renderer_source_sha256: str,
    pptx_sha256: str,
    receipt_payloads: list[dict[str, Any]],
    owner_id: uuid.UUID | None = None,
) -> str | None:
    semantic_owner_id = owner_id or get_current_owner_id()
    if semantic_owner_id is None:
        return None
    active = await get_active_authored_emission(
        session,
        presentation_id=attempt.presentation_id,
        owner_id=semantic_owner_id,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
    )
    if (
        active is None
        or active.canonical_mode != "bundleGraph"
        or active.manifest is None
        or active.manifest.canonical_digest is None
    ):
        return None
    manifest = active.manifest
    if (
        active.presentation_revision != attempt.source_revision
        or active.canonical_digest is None
        or active.canonical_digest.removeprefix("sha256:")
        != attempt.source_snapshot_sha256
    ):
        raise PresentationExportFreshnessError(
            "authored_semantic_export_stale_manifest"
        )
    semantic_digests: list[str] = []
    origins: list[uuid.UUID | None] = []
    for bundle in active.bundles:
        if not isinstance(bundle, AuthoredEmissionBundleV1):
            raise PresentationExportContractError(
                "authored_semantic_export_bundle_kind_unsupported"
            )
        if bundle.semantic_component.digest is None:
            raise PresentationExportContractError(
                "authored_semantic_export_component_digest_missing"
            )
        semantic_digests.append(bundle.semantic_component.digest)
        origins.append(bundle.origin_generation_attempt_id)
    binding_values = dict(
        export_attempt_id=attempt.id,
        presentation_id=attempt.presentation_id,
        presentation_revision=attempt.source_revision,
        operation_id=manifest.operation_id,
        generation_attempt_id=manifest.generation_attempt_id,
        slide_origin_generation_attempt_ids=origins,
        canonical_digest=active.canonical_digest,
        manifest_digest=manifest.canonical_digest,
        semantic_digests=semantic_digests,
        master_contract_digest=manifest.master_contract_digest,
        composition_digest=manifest.composition_digest,
        font_manifest_digest=manifest.font_manifest_digest,
        asset_manifest_digest=manifest.asset_manifest_digest,
        quality_policy_digest=semantic_canonical_digest(
            {"qualityPolicyVersion": manifest.quality_policy_version}
        ),
        mode=mode,
        renderer_source_digest=prefixed_digest(renderer_source_sha256),
        serializer_id=("authored-hybrid" if mode == "hybrid" else "authored-fidelity"),
        serializer_version=("2" if mode == "hybrid" else "1"),
        receipt_digests=[
            semantic_canonical_digest(payload) for payload in receipt_payloads
        ],
        pptx_digest=prefixed_digest(pptx_sha256),
    )
    release_pin = (
        ((attempt.provenance or {}).get("capability_evidence") or {}).get(
            "release_authority"
        )
        or {}
    )
    claim_payload = release_pin.get("claim")
    if claim_payload is None:
        claim = None
    else:
        try:
            claim = ExportAuthorityClaimV2.model_validate(claim_payload)
        except ValueError as error:
            raise PresentationExportContractError(
                "presentation_export_release_authority_pin_invalid"
            ) from error
        pin_schema = release_pin.get("schemaVersion")
        if pin_schema == "authored-export-release-authority-pin/v3":
            projection_v3 = await get_workbench_capability_projection_v3(
                session,
                presentation_id=attempt.presentation_id,
                owner_id=semantic_owner_id,
                tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
            )
            validate_v3_release_authority_claim(
                projection_v3, claim, export_mode=mode
            )
        elif pin_schema == "authored-export-release-authority-pin/v2":
            projection_v2 = await get_workbench_capability_projection_v2(
                session,
                presentation_id=attempt.presentation_id,
                owner_id=semantic_owner_id,
                tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
            )
            validate_export_authority_claim(projection_v2, claim)
        else:
            raise PresentationExportContractError(
                "presentation_export_release_authority_pin_schema_invalid"
            )
    binding = completed_export_binding(binding_values, claim)
    sealed = await bind_existing_export_attempt(
        session,
        owner_id=semantic_owner_id,
        binding=binding,
        tenant_id=os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
    )
    return sealed.binding_digest


__all__ = [
    "active_semantic_emission",
    "bind_completed_semantic_export_attempt",
    "completed_export_binding",
    "current_export_canonical_digest",
    "prefixed_digest",
    "release_authority_pin_from_projection",
    "validate_v3_release_authority_claim",
]
