"""Closed v1 contracts for the Authored semantic dual-output aggregate.

HTML remains the visual authority.  Semantic declarations and every derived
receipt are immutable, digest-bound inputs; none of these models infer support
or silently repair an authored identity.
"""

from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Any, Literal, TypeAlias
import uuid

from pydantic import Field, field_validator, model_validator

from models.authored_vnext.master.base import Digest, StrictCamelModel


QID_PATTERN = r"^[A-Za-z][A-Za-z0-9._:-]{0,127}$"
Qid = Annotated[str, Field(pattern=QID_PATTERN)]
ArtifactRef = Annotated[str, Field(pattern=r"^artifact://sha256/[0-9a-f]{64}$")]
PolicyVersion = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._/-]{0,127}$")]


class ArtifactPointerV1(StrictCamelModel):
    schema_version: Literal["authored-artifact-pointer/v1"] = "authored-artifact-pointer/v1"
    artifact_ref: ArtifactRef
    digest: Digest
    byte_length: int = Field(ge=0, le=64 * 1024 * 1024)
    media_type: str = Field(min_length=1, max_length=128)


class ResolvedAssetV1(StrictCamelModel):
    asset_id: str = Field(pattern=r"^asset:[a-z0-9][a-z0-9._:-]{0,191}$")
    artifact: ArtifactPointerV1
    locator_digest: Digest
    resolver_version: str = Field(min_length=1, max_length=128)
    width_px: int = Field(gt=0, le=32768)
    height_px: int = Field(gt=0, le=32768)
    provenance: Literal["authored", "master", "composition", "internal-reviewed"]
    retention_class: Literal["active", "undo-redo", "audit-hold", "evidence-hold"]


class ResolvedFontV1(StrictCamelModel):
    face_id: str = Field(pattern=r"^font:[a-z0-9][a-z0-9._:-]{0,191}$")
    family: str = Field(min_length=1, max_length=128)
    face: str = Field(min_length=1, max_length=128)
    artifact: ArtifactPointerV1
    locator_digest: Digest
    resolver_version: str = Field(min_length=1, max_length=128)
    embedding_permitted: bool
    subsetting_permitted: bool
    retention_class: Literal["active", "undo-redo", "audit-hold", "evidence-hold"]


class AssetManifestV1(StrictCamelModel):
    schema_version: Literal["authored-asset-manifest/v1"] = "authored-asset-manifest/v1"
    assets: list[ResolvedAssetV1] = Field(default_factory=list, max_length=512)
    digest: Digest | None = None

    @model_validator(mode="after")
    def unique_actual_assets(self) -> "AssetManifestV1":
        _require_unique((item.asset_id for item in self.assets), "authored_asset_id_duplicate")
        return self


class FontManifestV1(StrictCamelModel):
    schema_version: Literal["authored-font-manifest/v1"] = "authored-font-manifest/v1"
    fonts: list[ResolvedFontV1] = Field(default_factory=list, max_length=256)
    digest: Digest | None = None

    @model_validator(mode="after")
    def unique_actual_faces(self) -> "FontManifestV1":
        _require_unique((item.face_id for item in self.fonts), "authored_font_face_duplicate")
        return self


class ElementIdentityRegistryV1(StrictCamelModel):
    schema_version: Literal["authored-element-identity-registry/v1"] = (
        "authored-element-identity-registry/v1"
    )
    active_qids: list[Qid] = Field(max_length=1024)
    retired_qids: list[Qid] = Field(default_factory=list, max_length=4096)
    lineage_digest: Digest | None = None
    digest: Digest | None = None

    @model_validator(mode="after")
    def validate_registry(self) -> "ElementIdentityRegistryV1":
        _require_unique(self.active_qids, "authored_semantic_qid_duplicate")
        _require_unique(self.retired_qids, "authored_semantic_retired_qid_duplicate")
        if set(self.active_qids).intersection(self.retired_qids):
            raise ValueError("authored_semantic_qid_reused")
        if self.active_qids != sorted(self.active_qids):
            raise ValueError("authored_semantic_qid_registry_not_canonical")
        if self.retired_qids != sorted(self.retired_qids):
            raise ValueError("authored_semantic_retired_qid_registry_not_canonical")
        return self


