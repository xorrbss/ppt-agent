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

    # Older durable requests may still say ``verbatim``. New source reflow
    # treats extracted text as the authored outline so normal composition can
    # condense it to fit cleanly.
    text: Literal["verbatim", "outline"] = "outline"
    # Kept as booleans so already-queued v1 requests remain readable. The
    # authored reflow contract does not project source tables or charts.
    preserve_all_table_cells: bool = False
    preserve_all_chart_values: bool = False
    preserve_all_image_occurrences: Literal[True] = True
    preserve_source_order: Literal[True] = True
    preserve_source_slide_count: Literal[True] = True
    allow_slide_expansion: Literal[False] = False
    allow_summarization: bool = True
    overflow_policy: Literal["same_page_recompose_or_fail"] = "same_page_recompose_or_fail"


class SourceContentManifestPinInput(StrictCamelModel):
    manifest_id: str = Field(min_length=5, max_length=200)
    digest: Digest


class GeneratePresentationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Admission-only state. This never crosses the HTTP wire, but lets the
    # in-process background task execute the exact immutable revision that was
    # accepted instead of re-reading mutable current state.
    _template_v2_generation_target: Any = PrivateAttr(default=None)
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
    strategy: Optional[
        Literal["authored", "template_v2"]
    ] = Field(
        default=None,
        description=(
            "Explicit generation strategy. Omit to use the authored source."
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
    template_v2_id: Optional[str] = Field(
        default=None,
        description="Structured Template V2 id (required for strategy=template_v2)",
    )
    template_v2_revision: Optional[int] = Field(
        default=None,
        description=(
            "Immutable Template V2 revision (required for strategy=template_v2)"
        ),
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
    def validate_source_content_mode(self) -> "GeneratePresentationRequest":
        if self.source_mode is None:
            if self.source_content_manifest_pin is not None or self.content_policy is not None:
                raise ValueError("source_content_mode_required")
            return self
        if self.source_content_manifest_pin is None:
            raise ValueError("source_content_manifest_pin_required")
        if self.content_policy is None:
            raise ValueError("source_content_policy_required")
        if self.template.strip().lower() != "authored" or self.strategy not in {
            None,
            "authored",
        }:
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
