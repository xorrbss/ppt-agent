from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from models.authored_vnext.semantic.contracts import ChartElementV1, ExportAttemptDigestBindingV1
from models.authored_vnext.semantic.readiness import (
    CapabilityEnvironmentV2,
    CapabilityReceiptIssueRequestV2,
    CapabilityScopeV2,
)
from models.sql.authored_semantic_bundle import AuthoredSemanticExportBinding
from models.sql.presentation_export_attempt import PresentationExportAttempt, PresentationExportAttemptProvenance
from services.authored_vnext.semantic import readiness_validators
from services.authored_vnext.semantic.canonical import SemanticContractError, canonical_digest, seal_export_binding
from services.authored_vnext.semantic.readiness_v3 import ReleaseAuthorityTargetV3, load_release_profile_definition_v3
from services.authored_vnext.semantic.release_evidence_v3 import validate_ci_required_checks_evidence_v3


def _digest(character: str) -> str:
    return "sha256:" + character * 64


def _qa(*, native_table: int = 1) -> dict[str, object]:
    chart = {
        "chartType": 51,
        "title": {"present": True, "text": "Performance", "typeface": "Aptos", "sizePt": 12},
        "series": [{"name": "Revenue", "categories": ["Q1", "Q2"], "values": [12, 18]}],
        "axes": {
            "category": {
                "present": True,
                "hasTitle": True,
                "title": "Quarter",
                "tickLabelTypeface": "Aptos",
                "tickLabelSizePt": 10,
            },
            "value": {
                "present": True,
                "hasTitle": True,
                "title": "Value",
                "tickLabelTypeface": "Aptos",
                "tickLabelSizePt": 10,
            },
        },
        "legend": {"present": True, "position": -4152, "typeface": "Aptos", "sizePt": 10},
    }
    return {
        "status": "passed",
        "platform": "windows",
        "supported": True,
        "automation": "PowerPoint.COM",
        "reopened": True,
        "rendered": True,
        "readback": True,
        "repairDialogs": 0,
        "renderedPngSha256": ["a" * 64],
        "nativeText": 0,
        "nativeImage": 1,
        "nativeShape": 0,
        "nativeTable": native_table,
        "nativeChart": 1,
        "nativeImageCrop": 1,
        "semanticPayloadBinding": "passed",
        "renderHashesMatch": True,
        "readbackMatch": True,
        "firstOpenChartReadback": [{"slideNumber": 1, "elementId": "chart.performance", "chart": chart}],
        "reopenChartReadback": [{"slideNumber": 1, "elementId": "chart.performance", "chart": chart}],
    }


def _coverage(attempt_id: uuid.UUID) -> dict[str, object]:
    counts = {
        "nativeText": 0,
        "nativeImage": 1,
        "nativeShape": 0,
        "nativeTable": 1,
        "nativeChart": 1,
        "nativeImageCrop": 1,
        "rasterRemaining": 0,
    }
    slide = {
        "slideNumber": 1,
        **counts,
        "logicalCoverage": counts,
        "ooxmlReadback": {"status": "passed", **counts},
        "backplateCount": 1,
        "typographyIssues": [],
        "fallbackReasons": [],
        "visualParity": "not-verified",
        "nativeBackplateDeDuplication": "applied",
        "semanticReceipts": {"hybridPlanDigest": _digest("8")},
    }
    return {
        "schemaVersion": "presenton.authored-hybrid-coverage/v2",
        "mode": "hybrid",
        "cjkCoverageProfileId": "ja-ko-zh-bounded-fixture-v1",
        "nativeTextPolicy": {
            "koreanLatin": "bounded-native",
            "japanese": "raster-required",
            "chinese": "raster-required",
        },
        "source": {
            "exportAttemptId": str(attempt_id),
            "presentationRevision": 1,
            "canonicalSourceDigest": "b" * 64,
            "sourceDigest": "c" * 64,
        },
        "totalSlides": 1,
        **counts,
        "logicalCoverage": counts,
        "ooxmlReadback": {"status": "passed", **counts},
        "backplateCount": 1,
        "typographyIssues": [],
        "fullyEditable": True,
        "visualParity": "not-verified",
        "slides": [slide],
    }


