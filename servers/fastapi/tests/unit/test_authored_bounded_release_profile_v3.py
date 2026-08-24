from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from models.authored_vnext.semantic.readiness import ReleaseProfileDefinitionV2
from models.authored_vnext.semantic.release_profile_v3 import (
    BoundedHybridKillSwitchV3,
    BoundedHybridReleaseProfileDefinitionV3,
    BoundedHybridReleaseReadinessV3,
    WorkbenchCapabilityProjectionV3,
)
from services.authored_vnext.semantic.canonical import canonical_digest
from services.authored_vnext.semantic.readiness_v3 import load_release_profile_definition_v3
from services.presentation_export_direct_attempt_service import (
    _release_authority_pin_from_projection,
)


_ROOT = Path(__file__).resolve().parents[4]
_V2_PROFILE = _ROOT / "contracts/authored_vnext/semantic/release-profile.v2.json"
_V3_PROFILE = _ROOT / "contracts/authored_vnext/semantic/release-profile.v3.json"


def _projection(**updates) -> BoundedHybridReleaseReadinessV3:
    now = datetime(2026, 8, 23, tzinfo=timezone.utc)
    definition = json.loads(_V3_PROFILE.read_text(encoding="utf-8"))
    receipt_by_capability = {
        capability_id: canonical_digest({"capabilityId": capability_id})
        for capability_id in definition["allRequiredCapabilityIds"]
    }
    def receipt_list(field: str) -> list[str]:
        return [
            receipt_by_capability[capability_id]
            for capability_id in definition[field]
        ]
    payload = {
        "profile_id": "authored-windows-powerpoint-hybrid-v1",
        "platform_scope": definition["platformScope"],
        "deck_evidence_capability_ids": definition[
            "deckEvidenceCapabilityIds"
        ],
        "serializer_profile_capability_ids": definition[
            "serializerProfileCapabilityIds"
        ],
        "native_subset_capability_ids": definition[
            "nativeSubsetCapabilityIds"
        ],
        "release_commit_capability_ids": definition[
            "releaseCommitCapabilityIds"
        ],
        "all_required_capability_ids": definition[
            "allRequiredCapabilityIds"
        ],
        "profile_definition_digest": load_release_profile_definition_v3()[1],
        "evaluated_at": now,
        "expires_at": now + timedelta(days=1),
        "deck_evidence_ready": True,
        "serializer_profile_ready": True,
        "native_subset_status": definition["nativeSubsetStatus"],
        "unsupported_native_capability_ids": definition["unsupportedNativeCapabilityIds"],
        "native_subset_ready": False,
        "release_commit_ready": True,
        "deck_evidence_receipt_digests": receipt_list(
            "deckEvidenceCapabilityIds"
        ),
        "serializer_profile_receipt_digests": receipt_list(
            "serializerProfileCapabilityIds"
        ),
        "native_subset_receipt_digests": [],
        "release_commit_receipt_digests": receipt_list(
            "releaseCommitCapabilityIds"
        ),
        "promotion_receipt_digests": receipt_list(
            "allRequiredCapabilityIds"
        ),
        "blockers": [],
        "kill_switches": [],
        # Deliberately false: deployment authorization is downstream of this
        # evidence formula and cannot alter promotionReady.
        "deployment_authorized": False,
        "promotion_ready": True,
        "reason_code": "promotion_formula_satisfied",
    }
    payload.update(updates)
    return BoundedHybridReleaseReadinessV3(**payload)


