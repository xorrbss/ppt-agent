"""Wire contracts for deterministic PPTX-to-master analysis."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import Digest, MasterId, StrictCamelModel
from .contract import DeckMasterContract, ImageCrop, Rect
from .operations import MasterRevisionResponse


AiMasterPurpose = Literal["faithful", "cleanup", "rebrand"]
AI_MASTER_FEATURE_ALGORITHM_VERSION = "ai-master-features/v2"
AiCandidateDecisionAction = Literal[
    "pending", "fixed", "variable", "aiZone", "remove"
]


class AiSourceAsset(StrictCamelModel):
    asset_id: str = Field(pattern=r"^asset:[a-z0-9][a-z0-9._-]*$")
    digest: Digest
    media_type: str = Field(min_length=1, max_length=200)
    byte_length: int = Field(ge=1)
    part_name: str = Field(min_length=1, max_length=1000)


class AiSourceRelationship(StrictCamelModel):
    slide_number: int = Field(ge=1)
    relationship_id: str = Field(min_length=1, max_length=200)
    asset_id: str = Field(pattern=r"^asset:[a-z0-9][a-z0-9._-]*$")
    crop: ImageCrop | None = None
    fit: Literal["contain", "cover"]


class AiAnalysisSource(StrictCamelModel):
    filename: str = Field(min_length=1, max_length=500)
    digest: Digest
    slide_count: int = Field(ge=1, le=2000)
    width_px: int = Field(ge=320, le=7680)
    height_px: int = Field(ge=180, le=4320)
    assets: list[AiSourceAsset] = Field(default_factory=list, max_length=5000)
    asset_relationships: list[AiSourceRelationship] = Field(
        default_factory=list, max_length=10000
    )


class AiFontEvidence(StrictCamelModel):
    family: str = Field(min_length=1, max_length=200)
    count: int = Field(ge=1)
    source: Literal["slide", "theme", "fallback"]


class AiColorEvidence(StrictCamelModel):
    hex: str = Field(pattern=r"^#[0-9A-F]{6}$")
    count: int = Field(ge=1)
    source: Literal["slide", "theme", "fallback"]


class AiElementEvidence(StrictCamelModel):
    slide_number: int = Field(ge=1)
    bounds: Rect
    rotation: float = Field(ge=-360, le=360)
    z_index: int = Field(ge=0)
    layout_part: str | None = Field(default=None, max_length=1000)
    master_part: str | None = Field(default=None, max_length=1000)
    theme_part: str | None = Field(default=None, max_length=1000)


class AiCandidateElement(StrictCamelModel):
    id: str = Field(pattern=r"^candidate:[a-z0-9][a-z0-9._-]*$")
    type: Literal["text", "image", "shape", "pageNumber"]
    role: Literal[
        "logo",
        "background",
        "pageNumber",
        "documentCode",
        "customerName",
        "rfpNumber",
        "securityLevel",
        "repeatedText",
        "decoration",
    ]
    text: str | None = Field(default=None, max_length=10000)
    asset_id: str | None = Field(
        default=None, pattern=r"^asset:[a-z0-9][a-z0-9._-]*$"
    )
    digest: Digest | None = None
    media_type: str | None = Field(default=None, max_length=200)
    crop: ImageCrop | None = None
    fit: Literal["contain", "cover"] | None = None
    confidence: float = Field(ge=0, le=1)
    evidence: list[AiElementEvidence] = Field(min_length=1, max_length=2000)
    differences: list[str] = Field(default_factory=list, max_length=100)
    ai_zone_candidate: bool = False


class AiLayoutSlotEvidence(StrictCamelModel):
    id: str = Field(pattern=r"^slot-evidence:[a-z0-9][a-z0-9._-]*$")
    role: Literal["title", "body", "content", "image"]
    bounds: Rect
    placeholder_type: str | None = Field(default=None, max_length=100)
    placeholder_index: int | None = Field(default=None, ge=0, le=65535)
    font_family: str | None = Field(default=None, max_length=200)
    font_size: float | None = Field(default=None, ge=1, le=400)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-F]{6}$")


class AiLayoutCandidate(StrictCamelModel):
    id: str = Field(pattern=r"^layout-candidate:[a-z0-9][a-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["cover", "section", "standard", "closing", "custom"]
    representative_slide: int = Field(ge=1)
    slide_numbers: list[int] = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    differences: list[str] = Field(default_factory=list, max_length=100)
    slots: list[AiLayoutSlotEvidence] = Field(default_factory=list, max_length=64)
    layout_part: str | None = Field(default=None, max_length=1000)
    master_part: str | None = Field(default=None, max_length=1000)
    theme_part: str | None = Field(default=None, max_length=1000)
    excluded: bool = False
    provenance: Literal[
        "analysis", "autoDedupe", "userMerge", "userSplit", "userDecision"
    ] = "analysis"
    source_layout_ids: list[str] = Field(default_factory=list, max_length=2000)
    auto_dedupe_eligible: bool = True


class AiCandidateDecision(StrictCamelModel):
    candidate_id: str = Field(pattern=r"^candidate:[a-z0-9][a-z0-9._-]*$")
    action: AiCandidateDecisionAction = "pending"
    override_text: str | None = Field(default=None, max_length=10000)
    variable_name: str | None = Field(default=None, max_length=200)
    instruction: str | None = Field(default=None, max_length=4000)
    example_text: str | None = Field(default=None, max_length=4000)
    empty_behavior: Literal["hidden", "blank", "fallback"] | None = None
    fallback_text: str | None = Field(default=None, max_length=10000)
    max_chars: int | None = Field(default=None, ge=1, le=10000)

    @model_validator(mode="after")
    def _ai_zone_is_configured(self) -> "AiCandidateDecision":
        if self.action == "aiZone":
            if not self.instruction or self.empty_behavior is None:
                raise ValueError("aiZone decisions require instruction and emptyBehavior")
            if self.empty_behavior == "fallback" and not self.fallback_text:
                raise ValueError("fallback emptyBehavior requires fallbackText")
        return self


class AiLayoutAction(StrictCamelModel):
    action: Literal["merge", "split", "exclude", "include", "reanalyze"]
    layout_ids: list[str] = Field(min_length=1, max_length=64)
    slide_numbers: list[int] = Field(default_factory=list, max_length=2000)
    name: str | None = Field(default=None, max_length=200)


class AiElementFeature(StrictCamelModel):
    """Sanitized element evidence used only by the local deterministic cache.

    Text content is deliberately represented by a digest rather than persisted
    verbatim.  This is sufficient for repeat/furniture matching while keeping
    the canonical source of truth as the PPTX plus semantic manifest.
    """

    kind: Literal["text", "image", "shape"]
    bounds: Rect
    rotation: float = Field(ge=-360, le=360)
    z_order: int = Field(ge=0)
    font_family: str | None = Field(default=None, max_length=200)
    font_size: float | None = Field(default=None, ge=1, le=400)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    fill: str | None = Field(default=None, pattern=r"^#[0-9A-F]{6}$")
    image_digest: Digest | None = None
    crop: ImageCrop | None = None
    fit: Literal["contain", "cover"] | None = None
    placeholder_type: str | None = Field(default=None, max_length=100)
    placeholder_index: int | None = Field(default=None, ge=0, le=65535)
    inferred_role: Literal["title", "body", "content", "image", "furniture"]
    content_signature: Digest | None = None
    furniture: bool = False


class AiSlideFeature(StrictCamelModel):
    slide_number: int = Field(ge=1)
    layout_part: str | None = Field(default=None, max_length=1000)
    master_part: str | None = Field(default=None, max_length=1000)
    theme_part: str | None = Field(default=None, max_length=1000)
    design_family: str = Field(min_length=1, max_length=100)
    semantic_hint: Literal["cover", "section", "body", "closing"]
    clustering_vector: list[float] = Field(min_length=1, max_length=512)
    elements: list[AiElementFeature] = Field(default_factory=list, max_length=10000)


class AiFeatureSidecar(StrictCamelModel):
    algorithm_version: str = Field(min_length=1, max_length=100)
    source_digest: Digest
    slide_count: int = Field(ge=1, le=2000)
    width_px: int = Field(ge=320, le=7680)
    height_px: int = Field(ge=180, le=4320)
    slides: list[AiSlideFeature] = Field(min_length=1, max_length=2000)


class AiAnalysisSummary(StrictCamelModel):
    design_families: int = Field(ge=1)
    layout_candidates: int = Field(ge=1)
    font_count: int = Field(ge=0)
    color_count: int = Field(ge=0)
    repeated_element_count: int = Field(ge=0)
    ai_zone_candidate_count: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)


class AiReapplyIssue(StrictCamelModel):
    code: Literal[
        "textOverflow",
        "imageCrop",
        "overlap",
        "missingRepeatedElement",
        "positionDifference",
        "unusedLayout",
    ]
    severity: Literal["warning", "error"] = "warning"
    slide_number: int | None = Field(default=None, ge=1)
    layout_id: str | None = Field(default=None, max_length=200)
    candidate_id: str | None = Field(default=None, max_length=200)
    message: str = Field(min_length=1, max_length=1000)


class AiReapplyValidation(StrictCamelModel):
    valid: bool
    issues: list[AiReapplyIssue] = Field(default_factory=list, max_length=10000)
    checked_slide_count: int = Field(ge=0)


class AiMasterAnalysis(StrictCamelModel):
    analysis_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    status: Literal["completed", "partial"]
    purpose: AiMasterPurpose
    algorithm_version: str = "unknown"
    feature_sidecar_status: Literal["ready", "reextracted", "missing"] = "missing"
    source: AiAnalysisSource
    summary: AiAnalysisSummary
    fonts: list[AiFontEvidence] = Field(default_factory=list, max_length=1000)
    colors: list[AiColorEvidence] = Field(default_factory=list, max_length=1000)
    repeated_elements: list[AiCandidateElement] = Field(
        default_factory=list, max_length=5000
    )
    layout_candidates: list[AiLayoutCandidate] = Field(min_length=1, max_length=2000)
    decisions: list[AiCandidateDecision] = Field(default_factory=list, max_length=5000)
    layout_actions: list[AiLayoutAction] = Field(default_factory=list, max_length=1000)
    warnings: list[str] = Field(default_factory=list, max_length=1000)
    low_confidence: list[str] = Field(default_factory=list, max_length=1000)
    tolerances: dict[str, float]
    contract_draft: DeckMasterContract
    validation: AiReapplyValidation | None = None
    master_revision: MasterRevisionResponse | None = None


class UpdateAiMasterAnalysisRequest(StrictCamelModel):
    decisions: list[AiCandidateDecision] | None = Field(default=None, max_length=5000)
    layout_actions: list[AiLayoutAction] | None = Field(default=None, max_length=1000)


class SaveAiMasterAnalysisRequest(StrictCamelModel):
    expected_revision: int | None = Field(default=None, ge=1)
    expected_digest: Digest | None = None
    master_id: MasterId | None = None
    contract: DeckMasterContract | None = None

    @model_validator(mode="after")
    def _cas_is_a_pair(self) -> "SaveAiMasterAnalysisRequest":
        if (self.expected_revision is None) != (self.expected_digest is None):
            raise ValueError("expectedRevision and expectedDigest must be supplied together")
        return self
