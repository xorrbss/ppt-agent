"""Per-component height primitives for the Composer fit estimate.

Split out of ``estimate.py`` (file-size policy: keep stages under ~350 lines).
Everything here answers "how tall does this one component render at these
parameters" -- font sizing, wrapped line counts, and the process/comparison
row math. ``estimate.py`` composes these into the whole-slide estimate.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from services.authored_vnext.source_render_geometry import wrapped_line_count
from services.composer.plan import (
    BODY_FLOOR_PX,
    PX_PER_PT,
    SUPPORTING_FLOOR_PX,
    TEMPLATE_TYPE_SCALE_PX,
    ComposerComponent,
    ComposerDeckPlan,
    FitParameters,
    Paragraph,
)

#: Body line-height per density (ladder table: dense variant 1.6 -> 1.4).
LINE_HEIGHT: dict[int, float] = {0: 1.6, 1: 1.4}
#: Gap between columns and between stacked blocks per density (halved when dense).
GAP_PX: dict[int, float] = {0: 32.0, 1: 16.0}
#: Vertical space between consecutive paragraphs per density.
PARAGRAPH_GAP_PX: dict[int, float] = {0: 10.0, 1: 5.0}
#: Card chrome (padding plus border, both sides) of a process step or comparison column.
GROUP_CHROME_PX: dict[int, float] = {0: 28.0, 1: 16.0}
#: Table cell vertical padding (both sides) per density.
TABLE_CELL_PADDING_PX: dict[int, float] = {0: 12.0, 1: 6.0}
#: Table cell horizontal padding (both sides).
TABLE_CELL_HORIZONTAL_PADDING_PX = 16.0
TABLE_LINE_HEIGHT = 1.3
#: An image never takes more than this share of the content area height.
IMAGE_MAX_AREA_SHARE = 0.35
DEFAULT_IMAGE_ASPECT = 16.0 / 9.0
#: Floor on the image row's height when it shares the content area with a
#: wide component (``render_chrome._flex_image_row``): a recognisable
#: thumbnail at looser chrome, smaller once density and chrome are both
#: already tightened. Shared by the estimate (``estimated_content_height``)
#: and the renderer so neither can disagree about how much room the row is
#: guaranteed to keep, however little the text above it leaves behind.
WIDE_SIBLING_IMAGE_FLOOR_PX = 90.0
WIDE_SIBLING_IMAGE_TIGHT_FLOOR_PX = 72.0
#: Marker column of a bullet or numbered paragraph, in em, per nesting level.
MARKER_INDENT_EM = 1.5
#: Process steps / comparison columns per row before the archetype wraps.
MAX_GROUPS_PER_ROW = 4

_SUPPORTING_ROLES = frozenset({"supporting", "caption"})
_BAND_ROLES = frozenset({"title", "subtitle"})
_BLOCK_KINDS = frozenset({"process", "comparison", "table"})

#: Component kinds ``render_context.size_role`` sizes off their own kind rather
#: than ``semantic_role`` (``render_css._ROLE_SCALE_KEY``): a ``kpi`` value sets
#: at the subtitle scale, not the body scale its (typically "body") semantic
#: role would otherwise select. Counting it at the body scale understates the
#: height a ``kpi`` component actually renders at.
_KIND_SCALE_OVERRIDE: dict[str, str] = {"kpi": "subtitle"}


def type_scale_key(component: ComposerComponent, params: FitParameters | None = None) -> str:
    """Mirrors ``render_context.size_role``: a dense (``density >= 1``) process
    step drops to the supporting scale, the first practical dial once a
    "절감 동인"-shaped page (a process block plus several kicker paragraphs
    and screenshots) has already pushed past chrome/columns."""

    override = _KIND_SCALE_OVERRIDE.get(component.kind)
    if override is not None:
        return override
    if component.kind == "process" and params is not None and int(params.density) >= 1:
        return "supporting"
    role = component.semantic_role
    if role in _BAND_ROLES:
        return role
    if role in _SUPPORTING_ROLES:
        return "supporting"
    return "body"


def template_font_px(component: ComposerComponent, params: FitParameters) -> float:
    """Template size at the step's ``type_scale``, never below the design floor."""

    key = type_scale_key(component, params)
    floor = SUPPORTING_FLOOR_PX if key == "supporting" else BODY_FLOOR_PX
    return max(floor, TEMPLATE_TYPE_SCALE_PX[key] * params.type_scale)


