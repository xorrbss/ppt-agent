from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PptxCandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChartSeriesCandidate(PptxCandidateModel):
    name: str
    values: list[float]


class TextRunCandidate(PptxCandidateModel):
    """Explicit DrawingML run properties that can be edited without inheritance."""

    text: str
    font_size: float | None = Field(default=None, gt=0)
    font_family: str | None = None
    font_color: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None


class SmartArtNodeEvidence(PptxCandidateModel):
    """Bounded text-bearing node evidence from an inert SmartArt data model."""

    model_id: str = Field(min_length=1, max_length=256)
    node_type: str | None = Field(default=None, max_length=80)
    text: str | None = Field(default=None, max_length=2_000)


class SmartArtConnectionEvidence(PptxCandidateModel):
    """A bounded SmartArt edge; it is evidence, not an editable shape contract."""

    model_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=256)
    destination_id: str = Field(min_length=1, max_length=256)
    connection_type: str | None = Field(default=None, max_length=80)


class SmartArtEvidence(PptxCandidateModel):
    """Local-only SmartArt evidence retained behind the manual-review fallback."""

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
    data_part: str | None = Field(default=None, max_length=512)
    nodes: list[SmartArtNodeEvidence] = Field(default_factory=list)
    connections: list[SmartArtConnectionEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status_payload(self) -> SmartArtEvidence:
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


class ShapeCandidate(PptxCandidateModel):
    source_id: str
    name: str
    kind: Literal["text", "container", "table", "chart", "group", "unsupported"]
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    rotation: float = 0
    text: str | None = None
    text_runs: list[TextRunCandidate] | None = None
    table_rows: list[list[str]] | None = None
    chart_type: str | None = None
    chart_categories: list[str] | None = None
    chart_series: list[ChartSeriesCandidate] | None = None
    smartart_evidence: SmartArtEvidence | None = None
    children: list["ShapeCandidate"] | None = None
    fill_color: str | None = None
    confidence: float = Field(ge=0, le=1)
    unsupported_reason: str | None = None


class SlideCandidate(PptxCandidateModel):
    source_part: str
    relationship_id: str
    width: float
    height: float
    shapes: list[ShapeCandidate]
    external_relationships: list[str] = Field(default_factory=list)


class RelationshipEvidence(PptxCandidateModel):
    source_part: str
    relationship_id: str
    relationship_kind: str
    target_part: str | None = None
    external: bool = False
    cycle: bool = False
    missing: bool = False


class RelationshipGraphEvidence(PptxCandidateModel):
    evidence_version: Literal[0] = 0
    root_part: str = "ppt/presentation.xml"
    nodes: list[str] = Field(default_factory=list)
    relationships: list[RelationshipEvidence] = Field(default_factory=list)
    missing_parts: list[str] = Field(default_factory=list)
    cycle_count: int = Field(default=0, ge=0)
    skipped_relationship_count: int = Field(default=0, ge=0)
    blocked_relationship_kind_counts: dict[str, int] = Field(default_factory=dict)
    local_render_enabled: bool = False
    ocr_enabled: bool = False
    external_model_access: bool = False


class ThemeEvidence(PptxCandidateModel):
    part: str
    name: str | None = None
    major_font: str | None = None
    minor_font: str | None = None
    colors: dict[str, str] = Field(default_factory=dict)


class MasterEvidence(PptxCandidateModel):
    part: str
    theme_part: str | None = None
    placeholder_types: list[str] = Field(default_factory=list)


class LayoutEvidence(PptxCandidateModel):
    part: str
    name: str | None = None
    master_part: str | None = None
    theme_part: str | None = None
    placeholder_types: list[str] = Field(default_factory=list)


class SlideStyleBinding(PptxCandidateModel):
    slide_part: str
    layout_part: str | None = None
    master_part: str | None = None
    theme_part: str | None = None


class StyleGraphEvidence(PptxCandidateModel):
    evidence_version: Literal[1] = 1
    themes: list[ThemeEvidence] = Field(default_factory=list)
    masters: list[MasterEvidence] = Field(default_factory=list)
    layouts: list[LayoutEvidence] = Field(default_factory=list)
    slide_bindings: list[SlideStyleBinding] = Field(default_factory=list)


class PresentationCandidates(PptxCandidateModel):
    source_sha256: str
    slides: list[SlideCandidate] = Field(min_length=1)
    relationship_graph: RelationshipGraphEvidence | None = None
    style_graph: StyleGraphEvidence | None = None


ShapeCandidate.model_rebuild()
