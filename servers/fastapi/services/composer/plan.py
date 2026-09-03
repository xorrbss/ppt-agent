"""Typed plan, fit parameters and receipts shared by every Composer stage.

Everything the renderer needs to draw a slide is in ``ComposerSlidePlan``;
everything the ladder decided is in ``FitParameters``; everything the receipt
records is in ``FitReceipt``. No stage passes HTML or measurements to another
stage except through the types here.

The plan is a Deck IR slide subset. Component kinds are named like
``services.authored_vnext.deck_ir.contracts`` component kinds so phase 2 can
map a plan onto ``DeckIRV1`` without renaming; bindings reuse the block and
run identity of ``services.source_content.source_runs``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: The authored frame. Every slide is rendered against this exact box.
CANVAS_WIDTH = 1280
CANVAS_HEIGHT = 720

#: 1280 px is 13.333 in at 96 dpi, so one point is this many pixels.
PX_PER_PT = 96.0 / 72.0

#: Design floors of the authored contract (``SlideCapacity.min_font_px``).
BODY_FLOOR_PX = 15.0
SUPPORTING_FLOOR_PX = 12.0

#: A dense page may set text at its own size, never below this share of it.
MIN_SOURCE_RELATIVE_SCALE = 0.9

#: A title never shrinks below this to fit its band; it wraps instead.
TITLE_FLOOR_PX = 28.0

#: Template type scale at chrome level 0 (px). Body 20 → 15 is the rung-2 floor.
TEMPLATE_TYPE_SCALE_PX: dict[str, float] = {
    "title": 40.0,
    "subtitle": 26.0,
    "body": 20.0,
    "supporting": 15.0,
}

ComponentKind = Literal[
    "title",
    "subtitle",
    "paragraph",
    "bullets",
    "numbered",
    "process",
    "comparison",
    "table",
    "image",
    "caption",
    "quote",
    "kpi",
    "furniture",
]

FurnitureKind = Literal["page_number", "footer", "running_header", "date", "confidential"]

Archetype = Literal[
    "cover",
    "text",
    "list",
    "process",
    "comparison",
    "table",
    "image_grid",
    "mixed",
    "image_only",
]

#: Chrome level: how much of the frame the template keeps for itself.
#: 0 full bands, 1 slim bands, 2 title line + footer line, 3 minimal.
ChromeLevel = Literal[0, 1, 2, 3]

#: Content area per chrome level, (width, height) in px, used by fit estimates.
CHROME_CONTENT_AREA: dict[int, tuple[int, int]] = {
    0: (1152, 544),
    1: (1184, 584),
    2: (1216, 628),
    3: (1232, 648),
}

#: The ladder. Rung 5 always terminates: the source page laid this content
#: out at this size once already, so a uniform scale of it always fits.
FitRung = Literal[0, 1, 2, 3, 4, 5]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class SourceRect(StrictModel):
    """A block's rectangle on the source page, normalized 0..1 to that page."""

    x: float = Field(ge=-0.5, le=1.5)
    y: float = Field(ge=-0.5, le=1.5)
    width: float = Field(gt=0, le=2.0)
    height: float = Field(gt=0, le=2.0)


class SourceRunRef(StrictModel):
    """Identity of the source text a rendered element carries.

    ``binding_id`` is exactly what ``source_runs.source_run_binding_id`` returns
    for (block_id, run_start, run_end): the whole block, one run, or a run
    range. The renderer writes it into ``data-source-block-id`` and derives
    ``data-qid`` from it with ``source_stamp_dom.deterministic_source_qid``.
    """

    block_id: str = Field(min_length=1)
    run_start: int | None = Field(default=None, ge=0)
    run_end: int | None = Field(default=None, ge=0)
    binding_id: str = Field(min_length=1)


class TextRun(StrictModel):
    """One formatted run as the source carried it. ``text`` is verbatim."""

    text: str
    bold: bool = False
    italic: bool = False
    size_pt: float | None = Field(default=None, gt=0)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class Paragraph(StrictModel):
    """One source paragraph, bound to the projected run(s) it renders as."""

    text: str
    runs: list[TextRun] = Field(default_factory=list)
    bullet: bool = False
    bullet_kind: str | None = None
    level: int = Field(default=0, ge=0, le=8)
    source: SourceRunRef


class ComposerComponent(StrictModel):
    """One thing the renderer draws. Text components carry their paragraphs."""

    component_id: str = Field(min_length=1)
    kind: ComponentKind
    semantic_role: str = Field(min_length=1)
    paragraphs: list[Paragraph] = Field(default_factory=list)
    #: process steps / comparison columns: nested groups, in source order.
    groups: list[list[Paragraph]] = Field(default_factory=list)
    group_headers: list[Paragraph | None] = Field(default_factory=list)
    #: table cells as rows of cell text, each cell bound to its source run.
    table_rows: list[list[Paragraph]] = Field(default_factory=list)
    #: image components: manifest asset id and occurrence id.
    asset_id: str | None = None
    occurrence_id: str | None = None
    aspect_ratio: float | None = Field(default=None, gt=0)
    furniture_kind: FurnitureKind | None = None
    source_rect: SourceRect | None = None
    #: Character-weighted dominant size of the component's runs, in pt.
    source_size_pt: float | None = Field(default=None, gt=0)
    emphasis: Literal["none", "accent", "muted"] = "none"


