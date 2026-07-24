from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PptxCandidateModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShapeCandidate(PptxCandidateModel):
    source_id: str
    name: str
    kind: Literal["text", "container", "unsupported"]
    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    rotation: float = 0
    text: str | None = None
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
    local_render_enabled: bool = False
    ocr_enabled: bool = False
    external_model_access: bool = False


class PresentationCandidates(PptxCandidateModel):
    source_sha256: str
    slides: list[SlideCandidate] = Field(min_length=1)
    relationship_graph: RelationshipGraphEvidence | None = None
