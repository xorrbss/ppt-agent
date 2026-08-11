import uuid
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from enums.tone import Tone
from enums.verbosity import Verbosity


class GeneratePresentationRequest(BaseModel):
    # Admission-only state. This never crosses the HTTP wire, but lets the
    # in-process background task execute the exact immutable revision that was
    # accepted instead of re-reading mutable current state.
    _template_v2_generation_target: Any = PrivateAttr(default=None)
    _request_id: Optional[str] = PrivateAttr(default=None)
    _authored_exact_snapshot_id: Optional[str] = PrivateAttr(default=None)
    _reference_snapshots: tuple[Any, ...] = PrivateAttr(default=())

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
        default="adaptive", description="Template to use for the presentation"
    )
    strategy: Optional[
        Literal["legacy", "adaptive", "authored", "template_v2"]
    ] = Field(
        default=None,
        description=(
            "Explicit generation strategy. Omit to preserve the existing "
            "template-driven behavior."
        ),
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
