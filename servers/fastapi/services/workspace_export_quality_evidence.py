"""Quality-evidence validation and digest binding for workspace exports.

This module owns the read-only evidence rules an export operation applies
before it may publish: verifying the public render-identity receipt returned by
the render worker, verifying and re-sealing the persisted authored
quality-receipt/v3 digest, and the narrow legacy-Fidelity compatibility
bindings used when a deck predates V3 evidence entirely.

It was split out of ``services/workspace_quality_export_operation_handlers.py``
so that module keeps only the operation handlers themselves. Everything here is
a pure function over already-loaded payloads: no session, no lease, no I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime

from models.public_v2_contracts import QualityReceiptV3 as PublicQualityReceiptV3
from services.authored_quality_receipt_v2 import validate_current_quality_receipt_payload
from models.authored_vnext.capability_registry import CAPABILITY_REGISTRY_DIGEST
from services.public_export_operation import (
    PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
    PublicExportOperationError,
    public_render_runtime_binding_digest,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
)
from utils.hashing import canonical_json_digest, canonical_json_sha256


_PUBLISHED_FLAGGED_HYBRID_BLOCKER = re.compile(
    r"providerCalls\.slide_budget_invalid:(?:0|[1-9][0-9]*)"
)


def _published_flagged_hybrid_allowed(
    gate: Mapping[str, object], receipt: Mapping[str, object], *, published: bool
) -> bool:
    if (
        not published
        or gate.get("documentStatus") != "completed"
        or gate.get("documentCompleted") is not True
    ):
        return False
    try:
        v2 = validate_current_quality_receipt_payload(gate)
    except ValueError:
        return False
    flagged = gate.get("flaggedSlides")
    slide_count = gate.get("slideCount")
    if (
        not v2.gate_passed
        or not isinstance(slide_count, int)
        or isinstance(slide_count, bool)
        or not isinstance(flagged, Sequence)
        or isinstance(flagged, (str, bytes))
        or not flagged
    ):
        return False
    indices: list[int] = []
    for item in flagged:
        if (
            not isinstance(item, Mapping)
            or item.get("remakeOutcome") != "verbatim_flag_only"
        ):
            return False
        index = item.get("index")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < slide_count
        ):
            return False
        indices.append(index)
    blockers = receipt.get("blockers")
    return (
        len(indices) == len(set(indices))
        and receipt.get("gatePassed") is False
        and isinstance(blockers, list)
        and bool(blockers)
        and all(
            blocker == "visualReview.failed"
            or (
                isinstance(blocker, str)
                and _PUBLISHED_FLAGGED_HYBRID_BLOCKER.fullmatch(blocker) is not None
            )
            for blocker in blockers
        )
    )


def _validated_public_export_render_identity(
    resource_metrics: Mapping[str, object],
    *,
    expected_runtime_binding_digest: str | None = None,
    expected_deck_render_identity_digest: str | None = None,
) -> tuple[str, str]:
    receipt = resource_metrics.get("public_export_execution_receipt")
    if not isinstance(receipt, Mapping) or receipt.get("schemaVersion") != "public-export-execution-receipt/v1":
        raise NonRetryableWorkspaceOperationError(
            "public_export_execution_receipt_invalid"
        )
    runtime_identity = receipt.get("renderRuntimeIdentity")
    if not isinstance(runtime_identity, Mapping):
        raise NonRetryableWorkspaceOperationError(
            "public_export_render_runtime_identity_invalid"
        )
    supplied_runtime_digest = runtime_identity.get("bindingDigest")
    unsigned_runtime_identity = dict(runtime_identity)
    unsigned_runtime_identity.pop("bindingDigest", None)
    try:
        recomputed_runtime_digest = public_render_runtime_binding_digest(
            unsigned_runtime_identity
        )
    except PublicExportOperationError as error:
        raise NonRetryableWorkspaceOperationError(str(error)) from error
    deck_render_identity_digest = receipt.get("deckRenderIdentityDigest")
    if (
        not isinstance(supplied_runtime_digest, str)
        or supplied_runtime_digest != recomputed_runtime_digest
        or not isinstance(deck_render_identity_digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", deck_render_identity_digest)
    ):
        raise NonRetryableWorkspaceOperationError(
            "public_export_render_identity_invalid"
        )
    if (
        expected_runtime_binding_digest is not None
        and supplied_runtime_digest != expected_runtime_binding_digest
    ):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_runtime_changed"
        )
    if (
        expected_deck_render_identity_digest is not None
        and deck_render_identity_digest != expected_deck_render_identity_digest
    ):
        raise NonRetryableWorkspaceOperationError(
            "release_quality_review_render_identity_changed"
        )
    return supplied_runtime_digest, deck_render_identity_digest


def _validated_authored_quality_receipt_digest(
    theme: object,
    *,
    allow_visual_review_pending: bool = False,
    allow_published_flagged_hybrid: bool = False,
    allow_quality_advisory: bool = False,
) -> tuple[str, str | None]:
    """Return a sealed V3 digest and any export compatibility used.

    A regular source-preserving export may carry a failed or unassessed quality
    receipt as advisory evidence.  This does not relax the receipt schema or
    digest binding, and release-review exports never opt into the advisory path.
    """

    if not isinstance(theme, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    gate = theme.get("authoredQualityGate")
    receipt = gate.get("qualityReceiptV3") if isinstance(gate, Mapping) else None
    if not isinstance(receipt, Mapping):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_required")
    blockers = receipt.get("blockers")
    visual_review_pending = (
        allow_visual_review_pending
        and receipt.get("gatePassed") is False
        and blockers == ["visualReview.required_failed"]
    )
    published_flagged_hybrid = (
        allow_published_flagged_hybrid
        and _published_flagged_hybrid_allowed(gate, receipt, published=True)
    )
    quality_advisory = (
        allow_quality_advisory
        and receipt.get("gatePassed") is False
    )
    if receipt.get("schemaVersion") != "quality-receipt/v3" or not (
        receipt.get("gatePassed") is True
        or visual_review_pending
        or published_flagged_hybrid
        or quality_advisory
    ):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_blocked")
    receipt_digest = receipt.get("receiptDigest")
    sealed_fields = {
        "schemaVersion",
        "attemptId",
        "revision",
        "sequence",
        "eventType",
        "evidenceDigest",
        "capabilityRegistryDigest",
        "capabilityConsumerStatuses",
        "qualityTier",
        "t1Triggers",
        "blockers",
        "gatePassed",
        "previousSchemaVersion",
        "previousVersionReceiptDigest",
        "previousReceiptDigest",
    }
    if (
        not isinstance(receipt_digest, str)
        or not receipt_digest.startswith("sha256:")
        or len(receipt_digest) != 71
        or not sealed_fields.issubset(receipt)
    ):
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_digest_invalid")
    sealed = {field: receipt[field] for field in sealed_fields}
    actual = canonical_json_digest(sealed, allow_nan=True)
    if actual != receipt_digest:
        raise NonRetryableWorkspaceOperationError("quality_receipt_v3_digest_invalid")
    compatibility = (
        "visual-review-pending"
        if visual_review_pending
        else "published-flagged-hybrid"
        if published_flagged_hybrid
        else "quality-advisory"
        if quality_advisory
        else None
    )
    return receipt_digest.removeprefix("sha256:"), compatibility


def _legacy_fidelity_quality_digest(scene_digest: str) -> str:
    """Bind legacy reuse to the exact deck without claiming a quality pass."""

    payload = {
        "schemaVersion": "legacy-authored-fidelity-quality/v1",
        "sceneDigest": scene_digest,
        "releaseReadiness": "blocked",
        "reasonCode": "quality_receipt_v3_required",
    }
    return canonical_json_sha256(payload, ensure_ascii=True, allow_nan=True)


def _resolved_authored_quality_digest(
    theme: object,
    *,
    scene_digest: str,
    pptx_mode: str,
    published: bool = False,
    allow_quality_advisory: bool = False,
) -> tuple[str, str | None]:
    """Resolve strict evidence or a source-preserving export compatibility."""

    try:
        digest, compatibility = _validated_authored_quality_receipt_digest(
            theme,
            allow_visual_review_pending=pptx_mode == "fidelity",
            allow_published_flagged_hybrid=published and pptx_mode == "hybrid",
            allow_quality_advisory=allow_quality_advisory,
        )
        return digest, compatibility
    except NonRetryableWorkspaceOperationError as error:
        # Legacy compatibility covers only a wholly absent V3 receipt. A
        # present receipt remains bound and must be well-formed; ordinary
        # source-preserving exports may carry its failed state as advisory.
        if error.code != "quality_receipt_v3_required":
            raise
        return _legacy_fidelity_quality_digest(scene_digest), "legacy"


def _legacy_fidelity_public_quality_receipt(
    *,
    quality_receipt_id: str,
    operation_id: str,
    presentation_id: str,
    artifact_digest: str,
    source_revision: int,
    scene_digest: str,
    evaluated_at: datetime,
) -> dict[str, object]:
    """Describe absent legacy checker evidence truthfully while allowing Fidelity delivery."""

    return PublicQualityReceiptV3(
        quality_receipt_id=quality_receipt_id,
        operation_id=operation_id,
        presentation_id=presentation_id,
        artifact_digest=artifact_digest,
        source_revision=source_revision,
        scene_digest=scene_digest,
        capability_registry_digest=CAPABILITY_REGISTRY_DIGEST,
        exporter_version=PUBLIC_RENDER_WORKER_PROTOCOL_VERSION,
        evaluated_at=evaluated_at,
        content_brief={"required": False, "status": "not_required"},
        authoring={
            "required": True,
            "status": "failed",
            "reasonCode": "legacy_authoring_evidence_missing",
        },
        geometry_font={
            "required": True,
            "status": "failed",
            "reasonCode": "legacy_geometry_font_evidence_missing",
        },
        image_content_integrity={"required": False, "status": "not_required"},
        visual_review={
            "required": False,
            "status": "unavailable",
            "reasonCode": "legacy_visual_review_evidence_missing",
        },
        performance={
            "required": False,
            "status": "not_assessed",
            "reasonCode": "legacy_performance_evidence_missing",
        },
        native_coverage_ooxml={
            "required": False,
            "status": "pending",
            "reasonCode": "ooxml_checker_evidence_not_persisted",
        },
        release_readiness="blocked",
    ).model_dump(mode="json", by_alias=True)
