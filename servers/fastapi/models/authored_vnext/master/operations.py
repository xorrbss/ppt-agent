"""Request and response models for the Master Setup vertical slice."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .base import DeckId, Digest, LayoutId, MasterId, SlideId, StrictCamelModel
from .contract import DeckMasterContract, VisualDirection
from .slide import DeckSnapshot, SlideDraft, SlideSpecV2


class CreateDraftRequest(StrictCamelModel):
    contract: DeckMasterContract
    visual_direction: VisualDirection | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    expected_digest: Digest | None = None


class MasterCasRequest(StrictCamelModel):
    expected_revision: int = Field(ge=1)
    expected_digest: Digest


class PreviewRequest(StrictCamelModel):
    master_id: MasterId
    master_revision: int = Field(ge=1)
    master_digest: Digest
    slide: SlideDraft


class CreateDeckRequest(StrictCamelModel):
    master_id: MasterId
    master_revision: int = Field(ge=1)
    master_digest: Digest
    visual_direction: VisualDirection | None = None
    slides: list[SlideDraft] = Field(min_length=1, max_length=500)
    deck_id: DeckId | None = None


class RegenerateBodyRequest(StrictCamelModel):
    scope: Literal["bodyOnly"]
    expected_deck_revision: int = Field(ge=1)
    expected_deck_digest: Digest
    expected_master_digest: Digest
    expected_slide_revision: int = Field(ge=1)
    layout_id: LayoutId
    body: str = Field(min_length=1, max_length=50000)


class StatusResponse(StrictCamelModel):
    enabled: bool
    contract_version: Literal["deckmaster-contract/v1"]
    slide_spec_version: Literal["slidespec/v2"]
    compiler_port: Literal["OoxmlMasterCompilerPort"]


class ValidationIssue(StrictCamelModel):
    code: str
    path: str
    message: str


class ValidationSummary(StrictCamelModel):
    valid: bool
    errors: list[ValidationIssue]
    furniture_body_duplicates: int = 0
    writable_overlaps: int = 0
    title_fit_failures: int = 0


class MasterRevisionResponse(StrictCamelModel):
    master_id: MasterId
    revision: int = Field(ge=1)
    digest: Digest
    status: Literal["draft", "published"]
    contract: DeckMasterContract
    visual_direction: VisualDirection | None = None
    validation: ValidationSummary | None = None


class PngRenderRequest(StrictCamelModel):
    renderer: Literal["authored-vnext-web-preview/1"]
    canonical_digest: Digest
    html_digest: Digest
    viewport_width: int
    viewport_height: int
    device_scale_factor: Literal[1] = 1


class ExportSlidePlan(StrictCamelModel):
    slide_id: SlideId
    layout_id: LayoutId
    spec_digest: Digest


class ExportPlan(StrictCamelModel):
    compiler_port: Literal["OoxmlMasterCompilerPort"]
    compiler_version: Literal["ooxml-master-port/v1"]
    master_id: MasterId
    master_revision: int
    master_digest: Digest
    canonical_digest: Digest
    source_digest: Digest
    expected_package_hash: Digest | None = None
    slides: list[ExportSlidePlan]


class PreviewResponse(StrictCamelModel):
    canonical_digest: Digest
    html: str
    png_render_request: PngRenderRequest
    export_plan: ExportPlan
    slide: SlideSpecV2


class RegenerateResponse(StrictCamelModel):
    candidate_digest: Digest
    validation: ValidationSummary
    deck: DeckSnapshot