class ComposerSlidePlan(StrictModel):
    slide_index: int = Field(ge=0)
    source_unit_id: str = Field(min_length=1)
    archetype: Archetype
    title: ComposerComponent | None = None
    subtitle: ComposerComponent | None = None
    components: list[ComposerComponent] = Field(default_factory=list)
    images: list[ComposerComponent] = Field(default_factory=list)
    furniture: list[ComposerComponent] = Field(default_factory=list)
    #: Total verbatim characters on the page, for density estimates.
    text_chars: int = Field(default=0, ge=0)
    notes: list[str] = Field(default_factory=list)


class ComposerTokens(StrictModel):
    """Look of the deck: colors and type families from the authored style."""

    background: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    surface: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_primary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    text_secondary: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    accent: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_soft: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    rule: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    font_family: str = Field(default="Pretendard, 'Malgun Gothic', sans-serif")
    heading_font_family: str | None = None
    deck_title: str = ""


class ComposerDeckPlan(StrictModel):
    slides: list[ComposerSlidePlan]
    tokens: ComposerTokens
    #: Source page size in EMU; ``None`` when the manifest predates capture.
    source_page_emu: tuple[int, int] | None = None
    #: Uniform factor mapping source points onto canvas px (1.0 for 16:9 13.33in).
    source_px_scale: float = Field(default=1.0, gt=0)
    #: Deck-wide title size: the largest px at which every slide's title fits
    #: two lines of the title band, never below ``TITLE_FLOOR_PX``. ``None``
    #: lets the renderer fall back to ``TEMPLATE_TYPE_SCALE_PX["title"]``.
    title_px: float | None = Field(default=None, gt=0)


class FitParameters(StrictModel):
    """What the ladder decided for one slide. The renderer obeys, never guesses."""

    rung: FitRung = 0
    chrome_level: ChromeLevel = 0
    #: 0 template density, 1 dense variant (more columns, line-height 1.4, gaps halved).
    density: Literal[0, 1] = 0
    #: Multiplies ``TEMPLATE_TYPE_SCALE_PX`` at rungs 0-3; ignored when source_relative.
    type_scale: float = Field(default=1.0, gt=0, le=1.0)
    #: Rung 4: size = source_pt × PX_PER_PT × source_px_scale × source_factor.
    source_relative: bool = False
    source_factor: float = Field(default=1.0, ge=MIN_SOURCE_RELATIVE_SCALE)
    #: Rung 5: place each block in its own source rectangle.
    source_geometry: bool = False
    page_scale: float = Field(default=1.0, ge=MIN_SOURCE_RELATIVE_SCALE, le=1.0)
    #: Rung 5 per-box text shrink already applied, by component_id.
    box_text_scale: dict[str, float] = Field(default_factory=dict)
    columns: int = Field(default=1, ge=1, le=3)


class FitReceipt(StrictModel):
    slide_index: int = Field(ge=0)
    rung: FitRung
    parameters: FitParameters
    renders: int = Field(ge=0)
    estimate_used: bool
    blocking_issues: list[str] = Field(default_factory=list)
    #: Rungs tried before the final one, with the blocking issue that rejected each.
    trail: list[str] = Field(default_factory=list)


class ComposerSlideResult(StrictModel):
    slide_index: int = Field(ge=0)
    html: str = Field(min_length=1)
    receipt: FitReceipt
    #: Chromium metrics of the accepted render, verbatim.
    metrics: dict = Field(default_factory=dict)
    png_path: str | None = None


__all__ = [
    "Archetype",
    "BODY_FLOOR_PX",
    "CANVAS_HEIGHT",
    "CANVAS_WIDTH",
    "CHROME_CONTENT_AREA",
    "ChromeLevel",
    "ComponentKind",
    "ComposerComponent",
    "ComposerDeckPlan",
    "ComposerSlidePlan",
    "ComposerSlideResult",
    "ComposerTokens",
    "FitParameters",
    "FitReceipt",
    "FitRung",
    "FurnitureKind",
    "MIN_SOURCE_RELATIVE_SCALE",
    "PX_PER_PT",
    "Paragraph",
    "SUPPORTING_FLOOR_PX",
    "TITLE_FLOOR_PX",
    "SourceRect",
    "SourceRunRef",
    "StrictModel",
    "TEMPLATE_TYPE_SCALE_PX",
    "TextRun",
]
