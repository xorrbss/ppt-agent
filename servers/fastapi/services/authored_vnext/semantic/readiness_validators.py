"""Trusted, fail-closed evidence validators for Authored capability receipts.

The receipt issue request contains only pins.  Native capability authority is
reconstructed from the persisted export attempt and its tenant-scoped semantic
export binding; a caller-provided capability claim is never treated as proof.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import re
from types import MappingProxyType

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.authored_vnext.semantic.contracts import (
    ExportAttemptDigestBindingV1,
    ExportAttemptDigestBindingV2,
)
from models.authored_vnext.semantic.readiness import (
    CapabilityReceiptIssueRequestV2,
    ReleasePinnedExportAttemptDigestBindingV2,
)
from models.authored_vnext.semantic.typography import (
    NativeTypographyReleaseEvidenceV1,
)
from models.sql.authored_semantic_bundle import AuthoredSemanticExportBinding
from models.sql.presentation_export_attempt import (
    ExportCoverageCounts,
    ExportPowerPointQaEvidence,
    PresentationExportAttempt,
    PresentationExportAttemptProvenance,
)
from services.authored_vnext.semantic.canonical import (
    SemanticContractError,
    canonical_digest,
    seal_export_binding,
)
from services.authored_vnext.semantic.readiness import (
    CapabilityEvidenceValidatorV2,
    ReleaseAuthorityTargetV2,
    validate_visual_evidence_authority,
)
from services.authored_vnext.semantic.query import get_active_authored_emission
from services.authored_vnext.semantic.release_evidence_v3 import (
    validate_ci_required_checks_evidence_v3,
)
from services.authored_vnext.semantic.typography import (
    NativeFontByteRegistry,
    NativeTypographyRegistryError,
    load_bounded_windows_cjk_registry,
    native_typography_com_reopen_digest,
)


_SCHEMA_VERSION = "authored-trusted-capability-evidence/v1"
_NATIVE_COUNT_FIELDS = MappingProxyType(
    {
        "receipt.table": "native_table",
        "receipt.chart": "native_chart",
        "receipt.imageCrop": "native_image_crop",
    }
)
_CAPABILITY_IDS = (
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
)
_CAPABILITY_POLICY_NAMES = MappingProxyType(
    {
        "shape.gradient": "shape-gradient",
        "shape.shadow": "shape-shadow",
        "layout.transform": "layout-transform",
        "layout.flex": "layout-flex",
        "layout.grid": "layout-grid",
        "shape.borderRadius": "shape-border-radius",
        "shape.opacity": "shape-opacity",
        "shape.clip": "shape-clip",
        "receipt.cjk": "receipt-cjk",
        "receipt.table": "receipt-table",
        "receipt.chart": "receipt-chart",
        "receipt.imageCrop": "receipt-image-crop",
        "receipt.zBand": "receipt-z-band",
        "receipt.degradation": "receipt-degradation",
        "renderer.attestation": "renderer-attestation",
        "evidence.visual": "evidence-visual",
        "ci.requiredChecks": "ci-required-checks",
    }
)


@dataclass(frozen=True)
class _AuthoritativeExportEvidence:
    attempt: PresentationExportAttempt
    binding_record: AuthoredSemanticExportBinding
    binding: ExportAttemptDigestBindingV1 | ExportAttemptDigestBindingV2
    provenance: PresentationExportAttemptProvenance
    required_features_by_element: Mapping[str, frozenset[str]]
    semantic_elements_by_id: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    semantic_slide_by_element: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))


def _target_serializer_version(target: ReleaseAuthorityTargetV2) -> str:
    direct = getattr(target.profile_definition, "serializer_version", None)
    if isinstance(direct, str):
        return direct
    nested = getattr(target.profile_definition, "platform_scope", None)
    version = getattr(nested, "serializer_version", None)
    if not isinstance(version, str):
        raise SemanticContractError("authored_trusted_capability_serializer_profile_invalid")
    return version


def _target_semantic_serializer_id(target: ReleaseAuthorityTargetV2) -> str:
    nested = getattr(target.profile_definition, "platform_scope", None)
    serializer_id = getattr(nested, "serializer_profile_id", None)
    return serializer_id if isinstance(serializer_id, str) else "authored-hybrid"


def trusted_capability_evidence_policy_id(capability_id: str) -> str:
    """Return the exact policy id accepted by the trusted export validators."""

    if capability_id not in _CAPABILITY_IDS:
        raise ValueError("authored_trusted_capability_unknown")
    if capability_id == "evidence.visual":
        return "authored-visual-parity-gate/v1"
    return f"authored-trusted-capability/{_CAPABILITY_POLICY_NAMES[capability_id]}/v1"


def trusted_capability_feature_set_digest(capability_id: str, target: ReleaseAuthorityTargetV2) -> str:
    """Digest the exact profile/scope feature set a receipt is authorizing."""

    return canonical_digest(
        {
            "schemaVersion": _SCHEMA_VERSION,
            "capabilityId": capability_id,
            "profileId": target.profile_id,
            "profileDefinitionDigest": target.profile_definition_digest,
            "scope": target.scope,
            "serializerId": target.scope.renderer,
            "serializerVersion": _target_serializer_version(target),
        }
    )


def trusted_capability_evidence_digest(
    capability_id: str,
    target: ReleaseAuthorityTargetV2,
    evidence: _AuthoritativeExportEvidence,
) -> str:
    """Digest only server-resolved, immutable evidence and its exact release pins."""

    attempt = evidence.attempt
    binding_record = evidence.binding_record
    return canonical_digest(
        {
            "schemaVersion": _SCHEMA_VERSION,
            "capabilityId": capability_id,
            "profileId": target.profile_id,
            "profileDefinitionDigest": target.profile_definition_digest,
            "scope": target.scope,
            "environment": target.environment,
            "evidencePolicyId": trusted_capability_evidence_policy_id(capability_id),
            "exportAttempt": {
                "id": str(attempt.id),
                "presentationId": str(attempt.presentation_id),
                "sourceRevision": attempt.source_revision,
                "sourceSnapshotSha256": attempt.source_snapshot_sha256,
                "format": attempt.format,
                "state": attempt.state,
                "selectedAdapter": attempt.selected_adapter,
                "artifactSha256": attempt.artifact_sha256,
                "provenance": evidence.provenance,
            },
            "semanticExportBinding": {
                "tenantId": binding_record.tenant_id,
                "ownerId": str(binding_record.owner_id),
                "bindingDigest": binding_record.binding_digest,
                "payload": evidence.binding,
            },
        }
    )


def _binding_model(
    record: AuthoredSemanticExportBinding,
) -> ExportAttemptDigestBindingV1 | ExportAttemptDigestBindingV2:
    payload = record.payload
    try:
        if payload.get("schemaVersion") == "authored-export-attempt-binding/v2":
            model = (
                ReleasePinnedExportAttemptDigestBindingV2
                if "exportAuthorityClaim" in payload
                else ExportAttemptDigestBindingV2
            )
        else:
            model = ExportAttemptDigestBindingV1
        # JSON columns materialize UUIDs as strings; keep the model's closed
        # field contract while parsing those wire values.
        binding = model.model_validate(payload, strict=False)
        sealed = seal_export_binding(binding)
    except (AttributeError, TypeError, ValidationError, ValueError) as error:
        raise SemanticContractError("authored_trusted_evidence_semantic_binding_invalid") from error
    if sealed.binding_digest != record.binding_digest:
        raise SemanticContractError("authored_trusted_evidence_semantic_binding_digest_mismatch")
    return sealed


async def _resolve_authoritative_evidence(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
    tenant_id: str,
) -> _AuthoritativeExportEvidence:
    if (
        not target.configured
        or request.scope != target.scope
        or request.environment != target.environment
        or request.policy_digest != target.profile_definition_digest
        or request.serializer_id != target.scope.renderer
        or request.serializer_version != _target_serializer_version(target)
        or request.evidence_policy_id != trusted_capability_evidence_policy_id(request.capability_id)
        or request.feature_set_digest != trusted_capability_feature_set_digest(request.capability_id, target)
    ):
        raise SemanticContractError("authored_trusted_capability_authority_scope_mismatch")

    attempt = await session.get(PresentationExportAttempt, request.evidence_bundle_id)
    if attempt is None:
        raise SemanticContractError("authored_trusted_evidence_bundle_unresolvable")
    binding_record = await session.scalar(
        select(AuthoredSemanticExportBinding).where(
            AuthoredSemanticExportBinding.export_attempt_id == attempt.id,
            AuthoredSemanticExportBinding.tenant_id == tenant_id,
        )
    )
    if binding_record is None:
        raise SemanticContractError("authored_trusted_evidence_semantic_binding_unresolvable")
    binding = _binding_model(binding_record)
    try:
        provenance = PresentationExportAttemptProvenance.model_validate(attempt.provenance)
    except (ValidationError, ValueError) as error:
        raise SemanticContractError("authored_trusted_evidence_bundle_invalid") from error

    atomic = provenance.atomic_binding
    coverage = provenance.coverage_manifest
    expected_pptx = "sha256:" + attempt.artifact_sha256 if attempt.artifact_sha256 else None
    if (
        attempt.state != "succeeded"
        or attempt.format != "pptx"
        or attempt.artifact_sha256 is None
        or attempt.source_revision != binding.presentation_revision
        or attempt.source_snapshot_sha256 != binding.canonical_digest.removeprefix("sha256:")
        or binding_record.owner_id != attempt.owner_id
        or attempt.owner_scope != str(binding_record.owner_id)
        or binding_record.presentation_id != attempt.presentation_id
        or binding_record.presentation_revision != attempt.source_revision
        or binding_record.export_attempt_id != attempt.id
        or binding_record.canonical_digest != binding.canonical_digest
        or binding_record.renderer_source_digest != binding.renderer_source_digest
        or binding_record.pptx_digest != expected_pptx
        or binding_record.mode != "hybrid"
        or binding.mode != "hybrid"
        or binding.serializer_id != _target_semantic_serializer_id(target)
        or binding.serializer_version != _target_serializer_version(target)
        or atomic is None
        or atomic.export_attempt_id != attempt.id
        or atomic.presentation_id != attempt.presentation_id
        or atomic.presentation_revision != attempt.source_revision
        or atomic.canonical_source_sha256 != attempt.source_snapshot_sha256
        or atomic.renderer_source_sha256 != binding.renderer_source_digest.removeprefix("sha256:")
        or atomic.pptx_sha256 != attempt.artifact_sha256
        or coverage is None
        or coverage.schema_version != "presenton.authored-hybrid-coverage/v2"
        or coverage.source.export_attempt_id != attempt.id
    ):
        raise SemanticContractError("authored_trusted_evidence_bundle_identity_mismatch")

    evidence = _AuthoritativeExportEvidence(
        attempt=attempt,
        binding_record=binding_record,
        binding=binding,
        provenance=provenance,
        required_features_by_element=MappingProxyType({}),
    )
    active = await get_active_authored_emission(
        session,
        presentation_id=attempt.presentation_id,
        owner_id=attempt.owner_id,
        tenant_id=tenant_id,
    )
    if (
        active.canonical_mode != "bundleGraph"
        or active.manifest is None
        or active.presentation_revision != attempt.source_revision
        or active.canonical_digest != binding.canonical_digest
        or active.manifest.canonical_digest != binding.canonical_digest
    ):
        raise SemanticContractError("authored_trusted_evidence_active_manifest_mismatch")
    required_features: dict[str, frozenset[str]] = {}
    semantic_elements: dict[str, object] = {}
    semantic_slides: dict[str, int] = {}
    for slide_number, bundle in enumerate(active.bundles, start=1):
        component = getattr(bundle, "semantic_component", None)
        for element in getattr(component, "elements", ()):
            required_features[element.element_id] = frozenset(element.required_features)
            semantic_elements[element.element_id] = element
            semantic_slides[element.element_id] = slide_number
    evidence = _AuthoritativeExportEvidence(
        attempt=attempt,
        binding_record=binding_record,
        binding=binding,
        provenance=provenance,
        required_features_by_element=MappingProxyType(required_features),
        semantic_elements_by_id=MappingProxyType(semantic_elements),
        semantic_slide_by_element=MappingProxyType(semantic_slides),
    )
    if request.evidence_digest != trusted_capability_evidence_digest(request.capability_id, target, evidence):
        raise SemanticContractError("authored_trusted_evidence_digest_mismatch")
    return evidence


def _require_passed_powerpoint_qa(
    capability_id: str,
    qa: ExportPowerPointQaEvidence | None,
) -> ExportPowerPointQaEvidence:
    if qa is None:
        raise SemanticContractError(
            f"authored_trusted_{capability_id.removeprefix('receipt.').lower()}_powerpoint_qa_missing"
        )
    if qa.status != "passed":
        raise SemanticContractError(
            f"authored_trusted_{capability_id.removeprefix('receipt.').lower()}_powerpoint_qa_not_passed"
        )
    return qa


def _validate_native_count_evidence(capability_id: str, evidence: _AuthoritativeExportEvidence) -> None:
    field = _NATIVE_COUNT_FIELDS[capability_id]
    coverage = evidence.provenance.coverage_manifest
    assert coverage is not None
    logical: ExportCoverageCounts | None = coverage.logical_coverage
    readback = coverage.ooxml_readback
    qa = _require_passed_powerpoint_qa(capability_id, evidence.provenance.powerpoint_qa)
    declared_count = getattr(coverage, field)
    if declared_count <= 0:
        raise SemanticContractError(f"authored_trusted_{field}_evidence_empty")
    if (
        logical is None
        or readback is None
        or readback.status != "passed"
        or getattr(logical, field) != declared_count
        or getattr(readback, field) != declared_count
        or getattr(qa, field) != declared_count
    ):
        raise SemanticContractError(f"authored_trusted_{field}_readback_mismatch")


def _native_count_validator(capability_id: str) -> CapabilityEvidenceValidatorV2:
    async def validate(
        session: AsyncSession,
        request: CapabilityReceiptIssueRequestV2,
        target: ReleaseAuthorityTargetV2,
        tenant_id: str,
    ) -> None:
        evidence = await _resolve_authoritative_evidence(session, request, target, tenant_id)
        _validate_native_count_evidence(capability_id, evidence)

    return validate


_COM_CHART_TYPES = MappingProxyType({51: "column", 57: "bar", 4: "line", 1: "area", 5: "pie", -4120: "donut"})
_COM_LEGEND_POSITIONS = MappingProxyType({-4107: "bottom", -4160: "top", -4152: "right", -4131: "left"})


def _semantic_chart_projection(evidence: _AuthoritativeExportEvidence) -> dict[str, dict]:
    charts: dict[str, dict] = {}
    for element_id, element in evidence.semantic_elements_by_id.items():
        if getattr(element, "kind", None) != "chart":
            continue
        payload = element.payload
        base_family = payload.font_family or "Arial"
        axes_present = payload.chart_type not in {"pie", "donut"}
        charts[element_id] = {
            "slideNumber": evidence.semantic_slide_by_element[element_id],
            "chartType": payload.chart_type,
            "title": payload.title,
            "titleTypeface": base_family if payload.title else "",
            "categories": list(payload.categories),
            "series": [{"name": item.name, "values": list(item.values)} for item in payload.series],
            "categoryAxis": {
                "present": axes_present,
                "title": payload.category_axis.title if payload.category_axis else None,
                "typeface": (
                    payload.category_axis.font_family
                    if payload.category_axis and payload.category_axis.font_family
                    else base_family
                )
                if axes_present
                else "",
            },
            "valueAxis": {
                "present": axes_present,
                "title": payload.value_axis.title if payload.value_axis else None,
                "typeface": (
                    payload.value_axis.font_family
                    if payload.value_axis and payload.value_axis.font_family
                    else base_family
                )
                if axes_present
                else "",
            },
            "legend": {
                "present": payload.legend is None or payload.legend.visible,
                "position": (payload.legend.position if payload.legend is not None else "bottom")
                if payload.legend is None or payload.legend.visible
                else None,
                "typeface": (
                    payload.legend.font_family if payload.legend and payload.legend.font_family else base_family
                )
                if payload.legend is None or payload.legend.visible
                else "",
            },
        }
    return charts


def _ooxml_chart_projection(evidence: _AuthoritativeExportEvidence) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for slide in _quality_slides(evidence):
        entries = slide.get("nativeElementReadback")
        if not isinstance(entries, list):
            raise SemanticContractError("authored_trusted_chart_ooxml_detail_missing")
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("kind") != "chart":
                continue
            element_id, chart = entry.get("elementId"), entry.get("chart")
            if not isinstance(element_id, str) or not isinstance(chart, dict) or element_id in result:
                raise SemanticContractError("authored_trusted_chart_ooxml_detail_invalid")
            result[element_id] = {"slideNumber": entry.get("slideNumber"), **chart}
    return result


def _com_chart_projection(items: list) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in items:
        value = item.model_dump(mode="python", by_alias=True) if hasattr(item, "model_dump") else item
        if not isinstance(value, dict):
            raise SemanticContractError("authored_trusted_chart_com_detail_invalid")
        element_id, chart = value.get("elementId"), value.get("chart")
        if not isinstance(element_id, str) or not isinstance(chart, dict) or element_id in result:
            raise SemanticContractError("authored_trusted_chart_com_detail_invalid")
        series = chart.get("series")
        axes, legend, title = chart.get("axes"), chart.get("legend"), chart.get("title")
        if not isinstance(series, list) or not isinstance(axes, dict) or not isinstance(legend, dict):
            raise SemanticContractError("authored_trusted_chart_com_detail_invalid")
        categories = series[0].get("categories") if series and isinstance(series[0], dict) else []
        if any(not isinstance(entry, dict) or entry.get("categories") != categories for entry in series):
            raise SemanticContractError("authored_trusted_chart_com_detail_invalid")

        def axis(name: str, source: dict = axes) -> dict:
            raw = source.get(name)
            if not isinstance(raw, dict):
                raise SemanticContractError("authored_trusted_chart_com_detail_invalid")
            return {
                "present": raw.get("present") is True,
                "title": raw.get("title") or None,
                "typeface": raw.get("tickLabelTypeface") or "",
            }

        result[element_id] = {
            "slideNumber": value.get("slideNumber"),
            "chartType": _COM_CHART_TYPES.get(chart.get("chartType")),
            "title": title.get("text") if isinstance(title, dict) and title.get("present") else None,
            "titleTypeface": title.get("typeface") if isinstance(title, dict) and title.get("present") else "",
            "categories": [str(category) for category in categories],
            "series": [{"name": str(entry.get("name") or ""), "values": entry.get("values")} for entry in series],
            "categoryAxis": axis("category"),
            "valueAxis": axis("value"),
            "legend": {
                "present": legend.get("present") is True,
                "position": _COM_LEGEND_POSITIONS.get(legend.get("position")),
                "typeface": legend.get("typeface") or "",
            },
        }
    return result


def _canonical_ooxml_chart(value: dict) -> dict:
    projected = dict(value)
    title_latin = projected.pop("titleLatinTypefaces", [])
    title_east_asian = projected.pop("titleEastAsianTypefaces", [])
    projected["titleTypeface"] = title_latin[0] if len(title_latin) == 1 and title_latin == title_east_asian else ""
    for key in ("categoryAxis", "valueAxis"):
        axis = dict(projected[key])
        expected_typeface = axis.pop("typeface", None)
        latin = axis.pop("latinTypefaces", [])
        east_asian = axis.pop("eastAsianTypefaces", [])
        axis["typeface"] = latin[0] if len(latin) == 1 and latin == east_asian else expected_typeface or ""
        projected[key] = axis
    legend = dict(projected["legend"])
    legend.pop("title", None)
    latin = legend.pop("latinTypefaces", [])
    east_asian = legend.pop("eastAsianTypefaces", [])
    legend["typeface"] = latin[0] if len(latin) == 1 and latin == east_asian else ""
    projected["legend"] = legend
    return projected


def _validate_chart_evidence(evidence: _AuthoritativeExportEvidence) -> None:
    _validate_native_count_evidence("receipt.chart", evidence)
    expected = _semantic_chart_projection(evidence)
    observed_ooxml = {key: _canonical_ooxml_chart(value) for key, value in _ooxml_chart_projection(evidence).items()}
    qa = _require_passed_powerpoint_qa("receipt.chart", evidence.provenance.powerpoint_qa)
    first = _com_chart_projection(qa.first_open_chart_readback)
    reopened = _com_chart_projection(qa.reopen_chart_readback)
    if not expected or observed_ooxml != expected:
        raise SemanticContractError("authored_trusted_chart_ooxml_semantic_mismatch")
    if (
        first != expected
        or reopened != expected
        or first != reopened
        or qa.chart_content_changes
        or qa.object_identity_changes
        or qa.geometry_changes_gt_5pt
        or qa.native_qid_missing
        or qa.native_qid_ghosting
    ):
        raise SemanticContractError("authored_trusted_chart_com_semantic_mismatch")


async def _validate_chart(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
    tenant_id: str,
) -> None:
    evidence = await _resolve_authoritative_evidence(session, request, target, tenant_id)
    _validate_chart_evidence(evidence)


def _validate_degradation_evidence(evidence: _AuthoritativeExportEvidence) -> None:
    """Verify that every raster remainder is explicit, bounded and receipted."""

    coverage = evidence.provenance.coverage_manifest
    assert coverage is not None
    logical = coverage.logical_coverage
    readback = coverage.ooxml_readback
    _require_passed_powerpoint_qa("receipt.degradation", evidence.provenance.powerpoint_qa)
    if (
        logical is None
        or logical.raster_remaining != coverage.raster_remaining
        or readback is None
        or readback.status != "passed"
        or readback.raster_remaining != coverage.raster_remaining
        or any(
            slide.logical_coverage is None
            or slide.logical_coverage.raster_remaining != slide.raster_remaining
            or slide.ooxml_readback is None
            or slide.ooxml_readback.status != "passed"
            or slide.ooxml_readback.raster_remaining != slide.raster_remaining
            or (
                slide.raster_remaining > 0
                and (
                    not slide.fallback_reasons
                    or slide.semantic_receipts is None
                    or slide.semantic_receipts.raster_degradation_receipt_digest is None
                )
            )
            or (
                slide.raster_remaining == 0
                and any(
                    reason not in {"gradient-backplate-only", "shadow-backplate-only"}
                    for reason in slide.fallback_reasons
                )
            )
            for slide in coverage.slides
        )
    ):
        raise SemanticContractError("authored_trusted_degradation_receipt_mismatch")


_CSS_FEATURE_ALIASES = MappingProxyType(
    {
        "shape.gradient": frozenset({"gradient", "shape.gradient"}),
        "shape.shadow": frozenset({"shadow", "shape.shadow"}),
        "layout.transform": frozenset({"transform", "layout.transform"}),
        "layout.flex": frozenset({"flex", "layout.flex"}),
        "layout.grid": frozenset({"grid", "layout.grid"}),
        "shape.borderRadius": frozenset({"border-radius", "shape.borderRadius"}),
        "shape.opacity": frozenset({"opacity", "shape.opacity"}),
        "shape.clip": frozenset({"clip", "clip-path", "shape.clip"}),
    }
)
_STABLE_REASON = re.compile(r"^[a-z][a-z0-9._-]{1,127}$")


def _quality_slides(evidence: _AuthoritativeExportEvidence) -> list[dict]:
    quality = evidence.provenance.quality_sidecar
    if not isinstance(quality, dict) or quality.get("schemaVersion") != "presenton.export-quality/v1":
        raise SemanticContractError("authored_trusted_quality_sidecar_unavailable")
    slides = quality.get("slides")
    if (
        not isinstance(slides, list)
        or len(slides) != evidence.provenance.coverage_manifest.total_slides
        or any(not isinstance(item, dict) for item in slides)
    ):
        raise SemanticContractError("authored_trusted_quality_sidecar_invalid")
    return slides


def _element_outcomes(evidence: _AuthoritativeExportEvidence) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for slide in _quality_slides(evidence):
        backplates = int(slide.get("backplateCount", 0) or 0)
        for item in slide.get("qidCoverage", ()):
            if not isinstance(item, dict):
                continue
            element_id = item.get("elementId")
            if isinstance(element_id, str) and item.get("logical") == "native" and item.get("ooxml") == "present":
                outcomes[element_id] = "native"
        for item in slide.get("fallbackElements", ()):
            if not isinstance(item, dict):
                continue
            element_id = item.get("elementId")
            reasons = item.get("reasons")
            if (
                not isinstance(element_id, str)
                or not isinstance(reasons, list)
                or not reasons
                or any(not isinstance(reason, str) or not _STABLE_REASON.fullmatch(reason) for reason in reasons)
            ):
                continue
            backplate_only = all(reason in {"gradient-backplate-only", "shadow-backplate-only"} for reason in reasons)
            semantic = slide.get("semanticReceipts")
            has_raster_receipt = isinstance(semantic, dict) and isinstance(
                semantic.get("rasterDegradationReceiptDigest"), str
            )
            raster_count = int(slide.get("rasterFallbackElements", 0) or 0)
            if (backplate_only and backplates > 0) or (raster_count > 0 and has_raster_receipt):
                outcomes[element_id] = "fallback"
    return outcomes


def _validate_css_matrix(capability_id: str, evidence: _AuthoritativeExportEvidence) -> None:
    aliases = _CSS_FEATURE_ALIASES[capability_id]
    declared = {
        element_id
        for element_id, features in evidence.required_features_by_element.items()
        if features.intersection(aliases)
    }
    outcomes = _element_outcomes(evidence)
    qa = _require_passed_powerpoint_qa(capability_id, evidence.provenance.powerpoint_qa)
    if (
        not declared
        or not declared.issubset(outcomes)
        or qa.semantic_payload_binding != "passed"
        or qa.object_identity_changes
        or qa.geometry_changes_gt_5pt
    ):
        raise SemanticContractError(
            f"authored_trusted_{_CAPABILITY_POLICY_NAMES[capability_id].replace('-', '_')}_matrix_mismatch"
        )


def _css_validator(capability_id: str) -> CapabilityEvidenceValidatorV2:
    async def validate(
        session: AsyncSession,
        request: CapabilityReceiptIssueRequestV2,
        target: ReleaseAuthorityTargetV2,
        tenant_id: str,
    ) -> None:
        evidence = await _resolve_authoritative_evidence(session, request, target, tenant_id)
        _validate_css_matrix(capability_id, evidence)

    return validate


def _prefixed_digest(value: str | None) -> str | None:
    return f"sha256:{value}" if value else None


def _valid_admission_receipt_digest(
    evidence: NativeTypographyReleaseEvidenceV1,
) -> bool:
    for admission in evidence.admission_receipts:
        payload = admission.model_dump(mode="json", by_alias=True)
        supplied = payload.pop("receiptDigest", None)
        if supplied != canonical_digest(payload):
            return False
    return True


def validate_cjk_release_authority_evidence_v3(
    *,
    quality: Mapping[str, object],
    qa: ExportPowerPointQaEvidence,
    declared_element_ids: set[str],
    native_outcomes: Mapping[str, str],
    target: ReleaseAuthorityTargetV2,
    registry: NativeFontByteRegistry | None = None,
) -> None:
    """Bind CJK authority to installed bytes, admission, and COM reopen proof."""

    raw_authority = quality.get("nativeTypographyEvidence")
    try:
        authority = NativeTypographyReleaseEvidenceV1.model_validate(raw_authority, strict=False)
    except (TypeError, ValidationError, ValueError) as error:
        raise SemanticContractError("authored_trusted_cjk_native_evidence_invalid") from error
    try:
        actual_registry = registry or load_bounded_windows_cjk_registry()
    except NativeTypographyRegistryError as error:
        raise SemanticContractError(error.code) from error

    actual_faces = list(actual_registry.receipts)
    admission_ids = {item.element_id for item in authority.admission_receipts}
    if (
        target.environment.font_inventory_digest != actual_registry.digest
        or authority.font_registry_digest != actual_registry.digest
        or authority.font_faces != actual_faces
        or not declared_element_ids
        or admission_ids != declared_element_ids
        or any(native_outcomes.get(item) != "native" for item in admission_ids)
        or not _valid_admission_receipt_digest(authority)
    ):
        raise SemanticContractError("authored_trusted_cjk_font_registry_binding_mismatch")

    qa_digest = canonical_digest(qa)
    first_readback = _prefixed_digest(qa.first_open_readback_sha256)
    reopen_readback = _prefixed_digest(qa.reopen_readback_sha256)
    expected_com_readback = native_typography_com_reopen_digest(
        font_registry_digest=authority.font_registry_digest,
        admission_receipts=authority.admission_receipts,
    )
    if (
        qa.status != "passed"
        or qa.automation != "PowerPoint.COM"
        or not qa.reopened
        or not qa.readback
        or qa.verification_scope != "powerpoint-first-open-save-reopen-stability"
        or qa.semantic_payload_binding != "passed"
        or qa.readback_match is not True
        or not qa.first_open_readback_artifact
        or not qa.reopen_readback_artifact
        or first_readback is None
        or first_readback != reopen_readback
        or first_readback != expected_com_readback
        or authority.powerpoint_qa_digest != qa_digest
        or authority.first_open_readback_digest != first_readback
        or authority.reopen_readback_digest != reopen_readback
        or qa.text_changes
        or qa.line_break_changes
        or qa.typeface_changes
        or qa.typography_issues_gt_5pt
        or qa.typography_issues_gt_10pt
        or qa.native_text is None
        or qa.native_text < len(admission_ids)
    ):
        raise SemanticContractError("authored_trusted_cjk_com_reopen_mismatch")

    fonts = quality.get("fontEmbeddingStatus")
    faces = fonts.get("faces") if isinstance(fonts, dict) else None
    if not isinstance(fonts, dict) or not isinstance(faces, list):
        raise SemanticContractError("authored_trusted_cjk_font_embedding_evidence_invalid")
    actual_by_key = {(item.family.casefold(), item.face.casefold()): item for item in actual_faces}
    used_keys = {
        (
            (run.resolved_family or "").casefold(),
            (run.resolved_face or "").casefold(),
        )
        for admission in authority.admission_receipts
        for run in admission.runs
    }
    embedded_by_key: dict[tuple[str, str], dict] = {}
    for face in faces:
        if not isinstance(face, dict):
            continue
        typeface = face.get("typeface")
        face_name = face.get("face")
        if isinstance(typeface, str) and isinstance(face_name, str):
            embedded_by_key[(typeface.casefold(), face_name.casefold())] = face
    for key in used_keys:
        receipt = actual_by_key.get(key)
        embedded = embedded_by_key.get(key)
        expected_license = "allowed-editable" if receipt and receipt.fs_type & 0x0008 else "allowed-installable"
        if (
            receipt is None
            or embedded is None
            or embedded.get("sourceSha256") != receipt.font_byte_digest.removeprefix("sha256:")
            or embedded.get("sourceBytes") != receipt.size_bytes
            or embedded.get("fsType") != receipt.fs_type
            or embedded.get("licenseDecision") != expected_license
            or not isinstance(embedded.get("embeddedBytes"), int)
            or embedded["embeddedBytes"] <= 0
            or not isinstance(embedded.get("subset"), bool)
            or (embedded["subset"] and not receipt.subsetting_permitted)
        ):
            raise SemanticContractError("authored_trusted_cjk_font_embedding_evidence_mismatch")
    used_families = {actual_by_key[key].family for key in used_keys if key in actual_by_key}
    if (
        fonts.get("requested") is not True
        or fonts.get("applied") is not True
        or fonts.get("nativeTextUsage") != "used"
        or fonts.get("failures") not in (None, [])
        or fonts.get("nativeTextElementsUsingEmbeddedTypefaces") != len(admission_ids)
        or set(fonts.get("nativeTypefaceFamilies") or []) != used_families
    ):
        raise SemanticContractError("authored_trusted_cjk_font_embedding_evidence_invalid")


async def _validate_cjk(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
    tenant_id: str,
) -> None:
    evidence = await _resolve_authoritative_evidence(session, request, target, tenant_id)
    declared = {
        element_id for element_id, features in evidence.required_features_by_element.items() if "cjk" in features
    }
    outcomes = _element_outcomes(evidence)
    qa = _require_passed_powerpoint_qa("receipt.cjk", evidence.provenance.powerpoint_qa)
    quality = evidence.provenance.quality_sidecar or {}
    validate_cjk_release_authority_evidence_v3(
        quality=quality,
        qa=qa,
        declared_element_ids=declared,
        native_outcomes=outcomes,
        target=target,
    )


def _validate_z_band_evidence(evidence: _AuthoritativeExportEvidence) -> None:
    coverage = evidence.provenance.coverage_manifest
    assert coverage is not None
    qa = _require_passed_powerpoint_qa("receipt.zBand", evidence.provenance.powerpoint_qa)
    quality_by_slide = {item.get("slideNumber"): item for item in _quality_slides(evidence)}
    mixed_slides = [
        slide
        for slide in coverage.slides
        if slide.backplate_count > 0
        and (slide.native_text + slide.native_image + slide.native_shape + slide.native_table + slide.native_chart) > 0
        and slide.native_backplate_de_duplication == "applied"
    ]
    for slide in mixed_slides:
        quality = quality_by_slide.get(slide.slide_number)
        plan = quality.get("paintPlan") if isinstance(quality, dict) else None
        semantic = slide.semantic_receipts
        if not isinstance(plan, dict) or semantic is None:
            raise SemanticContractError("authored_trusted_z_band_paint_plan_missing")
        downgrade = plan.get("downgradeReceipt")
        demotions = plan.get("demotions")
        if not isinstance(downgrade, dict) or not isinstance(demotions, list):
            raise SemanticContractError("authored_trusted_z_band_paint_plan_invalid")
        receipt_payload = dict(downgrade)
        receipt_digest = receipt_payload.pop("receiptDigest", None)
        demoted_ids = sorted(
            item.get("elementId")
            for item in demotions
            if isinstance(item, dict) and isinstance(item.get("elementId"), str)
        )
        if (
            plan.get("schemaVersion") != "hybrid-paint-plan/browser-v1"
            or plan.get("strategy") != "single-backplate-fixed-point"
            or plan.get("fixedPointReached") is not True
            or not isinstance(plan.get("closureIterations"), int)
            or not 1 <= plan["closureIterations"] <= 4096
            or not isinstance(plan.get("closureDigest"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", plan["closureDigest"])
            or plan.get("hybridPlanDigest") != semantic.hybrid_plan_digest
            or plan.get("slideDemotedToRaster") is not False
            or downgrade.get("schemaVersion") != "hybrid-layer-downgrade-receipt/browser-v1"
            or downgrade.get("state") != "closed"
            or downgrade.get("reason") is not None
            or downgrade.get("demotedElementIds") != demoted_ids
            or receipt_digest != canonical_digest(receipt_payload)
            or any(
                not isinstance(item, dict)
                or item.get("fallbackReason") != "z-band-unsafe"
                or not isinstance(item.get("blockedByElementIds"), list)
                for item in demotions
            )
        ):
            raise SemanticContractError("authored_trusted_z_band_paint_plan_mismatch")
    if (
        not mixed_slides
        or qa.semantic_payload_binding != "passed"
        or qa.shape_count_changes
        or qa.object_identity_changes
        or qa.geometry_changes_gt_5pt
        or qa.geometry_changes_gt_10pt
        or qa.native_qid_missing
        or qa.native_qid_ghosting
        or qa.backplate_collisions
        or qa.render_hashes_match is not True
        or qa.readback_match is not True
    ):
        raise SemanticContractError("authored_trusted_z_band_readback_mismatch")


async def _validate_z_band(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
    tenant_id: str,
) -> None:
    evidence = await _resolve_authoritative_evidence(session, request, target, tenant_id)
    _validate_z_band_evidence(evidence)


async def _validate_renderer_attestation(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
    tenant_id: str,
) -> None:
    evidence = await _resolve_authoritative_evidence(session, request, target, tenant_id)
    qa = _require_passed_powerpoint_qa("renderer.attestation", evidence.provenance.powerpoint_qa)
    expected_office = str(target.environment.office_build).lower()
    observed_office = (qa.powerpoint_version or "").lower()
    if (
        qa.repair_observation != "interactive-observation"
        or qa.repair_dialogs != 0
        or qa.semantic_payload_binding != "passed"
        or qa.render_hashes_match is not True
        or qa.readback_match is not True
        or qa.slide_count != evidence.provenance.coverage_manifest.total_slides
        or not qa.first_open_rendered_png_sha256
        or qa.first_open_rendered_png_sha256 != qa.reopen_rendered_png_sha256
        or not observed_office
        or observed_office not in expected_office
    ):
        raise SemanticContractError("authored_trusted_renderer_attestation_readback_mismatch")


async def _validate_degradation(
    session: AsyncSession,
    request: CapabilityReceiptIssueRequestV2,
    target: ReleaseAuthorityTargetV2,
    tenant_id: str,
) -> None:
    evidence = await _resolve_authoritative_evidence(session, request, target, tenant_id)
    _validate_degradation_evidence(evidence)


_TRUSTED_CAPABILITY_VALIDATORS: Mapping[str, CapabilityEvidenceValidatorV2] = MappingProxyType(
    {
        "shape.gradient": _css_validator("shape.gradient"),
        "shape.shadow": _css_validator("shape.shadow"),
        "layout.transform": _css_validator("layout.transform"),
        "layout.flex": _css_validator("layout.flex"),
        "layout.grid": _css_validator("layout.grid"),
        "shape.borderRadius": _css_validator("shape.borderRadius"),
        "shape.opacity": _css_validator("shape.opacity"),
        "shape.clip": _css_validator("shape.clip"),
        "receipt.cjk": _validate_cjk,
        "receipt.table": _native_count_validator("receipt.table"),
        "receipt.chart": _validate_chart,
        "receipt.imageCrop": _native_count_validator("receipt.imageCrop"),
        "receipt.zBand": _validate_z_band,
        "receipt.degradation": _validate_degradation,
        "renderer.attestation": _validate_renderer_attestation,
        "evidence.visual": validate_visual_evidence_authority,
        "ci.requiredChecks": validate_ci_required_checks_evidence_v3,
    }
)


def get_trusted_capability_validators() -> Mapping[str, CapabilityEvidenceValidatorV2]:
    """Return the immutable router/receipt-service validator registry."""

    return _TRUSTED_CAPABILITY_VALIDATORS


__all__ = [
    "get_trusted_capability_validators",
    "trusted_capability_evidence_digest",
    "trusted_capability_evidence_policy_id",
    "trusted_capability_feature_set_digest",
]