def paragraph_source_size_pt(paragraph: Paragraph) -> float | None:
    """Character-weighted dominant run size of one paragraph, when the source carried one."""

    weights: dict[float, int] = {}
    for run in paragraph.runs:
        if run.size_pt is not None and run.text:
            weights[run.size_pt] = weights.get(run.size_pt, 0) + len(run.text)
    if not weights:
        return None
    return max(weights.items(), key=lambda item: (item[1], item[0]))[0]


def paragraph_font_px(
    paragraph: Paragraph | None,
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
) -> float:
    """Font size the renderer will set for this paragraph under ``params``."""

    if params.source_relative:
        size_pt = paragraph_source_size_pt(paragraph) if paragraph is not None else None
        if size_pt is None:
            size_pt = component.source_size_pt
        if size_pt is not None:
            return size_pt * PX_PER_PT * deck.source_px_scale * params.source_factor
    return template_font_px(component, params)


def paragraph_height_px(paragraph: Paragraph, *, width_px: float, font_px: float, line_height: float) -> float:
    """Height of one wrapped paragraph including its marker indent."""

    levels = paragraph.level + 1 if paragraph.bullet else paragraph.level
    text_width = max(font_px, width_px - MARKER_INDENT_EM * font_px * levels)
    lines = wrapped_line_count(paragraph.text, width_px=text_width, font_px=font_px)
    return lines * font_px * line_height


def paragraphs_height_px(
    paragraphs: Sequence[Paragraph],
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    width_px: float,
) -> float:
    if not paragraphs:
        return 0.0
    density = int(params.density)
    total = sum(
        paragraph_height_px(
            paragraph,
            width_px=width_px,
            font_px=paragraph_font_px(paragraph, component, params, deck),
            line_height=LINE_HEIGHT[density],
        )
        for paragraph in paragraphs
    )
    return total + PARAGRAPH_GAP_PX[density] * (len(paragraphs) - 1)


def _row_grouped_height_px(
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    width_px: float,
    per_row: int,
) -> float:
    """``per_row`` groups side by side per row, as many rows as ``groups`` needs."""

    groups = component.groups
    intro = paragraphs_height_px(component.paragraphs, component, params, deck, width_px=width_px)
    if not groups:
        return intro
    density = int(params.density)
    gap = GAP_PX[density]
    chrome = GROUP_CHROME_PX[density]
    per_row = max(1, min(per_row, len(groups)))
    rows = math.ceil(len(groups) / per_row)
    group_width = max(1.0, (width_px - gap * (per_row - 1)) / per_row - chrome)
    heights: list[float] = []
    for index, group in enumerate(groups):
        header = component.group_headers[index] if index < len(component.group_headers) else None
        paragraphs = ([header] if header is not None else []) + list(group)
        heights.append(paragraphs_height_px(paragraphs, component, params, deck, width_px=group_width) + chrome)
    row_heights = [max(heights[row * per_row : (row + 1) * per_row]) for row in range(rows)]
    total = sum(row_heights) + gap * (rows - 1)
    return total + (intro + gap if intro else 0.0)


def comparison_height_px(
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    width_px: float,
) -> float:
    """Comparison columns: ``render_comparison`` never wraps, so all of them share one row."""

    return _row_grouped_height_px(
        component, params, deck, width_px=width_px, per_row=len(component.groups) or 1
    )