class TextRunV1(StrictCamelModel):
    text: str = Field(max_length=16384)
    language: str = Field(min_length=2, max_length=35)
    script: Literal["cjk", "latin", "common"]
    font_family: str = Field(min_length=1, max_length=128)
    font_face: str = Field(min_length=1, max_length=128)
    font_size_pt: float = Field(gt=0, le=400)
    weight: int = Field(ge=100, le=1000, multiple_of=100)
    italic: bool = False
    underline: bool = False
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


class TextParagraphV1(StrictCamelModel):
    runs: list[TextRunV1] = Field(min_length=1, max_length=512)
    alignment: Literal["left", "center", "right", "justify"] = "left"
    direction: Literal["ltr", "rtl"] = "ltr"
    explicit_break_after: bool = False


class TextPayloadV1(StrictCamelModel):
    paragraphs: list[TextParagraphV1] = Field(min_length=1, max_length=256)
    whitespace: Literal["normal", "pre", "pre-wrap", "nowrap"] = "normal"


class ImagePayloadV1(StrictCamelModel):
    asset_id: str = Field(pattern=r"^asset:[a-z0-9][a-z0-9._:-]{0,191}$")
    asset_digest: Digest
    alt_text: str = Field(max_length=2048)
    intrinsic_width_px: int = Field(gt=0, le=32768)
    intrinsic_height_px: int = Field(gt=0, le=32768)
    object_fit: Literal["contain", "cover", "fill", "none", "scale-down"] = "contain"
    object_position: str = Field(default="50% 50%", min_length=1, max_length=128)


class ShapePayloadV1(StrictCamelModel):
    preset: Literal["rectangle", "roundedRectangle", "ellipse", "line"]
    fill: str | None = Field(default=None, max_length=128)
    stroke: str | None = Field(default=None, max_length=128)
    radius_pt: float | None = Field(default=None, ge=0, le=1000)


class BoundsIntentV1(StrictCamelModel):
    x_pt: float
    y_pt: float
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    rotation_deg: float = Field(default=0, ge=-360, le=360)


class SemanticElementBaseV1(StrictCamelModel):
    element_id: Qid
    semantic_role: str = Field(min_length=1, max_length=128)
    parent_id: Qid | None = None
    child_ids: list[Qid] = Field(default_factory=list, max_length=256)
    intended_order: int = Field(ge=0, le=4096)
    intended_bounds: BoundsIntentV1 | None = None
    required_features: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("required_features")
    @classmethod
    def canonical_features(cls, value: list[str]) -> list[str]:
        normalized = sorted({item.strip() for item in value if item.strip()})
        if any(len(item) > 128 for item in normalized):
            raise ValueError("authored_semantic_feature_invalid")
        return normalized


class TextElementV1(SemanticElementBaseV1):
    kind: Literal["text"]
    payload: TextPayloadV1


class ImageElementV1(SemanticElementBaseV1):
    kind: Literal["image"]
    payload: ImagePayloadV1


class ShapeElementV1(SemanticElementBaseV1):
    kind: Literal["shape"]
    payload: ShapePayloadV1


class ChartSeriesV1(StrictCamelModel):
    name: str = Field(min_length=1, max_length=256)
    values: list[float] = Field(min_length=1, max_length=256)
    color: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
    )


class ChartTextStyleV1(StrictCamelModel):
    title: str | None = Field(default=None, max_length=512)
    font_family: str | None = Field(default=None, min_length=1, max_length=128)
    font_size_pt: float | None = Field(default=None, ge=6, le=72)
    color: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
    )


class ChartLegendV1(ChartTextStyleV1):
    visible: bool = True
    position: Literal["top", "bottom", "left", "right"] = "right"