def _authority_fixture(*, native_table_qa: int = 1):
    attempt_id, presentation_id, owner_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    downgrade = {
        "schemaVersion": "hybrid-layer-downgrade-receipt/browser-v1",
        "state": "closed",
        "reason": None,
        "demotedElementIds": [],
    }
    chart_readback = {
        "slideNumber": 1,
        "elementId": "chart.performance",
        "kind": "chart",
        "latinTypefaces": ["Aptos"],
        "eastAsianTypefaces": ["Aptos"],
        "lineBreakCount": 0,
        "chart": {
            "chartType": "column",
            "title": "Performance",
            "titleLatinTypefaces": ["Aptos"],
            "titleEastAsianTypefaces": ["Aptos"],
            "categories": ["Q1", "Q2"],
            "series": [{"name": "Revenue", "values": [12, 18]}],
            "categoryAxis": {
                "present": True,
                "title": "Quarter",
                "latinTypefaces": ["Aptos"],
                "eastAsianTypefaces": ["Aptos"],
            },
            "valueAxis": {
                "present": True,
                "title": "Value",
                "latinTypefaces": ["Aptos"],
                "eastAsianTypefaces": ["Aptos"],
            },
            "legend": {
                "present": True,
                "title": None,
                "position": "right",
                "latinTypefaces": ["Aptos"],
                "eastAsianTypefaces": ["Aptos"],
            },
        },
    }
    provenance = PresentationExportAttemptProvenance.model_validate(
        {
            "schema_version": 1,
            "adapter_chain": ["general"],
            "capability_evidence": {"export_mode": "hybrid"},
            "asset_digests": {},
            "compiler_runtime": {
                "compiler": "authored-hybrid",
                "compiler_version": "2",
                "runtime": "node",
                "runtime_version": "22",
            },
            "freshness": {
                "source_revision": 1,
                "source_snapshot_sha256": "b" * 64,
                "renderer_source_sha256": "c" * 64,
                "package_sha256": "d" * 64,
                "asset_digests_match": True,
                "package_digest_match": True,
            },
            "coverage_manifest": _coverage(attempt_id),
            "quality_sidecar": {
                "schemaVersion": "presenton.export-quality/v1",
                "slides": [
                    {
                        "slideNumber": 1,
                        "nativeElementReadback": [chart_readback],
                        "paintPlan": {
                            "schemaVersion": "hybrid-paint-plan/browser-v1",
                            "strategy": "single-backplate-fixed-point",
                            "fixedPointReached": True,
                            "closureIterations": 1,
                            "closureDigest": _digest("7"),
                            "hybridPlanDigest": _digest("8"),
                            "slideDemotedToRaster": False,
                            "downgradeReceipt": {**downgrade, "receiptDigest": canonical_digest(downgrade)},
                            "demotions": [],
                        },
                    }
                ],
            },
            "atomic_binding": {
                "presentation_id": presentation_id,
                "export_attempt_id": attempt_id,
                "presentation_revision": 1,
                "canonical_source_sha256": "b" * 64,
                "renderer_source_sha256": "c" * 64,
                "pptx_sha256": "d" * 64,
            },
            "powerpoint_qa": _qa(native_table=native_table_qa),
        }
    )
    attempt = PresentationExportAttempt(
        id=attempt_id,
        owner_id=owner_id,
        owner_scope=str(owner_id),
        presentation_id=presentation_id,
        format="pptx",
        state="succeeded",
        source_revision=1,
        source_snapshot_sha256="b" * 64,
        request_hash="e" * 64,
        idempotency_key_hash="f" * 64,
        requested_adapter="general",
        selected_adapter="general",
        artifact_sha256="d" * 64,
        artifact_size_bytes=1024,
        provenance_schema_version=1,
        provenance=provenance.model_dump(mode="json"),
        started_at=now,
        completed_at=now,
    )
    binding = seal_export_binding(
        ExportAttemptDigestBindingV1(
            export_attempt_id=attempt_id,
            presentation_id=presentation_id,
            presentation_revision=1,
            operation_id=uuid.uuid4(),
            slide_origin_generation_attempt_ids=[None],
            canonical_digest=_digest("b"),
            manifest_digest=_digest("1"),
            semantic_digests=[_digest("2")],
            master_contract_digest=_digest("3"),
            composition_digest=_digest("4"),
            font_manifest_digest=_digest("5"),
            asset_manifest_digest=_digest("6"),
            quality_policy_digest=_digest("7"),
            mode="hybrid",
            renderer_source_digest=_digest("c"),
            serializer_id="authored-hybrid",
            serializer_version="2",
            receipt_digests=[_digest("8")],
            pptx_digest=_digest("d"),
        )
    )
    record = AuthoredSemanticExportBinding(
        owner_id=owner_id,
        tenant_id="default",
        export_attempt_id=attempt_id,
        presentation_id=presentation_id,
        presentation_revision=1,
        canonical_digest=_digest("b"),
        renderer_source_digest=_digest("c"),
        mode="hybrid",
        serializer_id="authored-hybrid",
        serializer_version="2",
        pptx_digest=_digest("d"),
        binding_digest=binding.binding_digest,
        payload=binding.model_dump(mode="json", by_alias=True),
    )
    definition, definition_digest = load_release_profile_definition_v3()
    target = ReleaseAuthorityTargetV3(
        profile_id=definition.profile_id,
        profile_definition=definition,
        profile_definition_digest=definition_digest,
        scope=CapabilityScopeV2(
            platform=definition.platform_scope.platform,
            renderer=definition.platform_scope.renderer,
            archetypes=definition.platform_scope.archetypes,
        ),
        environment=CapabilityEnvironmentV2(
            deployment_environment="production",
            os_build="windows-11-24h2",
            office_build="powerpoint-16.0",
            renderer_image_digest=_digest("9"),
            font_inventory_digest=_digest("a"),
            backend_build_digest=_digest("e"),
        ),
        configured=True,
    )
    evidence = readiness_validators._AuthoritativeExportEvidence(
        attempt=attempt,
        binding_record=record,
        binding=binding,
        provenance=provenance,
        required_features_by_element={"chart.performance": frozenset({"chart"})},
        semantic_elements_by_id={
            "chart.performance": ChartElementV1(
                element_id="chart.performance",
                semantic_role="nativeChart",
                intended_order=0,
                kind="chart",
                required_features=["chart"],
                payload={
                    "chartType": "column",
                    "categories": ["Q1", "Q2"],
                    "series": [{"name": "Revenue", "values": [12, 18]}],
                    "title": "Performance",
                    "fontFamily": "Aptos",
                    "legend": {"visible": True, "position": "right"},
                    "categoryAxis": {"title": "Quarter"},
                    "valueAxis": {"title": "Value"},
                },
            )
        },
        semantic_slide_by_element={"chart.performance": 1},
    )
    return attempt, record, target, evidence


