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

from models.authored_vnext.master.contract import CanvasContract
from services.authored_canvas import canvas_metadata_for_aspect_ratio

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

#: Ceiling of the grow step (``fit_ladder.grow_step``). A sparse page may set
#: its *body* text larger so it reads as composed rather than as a fragment,
#: but verbatim restyle must not turn a modest page into a poster, so the
#: growth stops where the deck's own type hierarchy does: 20 px body x 1.30 is
#: 26 px, exactly ``TEMPLATE_TYPE_SCALE_PX["subtitle"]``. Body text therefore
#: never reaches the subtitle rung, let alone the 40 px title rung, and a
#: grown page still reads as the same deck as an ungrown one. Title, subtitle
#: and furniture are excluded from the multiplier entirely
#: (``render_css.template_px``), so the title's size and position stay
#: identical at every rung, as section 4.3 requires.
MAX_TYPE_GROW = 1.30

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

#: The final source-placement rung terminates with measured evidence; residual
#: issues remain blocking and are never proof that source geometry fits.
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
    font_family: str | None = None


class Paragraph(StrictModel):
    """One source paragraph, bound to the projected run(s) it renders as."""

    text: str
    runs: list[TextRun] = Field(default_factory=list)
    bullet: bool = False
    bullet_kind: str | None = None
    level: int = Field(default=0, ge=0, le=8)
    source: SourceRunRef
    layout: dict = Field(default_factory=dict)


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
    source_order: int = 0
    source_text_body: dict = Field(default_factory=dict)
    source_transform: dict = Field(default_factory=dict)
    table_merged_cells: list[dict] = Field(default_factory=list)
    table_column_widths_emu: list[float] = Field(default_factory=list)
    table_row_heights_emu: list[float] = Field(default_factory=list)
    image_crop: dict = Field(default_factory=dict)
    image_color_transforms: list[dict] = Field(default_factory=list)
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
    source_placements: list[ComposerComponent] = Field(default_factory=list)
    #: Trusted per-capture resolver evidence; never populated from request HTML.
    source_image_materializations: dict[str, dict] = Field(default_factory=dict)
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
    #: The accent pushed to a legible contrast, for accent-coloured *text*
    #: (``utils.color_contrast.readable_accent_color``). ``accent`` stays the
    #: authored brand colour for fills and rules; only text-bearing rules use
    #: this instead (``services.composer.render_css.stylesheet``).
    accent_text: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_soft: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    rule: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    font_family: str = Field(default="Pretendard, 'Malgun Gothic', sans-serif")
    heading_font_family: str | None = None
    deck_title: str = ""


class ComposerStyleProfile(StrictModel):
    """Explicit reusable style grammar supplied by a selected style definition."""

    profile_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
    version: Literal["composer-style/v1"] = "composer-style/v1"
    header_style: Literal["tab-band", "rule"] = "rule"
    header_fill: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    header_tab_fill: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    heading_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    surface_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    table_header_fill: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    table_header_text: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    footer_fill: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    footer_text: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    cover_alignment: Literal["left", "center"] = "left"
    border_radius: float = Field(default=0.0, ge=0.0, le=32.0)
    cover_fill: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    cover_text: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    cover_accent_fill: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    emphasis_color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")


class ComposerDeckPlan(StrictModel):
    style_profile: ComposerStyleProfile | None = None
    canvas: CanvasContract = Field(default_factory=lambda: canvas_metadata_for_aspect_ratio("16:9").canvas_contract())
    slides: list[ComposerSlidePlan]
    fit_policy_revision: str | None = None
    font_floor_px: float | None = Field(default=None, ge=7.0)
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
    #: Grow step: uniform multiplier on the template body/supporting scale of
    #: a page whose *measured* content used far less than the content area.
    #: Only ever above 1.0 at rungs 0-1; never applied to title, subtitle or
    #: furniture. See ``MAX_TYPE_GROW``.
    type_grow: float = Field(default=1.0, ge=1.0, le=MAX_TYPE_GROW)
    #: Grow step: centre the content block in the content area instead of
    #: hanging it from the top edge.
    content_centered: bool = False


class FitReceipt(StrictModel):
    fit_policy_revision: str | None = None
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
    "MAX_TYPE_GROW",
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
