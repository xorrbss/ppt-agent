"""Strict contracts for Narrative IR and deterministic Design Pack routing.

The provider owns document meaning only.  Frame identity, geometry, styling,
safety policy, compiler choice, and publication authority remain server-owned.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _to_camel(name: str) -> str:
    first, *rest = name.split("_")
    return first + "".join(part.capitalize() for part in rest)


Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Key = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9]*(?:[._:/-][a-z0-9]+)*$",
        min_length=1,
        max_length=160,
    ),
]
DisplayText = Annotated[str, Field(min_length=1, max_length=4000)]
UnitScore = Annotated[float, Field(ge=0, le=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
    )


class SourceOwnershipV1(StrictModel):
    source_binding_id: Key
    source_content_digest: Digest
    use_mode: Literal["verbatim", "structured-data", "meaning-preserving", "reference-only"]
    required: bool = True


class NarrativeDatumV1(StrictModel):
    datum_key: Key
    label: DisplayText
    value: str | int | float | bool
    unit: Annotated[str, Field(max_length=80)] | None = None
    source_binding_id: Key | None = None


class ContentUnitPayloadV1(StrictModel):
    """Typed, deeply immutable payload retained from the admitted source."""

    text: Annotated[str, Field(max_length=32000)] | None = None
    data: tuple[NarrativeDatumV1, ...] = Field(default=(), max_length=2048)
    asset_occurrence_id: Key | None = None
    asset_digest: Digest | None = None


class ContentUnitV1(StrictModel):
    """Smallest digest-bound unit that downstream composition must place once."""

    content_unit_id: Key
    kind: Literal["text", "image", "table", "chart", "number"]
    payload: ContentUnitPayloadV1
    content_digest: Digest
    required: bool = True
    source_ownership: tuple[SourceOwnershipV1, ...] = Field(min_length=1, max_length=256)
    source_page_id: Key
    source_page_index: int = Field(ge=0, le=999)
    source_unit_index: int = Field(ge=0, le=9999)
    source_order: int = Field(ge=0, le=999999)

    @model_validator(mode="after")
    def _payload_matches_kind(self) -> "ContentUnitV1":
        if self.kind == "text" and self.payload.text is None:
            raise ValueError("content_unit_text_missing")
        if self.kind == "image" and (
            self.payload.asset_occurrence_id is None or self.payload.asset_digest is None
        ):
            raise ValueError("content_unit_image_binding_missing")
        source_ids = [item.source_binding_id for item in self.source_ownership]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("content_unit_source_ownership_duplicate")
        return self


class PageContentSnapshotV1(StrictModel):
    """Immutable source page snapshot; presentation layout is intentionally absent."""

    schema_version: Literal["page-content-snapshot/v1"] = "page-content-snapshot/v1"
    page_id: Key
    page_index: int = Field(ge=0, le=999)
    source_unit_id: Key
    content_units: tuple[ContentUnitV1, ...] = Field(default=(), max_length=10000)
    page_digest: Digest

    @model_validator(mode="after")
    def _unit_order_is_exact(self) -> "PageContentSnapshotV1":
        unit_indexes = [item.source_unit_index for item in self.content_units]
        if unit_indexes != list(range(len(self.content_units))):
            raise ValueError("page_content_unit_order_invalid")
        if any(
            item.source_page_id != self.page_id or item.source_page_index != self.page_index
            for item in self.content_units
        ):
            raise ValueError("page_content_unit_source_mismatch")
        return self


class ContentGraphV1(StrictModel):
    schema_version: Literal["content-graph/v1"] = "content-graph/v1"
    generation_lane: Literal["preserve-source", "new-authoring"]
    pages: tuple[PageContentSnapshotV1, ...] = Field(min_length=1, max_length=100)
    source_unit_order: tuple[SourceUnitOrderV1, ...]
    required_content_unit_ids: tuple[Key, ...]
    content_graph_digest: Digest

    @model_validator(mode="after")
    def _graph_order_is_exact(self) -> "ContentGraphV1":
        indexes = [page.page_index for page in self.pages]
        if indexes != list(range(len(self.pages))):
            raise ValueError("content_graph_page_order_invalid")
        expected_order = [page.source_unit_id for page in self.pages]
        actual_order = [item.source_unit_id for item in self.source_unit_order]
        if actual_order != expected_order:
            raise ValueError("content_graph_source_unit_order_mismatch")
        units = [unit for page in self.pages for unit in page.content_units]
        unit_ids = [unit.content_unit_id for unit in units]
        if len(unit_ids) != len(set(unit_ids)):
            raise ValueError("content_graph_content_unit_duplicate")
        if [unit.source_order for unit in units] != list(range(len(units))):
            raise ValueError("content_graph_source_order_invalid")
        required = [unit.content_unit_id for unit in units if unit.required]
        if list(self.required_content_unit_ids) != required:
            raise ValueError("content_graph_required_unit_mismatch")
        return self


class PreserveSourceInputV1(StrictModel):
    """Provider-free preserve lane input; page split/merge is not representable."""

    schema_version: Literal["preserve-source-input/v1"] = "preserve-source-input/v1"
    operation_id: UUID
    generation_lane: Literal["preserve-source"] = "preserve-source"
    content_graph: ContentGraphV1
    input_digest: Digest

    @model_validator(mode="after")
    def _lane_matches_graph(self) -> "PreserveSourceInputV1":
        if self.content_graph.generation_lane != "preserve-source":
            raise ValueError("preserve_source_content_graph_lane_invalid")
        return self


class NarrativeSlideDraftV1(StrictModel):
    slide_key: Key
    sequence_index: int = Field(ge=0, le=999)
    role: Key
    key_message: DisplayText
    supporting_points: list[DisplayText] = Field(default_factory=list, max_length=24)
    data_representation: Literal[
        "none",
        "metric",
        "table",
        "chart",
        "timeline",
        "process",
        "comparison",
        "image",
        "quote",
    ] = "none"
    data: list[NarrativeDatumV1] = Field(default_factory=list, max_length=128)
    source_ownership: list[SourceOwnershipV1] = Field(default_factory=list, max_length=256)
    frame_semantics: list[Key] = Field(default_factory=list, max_length=24)
    density: Literal["sparse", "medium", "dense"] = "medium"
    content_unit_ids: tuple[Key, ...] = Field(default=(), max_length=10000)
    sealed_content_units: tuple[ContentUnitV1, ...] = Field(default=(), max_length=10000)

    @model_validator(mode="after")
    def _data_matches_representation(self) -> "NarrativeSlideDraftV1":
        if self.data_representation == "none" and self.data:
            raise ValueError("narrative_ir_data_representation_invalid")
        source_ids = [item.source_binding_id for item in self.source_ownership]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("narrative_ir_source_ownership_duplicate")
        owned = set(source_ids)
        if any(item.source_binding_id not in owned for item in self.data if item.source_binding_id):
            raise ValueError("narrative_ir_datum_source_unowned")
        if len(self.content_unit_ids) != len(set(self.content_unit_ids)):
            raise ValueError("narrative_ir_content_unit_duplicate")
        if self.sealed_content_units:
            sealed_ids = [item.content_unit_id for item in self.sealed_content_units]
            if list(self.content_unit_ids) != sealed_ids:
                raise ValueError("narrative_ir_content_unit_binding_mismatch")
        return self


class SourceUnitOrderV1(StrictModel):
    source_index: int = Field(ge=0, le=999)
    source_unit_id: Key


class NarrativeDraftV1(StrictModel):
    schema_version: Literal["narrative-ir-draft/v1"] = "narrative-ir-draft/v1"
    title: DisplayText
    language: Annotated[str, Field(min_length=2, max_length=64)]
    slides: list[NarrativeSlideDraftV1] = Field(min_length=1, max_length=100)
    source_unit_order: list[SourceUnitOrderV1] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _exact_sequence(self) -> "NarrativeDraftV1":
        indexes = [item.sequence_index for item in self.slides]
        if indexes != list(range(len(self.slides))):
            raise ValueError("narrative_ir_slide_order_invalid")
        keys = [item.slide_key for item in self.slides]
        if len(keys) != len(set(keys)):
            raise ValueError("narrative_ir_slide_key_duplicate")
        if self.source_unit_order:
            source_indexes = [item.source_index for item in self.source_unit_order]
            if source_indexes != list(range(len(self.source_unit_order))):
                raise ValueError("narrative_ir_source_unit_order_invalid")
            source_ids = [item.source_unit_id for item in self.source_unit_order]
            if len(source_ids) != len(set(source_ids)):
                raise ValueError("narrative_ir_source_unit_duplicate")
        return self


class NarrativeIRV1(NarrativeDraftV1):
    schema_version: Literal["narrative-ir/v1"] = "narrative-ir/v1"
    operation_id: UUID
    generation_lane: Literal["new-authoring"] = "new-authoring"
    content_graph_digest: Digest | None = None
    narrative_digest: Digest


class PageContentIntegrityV1(StrictModel):
    page_index: int = Field(ge=0, le=999)
    expected_content_unit_ids: tuple[Key, ...]
    bound_content_unit_ids: tuple[Key, ...]
    rendered_content_unit_ids: tuple[Key, ...]
    expected_digest: Digest
    bound_digest: Digest
    rendered_digest: Digest
    unmapped_required_units: tuple[Key, ...] = ()
    duplicate_required_units: tuple[Key, ...] = ()
    transformed_required_units: tuple[Key, ...] = ()
    hidden_required_units: tuple[Key, ...] = ()
    off_canvas_required_units: tuple[Key, ...] = ()
    expected_asset_occurrences: tuple[Key, ...] = ()
    rendered_asset_occurrences: tuple[Key, ...] = ()
    source_ownership_digest: Digest
    page_order_error: bool = False


class ContentIntegrityReceiptV1(StrictModel):
    schema_version: Literal["content-integrity-receipt/v1"] = (
        "content-integrity-receipt/v1"
    )
    operation_id: UUID
    generation_lane: Literal["preserve-source", "new-authoring"]
    content_graph_digest: Digest
    narrative_digest: Digest | None = None
    pages: tuple[PageContentIntegrityV1, ...] = Field(min_length=1, max_length=100)
    required_coverage: Literal[1.0]
    duplicate_required_count: Literal[0]
    visible_missing_count: Literal[0]
    required_asset_missing_count: Literal[0]
    page_order_error_count: Literal[0]
    status: Literal["passed"]
    receipt_digest: Digest


class ProviderCallReservationV1(StrictModel):
    reservation_id: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$"),
    ]
    purpose: Literal["narrative.initial", "narrative.recovery"]
    logical_ordinal: int = Field(ge=1, le=2)
    physical_ordinal: int = Field(ge=1, le=2)


class ProviderCallBudgetV1(StrictModel):
    schema_version: Literal["narrative-provider-call-budget/v1"] = (
        "narrative-provider-call-budget/v1"
    )
    maximum_logical_calls: Literal[2] = 2
    maximum_physical_calls: Literal[2] = 2
    actual_logical_calls: int = Field(ge=0, le=2)
    actual_physical_calls: int = Field(ge=0, le=2)
    reservations: list[ProviderCallReservationV1] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def _reservations_match_calls(self) -> "ProviderCallBudgetV1":
        if len(self.reservations) != self.actual_physical_calls:
            raise ValueError("narrative_provider_reservation_count_invalid")
        physical = [item.physical_ordinal for item in self.reservations]
        if physical != list(range(1, len(physical) + 1)):
            raise ValueError("narrative_provider_reservation_order_invalid")
        if len({item.reservation_id for item in self.reservations}) != len(self.reservations):
            raise ValueError("narrative_provider_reservation_duplicate")
        return self


class FrameQualityVectorV1(StrictModel):
    role_fitness: UnitScore
    content_capacity: UnitScore
    source_compatibility: UnitScore
    semantic_fit: UnitScore


class FrameSearchCandidateV1(StrictModel):
    candidate_id: Key
    frame_id: Key
    frame_digest: Digest
    source_part_digest: Digest
    hard_constraint_status: Literal["passed", "failed"]
    rejection_reasons: list[Key] = Field(default_factory=list, max_length=16)
    quality_vector: FrameQualityVectorV1
    pareto_layer: int = Field(ge=0, le=999)
    rank: int = Field(ge=1, le=10000)
    candidate_digest: Digest


class SlideFrameSearchV1(StrictModel):
    slide_key: Key
    sequence_index: int = Field(ge=0, le=999)
    candidates: list[FrameSearchCandidateV1] = Field(min_length=1, max_length=1000)
    eligible_count: int = Field(ge=0, le=1000)


class FrameSearchReceiptV1(StrictModel):
    schema_version: Literal["frame-search-receipt/v1"] = "frame-search-receipt/v1"
    operation_id: UUID
    design_pack_id: Key
    design_pack_version: int = Field(ge=1)
    design_pack_manifest_digest: Digest
    frame_registry_digest: Digest
    search_policy_digest: Digest
    narrative_digest: Digest
    slides: list[SlideFrameSearchV1] = Field(min_length=1, max_length=100)
    status: Literal["passed", "no-compatible-frame"]
    receipt_digest: Digest


class SlideFrameSelectionV1(StrictModel):
    slide_key: Key
    sequence_index: int = Field(ge=0, le=999)
    selected_candidate_id: Key
    selected_frame_id: Key
    selected_frame_digest: Digest
    selected_source_part_digest: Digest
    quality_vector: FrameQualityVectorV1
    tie_break_digest: Digest


class FrameSelectionReceiptV1(StrictModel):
    schema_version: Literal["frame-selection-receipt/v1"] = (
        "frame-selection-receipt/v1"
    )
    operation_id: UUID
    design_pack_id: Key
    design_pack_version: int = Field(ge=1)
    design_pack_manifest_digest: Digest
    frame_registry_digest: Digest
    search_receipt_digest: Digest
    narrative_digest: Digest
    selection_policy_digest: Digest
    slides: list[SlideFrameSelectionV1] = Field(min_length=1, max_length=100)
    receipt_digest: Digest


class RouteResolutionReceiptV1(StrictModel):
    schema_version: Literal["narrative-route-resolution-receipt/v1"] = (
        "narrative-route-resolution-receipt/v1"
    )
    operation_id: UUID
    narrative_digest: Digest
    design_pack_id: Key | None = None
    design_pack_version: int | None = Field(default=None, ge=1)
    design_pack_manifest_digest: Digest | None = None
    frame_registry_digest: Digest | None = None
    frame_search_receipt_digest: Digest | None = None
    frame_selection_receipt_digest: Digest | None = None
    provider_call_budget: ProviderCallBudgetV1
    final_route: Literal["source-native", "authored-scene-fallback", "reject"]
    fallback_reason: Literal["design-pack-unavailable", "no-compatible-frame"] | None = None
    whole_operation: Literal[True] = True
    legacy_provider_html_allowed: Literal[False] = False
    receipt_digest: Digest

    @model_validator(mode="after")
    def _route_fields_match(self) -> "RouteResolutionReceiptV1":
        pack_fields = (
            self.design_pack_id,
            self.design_pack_version,
            self.design_pack_manifest_digest,
            self.frame_registry_digest,
        )
        if self.final_route == "source-native":
            if any(value is None for value in pack_fields):
                raise ValueError("narrative_route_pack_binding_missing")
            if not self.frame_search_receipt_digest or not self.frame_selection_receipt_digest:
                raise ValueError("narrative_route_selection_binding_missing")
            if self.fallback_reason is not None:
                raise ValueError("narrative_route_fallback_reason_invalid")
        elif self.final_route == "authored-scene-fallback":
            if self.fallback_reason is None:
                raise ValueError("narrative_route_fallback_reason_missing")
        return self


class NarrativeCheckpointV1(StrictModel):
    schema_version: Literal["narrative-ir-checkpoint/v1"] = "narrative-ir-checkpoint/v1"
    operation_id: UUID
    job_id: UUID
    attempt_id: Digest
    fencing_token: int = Field(ge=1)
    strategy_receipt_digest: Digest
    narrative: NarrativeIRV1
    provider_call_budget: ProviderCallBudgetV1
    frame_search_receipt: FrameSearchReceiptV1 | None = None
    frame_selection_receipt: FrameSelectionReceiptV1 | None = None
    route_resolution_receipt: RouteResolutionReceiptV1
    lineage_digest: Digest
    checkpoint_digest: Digest


ContentGraphV1.model_rebuild()


def model_payload(value: StrictModel) -> dict[str, Any]:
    return value.model_dump(mode="json", by_alias=True)


__all__ = [
    "ContentGraphV1",
    "ContentIntegrityReceiptV1",
    "ContentUnitPayloadV1",
    "ContentUnitV1",
    "Digest",
    "FrameQualityVectorV1",
    "FrameSearchCandidateV1",
    "FrameSearchReceiptV1",
    "FrameSelectionReceiptV1",
    "Key",
    "NarrativeCheckpointV1",
    "NarrativeDraftV1",
    "NarrativeIRV1",
    "PageContentIntegrityV1",
    "PageContentSnapshotV1",
    "PreserveSourceInputV1",
    "ProviderCallBudgetV1",
    "ProviderCallReservationV1",
    "RouteResolutionReceiptV1",
    "SlideFrameSearchV1",
    "SlideFrameSelectionV1",
    "SourceOwnershipV1",
    "SourceUnitOrderV1",
    "model_payload",
]
