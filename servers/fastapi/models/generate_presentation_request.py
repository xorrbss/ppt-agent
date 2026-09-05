import uuid
from typing import Any, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
)

from enums.tone import Tone
from enums.verbosity import Verbosity
from models.authored_vnext.master.base import Digest, MasterId, StrictCamelModel


GenerationMode = Literal[
    "auto",
    "registered-template",
    "brand-style",
    "free-design",
    "fidelity-first",
    "editable-first",
]


class AuthoredMasterPinInput(StrictCamelModel):
    """Small public pin accepted by the existing presentation flow.

    Layout/font digests are deliberately server-owned.  They are resolved from
    the immutable published revision after the caller's three-field pin passes
    the published-head check.
    """

    master_id: MasterId
    revision: int = Field(ge=1)
    digest: Digest


class SourceContentPolicy(StrictCamelModel):
    """Page-locked text/image policy for authored source-content reflow."""

    # The caller must choose the source text contract. ``verbatim`` preserves
    # source wording and fails closed when a page cannot fit; ``outline`` is the
    # explicit opt-in that permits concise rephrasing inside the same page.
    text: Literal["verbatim", "outline"] = "verbatim"
    # Kept as booleans so already-queued v1 requests remain readable. The
    # authored reflow contract does not project source tables or charts.
    preserve_all_table_cells: bool = False
    preserve_all_chart_values: bool = False
    preserve_all_image_occurrences: Literal[True] = True
    preserve_source_order: Literal[True] = True
    preserve_source_slide_count: Literal[True] = True
    allow_slide_expansion: Literal[False] = False
    allow_summarization: bool = False
    overflow_policy: Literal["same_page_recompose_or_fail"] = "same_page_recompose_or_fail"


class SourceContentManifestPinInput(StrictCamelModel):
    manifest_id: str = Field(min_length=5, max_length=200)
    digest: Digest


class DesignPackPinInput(StrictCamelModel):
    design_pack_id: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
    )
    version: int = Field(ge=1)
    digest: Digest


class GeneratePresentationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    _request_id: Optional[str] = PrivateAttr(default=None)
    _authored_exact_snapshot_id: Optional[str] = PrivateAttr(default=None)
    _authored_master_pin: Any = PrivateAttr(default=None)
    _authored_master_contract: Any = PrivateAttr(default=None)
    _authored_master_resolution: Any = PrivateAttr(default=None)
    _authored_composition_pin: Any = PrivateAttr(default=None)
    _authored_style_snapshot: Any = PrivateAttr(default=None)
    _authored_deck_metadata: Any = PrivateAttr(default=None)
    _authored_source_semantic_snapshot: Any = PrivateAttr(default=None)
    _authored_design_replay: bool = PrivateAttr(default=False)
    _reference_snapshots: tuple[Any, ...] = PrivateAttr(default=())
    _source_content_manifest: Any = PrivateAttr(default=None)
    _source_document_outline: Any = PrivateAttr(default=None)
    _source_content_authored_reflow: bool = PrivateAttr(default=False)
    # Set at admission when COMPOSER_RESTYLE_ENABLED routes a restyle to the
    # server-owned Composer; the request then keeps its source_mode and skips
    # the authored rewrite.
    _composer_restyle: bool = PrivateAttr(default=False)
    _authored_source_readback_receipt: Any = PrivateAttr(default=None)
    # Resolved once at generation admission and reused by authoring, repair and
    # publication.  It is intentionally private: callers select source policy
    # through ``contentPolicy``; the server owns the effective profile.
    _authored_composition_profile: Any = PrivateAttr(default=None)

    # Chat-first async generation reuses the durable presentation shell and
    # awaiting-approval operation created by /presentation/create.  Legacy
    # generation ignores this field while the feature flag is disabled.
    presentation_id: Optional[uuid.UUID] = Field(default=None)

    content: str = Field(..., description="The content for generating the presentation")
    slides_markdown: Optional[List[str]] = Field(
        default=None, description="The markdown for the slides"
    )
    instructions: Optional[str] = Field(
        default=None, description="The instruction for generating the presentation"
    )
    tone: Tone = Field(default=Tone.DEFAULT, description="The tone to use for the text")
    verbosity: Verbosity = Field(
        default=Verbosity.STANDARD, description="How verbose the presentation should be"
    )
    web_search: bool = Field(default=False, description="Whether to enable web search")
    n_slides: Optional[int] = Field(
        default=None,
        description="Number of slides to generate. If omitted, model auto-detects slide count.",
    )
    language: Optional[str] = Field(
        default=None,
        description="Language for the presentation. If omitted, model auto-detects language.",
    )
    template: str = Field(
        default="authored", description="Presentation source to use for generation"
    )
    quality_profile: Literal["development", "release"] = Field(
        default="development",
        alias="qualityProfile",
        description=(
            "Requested Authored quality policy. Release remains subject to the "
            "server-owned pre-export and export evidence gates."
        ),
    )
    strategy: Optional[Literal["authored"]] = Field(
        default=None,
        description=(
            "Explicit generation strategy. Omit to use the authored source."
        ),
    )
    generation_mode: GenerationMode = Field(
        default="auto",
        alias="generationMode",
        description="Public product routing intent; the server owns route resolution.",
    )
    design_pack_pin: Optional[DesignPackPinInput] = Field(
        default=None,
        alias="designPackPin",
    )
    generation_engine: Optional[Literal["authored", "deck-ir"]] = Field(
        default="authored",
        alias="generationEngine",
        description=(
            "Compatibility-only internal engine input. Product routing is owned "
            "by generationMode and its durable route receipt."
        ),
    )
    source_mode: Optional[Literal["source_content_restyle"]] = Field(
        default=None,
        alias="sourceMode",
        description=(
            "Preserve an admitted immutable source manifest while applying the "
            "selected authored style and optional company standard."
        ),
    )
    source_content_manifest_pin: Optional[SourceContentManifestPinInput] = Field(
        default=None,
        alias="sourceContentManifestPin",
    )
    content_policy: Optional[SourceContentPolicy] = Field(
        default=None,
        alias="contentPolicy",
    )
    include_table_of_contents: bool = Field(
        default=False, description="Whether to include a table of contents"
    )
    include_title_slide: bool = Field(
        default=True, description="Whether to include a title slide"
    )
    files: Optional[List[str]] = Field(
        default=None,
        json_schema_extra={"deprecated": True},
        description=(
            "Admission-only compatibility input. It is immediately copied into "
            "canonical assets and replaced by an immutable reference snapshot; "
            "it is never persisted in a generation job. New callers must upload "
            "through the asset gateway and pass reference_snapshot_ids."
        ),
    )
    reference_snapshot_ids: List[uuid.UUID] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "Server-created immutable reference snapshots admitted for generation."
        ),
    )

    @field_validator("reference_snapshot_ids")
    @classmethod
    def validate_unique_reference_snapshot_ids(
        cls, value: List[uuid.UUID]
    ) -> List[uuid.UUID]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate_reference_snapshot")
        return value
    export_as: Literal["pptx", "pdf"] = Field(
        default="pptx", description="Export format"
    )
    trigger_webhook: bool = Field(
        default=False, description="Whether to trigger subscribed webhooks"
    )
    vision_qa: bool = Field(
        default=False,
        description=(
            "Opt-in high-quality pass: after composing/authoring, render and "
            "vision-critique each slide and re-do any flagged as broken. Slower "
            "and token-heavier; off by default."
        ),
    )
    # Authored-mode brand tokens (ignored by the template path). When omitted, authored
    # mode uses a brand-blue primary + language-aware fonts and no wordmark.
    primary_color: Optional[str] = Field(
        default=None, description="Authored mode: brand primary colour (hex, e.g. #2563EB)"
    )
    fonts: Optional[str] = Field(
        default=None, description="Authored mode: brand font family (e.g. 'Noto Sans KR')"
    )
    wordmark: Optional[str] = Field(
        default=None, description="Authored mode: small footer wordmark text"
    )
    authored_style: Optional[str] = Field(
        default=None, description="Authored mode: authored style preset id"
    )
    master_pin: Optional[AuthoredMasterPinInput] = Field(
        default=None,
        alias="masterPin",
        description=(
            "Authored mode only: immutable published master identity. The server "
            "resolves layout/font manifest digests after validating the published head."
        ),
    )

    @model_validator(mode="after")
    def freeze_fixed_outline_slide_count(self) -> "GeneratePresentationRequest":
        # A supplied outline already fixes the deck cardinality. Normalize it
        # before queue admission and request fingerprinting; the generation
        # worker historically did this later, after capacity planning.
        if self.slides_markdown:
            self.n_slides = len(self.slides_markdown)
        return self

    @model_validator(mode="after")
    def validate_generation_mode(self) -> "GeneratePresentationRequest":
        if self.generation_mode == "editable-first":
            raise ValueError("capability_not_supported: editable-first")
        if (
            self.generation_mode == "registered-template"
            and self.design_pack_pin is None
        ):
            raise ValueError("design_pack_pin_required")
        if (
            self.design_pack_pin is not None
            and self.generation_mode != "registered-template"
        ):
            raise ValueError("design_pack_pin_requires_registered_template")
        return self

    @model_validator(mode="after")
    def validate_source_content_mode(self) -> "GeneratePresentationRequest":
        if self.source_mode is None:
            if self.source_content_manifest_pin is not None or self.content_policy is not None:
                raise ValueError("source_content_mode_required")
            return self
        if self.source_content_manifest_pin is None:
            raise ValueError("source_content_manifest_pin_required")
        if self.content_policy is None:
            raise ValueError("source_content_policy_required")
        if self.template.strip().lower() != "authored":
            raise ValueError("generation_strategy_conflict")
        if not (self.authored_style or "").strip():
            raise ValueError("source_content_template_required")
        if self.export_as != "pptx":
            raise ValueError("source_content_format_not_supported")
        if self.web_search or self.vision_qa:
            raise ValueError("source_content_generation_option_conflict")
        if self.slides_markdown is not None:
            raise ValueError("source_content_generation_option_conflict")
        return self
