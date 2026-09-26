import uuid
from typing import Any, List, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    field_validator,
    model_validator,
    model_serializer,
)
from pydantic.alias_generators import to_camel

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

CodexGenerationPreset = Literal[
    "luna-medium",
    "luna-high",
    "sol-low",
    "sol-medium",
    "sol-high",
    "sol6-low",
    "sol6-medium",
    "sol6-high",
    "astra-low",
    "astra-medium",
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


class RasterApprovalPinInput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, alias_generator=to_camel)
    contract_version: Literal["raster-reconstruction-approval/v1"] = "raster-reconstruction-approval/v1"
    operation_id: uuid.UUID
    analysis_id: uuid.UUID
    analysis_revision: int = Field(ge=1)
    analysis_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_region_ids: tuple[str, ...]
    full_page_preserve_pages: tuple[int, ...] = ()
    automatic_preserve_pages: tuple[int, ...] = ()
    automatic_redesign_pages: tuple[int, ...] = ()
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    _source_content_svg_reflow: bool = PrivateAttr(default=False)
    _svg_source_binding: Any = PrivateAttr(default=None)
    _raster_approval_pin: Any = PrivateAttr(default=None)
    _raster_analysis: Any = PrivateAttr(default=None)
    _raster_reflow: bool = PrivateAttr(default=False)
    _raster_reconstruction: bool = PrivateAttr(default=False)
    _raster_tenant_id: Optional[str] = PrivateAttr(default=None)
    _authored_source_readback_receipt: Any = PrivateAttr(default=None)
    _authored_local_fit_policy_revision: Optional[str] = PrivateAttr(default=None)
    # Authored rollout switches are pinned at staging and restored on retries;
    # services.authored_rollout_profile owns their values.
    _authored_rollout_profile: Any = PrivateAttr(default=None)
    # Resolved once at generation admission and reused by authoring, repair and
    # publication.  It is intentionally private: callers select source policy
    # through ``contentPolicy``; the server owns the effective profile.
    _authored_composition_profile: Any = PrivateAttr(default=None)
    _authored_canvas: Any = PrivateAttr(default=None)
    _document_canvas: Any = PrivateAttr(default=None)
    _svg_style_snapshot: Any = PrivateAttr(default=None)
    _generation_strategy_reason: Optional[str] = PrivateAttr(default=None)
    # Chat-first async generation reuses the durable presentation shell and
    # awaiting-approval operation created by /presentation/create.  Legacy
    # generation ignores this field while the feature flag is disabled.
    presentation_id: Optional[uuid.UUID] = Field(default=None)

    content: str = Field(..., description="The content for generating the presentation")
    codex_generation_preset: Optional[CodexGenerationPreset] = Field(
        default=None,
        description="Request-scoped Codex model and reasoning preset.",
    )
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
    aspect_ratio: Literal["16:9", "9:16", "9:13"] = Field(
        default="16:9",
        description="Authored presentation canvas aspect ratio.",
    )
    page_size: Optional[Literal["A4"]] = None
    orientation: Optional[Literal["landscape", "portrait"]] = None

    @model_validator(mode="before")
    @classmethod
    def validate_canvas_selectors(cls, value: Any) -> Any:
        if isinstance(value, dict):
            present = {key for key in ("page_size", "orientation") if key in value}
            if present:
                if present != {"page_size", "orientation"}:
                    raise ValueError("document_canvas_selectors_incomplete")
                if "aspect_ratio" in value:
                    raise ValueError("document_canvas_selectors_conflict")
                if value["page_size"] is None or value["orientation"] is None:
                    raise ValueError("document_canvas_selectors_null")
                if value["page_size"] != "A4":
                    raise ValueError("document_canvas_page_size_invalid")
                if value["orientation"] not in ("landscape", "portrait"):
                    raise ValueError("document_canvas_orientation_invalid")
        return value

    @model_serializer(mode="wrap")
    def serialize_canvas_selectors(self, handler):
        payload = handler(self)
        if self.page_size is not None:
            payload.pop("aspect_ratio", None)
        else:
            payload.pop("page_size", None)
            payload.pop("orientation", None)
        return payload
    quality_profile: Literal["development", "release"] = Field(
        default="development",
        alias="qualityProfile",
        description=(
            "Requested Authored quality policy. Release remains subject to the "
            "server-owned pre-export and export evidence gates."
        ),
    )
    strategy: Optional[Literal["authored", "svg"]] = Field(
        default=None,
        description=(
            "Explicit generation strategy. Omit to use the server default (svg), "
            "except for registered-template/design-pack requests and combinations "
            "svg admission does not support, which stay on authored -- see "
            "services.generation_core.admission.resolve_generation_strategy."
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
            "by generationMode and its durable route receipt. 'deck-ir' is "
            "retired as an explicit client selection -- see "
            "reject_retired_deck_ir_engine_selection -- the server's internal "
            "auto-routing to that engine is unaffected."
        ),
    )
    source_mode: Optional[Literal["source_content_restyle", "raster_reconstruction"]] = Field(
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
    raster_approval_pin: Optional[RasterApprovalPinInput] = Field(
        default=None,
        alias="rasterApprovalPin",
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
            if (self.source_content_manifest_pin is not None or self.content_policy is not None
                    or self.raster_approval_pin is not None):
                raise ValueError("source_content_mode_required")
            return self
        if self.source_mode == "raster_reconstruction":
            if self.raster_approval_pin is None:
                raise ValueError("raster_approval_pin_required")
            if self.source_content_manifest_pin is not None:
                raise ValueError("raster_native_manifest_not_expected")
            if len(self.reference_snapshot_ids) != 1:
                raise ValueError("raster_single_source_snapshot_required")
            from services.generation_core.admission.strategy import (
                resolve_generation_strategy,
            )

            if (
                resolve_generation_strategy(self) != "svg"
                or self.page_size != "A4"
                or self.orientation is None
            ):
                raise ValueError("raster_a4_svg_required")
            if self.template.strip().lower() != "authored" or self.authored_style != "screenshot-blue":
                raise ValueError("source_content_template_required")
            # Both formats are supported: the isolated export worker routes any
            # svg-mode presentation (raster-reconstructed decks included)
            # through execute_svg_workspace_export for export.pptx/export.pdf
            # alike. Only the source-content *reflow* pathway's byte-for-byte
            # sourcePreservation receipts are pptx-only (svg_export_operation.py
            # raises svg_source_export_format_unsupported for those), and raster
            # reconstruction never sets that marker.
            if (
                self.export_as not in ("pptx", "pdf")
                or self.web_search or self.vision_qa or self.slides_markdown is not None
            ):
                raise ValueError("source_content_generation_option_conflict")
            self.include_title_slide = False
            self.include_table_of_contents = False
            return self
        if self.raster_approval_pin is not None:
            raise ValueError("raster_source_mode_required")
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