def test_bounded_release_v3_formula_and_v2_side_by_side_compatibility() -> None:
    v3_payload = json.loads(_V3_PROFILE.read_text(encoding="utf-8"))
    definition = BoundedHybridReleaseProfileDefinitionV3.model_validate(v3_payload)
    assert definition.promotion_readiness_fields == [
        "deckEvidenceReady",
        "serializerProfileReady",
        "releaseCommitReady",
    ]
    assert definition.platform_scope.archetypes == [
        "boundary",
        "chart",
        "diagram",
        "image-crop",
        "long-text",
        "mixed-css",
        "multi-column",
        "table",
    ]
    assert definition.renderer_attestation_capability_id == "renderer.attestation"
    assert definition.visual_evidence_capability_id == "evidence.visual"
    assert (
        definition.visual_evidence_policy.required_archetypes
        == definition.platform_scope.archetypes
    )
    assert definition.native_subset_status == "notApplicable"
    assert definition.native_subset_capability_ids == []
    assert set(definition.unsupported_native_capability_ids).isdisjoint(
        definition.all_required_capability_ids
    )
    assert set(definition.all_required_capability_ids) == set().union(
        definition.deck_evidence_capability_ids,
        definition.serializer_profile_capability_ids,
        definition.native_subset_capability_ids,
        definition.release_commit_capability_ids,
    )
    assert definition.full_native_parity_status == "notApplicable"
    assert definition.independent_vnext_generation_status == "notApplicable"

    ready = _projection()
    assert ready.promotion_ready is True
    assert ready.deployment_authorized is False
    wire = ready.model_dump(mode="json", by_alias=True)
    assert wire["fullNativeParityStatus"] == "notApplicable"
    assert wire["independentVnextGenerationStatus"] == "notApplicable"
    assert "fullNativeParityReady" not in wire
    assert "independentVnextGenerationReady" not in wire

    for field, receipts_field in (
        ("deck_evidence_ready", "deck_evidence_receipt_digests"),
        ("serializer_profile_ready", "serializer_profile_receipt_digests"),
        ("release_commit_ready", "release_commit_receipt_digests"),
    ):
        with pytest.raises(
            ValidationError, match="authored_release_v3_promotion_formula_invalid"
        ):
            _projection(**{field: False, receipts_field: []})
    with pytest.raises(
        ValidationError, match="authored_release_v3_promotion_formula_invalid"
    ):
        _projection(blockers=[{"blockerId": "evidence.missing", "reasonCode": "evidence_missing"}])
    with pytest.raises(
        ValidationError, match="authored_release_v3_promotion_formula_invalid"
    ):
        _projection(
            kill_switches=[
                BoundedHybridKillSwitchV3(
                    capability_id="hybrid.all",
                    active=True,
                    reason_code="emergency_stop",
                )
            ]
        )
    with pytest.raises(ValidationError, match="authored_release_v3_native_subset_not_applicable"):
        _projection(native_subset_ready=True)

    # Compatibility strategy: version dispatch remains explicit and side by
    # side. V2 keeps its own model/wire schema; V3 never consumes or derives
    # authority from V2's deprecated readiness booleans.
    v2_payload = json.loads(_V2_PROFILE.read_text(encoding="utf-8"))
    assert ReleaseProfileDefinitionV2.model_validate(v2_payload).schema_version == (
        "authored-release-profile-definition/v2"
    )
    with pytest.raises(ValidationError):
        BoundedHybridReleaseProfileDefinitionV3.model_validate(v2_payload)
    with pytest.raises(ValidationError):
        ReleaseProfileDefinitionV2.model_validate(v3_payload)
    invalid_union = {
        **v3_payload,
        "allRequiredCapabilityIds": v3_payload["allRequiredCapabilityIds"][:-1],
    }
    with pytest.raises(
        ValidationError, match="authored_release_v3_capability_sets_invalid"
    ):
        BoundedHybridReleaseProfileDefinitionV3.model_validate(invalid_union)
    with pytest.raises(
        ValidationError, match="authored_release_v3_promotion_receipts_invalid"
    ):
        _projection(promotion_receipt_digests=[])
    with pytest.raises(ValidationError):
        BoundedHybridReleaseReadinessV3.model_validate(
            {
                **wire,
                "fullNativeParityReady": False,
                "independentVnextGenerationReady": False,
            }
        )


def test_direct_export_authority_uses_v3_mode_claims_not_deployment() -> None:
    projection = WorkbenchCapabilityProjectionV3.model_construct(
        release_profile=_projection(),
        projection_digest=canonical_digest({"projection": "direct-export"}),
    )
    hybrid = _release_authority_pin_from_projection(
        projection, export_mode="hybrid"
    )
    fidelity = _release_authority_pin_from_projection(
        projection, export_mode="fidelity"
    )

    assert projection.release_profile.deployment_authorized is False
    assert hybrid["schemaVersion"] == "authored-export-release-authority-pin/v3"
    assert hybrid["state"] == "ready"
    assert hybrid["kind"] == "promotion"
    assert hybrid["claim"]["receiptDigests"] == (
        projection.release_profile.promotion_receipt_digests
    )
    assert fidelity["state"] == "ready"
    assert fidelity["kind"] == "promotion"
    assert fidelity["claim"]["receiptDigests"] == (
        projection.release_profile.promotion_receipt_digests
    )