class ChartPayloadV1(StrictCamelModel):
    chart_type: Literal["column", "bar", "line", "area", "pie", "donut"]
    categories: list[str] = Field(min_length=1, max_length=256)
    series: list[ChartSeriesV1] = Field(min_length=1, max_length=32)
    title: str | None = Field(default=None, max_length=512)
    legend: ChartLegendV1 | None = None
    category_axis: ChartTextStyleV1 | None = None
    value_axis: ChartTextStyleV1 | None = None
    font_family: str | None = Field(default=None, min_length=1, max_length=128)
    font_size_pt: float | None = Field(default=None, ge=6, le=72)
    text_color: str | None = Field(
        default=None, pattern=r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$"
    )
    palette: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def aligned_series(self) -> "ChartPayloadV1":
        if any(len(item.values) != len(self.categories) for item in self.series):
            raise ValueError("authored_chart_category_series_mismatch")
        if any(not re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", item) for item in self.palette):
            raise ValueError("authored_chart_palette_invalid")
        return self


class ChartElementV1(SemanticElementBaseV1):
    kind: Literal["chart"]
    payload: ChartPayloadV1


class ReferencedElementV1(SemanticElementBaseV1):
    kind: Literal["connector", "group", "table", "complexIsland"]
    source_ref: Digest
    raster_reason: str | None = Field(default=None, max_length=128)


SemanticElementV1: TypeAlias = (
    TextElementV1 | ImageElementV1 | ShapeElementV1 | ChartElementV1 | ReferencedElementV1
)


class AuthoredSemanticSourceV1(StrictCamelModel):
    schema_version: Literal["authored-semantic-source/v1"] = "authored-semantic-source/v1"
    component_kind: Literal["authoredSemanticSource"] = "authoredSemanticSource"
    presentation_id: uuid.UUID
    slide_id: uuid.UUID
    slide_revision: int = Field(ge=1)
    operation_id: uuid.UUID
    origin: Literal["authored-declared-v1"] = "authored-declared-v1"
    authored_html_digest: Digest
    elements: list[SemanticElementV1] = Field(min_length=1, max_length=1024)
    identity_registry: ElementIdentityRegistryV1
    digest: Digest | None = None

    @model_validator(mode="after")
    def validate_identity_graph(self) -> "AuthoredSemanticSourceV1":
        by_id = {element.element_id: element for element in self.elements}
        if len(by_id) != len(self.elements):
            raise ValueError("authored_semantic_qid_duplicate")
        if sorted(by_id) != self.identity_registry.active_qids:
            raise ValueError("authored_semantic_qid_mismatch")
        for element in self.elements:
            _require_unique(element.child_ids, "authored_semantic_child_duplicate")
            if element.parent_id == element.element_id or element.element_id in element.child_ids:
                raise ValueError("authored_semantic_graph_cycle")
            if element.parent_id is not None:
                parent = by_id.get(element.parent_id)
                if parent is None or element.element_id not in parent.child_ids:
                    raise ValueError("authored_semantic_ownership_mismatch")
            for child_id in element.child_ids:
                child = by_id.get(child_id)
                if child is None or child.parent_id != element.element_id:
                    raise ValueError("authored_semantic_ownership_mismatch")
        _reject_cycles(by_id)
        return self


class SemanticUnavailableReceiptV1(StrictCamelModel):
    schema_version: Literal["semantic-unavailable-receipt/v1"] = (
        "semantic-unavailable-receipt/v1"
    )
    component_kind: Literal["semanticUnavailable"] = "semanticUnavailable"
    presentation_id: uuid.UUID
    slide_id: uuid.UUID
    slide_revision: int = Field(ge=1)
    operation_id: uuid.UUID
    origin_generation_attempt_id: uuid.UUID | None = None
    authored_html_digest: Digest
    state: Literal["degradedFidelityOnly"] = "degradedFidelityOnly"
    failure_code: Literal[
        "authored_semantic_generation_failed",
        "authored_semantic_validation_failed",
        "authored_semantic_retry_exhausted",
        "legacy_semantic_unavailable",
    ]
    bounded_attempt_count: int = Field(ge=0, le=8)
    compiler_version: str = Field(min_length=1, max_length=128)
    policy_version: PolicyVersion
    digest: Digest | None = None


class CompatibilityElementV1(StrictCamelModel):
    element_id: Qid
    kind: Literal["text", "image", "shape"]
    remapped_from: str | None = Field(default=None, max_length=128)


class CompatibilitySemanticObservationV1(StrictCamelModel):
    schema_version: Literal["compatibility-semantic-observation/v1"] = (
        "compatibility-semantic-observation/v1"
    )
    component_kind: Literal["compatibilityObservation"] = "compatibilityObservation"
    presentation_id: uuid.UUID
    slide_id: uuid.UUID
    slide_revision: int = Field(ge=1)
    operation_id: uuid.UUID
    authored_html_digest: Digest
    extractor_version: str = Field(min_length=1, max_length=128)
    ordered_remap_digest: Digest | None = None
    elements: list[CompatibilityElementV1] = Field(default_factory=list, max_length=1024)
    digest: Digest | None = None

    @model_validator(mode="after")
    def unique_elements(self) -> "CompatibilitySemanticObservationV1":
        _require_unique((item.element_id for item in self.elements), "authored_semantic_qid_duplicate")
        return self


AuthoredSemanticComponentV1: TypeAlias = (
    AuthoredSemanticSourceV1 | SemanticUnavailableReceiptV1
)
LegacyCompatibilityComponentV1: TypeAlias = (
    CompatibilitySemanticObservationV1 | SemanticUnavailableReceiptV1
)


class EmissionBundleBaseV1(StrictCamelModel):
    presentation_id: uuid.UUID
    slide_id: uuid.UUID
    slide_revision: int = Field(ge=1)
    operation_id: uuid.UUID
    origin_generation_attempt_id: uuid.UUID | None = None
    authored_html: ArtifactPointerV1
    asset_manifest: ArtifactPointerV1
    font_manifest: ArtifactPointerV1
    master_contract_digest: Digest
    composition_digest: Digest
    element_identity_registry_digest: Digest
    quality_policy_version: PolicyVersion
    bundle_digest: Digest | None = None


class AuthoredEmissionBundleV1(EmissionBundleBaseV1):
    schema_version: Literal["authored-emission-bundle/v1"] = "authored-emission-bundle/v1"
    bundle_kind: Literal["authored"] = "authored"
    semantic_component: AuthoredSemanticComponentV1

    @model_validator(mode="after")
    def bind_component(self) -> "AuthoredEmissionBundleV1":
        _require_component_binding(self, self.semantic_component)
        return self


class LegacyHtmlBundleV1(EmissionBundleBaseV1):
    schema_version: Literal["legacy-html-bundle/v1"] = "legacy-html-bundle/v1"
    bundle_kind: Literal["legacyHtml"] = "legacyHtml"
    compatibility_component: LegacyCompatibilityComponentV1

    @model_validator(mode="after")
    def bind_component(self) -> "LegacyHtmlBundleV1":
        _require_component_binding(self, self.compatibility_component)
        if isinstance(self.compatibility_component, AuthoredSemanticSourceV1):
            raise ValueError("legacy_bundle_authored_semantic_forbidden")
        return self


EmissionBundleV1: TypeAlias = AuthoredEmissionBundleV1 | LegacyHtmlBundleV1


class OrderedSlideBundleV1(StrictCamelModel):
    slide_id: uuid.UUID
    slide_revision: int = Field(ge=1)
    bundle_kind: Literal["authored", "legacyHtml"]
    bundle_digest: Digest


class AuthoredDeckEmissionManifestV1(StrictCamelModel):
    schema_version: Literal["authored-deck-emission-manifest/v1"] = (
        "authored-deck-emission-manifest/v1"
    )
    presentation_id: uuid.UUID
    presentation_revision: int = Field(ge=1)
    operation_id: uuid.UUID
    generation_attempt_id: uuid.UUID | None = None
    ordered_slides: list[OrderedSlideBundleV1] = Field(min_length=1, max_length=100)
    master_contract_digest: Digest
    composition_digest: Digest
    asset_manifest_digest: Digest
    font_manifest_digest: Digest
    quality_policy_version: PolicyVersion
    canonical_digest: Digest | None = None

    @model_validator(mode="after")
    def complete_ordered_deck(self) -> "AuthoredDeckEmissionManifestV1":
        _require_unique((item.slide_id for item in self.ordered_slides), "authored_manifest_slide_duplicate")
        return self


class ActiveAuthoredEmissionResponseV1(StrictCamelModel):
    schema_version: Literal["active-authored-emission-response/v1"] = (
        "active-authored-emission-response/v1"
    )
    presentation_id: uuid.UUID
    canonical_mode: Literal["legacyHtml", "bundleGraph"]
    presentation_revision: int = Field(ge=0)
    active_generation: int = Field(default=0, ge=0)
    canonical_digest: Digest | None = None
    manifest: AuthoredDeckEmissionManifestV1 | None = None
    bundles: list[EmissionBundleV1] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_mode(self) -> "ActiveAuthoredEmissionResponseV1":
        if self.canonical_mode == "legacyHtml" and (self.manifest or self.bundles):
            raise ValueError("legacy_active_response_bundle_graph_forbidden")
        if self.canonical_mode == "bundleGraph" and (
            self.manifest is None
            or len(self.bundles) != len(self.manifest.ordered_slides)
        ):
            raise ValueError("authored_active_response_partial_manifest")
        return self


class ObservedBoundsV1(StrictCamelModel):
    x_pt: float
    y_pt: float
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    rotation_deg: float = 0


class ObservedElementV1(StrictCamelModel):
    element_id: Qid
    bounds: ObservedBoundsV1
    parent_id: Qid | None = None
    source_digest: Digest
    paint_digest: Digest
    computed_font_families: list[str] = Field(default_factory=list, max_length=32)
    line_rect_digests: list[Digest] = Field(default_factory=list, max_length=512)
    observed_features: list[str] = Field(default_factory=list, max_length=128)
    ordinal: int | None = Field(default=None, ge=0)
    dom_path: str | None = Field(default=None, max_length=2048)


class BrowserObservationReceiptV1(StrictCamelModel):
    schema_version: Literal["browser-observation-receipt/v1"] = (
        "browser-observation-receipt/v1"
    )
    input_bundle_digest: Digest
    renderer_source_digest: Digest
    renderer_version: str = Field(min_length=1, max_length=128)
    policy_version: PolicyVersion
    font_set_digest: Digest
    fonts_ready: bool
    images_ready: bool
    unresolved_resource_digests: list[Digest] = Field(default_factory=list, max_length=512)
    mutation_free_window_ms: int = Field(ge=0, le=60000)
    capture_epoch: int = Field(ge=1)
    captured_png_digest: Digest
    elements: list[ObservedElementV1] = Field(max_length=1024)
    receipt_digest: Digest | None = None


class EditableElementProjectionV1(StrictCamelModel):
    element_id: Qid
    editable_fields: list[Literal["text", "imageAsset", "fill", "stroke"]] = Field(
        default_factory=list, max_length=8
    )
    read_only_reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def truthful_editability(self) -> "EditableElementProjectionV1":
        if bool(self.editable_fields) == bool(self.read_only_reason):
            raise ValueError("authored_capability_projection_ambiguous")
        return self


class CapabilityAvailabilityV1(StrictCamelModel):
    state: Literal["disabled", "shadow", "ready", "blocked"]
    receipt_digest: Digest | None = None
    reason: str | None = Field(default=None, max_length=128)


class HybridAvailabilityV1(StrictCamelModel):
    text: CapabilityAvailabilityV1
    image: CapabilityAvailabilityV1
    shape: CapabilityAvailabilityV1


class ProductReadinessProjectionV1(StrictCamelModel):
    authored_creation_ready: bool = True
    fidelity_export_ready: bool = True
    promotion_ready: Literal[False] = False
    full_native_parity_ready: Literal[False] = False
    independent_vnext_generation_ready: Literal[False] = False


class KillSwitchProjectionV1(StrictCamelModel):
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    active: bool
    reason: str | None = Field(default=None, max_length=128)
    expires_at: datetime | None = None


SemanticDecisionV1: TypeAlias = Literal[
    "native",
    "representationFallback",
    "identityFailure",
    "ownershipFailure",
    "assetPublicationFailure",
    "blocked",
]


class SemanticCapabilityDecisionV1(StrictCamelModel):
    capability_id: Literal[
        "shape.gradient",
        "shape.shadow",
        "layout.transform",
        "layout.flex",
        "layout.grid",
        "shape.borderRadius",
        "shape.opacity",
        "shape.clip",
    ]
    state: Literal["disabled", "shadow", "ready", "blocked"]
    decision: SemanticDecisionV1
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    receipt_digest: Digest


class SemanticReceiptDecisionV1(StrictCamelModel):
    receipt_id: Literal["cjk", "chart", "zBand", "degradation"]
    state: Literal["disabled", "shadow", "ready", "blocked"]
    decision: SemanticDecisionV1
    reason_code: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    receipt_digest: Digest


class WorkbenchCapabilityProjectionV1(StrictCamelModel):
    schema_version: Literal["workbench-capability-projection/v1"] = (
        "workbench-capability-projection/v1"
    )
    presentation_id: uuid.UUID
    canonical_mode: Literal["legacyHtml", "bundleGraph"]
    presentation_revision: int = Field(ge=0)
    canonical_digest: Digest | None = None
    manifest_digest: Digest | None = None
    degraded_fidelity_only: bool
    fidelity: CapabilityAvailabilityV1
    hybrid: HybridAvailabilityV1
    capability_matrix: list["SemanticCapabilityDecisionV1"] = Field(
        min_length=8, max_length=8
    )
    receipts: list["SemanticReceiptDecisionV1"] = Field(min_length=4, max_length=4)
    readiness: ProductReadinessProjectionV1 = Field(
        default_factory=ProductReadinessProjectionV1
    )
    kill_switches: list[KillSwitchProjectionV1] = Field(default_factory=list, max_length=128)
    elements: list[EditableElementProjectionV1] = Field(default_factory=list, max_length=1024)
    projection_digest: Digest | None = None

    @model_validator(mode="after")
    def complete_server_registry(self) -> "WorkbenchCapabilityProjectionV1":
        expected_capabilities = {
            "shape.gradient",
            "shape.shadow",
            "layout.transform",
            "layout.flex",
            "layout.grid",
            "shape.borderRadius",
            "shape.opacity",
            "shape.clip",
        }
        expected_receipts = {"cjk", "chart", "zBand", "degradation"}
        if {item.capability_id for item in self.capability_matrix} != expected_capabilities:
            raise ValueError("authored_capability_matrix_incomplete")
        if {item.receipt_id for item in self.receipts} != expected_receipts:
            raise ValueError("authored_capability_receipts_incomplete")
        return self


class AdmissionElementV1(StrictCamelModel):
    element_id: Qid
    kind: Literal["text", "image", "shape", "connector", "group", "table", "chart", "complexIsland"]
    state: Literal["admitted", "rasterFallback", "publicationFailed"]
    admitted_features: list[str] = Field(default_factory=list, max_length=128)
    fallback_reason: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def truthful_state(self) -> "AdmissionElementV1":
        if self.state == "admitted" and (self.fallback_reason or self.kind not in {"text", "image", "shape", "chart"}):
            raise ValueError("authored_native_admission_unsupported")
        if self.state != "admitted" and not self.fallback_reason:
            raise ValueError("authored_native_fallback_reason_required")
        return self


class NativeAdmissionReceiptV1(StrictCamelModel):
    schema_version: Literal["native-admission-receipt/v1"] = "native-admission-receipt/v1"
    input_bundle_digest: Digest
    observation_receipt_digest: Digest
    reconciliation_policy_version: PolicyVersion
    serializer_id: str = Field(min_length=1, max_length=128)
    serializer_version: str = Field(min_length=1, max_length=128)
    elements: list[AdmissionElementV1] = Field(max_length=1024)
    receipt_digest: Digest | None = None


class HybridPaintEntryV1(StrictCamelModel):
    element_id: Qid | None = None
    paint_owner: Literal["native", "rasterIsland"]
    z_band: int = Field(ge=0, le=4096)
    artifact_digest: Digest | None = None
    fallback_reason: str | None = Field(default=None, max_length=128)


class HybridExportPlanV1(StrictCamelModel):
    schema_version: Literal["hybrid-export-plan/v1"] = "hybrid-export-plan/v1"
    export_attempt_id: uuid.UUID
    input_bundle_digest: Digest
    admission_receipt_digest: Digest
    renderer_source_digest: Digest
    paint_entries: list[HybridPaintEntryV1] = Field(min_length=1, max_length=4096)
    fixed_point_reached: bool
    slide_demoted_to_raster: bool = False
    plan_digest: Digest | None = None


class CapabilityScopeV1(StrictCamelModel):
    platform: str = Field(min_length=1, max_length=128)
    renderer: str = Field(min_length=1, max_length=128)
    archetypes: list[str] = Field(min_length=1, max_length=128)


class CapabilityEnvironmentV1(StrictCamelModel):
    os_build: str = Field(min_length=1, max_length=128)
    office_build: str = Field(min_length=1, max_length=128)
    renderer_image_digest: Digest
    font_inventory_digest: Digest


class CapabilityReceiptV1(StrictCamelModel):
    schema_version: Literal["authored-capability-receipt/v1"] = (
        "authored-capability-receipt/v1"
    )
    capability_id: str = Field(pattern=r"^[a-z][a-z0-9._-]{1,127}$")
    state: Literal["disabled", "shadow", "ready", "blocked"]
    scope: CapabilityScopeV1
    serializer_id: str = Field(min_length=1, max_length=128)
    serializer_version: str = Field(min_length=1, max_length=128)
    feature_set_digest: Digest
    policy_digest: Digest
    evidence_bundle_id: uuid.UUID
    evidence_digest: Digest
    issuer: str = Field(min_length=1, max_length=128)
    environment: CapabilityEnvironmentV1
    evaluated_at: datetime
    expires_at: datetime
    state_reason: str = Field(min_length=1, max_length=128)
    supersedes_receipt_digest: Digest | None = None
    revokes_receipt_digest: Digest | None = None
    receipt_digest: Digest | None = None

    @model_validator(mode="after")
    def valid_window(self) -> "CapabilityReceiptV1":
        if self.expires_at <= self.evaluated_at:
            raise ValueError("authored_capability_receipt_expiry_invalid")
        if self.supersedes_receipt_digest and self.revokes_receipt_digest:
            raise ValueError("authored_capability_receipt_transition_ambiguous")
        return self


class ExportAttemptDigestBindingV1(StrictCamelModel):
    schema_version: Literal["authored-export-attempt-binding/v1"] = (
        "authored-export-attempt-binding/v1"
    )
    export_attempt_id: uuid.UUID
    presentation_id: uuid.UUID
    presentation_revision: int = Field(ge=1)
    operation_id: uuid.UUID
    generation_attempt_id: uuid.UUID | None = None
    slide_origin_generation_attempt_ids: list[uuid.UUID | None] = Field(max_length=100)
    canonical_digest: Digest
    manifest_digest: Digest
    semantic_digests: list[Digest] = Field(max_length=100)
    master_contract_digest: Digest
    composition_digest: Digest
    font_manifest_digest: Digest
    asset_manifest_digest: Digest
    quality_policy_digest: Digest
    mode: Literal["fidelity", "hybrid"]
    renderer_source_digest: Digest
    serializer_id: str = Field(min_length=1, max_length=128)
    serializer_version: str = Field(min_length=1, max_length=128)
    receipt_digests: list[Digest] = Field(max_length=1024)
    pptx_digest: Digest | None = None
    binding_digest: Digest | None = None


class ExportAttemptDigestBindingV2(ExportAttemptDigestBindingV1):
    """Export binding with release-authority pins kept distinct from evidence receipts."""

    schema_version: Literal["authored-export-attempt-binding/v2"] = (
        "authored-export-attempt-binding/v2"
    )
    release_authority_receipt_digests: list[Digest] = Field(
        default_factory=list, max_length=128
    )


def _require_unique(values: Any, code: str) -> None:
    items = list(values)
    if len(items) != len(set(items)):
        raise ValueError(code)


def _reject_cycles(by_id: dict[str, SemanticElementBaseV1]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(element_id: str) -> None:
        if element_id in visiting:
            raise ValueError("authored_semantic_graph_cycle")
        if element_id in visited:
            return
        visiting.add(element_id)
        for child_id in by_id[element_id].child_ids:
            visit(child_id)
        visiting.remove(element_id)
        visited.add(element_id)

    for element_id in by_id:
        visit(element_id)


def _require_component_binding(bundle: EmissionBundleBaseV1, component: Any) -> None:
    if (
        component.presentation_id != bundle.presentation_id
        or component.slide_id != bundle.slide_id
        or component.slide_revision != bundle.slide_revision
        or component.operation_id != bundle.operation_id
        or component.authored_html_digest != bundle.authored_html.digest
    ):
        raise ValueError("authored_semantic_component_binding_mismatch")


__all__ = [name for name in globals() if name.endswith("V1") or name in {"EmissionBundleV1"}]
