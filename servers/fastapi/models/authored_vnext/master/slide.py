"""Minimal SlideSpec v2 and immutable deck snapshot models."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import DeckId, Digest, LayoutId, MasterId, SlideId, StrictCamelModel
from .contract import Ownership, PowerPointMargins, VisualDirection


class ExplicitTitle(StrictCamelModel):
    text: str = Field(min_length=1, max_length=4000)
    lines: list[str] = Field(min_length=1, max_length=3)
    line_break_policy: Literal["canonical-explicit-uax14-v1"] = (
        "canonical-explicit-uax14-v1"
    )
    uax14_version: Literal["17.0.0"] = "17.0.0"
    tailoring: Literal["ko-space-first-with-id-fallback-v1"] = (
        "ko-space-first-with-id-fallback-v1"
    )
    kinsoku_policy: Literal[
        "cjk-no-leading-closing-no-trailing-opening"
    ] = "cjk-no-leading-closing-no-trailing-opening"

    @model_validator(mode="after")
    def _lines_are_canonical(self) -> "ExplicitTitle":
        if any(not line.strip() for line in self.lines):
            raise ValueError("explicit title lines cannot be empty")
        normalized_text = " ".join(self.text.split())
        normalized_lines = " ".join(" ".join(self.lines).split())
        if normalized_text != normalized_lines:
            raise ValueError("title text must equal its explicit lines")
        return self


class SlideDraft(StrictCamelModel):
    slide_id: SlideId
    layout_id: LayoutId
    title: ExplicitTitle
    body: str = Field(min_length=1, max_length=50000)


class TitleFitResult(StrictCamelModel):
    engine_version: Literal["authored-vnext-title-fit/1"] = (
        "authored-vnext-title-fit/1"
    )
    layout_id: LayoutId
    explicit_lines: list[str] = Field(min_length=1, max_length=3)
    font_family: str = Field(min_length=1)
    font_manifest_digest: Digest
    chosen_font_pt: float = Field(gt=0)
    margins: PowerPointMargins
    clipped: bool
    violations: list[str] = Field(default_factory=list)


class SlideSpecV2(StrictCamelModel):
    schema_version: Literal["slidespec/v2"] = "slidespec/v2"
    slide_id: SlideId
    revision: int = Field(ge=1)
    layout_id: LayoutId
    title: ExplicitTitle
    body: str = Field(min_length=1, max_length=50000)
    ownership: Ownership
    master_digest: Digest
    title_fit: TitleFitResult
    content_digest: Digest
    spec_digest: Digest


class DeckSnapshot(StrictCamelModel):
    schema_version: Literal["deck-document/v1"] = "deck-document/v1"
    deck_id: DeckId
    revision: int = Field(ge=1)
    digest: Digest
    master_id: MasterId
    master_revision: int = Field(ge=1)
    master_digest: Digest
    visual_direction: VisualDirection | None = None
    slides: list[SlideSpecV2] = Field(min_length=1)