def process_per_row(
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    width_px: float,
) -> int:
    """Steps per row for one process component -- shared by ``process_height_px``
    and ``render_process`` so the estimate and the renderer never disagree
    about how many rows a process wraps to.

    Past ``MAX_GROUPS_PER_ROW`` steps this always wraps to exactly two rows,
    as before. At or under that count it additionally wraps early when a
    single row would actually come out *taller* than two rows would -- the
    count-only rule alone still renders a <=4-step row as one very tall row
    when one step's text is disproportionately long next to short siblings
    (measured on ``composer-offline-eval`` final2-43 slide 32's 3-step
    "절감 동인" process blocks: a 61-char step beside two 7-char ones). Two
    rows widen each step's own column, which can wrap its text onto *fewer*
    lines than the cramped single-row column despite adding a row and a gap;
    comparing the two real estimates -- rather than guessing at a fixed
    height budget -- picks whichever arrangement is actually shorter, and
    never wraps a genuinely short row that already fits comfortably in one.
    """

    count = len(component.groups)
    if count <= 1:
        return max(1, count)
    if count > MAX_GROUPS_PER_ROW:
        return -(-count // 2)
    two_row = -(-count // 2)
    single_row_height = _row_grouped_height_px(component, params, deck, width_px=width_px, per_row=count)
    two_row_height = _row_grouped_height_px(component, params, deck, width_px=width_px, per_row=two_row)
    return two_row if two_row_height < single_row_height else count


def process_height_px(
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    width_px: float,
) -> float:
    """Process steps: mirrors ``render_process``'s own row split via ``process_per_row``."""

    per_row = process_per_row(component, params, deck, width_px=width_px)
    return _row_grouped_height_px(component, params, deck, width_px=width_px, per_row=per_row)


def group_component_height_px(
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    width_px: float,
) -> float:
    """Fallback for a component that carries groups under a kind other than process or
    comparison: the old, conservative wrap-past-``MAX_GROUPS_PER_ROW`` model."""

    return _row_grouped_height_px(component, params, deck, width_px=width_px, per_row=MAX_GROUPS_PER_ROW)


def table_height_px(
    component: ComposerComponent,
    params: FitParameters,
    deck: ComposerDeckPlan,
    *,
    width_px: float,
) -> float:
    """Rows x (tallest wrapped cell x line height) plus cell padding."""

    rows = component.table_rows
    if not rows:
        return 0.0
    density = int(params.density)
    column_count = max(len(row) for row in rows) or 1
    font_px = paragraph_font_px(None, component, params, deck)
    cell_width = max(font_px, width_px / column_count - TABLE_CELL_HORIZONTAL_PADDING_PX)
    total = 0.0
    for row in rows:
        lines = max(
            (wrapped_line_count(cell.text, width_px=cell_width, font_px=font_px) for cell in row),
            default=1,
        )
        total += lines * font_px * TABLE_LINE_HEIGHT + TABLE_CELL_PADDING_PX[density]
    return total


def image_height_px(component: ComposerComponent, *, width_px: float, area_height: float) -> float:
    """Height an image would need at its own aspect ratio, capped to a share of the area.

    Not used by ``estimate.estimated_content_height``: ``render_chrome._content``
    lays images out in their own region (side-by-side with text, or their own
    grid when a slide carries no other content) sized to ``content_height`` --
    never stacked on top of the text flow -- so an image can never be the
    reason a slide does not fit vertically. Kept as a sizing utility for
    callers that do need a natural image height (e.g. rung 5 box sizing).
    """

    aspect = component.aspect_ratio or DEFAULT_IMAGE_ASPECT
    return min(IMAGE_MAX_AREA_SHARE * area_height, width_px / aspect)


def wide_sibling_image_floor_px(params: FitParameters) -> float:
    """The floor ``render_chrome._flex_image_row`` and ``estimated_content_height``
    both use for the image row's ``min-height`` when it shares the content
    area with a wide component: 72px once density and chrome are both
    already tight (``density >= 1`` and ``chrome_level >= 2``), 90px otherwise."""

    if int(params.density) >= 1 and int(params.chrome_level) >= 2:
        return WIDE_SIBLING_IMAGE_TIGHT_FLOOR_PX
    return WIDE_SIBLING_IMAGE_FLOOR_PX


__all__ = [
    "DEFAULT_IMAGE_ASPECT",
    "GAP_PX",
    "GROUP_CHROME_PX",
    "IMAGE_MAX_AREA_SHARE",
    "LINE_HEIGHT",
    "MARKER_INDENT_EM",
    "MAX_GROUPS_PER_ROW",
    "PARAGRAPH_GAP_PX",
    "TABLE_CELL_HORIZONTAL_PADDING_PX",
    "TABLE_CELL_PADDING_PX",
    "TABLE_LINE_HEIGHT",
    "WIDE_SIBLING_IMAGE_FLOOR_PX",
    "WIDE_SIBLING_IMAGE_TIGHT_FLOOR_PX",
    "comparison_height_px",
    "group_component_height_px",
    "image_height_px",
    "paragraph_font_px",
    "paragraph_height_px",
    "paragraph_source_size_pt",
    "paragraphs_height_px",
    "process_height_px",
    "process_per_row",
    "table_height_px",
    "template_font_px",
    "type_scale_key",
    "wide_sibling_image_floor_px",
]
