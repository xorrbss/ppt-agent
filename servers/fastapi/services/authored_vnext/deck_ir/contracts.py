"""Pydantic contracts for Deck IR v1.3.

These models are the fail-closed boundary between provider-authored intent and
server-owned grammar/compiler code.  Every object rejects unknown fields and is
immutable after validation.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _to_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", min_length=1, max_length=128)]
RegistryId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:[._/-][a-z0-9]+)*$", min_length=1, max_length=128)]
LanguageTag = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?(?:-[A-Z]{2}|-[0-9]{3})?$")]
UnitScore = Annotated[float, Field(ge=0, le=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class RequirementV1(StrictModel):
    requirement_id: Key
    kind: Literal["claim", "evidence", "source", "data", "action", "constraint", "derivedSuggestion"]
    priority: Literal["required", "optional"]
    normalized_intent: Annotated[str, Field(min_length=1, max_length=2000)]
    source_binding_ids: list[Key] = Field(default_factory=list, max_length=128)
    minimum_occurrences: int = Field(ge=0, le=30)
    maximum_occurrences: int = Field(ge=0, le=30)
    allowed_slide_roles: list[Key] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def _occurrence_range(self) -> "RequirementV1":
        if self.minimum_occurrences > self.maximum_occurrences:
            raise ValueError("deck_ir_requirement_occurrence_range_invalid")
        if self.priority == "required" and self.minimum_occurrences < 1:
            raise ValueError("deck_ir_required_requirement_minimum_invalid")
        if self.kind == "derivedSuggestion" and self.priority == "required":
            raise ValueError("deck_ir_derived_suggestion_cannot_be_required")
        return self


class RequirementGraphV1(StrictModel):
    schema_version: Literal["requirement-graph/v1"]
    request_digest: Digest
    requirements: list[RequirementV1] = Field(min_length=1, max_length=512)
    graph_digest: Digest

    @model_validator(mode="after")
    def _unique_ids(self) -> "RequirementGraphV1":
        values = [item.requirement_id for item in self.requirements]
        if len(values) != len(set(values)):
            raise ValueError("deck_ir_requirement_id_duplicate")
        return self


class SourceBindingV1(StrictModel):
    schema_version: Literal["source-binding/v1"]
    source_binding_id: Key
    snapshot_digest: Digest
    block_id: Annotated[str, Field(min_length=1, max_length=512)]
    content_digest: Digest
    use_mode: Literal["verbatim", "structured-data", "paraphrase", "reference-only"]
    allowed_transform: Literal["none", "typed-structure", "meaning-preserving-summary", "reference"]
    required_occurrences: int = Field(ge=0, le=30)
    normalization_policy: Literal["unicode-nfc-whitespace-v1"]

    @model_validator(mode="after")
    def _transform_matches_mode(self) -> "SourceBindingV1":
        expected = {
            "verbatim": "none",
            "structured-data": "typed-structure",
            "paraphrase": "meaning-preserving-summary",
            "reference-only": "reference",
        }
        if self.allowed_transform != expected[self.use_mode]:
            raise ValueError("deck_ir_source_binding_invalid")
        if "/" in self.block_id or "\\" in self.block_id or ":\\" in self.block_id:
            raise ValueError("deck_ir_source_block_locator_invalid")
        return self


class CoverageOccurrenceV1(StrictModel):
    requirement_id: Key
    slide_key: Key
    element_key: Key
    source_binding_id: Key | None = None
    visibility: Literal["visible", "hidden"] = "visible"


class DuplicateViolationV1(StrictModel):
    requirement_id: Key
    slide_keys: list[Key] = Field(min_length=1, max_length=30)
    reason: Literal["maximum-occurrences-exceeded", "unintentional-normalized-duplicate"]


class RequirementCoverageReceiptV1(StrictModel):
    schema_version: Literal["requirement-coverage-receipt/v1"]
    requirement_graph_digest: Digest
    deck_ir_digest: Digest
    required_total: int = Field(ge=0)
    required_covered: int = Field(ge=0)
    optional_total: int = Field(ge=0)
    optional_covered: int = Field(ge=0)
    duplicate_violations: list[DuplicateViolationV1] = Field(default_factory=list)
    uncovered_requirement_ids: list[Key] = Field(default_factory=list)
    occurrences: list[CoverageOccurrenceV1] = Field(default_factory=list)
    status: Literal["passed", "failed"]
    receipt_digest: Digest

    @model_validator(mode="after")
    def _totals(self) -> "RequirementCoverageReceiptV1":
        if self.required_covered > self.required_total or self.optional_covered > self.optional_total:
            raise ValueError("deck_ir_coverage_total_invalid")
        passed = (
            self.required_covered == self.required_total
            and not self.duplicate_violations
        )
        if (self.status == "passed") != passed:
            raise ValueError("deck_ir_coverage_status_invalid")
        return self


class StoryV1(StrictModel):
    audience: Annotated[str, Field(min_length=1, max_length=500)]
    objective: Key
    thesis: Annotated[str, Field(min_length=1, max_length=2000)]
    arc: list[Key] = Field(min_length=1, max_length=30)


class DesignLanguageV1(StrictModel):
    grammar_id: Key
    mood: list[Key] = Field(min_length=1, max_length=8)
    palette_id: Key
    type_scale_id: Key
    grid_id: Key
    motif_id: Key
    density: Literal["low", "medium", "high"]
    variation_budget: int = Field(ge=0, le=8)


class ProcessStepV1(StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=500)]
    detail: Annotated[str | None, Field(max_length=1000)] = None


class TimelineEventV1(StrictModel):
    label: Annotated[str, Field(min_length=1, max_length=500)]
    time: Annotated[str, Field(min_length=1, max_length=100)]
    detail: Annotated[str | None, Field(max_length=1000)] = None


class ChartSeriesV1(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    values: list[float] = Field(min_length=1, max_length=100)


class ComponentPayloadV1(StrictModel):
    text: Annotated[str | None, Field(max_length=4000)] = None
    value: Annotated[str | float | int | None, Field()] = None
    label: Annotated[str | None, Field(max_length=500)] = None
    unit: Annotated[str | None, Field(max_length=100)] = None
    title: Annotated[str | None, Field(max_length=500)] = None
    body: Annotated[str | None, Field(max_length=3000)] = None
    badge: Annotated[str | None, Field(max_length=100)] = None
    left_items: list[str] | None = Field(default=None, max_length=30)
    right_items: list[str] | None = Field(default=None, max_length=30)
    axis_label: Annotated[str | None, Field(max_length=200)] = None
    steps: list[ProcessStepV1] | None = Field(default=None, max_length=30)
    events: list[TimelineEventV1] | None = Field(default=None, max_length=30)
    asset_slot: Key | None = None
    alt_text: Annotated[str | None, Field(max_length=1000)] = None
    caption: Annotated[str | None, Field(max_length=1000)] = None
    quote: Annotated[str | None, Field(max_length=3000)] = None
    attribution: Annotated[str | None, Field(max_length=500)] = None
    columns: list[str] | None = Field(default=None, max_length=30)
    rows: list[list[str | int | float | bool | None]] | None = Field(default=None, max_length=100)
    chart_type: Literal["bar", "line", "area", "donut", "scatter"] | None = None
    series: list[ChartSeriesV1] | None = Field(default=None, max_length=30)
    categories: list[str] | None = Field(default=None, max_length=100)
    shape_kind: Literal["rectangle", "circle", "pill", "line", "arrow"] | None = None
    from_element_key: Key | None = None
    to_element_key: Key | None = None
    child_element_keys: list[Key] | None = Field(default=None, max_length=50)
    motif: Key | None = None
    source_content_digest: Digest | None = None


class SlideComponentV1(StrictModel):
    PAYLOAD_FIELDS: ClassVar[dict[str, tuple[set[str], set[str]]]] = {
        "text": ({"text"}, {"source_content_digest"}),
        "metric": ({"value", "label"}, {"unit", "source_content_digest"}),
        "card": ({"title", "body"}, {"badge", "source_content_digest"}),
        "comparison": ({"left_items", "right_items"}, {"axis_label", "source_content_digest"}),
        "process": ({"steps"}, {"source_content_digest"}),
        "timeline": ({"events"}, {"source_content_digest"}),
        "image": ({"asset_slot", "alt_text"}, {"caption", "source_content_digest"}),
        "quote": ({"quote"}, {"attribution", "source_content_digest"}),
        "table": ({"columns", "rows"}, {"caption", "source_content_digest"}),
        "chart": ({"chart_type", "series", "categories"}, {"caption", "source_content_digest"}),
        "shape": ({"shape_kind"}, {"label"}),
        "connector": ({"from_element_key", "to_element_key"}, {"label"}),
        "group": ({"child_element_keys"}, set()),
        "decoration": ({"motif"}, set()),
    }

    element_key: Key
    kind: Literal[
        "text", "metric", "card", "comparison", "process", "timeline", "image", "quote",
        "table", "chart", "shape", "connector", "group", "decoration",
    ]
    semantic_role: Key
    content_priority: Literal["must-keep", "supporting", "optional"]
    requirement_ids: list[Key] = Field(default_factory=list, max_length=128)
    source_binding_id: Key | None = None
    layout_slot: Key
    variant: Key
    payload: ComponentPayloadV1

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> "SlideComponentV1":
        required, optional = self.PAYLOAD_FIELDS[self.kind]
        supplied = {name for name in self.payload.model_fields_set if getattr(self.payload, name) is not None}
        if not required.issubset(supplied) or not supplied.issubset(required | optional):
            raise ValueError("deck_ir_component_payload_invalid")
        if self.kind == "decoration" and (self.requirement_ids or self.source_binding_id):
            raise ValueError("deck_ir_decoration_content_ownership_invalid")
        if self.source_binding_id is not None and self.payload.source_content_digest is None:
            raise ValueError("deck_ir_source_content_digest_required")
        if self.source_binding_id is None and self.payload.source_content_digest is not None:
            raise ValueError("deck_ir_source_content_digest_unbound")
        if self.kind == "table" and self.payload.rows is not None and self.payload.columns is not None:
            if any(len(row) != len(self.payload.columns) for row in self.payload.rows):
                raise ValueError("deck_ir_table_shape_invalid")
        if self.kind == "chart" and self.payload.series is not None and self.payload.categories is not None:
            if any(len(series.values) != len(self.payload.categories) for series in self.payload.series):
                raise ValueError("deck_ir_chart_shape_invalid")
        return self


class LayoutIntentV1(StrictModel):
    composition: Key
    emphasis: Literal["left", "right", "center", "balanced", "none"]
    reading_order: Key
    density: Literal["low", "medium", "high"]


class SlideIRV1(StrictModel):
    slide_key: Key
    role: Key
    claim: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence: list[str] = Field(default_factory=list, max_length=64)
    requirement_ids: list[Key] = Field(default_factory=list, max_length=128)
    source_binding_ids: list[Key] = Field(default_factory=list, max_length=128)
    composition_intent_graph_digest: Digest
    art_direction_intent_digest: Digest
    repetition_purpose: Literal["none", "recap", "continuity", "comparison"]
    layout_intent: LayoutIntentV1
    components: list[SlideComponentV1] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _unique_components(self) -> "SlideIRV1":
        keys = [item.element_key for item in self.components]
        if len(keys) != len(set(keys)):
            raise ValueError("deck_ir_element_key_duplicate")
        if len(self.requirement_ids) != len(set(self.requirement_ids)):
            raise ValueError("deck_ir_requirement_reference_duplicate")
        if len(self.source_binding_ids) != len(set(self.source_binding_ids)):
            raise ValueError("deck_ir_source_binding_reference_duplicate")
        refs = {item for component in self.components for item in component.requirement_ids}
        if not refs.issubset(set(self.requirement_ids)):
            raise ValueError("deck_ir_component_requirement_unbound")
        source_refs = {component.source_binding_id for component in self.components if component.source_binding_id}
        if not source_refs.issubset(set(self.source_binding_ids)):
            raise ValueError("deck_ir_component_source_unbound")
        return self


class DeckIRV1(StrictModel):
    schema_version: Literal["deck-ir/v1"]
    deck_key: Key
    title: Annotated[str, Field(min_length=1, max_length=500)]
    language: LanguageTag
    story: StoryV1
    design_language: DesignLanguageV1
    slides: list[SlideIRV1] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def _deck_invariants(self) -> "DeckIRV1":
        keys = [slide.slide_key for slide in self.slides]
        if len(keys) != len(set(keys)):
            raise ValueError("deck_ir_slide_key_duplicate")
        roles = {slide.role for slide in self.slides}
        if not set(self.story.arc).issubset(roles):
            raise ValueError("deck_ir_story_arc_uncovered")
        claims: dict[str, SlideIRV1] = {}
        for slide in self.slides:
            normalized = " ".join(slide.claim.casefold().split())
            previous = claims.get(normalized)
            if previous and slide.repetition_purpose == "none" and previous.repetition_purpose == "none":
                raise ValueError("deck_ir_claim_duplicate")
            claims[normalized] = slide
        return self


class CompositionZoneV1(StrictModel):
    zone_key: Key
    semantic_role: Key
    content_refs: list[Key] = Field(min_length=1, max_length=128)
    preferred_column_span: int = Field(ge=1, le=24)
    minimum_column_span: int = Field(ge=1, le=24)
    maximum_column_span: int = Field(ge=1, le=24)
    visual_weight: Literal["dominant", "primary", "supporting", "ambient"]
    flow: Literal["vertical", "horizontal", "cluster", "stack", "flow"]

    @model_validator(mode="after")
    def _span_range(self) -> "CompositionZoneV1":
        if not self.minimum_column_span <= self.preferred_column_span <= self.maximum_column_span:
            raise ValueError("composition_intent_span_invalid")
        return self


class CompositionRelationV1(StrictModel):
    from_: Key = Field(alias="from")
    relation: Literal["leftOf", "rightOf", "above", "below", "alignTop", "alignCenter", "equalWidth", "emphasis", "flow"]
    to: Key
    strength: Literal["hard", "soft"]


class CompositionIntentGraphV1(StrictModel):
    schema_version: Literal["composition-intent-graph/v1"]
    slide_key: Key
    zones: list[CompositionZoneV1] = Field(min_length=1, max_length=32)
    relations: list[CompositionRelationV1] = Field(default_factory=list, max_length=128)
    reading_order: list[Key] = Field(min_length=1, max_length=32)
    graph_digest: Digest


class FocalAnchorV1(StrictModel):
    zone_key: Key
    strength: Literal["subtle", "medium", "strong"]


class NegativeSpaceV1(StrictModel):
    bias: Literal["left", "right", "top", "bottom", "balanced", "center"]
    amount: Literal["compact", "balanced", "generous"]


class OverlapPolicyV1(StrictModel):
    mode: Literal["none", "decorative-layering", "background-layering"]
    allowed_zone_pairs: list[tuple[Key, Key]] = Field(default_factory=list, max_length=16)
    text_occlusion: Literal["forbidden"]
    source_occlusion: Literal["forbidden"]

    @model_validator(mode="after")
    def _mode_pairs(self) -> "OverlapPolicyV1":
        if self.mode == "none" and self.allowed_zone_pairs:
            raise ValueError("art_direction_overlap_pairs_invalid")
        return self


class ArtDirectionIntentV1(StrictModel):
    schema_version: Literal["art-direction-intent/v1"]
    slide_key: Key
    composition_intent_graph_digest: Digest
    focal_anchor: FocalAnchorV1
    negative_space: NegativeSpaceV1
    asymmetry: Literal["none", "subtle", "moderate", "strong"]
    overlap_policy: OverlapPolicyV1
    image_treatment: Literal["contained", "edge-crop", "full-bleed", "framed", "masked"]
    crop_intent: Literal["none", "subject-emphasis", "context-preserving", "dramatic"]
    typography_rhythm: Literal["steady", "editorial-contrast", "metric-led", "narrative"]
    depth_intent: Literal["flat", "layered", "stage"]
    visual_energy: Literal["calm", "credible", "progressive", "dynamic"]
    intent_digest: Digest


class DesignGrammarManifestV1(StrictModel):
    schema_version: Literal["design-grammar-manifest/v1"]
    grammar_id: Key
    version: int = Field(ge=1)
    tokens_digest: Digest
    layout_registry_digest: Digest
    component_registry_digest: Digest
    art_direction_registry_digest: Digest
    candidate_policy_digest: Digest
    font_policy_digest: Digest
    capacity_policy_digest: Digest
    renderer_abi_version: Literal["design-component-renderer/v1"]
    solver_abi_version: Literal["constraint-solver/v1"]
    canonicalization_policy: Literal["canonical-json-unicode-nfc-f64-v1"]
    compiler_compatibility: list[Literal["constraint-compiler/v1"]] = Field(min_length=1)
    deprecated_capabilities: list[Key] = Field(default_factory=list)
    manifest_digest: Digest


class GenerationStrategyDecisionReceiptV1(StrictModel):
    schema_version: Literal["generation-strategy-decision-receipt/v1"]
    operation_id: UUID
    requirement_graph_digest: Digest
    requested_profile: Literal["balanced-quality-v1", "fast-preview-v1", "release-v1"]
    grammar_capability_digest: Digest
    compiler_capability_digest: Digest
    rollout_phase: Literal["D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]
    selected_strategy: Literal["deck-ir", "legacy-authored", "reject"]
    missing_capabilities: list[Key] = Field(default_factory=list)
    decision_reason: Key
    routing_policy_digest: Digest
    receipt_digest: Digest

    @model_validator(mode="after")
    def _routing(self) -> "GenerationStrategyDecisionReceiptV1":
        if self.rollout_phase in {"D6", "D7", "D8"} and self.missing_capabilities and self.selected_strategy != "reject":
            raise ValueError("deck_ir_post_d6_unsupported_strategy_invalid")
        if self.selected_strategy == "deck-ir" and self.missing_capabilities:
            raise ValueError("deck_ir_strategy_capability_mismatch")
        return self


class CandidateQualityVectorV1(StrictModel):
    hierarchy: UnitScore
    whitespace: UnitScore
    role_fitness: UnitScore
    art_direction_alignment: UnitScore = 0
    density: UnitScore = 0


class CandidateV1(StrictModel):
    candidate_id: Key
    layout_id: RegistryId
    hard_constraint_status: Literal["passed", "failed"]
    quality_vector: CandidateQualityVectorV1
    diversity_signature: Digest
    candidate_digest: Digest


class SlideCandidateSelectionV1(StrictModel):
    slide_key: Key
    requested_k: int = Field(ge=2, le=8)
    enumerated_count: int = Field(ge=0, le=8)
    feasible_count: int = Field(ge=0, le=8)
    candidates: list[CandidateV1] = Field(min_length=1, max_length=8)
    selected_candidate_id: Key

    @model_validator(mode="after")
    def _selection(self) -> "SlideCandidateSelectionV1":
        if self.enumerated_count != len(self.candidates):
            raise ValueError("deck_ir_candidate_count_invalid")
        feasible = [item for item in self.candidates if item.hard_constraint_status == "passed"]
        if self.feasible_count != len(feasible):
            raise ValueError("deck_ir_feasible_count_invalid")
        selected = next((item for item in self.candidates if item.candidate_id == self.selected_candidate_id), None)
        if selected is None or selected.hard_constraint_status != "passed":
            raise ValueError("deck_ir_hard_failure_candidate_selected")
        return self


class DeckSelectionV1(StrictModel):
    algorithm: Literal["pareto-beam-v1"]
    beam_width: int = Field(ge=1, le=8)
    objective_policy_digest: Digest
    selected_combination_digest: Digest


class CandidateSetSelectionReceiptV1(StrictModel):
    schema_version: Literal["candidate-set-selection-receipt/v1"]
    operation_id: UUID
    deck_ir_digest: Digest
    grammar_digest: Digest
    compiler_version: Literal["constraint-compiler/v1"]
    candidate_policy_digest: Digest
    slides: list[SlideCandidateSelectionV1] = Field(min_length=1, max_length=30)
    deck_selection: DeckSelectionV1
    provider_calls_added: Literal[0]
    receipt_digest: Digest

    @model_validator(mode="after")
    def _unique_slides(self) -> "CandidateSetSelectionReceiptV1":
        keys = [item.slide_key for item in self.slides]
        if len(keys) != len(set(keys)):
            raise ValueError("deck_ir_candidate_slide_duplicate")
        return self


class ConstraintCompileReceiptV1(StrictModel):
    schema_version: Literal["constraint-compile-receipt/v1"]
    operation_id: UUID
    slide_key: Key
    strategy_decision_digest: Digest
    deck_ir_digest: Digest
    slide_ir_digest: Digest
    composition_intent_graph_digest: Digest
    art_direction_intent_digest: Digest
    candidate_selection_receipt_digest: Digest
    grammar_digest: Digest
    compiler_version: Literal["constraint-compiler/v1"]
    chosen_layout_id: RegistryId
    qid_registry_digest: Digest
    html_digest: Digest
    semantic_digest: Digest
    hard_constraint_status: Literal["passed", "failed"]
    soft_score: float
    measurement_closure_revision: int = Field(ge=0, le=2)
    browser_measurement_digest: Digest | None
    recovery_steps: list[Key] = Field(default_factory=list, max_length=8)
    receipt_digest: Digest


class OverlapPairDecisionV1(StrictModel):
    zone_pair: tuple[Key, Key]
    decision: Literal["separate"]
    reason: Literal["exclusive_content_overlap_forbidden", "compiler_owned_overlay_evidence_unavailable"]


class ArtIntentResolutionReceiptV1(StrictModel):
    schema_version: Literal["art-intent-resolution-receipt/v1"]
    slide_key: Key
    provider_intent_digest: Digest
    resolved_intent_digest: Digest
    slide_digest: Digest
    composition_digest: Digest
    grammar_digest: Digest
    layout_id: RegistryId
    pair_decisions: list[OverlapPairDecisionV1] = Field(max_length=16)
    content_preserved: Literal[True]
    provider_calls_added: Literal[0]
    receipt_digest: Digest


class LocalCandidateV2(StrictModel):
    candidate_id: Key
    slide_key: Key
    layout_id: RegistryId
    variant_id: Key
    hard_constraint_status: Literal["passed", "failed"]
    quality_vector: dict[Literal["hierarchy", "whitespace", "roleFitness", "artDirection"], UnitScore]
    diversity_signature: Digest
    candidate_digest: Digest
    rank: int = Field(ge=1, le=8)
    resolution_receipt: ArtIntentResolutionReceiptV1


class LocalSlideSelectionV2(StrictModel):
    slide_key: Key
    requested_k: int = Field(ge=4, le=8)
    enumerated_count: int = Field(ge=4, le=8)
    feasible_count: int = Field(ge=1, le=8)
    candidates: list[LocalCandidateV2] = Field(min_length=4, max_length=8)
    selected_candidate_id: Key


class LocalDeckSelectionV2(StrictModel):
    algorithm: Literal["pareto-beam-v1"]
    beam_width: int = Field(ge=1, le=8)
    selected_combination_digest: Digest


class CandidateSetSelectionReceiptV2(StrictModel):
    schema_version: Literal["candidate-set-selection-receipt/v2"]
    grammar_digest: Digest
    compiler_version: Literal["constraint-compiler/v1"]
    candidate_policy_digest: Digest
    slides: list[LocalSlideSelectionV2] = Field(min_length=1, max_length=30)
    deck_selection: LocalDeckSelectionV2
    initial_selection_receipt_digest: Digest | None = None
    # Deterministic composition ranking, never a measured aesthetic score.
    scoring_basis: Literal["solver-structural-fitness/v1"] | None = None
    provider_calls_added: Literal[0]
    receipt_digest: Digest


class ZoneContractV2(StrictModel):
    zone_key: Key
    qid: Key
    content_qids: list[Key] = Field(min_length=1, max_length=128)
    minimum_column_span: int = Field(ge=1, le=24)
    maximum_column_span: int = Field(ge=1, le=24)
    capability: Literal["exclusive-content", "overlay-host/backplate", "annotation", "ambient"]


class ConnectorContractV2(StrictModel):
    qid: Key
    from_qid: Key
    to_qid: Key
    direction: Literal["right", "left", "up", "down"]


class ConstraintCompileReceiptV2(ConstraintCompileReceiptV1):
    schema_version: Literal["constraint-compile-receipt/v2"]
    candidate_id: Key
    provider_intent_digest: Digest
    resolved_intent_digest: Digest
    intent_resolution_receipt: ArtIntentResolutionReceiptV1
    zone_contracts: list[ZoneContractV2] = Field(min_length=1, max_length=32)
    composition_relations: list[CompositionRelationV1] = Field(max_length=128)
    connector_contracts: list[ConnectorContractV2] = Field(default_factory=list, max_length=12)
    content_text_digest: Digest
    initial_candidate_selection_receipt_digest: Digest | None = None
    reference_frame: dict[str, Any] | None = None
    style_profile_digest: Digest | None = None


class CriticOperationV1(StrictModel):
    op: Literal[
        "replaceLayoutVariant", "setDensity", "setEmphasis", "setComponentVariant",
        "setAssetCropIntent", "setReadingOrderVariant", "setFocalStrength",
        "setNegativeSpaceIntent", "setAsymmetryIntent", "setImageTreatment", "setTypographyRhythm",
    ]
    value: RegistryId


class CriticTargetV1(StrictModel):
    slide_key: Key
    allowed_operations: list[CriticOperationV1] = Field(min_length=1, max_length=16)


class DeckCriticPatchV1(StrictModel):
    schema_version: Literal["deck-critic-patch/v1"]
    deck_ir_digest: Digest
    observation_digest: Digest
    targets: list[CriticTargetV1] = Field(min_length=1, max_length=30)


class GenerationIntentLineageV1(StrictModel):
    schema_version: Literal["generation-intent-lineage/v1"]
    intent_consistency_state: Literal["exact", "derived-by-command", "origin-only", "invalidated"]
    origin_generation_intent_digest: Digest | None = None
    generation_intent_digest: Digest | None = None
    compile_receipt_digest: Digest | None = None
    parent_bundle_digest: Digest | None = None
    edit_command_receipt_digest: Digest | None = None

    @model_validator(mode="after")
    def _state_shape(self) -> "GenerationIntentLineageV1":
        if self.intent_consistency_state == "exact":
            if self.generation_intent_digest is None or self.compile_receipt_digest is None:
                raise ValueError("deck_ir_exact_lineage_invalid")
            if self.parent_bundle_digest is not None or self.edit_command_receipt_digest is not None:
                raise ValueError("deck_ir_exact_lineage_invalid")
        elif self.intent_consistency_state == "derived-by-command":
            required = (self.origin_generation_intent_digest, self.compile_receipt_digest, self.parent_bundle_digest, self.edit_command_receipt_digest)
            if any(value is None for value in required) or self.generation_intent_digest is not None:
                raise ValueError("deck_ir_derived_lineage_invalid")
        elif self.intent_consistency_state == "origin-only" and self.origin_generation_intent_digest is None:
            raise ValueError("deck_ir_origin_lineage_invalid")
        return self


class PowerAnalysisV1(StrictModel):
    one_sided_alpha: Literal[0.025]
    target_power: Literal[0.8]
    non_inferiority_margin: Literal[0.25]
    minimum_overall_pairs: int = Field(ge=120)
    minimum_pairs_per_required_marginal_stratum: int = Field(ge=30)


class ReviewProtocolV1(StrictModel):
    reviewers_per_pair: int = Field(ge=3)
    rating_scale: Literal["1..5"]
    calibration_required: Literal[True]
    path_rc_and_filename_blinded: Literal[True]
    tie_handling: Literal["explicit-tie"]


class AbsoluteFloorsV1(StrictModel):
    overall_expert_mean_lower_bound: Annotated[float, Field(ge=4, le=5)]
    required_dimension_mean_lower_bound: Annotated[float, Field(ge=3.75, le=5)]
    worst_decile_mean_lower_bound: Annotated[float, Field(ge=3.5, le=5)]
    p0_maximum: Literal[0]
    p1_maximum: Literal[0]


class PairwisePreferenceV1(StrictModel):
    candidate_preferred_or_tied_point_minimum: Annotated[float, Field(ge=0.9, le=1)]
    one_sided95_lower_bound_minimum: Annotated[float, Field(ge=0.85, le=1)]


class ReliabilityV1(StrictModel):
    metric: Literal["krippendorff-alpha-ordinal"]
    minimum: Annotated[float, Field(ge=0.67, le=1)]
    below_minimum_action: Literal["evaluation-invalid-recalibrate"]


class VisualQualityEvaluationPolicyV1(StrictModel):
    schema_version: Literal["visual-quality-evaluation-policy/v1"]
    corpus_digest: Digest
    sealed_holdout_digest: Digest
    study_design: Literal["paired-stratified-blind"]
    power_analysis: PowerAnalysisV1
    review_protocol: ReviewProtocolV1
    absolute_floors: AbsoluteFloorsV1
    pairwise_preference: PairwisePreferenceV1
    reliability: ReliabilityV1
    confidence_method: Literal["stratified-bootstrap-one-sided-95-v1"]
    multiplicity_correction: Literal["holm-v1"]
    policy_digest: Digest


class VisualNonRegressionReportV1(StrictModel):
    schema_version: Literal["visual-non-regression-report/v1"]
    baseline_rc_digest: Digest
    candidate_rc_digest: Digest
    corpus_digest: Digest
    evaluation_policy_digest: Digest
    strata: list[str] = Field(min_length=1)
    blind_assignment_digest: Digest
    reviewer_receipt_digests: list[Digest] = Field(min_length=3)
    evaluation_validity: Literal["valid", "invalid"]
    overall_pair_count: int = Field(ge=0)
    minimum_marginal_stratum_pair_count: int = Field(ge=0)
    reviewer_agreement: UnitScore
    required_coverage_ratio: UnitScore
    p0_count: int = Field(ge=0)
    p1_count: int = Field(ge=0)
    absolute_overall_mean_lower_bound: float
    absolute_required_dimension_minimum_lower_bound: float
    absolute_worst_decile_mean_lower_bound: float
    candidate_preferred_or_tied_ratio: UnitScore
    non_inferiority_lower_bound: float
    worst_decile_rating_delta: float
    composition_entropy_delta: float
    consecutive_repeat_rate_delta: float
    absolute_quality_status: Literal["passed", "failed", "evaluation-invalid"]
    non_inferiority_status: Literal["passed", "failed", "evaluation-invalid"]
    failed_strata: list[str] = Field(default_factory=list)
    status: Literal["passed", "failed", "evaluation-invalid"]
    report_digest: Digest

    @model_validator(mode="after")
    def _derived_status(self) -> "VisualNonRegressionReportV1":
        if self.evaluation_validity == "invalid":
            if self.status != "evaluation-invalid":
                raise ValueError("deck_ir_evaluation_invalid_status_required")
            return self
        passed = (
            self.overall_pair_count >= 120
            and self.minimum_marginal_stratum_pair_count >= 30
            and self.reviewer_agreement >= 0.67
            and self.required_coverage_ratio == 1
            and self.p0_count == self.p1_count == 0
            and self.absolute_overall_mean_lower_bound >= 4
            and self.absolute_required_dimension_minimum_lower_bound >= 3.75
            and self.absolute_worst_decile_mean_lower_bound >= 3.5
            and self.candidate_preferred_or_tied_ratio >= 0.9
            and self.non_inferiority_lower_bound >= -0.25
            and self.worst_decile_rating_delta >= -0.25
            and self.composition_entropy_delta >= -0.05
            and self.consecutive_repeat_rate_delta <= 0.02
            and not self.failed_strata
            and self.absolute_quality_status == "passed"
            and self.non_inferiority_status == "passed"
        )
        if (self.status == "passed") != passed:
            raise ValueError("deck_ir_non_regression_status_invalid")
        return self


class ChangedDigestsV1(StrictModel):
    grammar: Digest
    compiler: Digest
    model_fingerprint: Digest
    font: Digest | None = None
    asset: Digest | None = None
    candidate_policy: Digest | None = None
    evaluation_policy: Digest | None = None


class CanaryWindowV1(StrictModel):
    cohort_percent: Literal[1, 5, 25, 100]
    minimum_operations: int = Field(ge=0)
    minimum_hours: int = Field(ge=0)
    status: Literal["pending", "passed", "failed"]

    @model_validator(mode="after")
    def _minimums(self) -> "CanaryWindowV1":
        expected = {1: (100, 24), 5: (500, 24), 25: (1000, 48), 100: (0, 0)}[self.cohort_percent]
        if self.status == "passed" and (self.minimum_operations < expected[0] or self.minimum_hours < expected[1]):
            raise ValueError("deck_ir_canary_minimum_invalid")
        return self


class ObservedDriftV1(StrictModel):
    p0: int = Field(ge=0)
    p1: int = Field(ge=0)
    repeat_rate_delta: float
    composition_entropy_delta: float = 0
    p95_latency_delta_ms: int
    generation_failure_rate_delta_points: float = 0


class QualityDriftReceiptV1(StrictModel):
    schema_version: Literal["quality-drift-receipt/v1"]
    candidate_rc_digest: Digest
    previous_qualified_rc_digest: Digest
    changed_digests: ChangedDigestsV1
    shadow_visual_report_digest: Digest
    canary_windows: list[CanaryWindowV1] = Field(default_factory=list, max_length=4)
    observed_drift: ObservedDriftV1
    triggered_rules: list[Key] = Field(default_factory=list)
    action: Literal["advance-canary", "hold-canary", "pause-and-rollback", "qualified"]
    rollback_target_rc_digest: Digest
    receipt_digest: Digest

    @model_validator(mode="after")
    def _immediate_stop(self) -> "QualityDriftReceiptV1":
        if (self.observed_drift.p0 or self.observed_drift.p1) and self.action != "pause-and-rollback":
            raise ValueError("deck_ir_quality_drift_immediate_stop_required")
        if self.action == "pause-and-rollback" and self.rollback_target_rc_digest != self.previous_qualified_rc_digest:
            raise ValueError("deck_ir_quality_drift_rollback_target_invalid")
        return self


SEALED_CONTRACT_DIGEST_FIELDS: dict[type[BaseModel], str] = {
    RequirementGraphV1: "graph_digest",
    RequirementCoverageReceiptV1: "receipt_digest",
    CompositionIntentGraphV1: "graph_digest",
    ArtDirectionIntentV1: "intent_digest",
    DesignGrammarManifestV1: "manifest_digest",
    GenerationStrategyDecisionReceiptV1: "receipt_digest",
    ConstraintCompileReceiptV1: "receipt_digest",
    ConstraintCompileReceiptV2: "receipt_digest",
    CandidateSetSelectionReceiptV2: "receipt_digest",
    ArtIntentResolutionReceiptV1: "receipt_digest",
    CandidateSetSelectionReceiptV1: "receipt_digest",
    VisualQualityEvaluationPolicyV1: "policy_digest",
    VisualNonRegressionReportV1: "report_digest",
    QualityDriftReceiptV1: "receipt_digest",
}


CONTRACT_MODELS: tuple[type[BaseModel], ...] = (
    RequirementGraphV1,
    SourceBindingV1,
    RequirementCoverageReceiptV1,
    DeckIRV1,
    SlideIRV1,
    CompositionIntentGraphV1,
    ArtDirectionIntentV1,
    DesignGrammarManifestV1,
    GenerationStrategyDecisionReceiptV1,
    ConstraintCompileReceiptV1,
    ConstraintCompileReceiptV2,
    CandidateSetSelectionReceiptV2,
    ArtIntentResolutionReceiptV1,
    CandidateSetSelectionReceiptV1,
    DeckCriticPatchV1,
    GenerationIntentLineageV1,
    VisualQualityEvaluationPolicyV1,
    VisualNonRegressionReportV1,
    QualityDriftReceiptV1,
)


__all__ = [model.__name__ for model in CONTRACT_MODELS] + [
    "CONTRACT_MODELS", "SEALED_CONTRACT_DIGEST_FIELDS", "Digest", "SlideIRV1", "StrictModel",
]
