from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Protocol, TypeAlias, runtime_checkable

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictBool,
    StrictInt,
    StrictStr,
    model_validator,
)


NonEmptyString = Annotated[StrictStr, Field(min_length=1)]
Sha256Digest = Annotated[StrictStr, Field(pattern=r"^[0-9a-f]{64}$")]
NonNegativeFinite = Annotated[FiniteFloat, Field(ge=0)]
PositiveFinite = Annotated[FiniteFloat, Field(gt=0)]
Confidence = Annotated[FiniteFloat, Field(ge=0, le=1)]
CandidateAnalyzerInput: TypeAlias = BaseModel | Mapping[str, Any]


class AnalyzerContractModel(BaseModel):
    """Closed, strict JSON boundary shared by analyzer implementations."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ValidatedChartSeriesCandidate(AnalyzerContractModel):
    name: StrictStr
    values: list[FiniteFloat]


class ValidatedTextRunCandidate(AnalyzerContractModel):
    text: StrictStr
    font_size: PositiveFinite | None = None
    font_family: StrictStr | None = None
    font_color: StrictStr | None = None
    bold: StrictBool | None = None
    italic: StrictBool | None = None
    underline: StrictBool | None = None


class ValidatedSmartArtNodeEvidence(AnalyzerContractModel):
    model_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    node_type: Annotated[StrictStr, Field(max_length=80)] | None = None
    text: Annotated[StrictStr, Field(max_length=2_000)] | None = None


class ValidatedSmartArtConnectionEvidence(AnalyzerContractModel):
    model_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    source_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    destination_id: Annotated[StrictStr, Field(min_length=1, max_length=256)]
    connection_type: Annotated[StrictStr, Field(max_length=80)] | None = None


class ValidatedSmartArtEvidence(AnalyzerContractModel):
    evidence_version: Literal[1] = 1
    status: Literal["structured", "unavailable"]
    diagnostic: Literal[
        "none",
        "data_relationship_missing",
        "data_relationship_invalid",
        "data_part_missing",
        "data_model_invalid",
        "data_model_limits_exceeded",
    ]
    data_part: Annotated[StrictStr, Field(max_length=512)] | None = None
    nodes: list[ValidatedSmartArtNodeEvidence] = Field(default_factory=list)
    connections: list[ValidatedSmartArtConnectionEvidence] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_status_payload(self) -> ValidatedSmartArtEvidence:
        if self.status == "structured":
            if self.diagnostic != "none" or self.data_part is None:
                raise ValueError("invalid_structured_smartart_evidence")
            node_ids = [node.model_id for node in self.nodes]
            connection_ids = [
                connection.model_id for connection in self.connections
            ]
            if len(node_ids) != len(set(node_ids)) or len(connection_ids) != len(
                set(connection_ids)
            ):
                raise ValueError("duplicate_smartart_evidence_id")
            known_nodes = set(node_ids)
            if any(
                connection.source_id not in known_nodes
                or connection.destination_id not in known_nodes
                for connection in self.connections
            ):
                raise ValueError("unknown_smartart_connection_endpoint")
        elif self.diagnostic == "none" or self.nodes or self.connections:
            raise ValueError("invalid_unavailable_smartart_evidence")
        return self


class ValidatedShapeCandidate(AnalyzerContractModel):
    source_id: NonEmptyString
    name: NonEmptyString
    kind: Literal["text", "container", "table", "chart", "group", "unsupported"]
    x: NonNegativeFinite = 0
    y: NonNegativeFinite = 0
    width: NonNegativeFinite = 0
    height: NonNegativeFinite = 0
    rotation: FiniteFloat = 0
    text: StrictStr | None = None
    text_runs: list[ValidatedTextRunCandidate] | None = None
    table_rows: list[list[StrictStr]] | None = None
    chart_type: StrictStr | None = None
    chart_categories: list[StrictStr] | None = None
    chart_series: list[ValidatedChartSeriesCandidate] | None = None
    smartart_evidence: ValidatedSmartArtEvidence | None = None
    children: list["ValidatedShapeCandidate"] | None = None
    fill_color: StrictStr | None = None
    confidence: Confidence
    unsupported_reason: StrictStr | None = None

    @model_validator(mode="after")
    def validate_kind_specific_fields(self) -> ValidatedShapeCandidate:
        if self.kind == "text" and self.text is None:
            raise ValueError("text_candidate_requires_text")
        if self.kind != "text" and self.text is not None:
            raise ValueError("non_text_candidate_cannot_contain_text")
        if self.kind == "text":
            if self.text_runs is not None:
                if not self.text_runs:
                    raise ValueError("text_candidate_runs_cannot_be_empty")
                if "".join(run.text for run in self.text_runs) != self.text:
                    raise ValueError("text_candidate_run_content_mismatch")
        elif self.text_runs is not None:
            raise ValueError("non_text_candidate_cannot_contain_text_runs")
        if self.kind == "table":
            if not self.table_rows or not self.table_rows[0]:
                raise ValueError("table_candidate_requires_cells")
            column_count = len(self.table_rows[0])
            if any(len(row) != column_count for row in self.table_rows):
                raise ValueError("table_candidate_rows_must_be_rectangular")
        elif self.table_rows is not None:
            raise ValueError("non_table_candidate_cannot_contain_table_rows")
        if self.kind == "chart":
            if not self.chart_type or self.chart_categories is None:
                raise ValueError("chart_candidate_requires_type_and_categories")
            if self.chart_series is None:
                raise ValueError("chart_candidate_requires_series")
            if any(
                len(series.values) != len(self.chart_categories)
                for series in self.chart_series
            ):
                raise ValueError("chart_candidate_series_length_mismatch")
        elif any(
            value is not None
            for value in (
                self.chart_type,
                self.chart_categories,
                self.chart_series,
            )
        ):
            raise ValueError("non_chart_candidate_cannot_contain_chart_data")
        if self.kind == "group":
            if not self.children:
                raise ValueError("group_candidate_requires_children")
        elif self.children is not None:
            raise ValueError("non_group_candidate_cannot_contain_children")
        if self.kind == "unsupported" and not self.unsupported_reason:
            raise ValueError("unsupported_candidate_requires_reason")
        if self.kind != "unsupported" and self.unsupported_reason is not None:
            raise ValueError("supported_candidate_cannot_have_unsupported_reason")
        if self.smartart_evidence is not None and (
            self.kind != "unsupported"
            or self.unsupported_reason != "unsupported_ooxml:smartArt"
        ):
            raise ValueError("smartart_evidence_requires_smartart_fallback")
        return self


class ValidatedSlideCandidate(AnalyzerContractModel):
    source_part: NonEmptyString
    relationship_id: NonEmptyString
    width: PositiveFinite
    height: PositiveFinite
    shapes: list[ValidatedShapeCandidate]
    external_relationships: list[NonEmptyString] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_shape_identity_and_bounds(self) -> ValidatedSlideCandidate:
        source_ids = [shape.source_id for shape in self.shapes]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("duplicate_shape_source_id")
        if len(self.external_relationships) != len(
            set(self.external_relationships)
        ):
            raise ValueError("duplicate_external_relationship_id")
        for shape in self.shapes:
            right = shape.x + shape.width
            bottom = shape.y + shape.height
            if not math.isfinite(right) or not math.isfinite(bottom):
                raise ValueError("shape_geometry_overflow")
            if right > self.width and not math.isclose(
                right,
                self.width,
                rel_tol=0,
                abs_tol=1e-6,
            ):
                raise ValueError(
                    f"shape_out_of_canvas_bounds:{shape.source_id}:x"
                )
            if bottom > self.height and not math.isclose(
                bottom,
                self.height,
                rel_tol=0,
                abs_tol=1e-6,
            ):
                raise ValueError(
                    f"shape_out_of_canvas_bounds:{shape.source_id}:y"
                )
        return self


class ValidatedRelationshipEvidence(AnalyzerContractModel):
    source_part: NonEmptyString
    relationship_id: NonEmptyString
    relationship_kind: NonEmptyString
    target_part: StrictStr | None = None
    external: StrictBool = False
    cycle: StrictBool = False
    missing: StrictBool = False


class ValidatedRelationshipGraphEvidence(AnalyzerContractModel):
    evidence_version: Literal[0] = 0
    root_part: NonEmptyString = "ppt/presentation.xml"
    nodes: list[NonEmptyString] = Field(default_factory=list)
    relationships: list[ValidatedRelationshipEvidence] = Field(
        default_factory=list
    )
    missing_parts: list[NonEmptyString] = Field(default_factory=list)
    cycle_count: Annotated[StrictInt, Field(ge=0)] = 0
    skipped_relationship_count: Annotated[StrictInt, Field(ge=0)] = 0
    blocked_relationship_kind_counts: dict[
        NonEmptyString, Annotated[StrictInt, Field(ge=1)]
    ] = Field(default_factory=dict)
    local_render_enabled: StrictBool = False
    ocr_enabled: StrictBool = False
    external_model_access: StrictBool = False


class ValidatedThemeEvidence(AnalyzerContractModel):
    part: NonEmptyString
    name: StrictStr | None = None
    major_font: StrictStr | None = None
    minor_font: StrictStr | None = None
    colors: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)


class ValidatedMasterEvidence(AnalyzerContractModel):
    part: NonEmptyString
    theme_part: StrictStr | None = None
    placeholder_types: list[NonEmptyString] = Field(default_factory=list)


class ValidatedLayoutEvidence(AnalyzerContractModel):
    part: NonEmptyString
    name: StrictStr | None = None
    master_part: StrictStr | None = None
    theme_part: StrictStr | None = None
    placeholder_types: list[NonEmptyString] = Field(default_factory=list)


class ValidatedSlideStyleBinding(AnalyzerContractModel):
    slide_part: NonEmptyString
    layout_part: StrictStr | None = None
    master_part: StrictStr | None = None
    theme_part: StrictStr | None = None


class ValidatedStyleGraphEvidence(AnalyzerContractModel):
    evidence_version: Literal[1] = 1
    themes: list[ValidatedThemeEvidence] = Field(default_factory=list)
    masters: list[ValidatedMasterEvidence] = Field(default_factory=list)
    layouts: list[ValidatedLayoutEvidence] = Field(default_factory=list)
    slide_bindings: list[ValidatedSlideStyleBinding] = Field(
        default_factory=list
    )


class ValidatedPresentationCandidates(AnalyzerContractModel):
    source_sha256: Sha256Digest
    slides: list[ValidatedSlideCandidate] = Field(min_length=1)
    relationship_graph: ValidatedRelationshipGraphEvidence | None = None
    style_graph: ValidatedStyleGraphEvidence | None = None

    @model_validator(mode="after")
    def validate_slide_identity(self) -> ValidatedPresentationCandidates:
        source_parts = [slide.source_part for slide in self.slides]
        relationship_ids = [slide.relationship_id for slide in self.slides]
        if len(source_parts) != len(set(source_parts)):
            raise ValueError("duplicate_slide_source_part")
        if len(relationship_ids) != len(set(relationship_ids)):
            raise ValueError("duplicate_slide_relationship_id")
        return self


def candidate_payload_sha256(
    candidates: ValidatedPresentationCandidates,
) -> str:
    payload_value = candidates.model_dump(mode="json")

    def remove_empty_additive_fields(shape: dict[str, Any]) -> None:
        if shape.get("text_runs") is None:
            shape.pop("text_runs", None)
        if shape.get("smartart_evidence") is None:
            shape.pop("smartart_evidence", None)
        for child in shape.get("children") or []:
            remove_empty_additive_fields(child)

    for slide in payload_value["slides"]:
        for shape in slide["shapes"]:
            remove_empty_additive_fields(shape)
    relationship_graph = payload_value.get("relationship_graph")
    if (
        relationship_graph is not None
        and not relationship_graph.get("blocked_relationship_kind_counts")
    ):
        relationship_graph.pop("blocked_relationship_kind_counts", None)
    payload = json.dumps(
        payload_value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class AnalyzerProviderMetadata(AnalyzerContractModel):
    """Provider-neutral execution facts; the local implementation fills these."""

    id: NonEmptyString
    version: NonEmptyString
    capability: NonEmptyString
    execution: Literal["local"]
    status: Literal["available", "unavailable"]
    network_access: Literal[False]
    external_ai: Literal[False]


class ArtifactMetadata(AnalyzerContractModel):
    role: Literal["source", "preview", "render", "candidate"]
    status: Literal["available", "not_provided", "not_run", "unavailable"]
    media_type: StrictStr | None = None
    sha256: Sha256Digest | None = None
    reason: StrictStr | None = None

    @model_validator(mode="after")
    def validate_status_fields(self) -> ArtifactMetadata:
        if self.status == "available":
            if self.media_type is None or self.sha256 is None:
                raise ValueError("available_artifact_requires_media_type_and_sha256")
            if self.reason is not None:
                raise ValueError("available_artifact_cannot_have_reason")
        else:
            if self.sha256 is not None:
                raise ValueError("unavailable_artifact_cannot_have_sha256")
            if not self.reason:
                raise ValueError("unavailable_artifact_requires_reason")
        return self


class CanvasSlideMetadata(AnalyzerContractModel):
    slide_index: Annotated[StrictInt, Field(ge=1)]
    source_part: NonEmptyString
    width: PositiveFinite
    height: PositiveFinite


class CanvasMetadata(AnalyzerContractModel):
    coordinate_space: Literal["template-v2"]
    origin: Literal["top-left"]
    units: Literal["px"]
    slides: list[CanvasSlideMetadata] = Field(min_length=1)


class CandidateAnalysisSummary(AnalyzerContractModel):
    slide_count: Annotated[StrictInt, Field(ge=1)]
    shape_count: Annotated[StrictInt, Field(ge=0)]
    supported_shape_count: Annotated[StrictInt, Field(ge=0)]
    unsupported_shape_count: Annotated[StrictInt, Field(ge=0)]
    visual_fidelity_status: Literal["not_evaluated", "passed", "failed"]
    review_required: StrictBool


class VisualFidelityMetrics(AnalyzerContractModel):
    mean_absolute_error: NonNegativeFinite
    bad_pixel_ratio: Annotated[FiniteFloat, Field(ge=0, le=1)]
    largest_bad_component: Annotated[StrictInt, Field(ge=0)]


class VisualFidelityThresholds(AnalyzerContractModel):
    mean_absolute_error: NonNegativeFinite
    bad_pixel_ratio: Annotated[FiniteFloat, Field(ge=0, le=1)]
    largest_bad_component: Annotated[StrictInt, Field(ge=0)]


class VisualFidelityEvaluation(AnalyzerContractModel):
    method: Literal["pixel-diff-v1"]
    status: Literal["passed", "failed"]
    metrics: VisualFidelityMetrics
    thresholds: VisualFidelityThresholds

    @model_validator(mode="after")
    def validate_status_matches_thresholds(self) -> VisualFidelityEvaluation:
        passed = (
            self.metrics.mean_absolute_error
            <= self.thresholds.mean_absolute_error
            and self.metrics.bad_pixel_ratio
            <= self.thresholds.bad_pixel_ratio
            and self.metrics.largest_bad_component
            <= self.thresholds.largest_bad_component
        )
        if self.status != ("passed" if passed else "failed"):
            raise ValueError("visual_fidelity_status_mismatch")
        return self


class CandidateAnalysis(AnalyzerContractModel):
    contract_version: Literal[1]
    provider: AnalyzerProviderMetadata
    status: Literal["completed"]
    source: ArtifactMetadata
    preview: ArtifactMetadata
    render: ArtifactMetadata
    candidate: ArtifactMetadata
    canvas: CanvasMetadata
    candidates: ValidatedPresentationCandidates
    visual_fidelity: VisualFidelityEvaluation | None = None
    summary: CandidateAnalysisSummary

    @model_validator(mode="after")
    def validate_cross_field_contract(self) -> CandidateAnalysis:
        expected_roles = {
            "source": self.source,
            "preview": self.preview,
            "render": self.render,
            "candidate": self.candidate,
        }
        for role, artifact in expected_roles.items():
            if artifact.role != role:
                raise ValueError(f"artifact_role_mismatch:{role}")
        if self.provider.status != "available":
            raise ValueError("completed_analysis_requires_available_provider")
        if self.source.status != "available":
            raise ValueError("completed_analysis_requires_available_source")
        if self.source.sha256 != self.candidates.source_sha256:
            raise ValueError("source_digest_mismatch")
        if self.candidate.status != "available":
            raise ValueError("completed_analysis_requires_available_candidates")
        if self.candidate.sha256 != candidate_payload_sha256(self.candidates):
            raise ValueError("candidate_digest_mismatch")
        if self.visual_fidelity is None:
            if self.summary.visual_fidelity_status != "not_evaluated":
                raise ValueError("missing_visual_fidelity_evaluation")
        else:
            if self.preview.status != "available" or self.render.status != "available":
                raise ValueError(
                    "visual_fidelity_requires_preview_and_render_artifacts"
                )
            if self.summary.visual_fidelity_status != self.visual_fidelity.status:
                raise ValueError("summary_visual_fidelity_status_mismatch")

        expected_canvas = [
            CanvasSlideMetadata(
                slide_index=index,
                source_part=slide.source_part,
                width=slide.width,
                height=slide.height,
            )
            for index, slide in enumerate(self.candidates.slides, start=1)
        ]
        if self.canvas.slides != expected_canvas:
            raise ValueError("canvas_metadata_mismatch")

        shape_count = sum(len(slide.shapes) for slide in self.candidates.slides)
        unsupported_count = sum(
            shape.kind == "unsupported"
            for slide in self.candidates.slides
            for shape in slide.shapes
        )
        if self.summary.slide_count != len(self.candidates.slides):
            raise ValueError("summary_slide_count_mismatch")
        if self.summary.shape_count != shape_count:
            raise ValueError("summary_shape_count_mismatch")
        if self.summary.unsupported_shape_count != unsupported_count:
            raise ValueError("summary_unsupported_shape_count_mismatch")
        if self.summary.supported_shape_count != shape_count - unsupported_count:
            raise ValueError("summary_supported_shape_count_mismatch")
        return self


@runtime_checkable
class CandidateAnalyzer(Protocol):
    """Provider-neutral executable boundary for candidate analyzers."""

    @property
    def provider(self) -> AnalyzerProviderMetadata: ...

    def analyze(
        self,
        candidates: CandidateAnalyzerInput,
        /,
    ) -> CandidateAnalysis: ...