def _request(capability_id: str, target, evidence) -> CapabilityReceiptIssueRequestV2:
    return CapabilityReceiptIssueRequestV2(
        capability_id=capability_id,
        state="ready",
        scope=target.scope,
        environment=target.environment,
        serializer_id=target.scope.renderer,
        serializer_version="2",
        feature_set_digest=readiness_validators.trusted_capability_feature_set_digest(capability_id, target),
        evidence_policy_id=readiness_validators.trusted_capability_evidence_policy_id(capability_id),
        policy_digest=target.profile_definition_digest,
        evidence_bundle_id=evidence.attempt.id,
        evidence_digest=readiness_validators.trusted_capability_evidence_digest(capability_id, target, evidence),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        reason_code="verified_authoritative_evidence",
    )


class _Session:
    def __init__(self, attempt, binding) -> None:
        self.attempt, self.binding = attempt, binding

    async def get(self, _model, identity):
        return self.attempt if identity == self.attempt.id else None

    async def scalar(self, _statement):
        return self.binding


def test_v3_registry_uses_persisted_hybrid_authority_and_fails_closed() -> None:
    registry = readiness_validators.get_trusted_capability_validators()
    assert set(registry) == {
        "shape.gradient",
        "shape.shadow",
        "layout.transform",
        "layout.flex",
        "layout.grid",
        "shape.borderRadius",
        "shape.opacity",
        "shape.clip",
        "receipt.cjk",
        "receipt.table",
        "receipt.chart",
        "receipt.imageCrop",
        "receipt.zBand",
        "receipt.degradation",
        "renderer.attestation",
        "evidence.visual",
        "ci.requiredChecks",
    }
    with pytest.raises(TypeError):
        registry["receipt.fake"] = registry["receipt.table"]  # type: ignore[index]
    assert registry["ci.requiredChecks"] is validate_ci_required_checks_evidence_v3

    _, _, _, evidence = _authority_fixture()
    for capability_id in ("receipt.table", "receipt.imageCrop"):
        readiness_validators._validate_native_count_evidence(capability_id, evidence)
    readiness_validators._validate_chart_evidence(evidence)
    readiness_validators._validate_z_band_evidence(evidence)
    readiness_validators._validate_degradation_evidence(evidence)
    with pytest.raises(SemanticContractError, match="authored_trusted_shape_gradient_matrix_mismatch"):
        readiness_validators._validate_css_matrix("shape.gradient", evidence)

    _, _, _, bad_evidence = _authority_fixture(native_table_qa=0)
    with pytest.raises(
        SemanticContractError,
        match="authored_trusted_native_table_readback_mismatch",
    ):
        readiness_validators._validate_native_count_evidence("receipt.table", bad_evidence)
