"""Canonical render geometry for the source-native slide flow.

Admission and rendering previously carried two copies of the same height
arithmetic and drifted apart, so a page could be admitted at a height the
browser never produces.  This module is the single implementation and every
number in it is derived from the CSS that ``_append_semantic_block`` emits:

* text nodes  ``margin:0;padding-bottom:1px`` plus ``_flow_text_css``
  (``line-height:1.18`` for the title role, ``1.32`` otherwise)
* card groups ``padding:10px 12px;border:1px solid``
* media cards ``grid-template-columns:52px minmax(0,1fr);gap:10px;padding:10px``
* process steps ``grid-template-columns:auto minmax(0,1fr);gap:5px 10px``
  over two declared rows, or three when the step owns a media header
* tables      ``gap:1px 10px`` with ``padding:1px 6px;border-left:2px`` cells
  whose ``line-height:1.08!important`` overrides the text role
* footers     a ``height:23px`` lane with a ``16px`` item gap

Callers pass neutral :class:`RenderUnit` values so that both the sealed
renderer units and the admission units resolve to identical numbers.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
import unicodedata


# Typography.  CSS pixels convert to points at 0.75pt/px, so a 12pt readable
# floor is 16px.  Undeclared slot styles fall back to the renderer defaults.
BODY_FONT_FLOOR_PX = 12.0 / 0.75
TITLE_FONT_FLOOR_PX = 32.0
BODY_FONT_DEFAULT_PX = 22.0
TITLE_FONT_DEFAULT_PX = 40.0
TITLE_LINE_HEIGHT = 1.18
BODY_LINE_HEIGHT = 1.32
TABLE_CELL_LINE_HEIGHT = 1.08
# Every emitted text node carries `padding-bottom:1px` so a fractionally
# resolved CJK glyph cannot be clipped by the container's `overflow:hidden`.
TEXT_LINE_BOX_BREATHING_PX = 1.0

# Visual media heights emitted as explicit `height:` declarations.
FLOW_ICON_HEIGHT_PX = 48.0
FLOW_MEDIA_HEIGHT_PX = 120.0
FLOW_PROCESS_MEDIA_HEIGHT_PX = 96.0

# Card chrome: `padding:10px 12px` plus a 1px border on every side.
CARD_BORDER_PX = 1.0
CARD_PADDING_Y_PX = 10.0
CARD_PADDING_X_PX = 12.0
CARD_VERTICAL_CHROME_PX = 2.0 * (CARD_PADDING_Y_PX + CARD_BORDER_PX)
CARD_HORIZONTAL_CHROME_PX = 2.0 * (CARD_PADDING_X_PX + CARD_BORDER_PX)

# Media cards use a symmetric `padding:10px` plus a fixed icon column.
MEDIA_CARD_PADDING_PX = 10.0
MEDIA_CARD_VERTICAL_CHROME_PX = 2.0 * (MEDIA_CARD_PADDING_PX + CARD_BORDER_PX)
MEDIA_CARD_HORIZONTAL_CHROME_PX = 2.0 * (MEDIA_CARD_PADDING_PX + CARD_BORDER_PX)
MEDIA_CARD_ICON_COLUMN_PX = 52.0
MEDIA_CARD_COLUMN_GAP_PX = 10.0

# Stack gaps, one per emitted wrapper.
FLOW_STACK_GAP_PX = 4.0
CARD_STACK_GAP_PX = 5.0
TITLE_STACK_GAP_PX = 6.0
MEDIA_CARD_TEXT_GAP_PX = 4.0
PROCESS_ROW_GAP_PX = 5.0
PROCESS_COLUMN_GAP_PX = 10.0
PROCESS_BODY_GAP_PX = 4.0

# Table chrome: `gap:1px 10px` with `padding:1px 6px;border-left:2px` cells.
TABLE_ROW_GAP_PX = 1.0
TABLE_COLUMN_GAP_PX = 10.0
TABLE_CELL_VERTICAL_CHROME_PX = 2.0
TABLE_CELL_HORIZONTAL_CHROME_PX = 14.0

# Compact footer lane: `height:23px` with a `16px` item gap, separated from the
# body track by an equal spacer row.
FOOTER_LANE_HEIGHT_PX = 23.0
FOOTER_BODY_GAP_PX = 16.0

TITLE_SLOT_ROLES = frozenset({"title", "headline", "heading"})
TITLE_TEXT_ROLES = frozenset({"title", "heading", "section-heading"})
BODY_TEXT_ROLES = frozenset(
    {"body", "lede", "eyebrow", "kpi-label", "compact-footer"}
)
PROCESS_HEADER_ROLES = frozenset(
    {"title", "heading", "step-title", "step-heading"}
)
PROCESS_NUMBER_ROLES = frozenset({"process-number", "step-number"})
EMPHASIS_NUMBER_ROLES = frozenset(
    {"process-number", "step-number", "kpi-value"}
)
FLOOR_FONT_ROLES = frozenset({"eyebrow", "kpi-label", "compact-footer"})
CARD_GROUP_KINDS = frozenset({"media-card", "process-step", "kpi"})


class RenderGeometryError(ValueError):
    """Fail-closed geometry error carrying the shared renderer reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RenderUnit:
    """Renderer-neutral view of one sealed content unit."""

    text: str = ""
    kind: str = "text"
    semantic_role: str = "body"
    group_kind: str = ""
    image_kind: str = "media"
    table_row: int | None = None
    table_column: int | None = None
    table_row_span: int = 1
    table_column_span: int = 1


@dataclass(frozen=True)
class TextMetrics:
    """Resolved ``font-size`` and ``line-height`` for one emitted text node."""

    font_px: float
    line_height: float


def slot_declared_font_px(slot: Mapping[str, object]) -> float:
    """Read the slot's declared ``textStyle.fontSize`` in CSS pixels."""

    style = slot.get("textStyle")
    if not isinstance(style, Mapping):
        raw = slot.get("raw")
        style = raw.get("textStyle") if isinstance(raw, Mapping) else None
    if not isinstance(style, Mapping):
        return 0.0
    value = style.get("fontSize")
    if isinstance(value, bool):
        return 0.0
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return 0.0
    return resolved if math.isfinite(resolved) else 0.0


def flow_font_px(
    declared_font_px: float, *, role: str, adaptive_scale: float
) -> float:
    """Resolve the slot font size the renderer writes for a text role."""

    title_like = role in TITLE_SLOT_ROLES
    default = TITLE_FONT_DEFAULT_PX if title_like else BODY_FONT_DEFAULT_PX
    minimum = TITLE_FONT_FLOOR_PX if title_like else BODY_FONT_FLOOR_PX
    return max(
        minimum, max(minimum, declared_font_px or default) * adaptive_scale
    )


def semantic_text_role(*, semantic_role: str, fallback_role: str) -> str:
    """Mirror the role that selects the emitted ``line-height``."""

    role = semantic_role.strip().lower()
    if role in TITLE_TEXT_ROLES:
        return "title"
    if role in BODY_TEXT_ROLES:
        return "body"
    return "title" if fallback_role.strip().lower() in TITLE_SLOT_ROLES else "body"


def semantic_text_metrics(
    *,
    semantic_role: str,
    group_kind: str,
    fallback_role: str,
    declared_font_px: float,
    adaptive_scale: float,
) -> TextMetrics:
    """Resolve the exact font size and line height emitted for one unit."""

    role = semantic_role.strip().lower()
    kind = group_kind.strip().lower()
    process_header = kind == "process-step" and role in PROCESS_HEADER_ROLES
    text_role = (
        "body"
        if process_header
        else semantic_text_role(
            semantic_role=role, fallback_role=fallback_role
        )
    )
    line_height = TITLE_LINE_HEIGHT if text_role == "title" else BODY_LINE_HEIGHT
    body_px = flow_font_px(
        declared_font_px, role="body", adaptive_scale=adaptive_scale
    )
    if process_header:
        return TextMetrics(max(20.0, body_px * 1.08), line_height)
    if role in {"heading", "section-heading"}:
        return TextMetrics(max(20.0, body_px * 1.18), line_height)
    if role in EMPHASIS_NUMBER_ROLES:
        return TextMetrics(max(24.0, body_px * 1.35), line_height)
    if role in FLOOR_FONT_ROLES:
        return TextMetrics(BODY_FONT_FLOOR_PX, line_height)
    if role == "lede":
        return TextMetrics(max(18.0, body_px), line_height)
    return TextMetrics(
        flow_font_px(
            declared_font_px, role=text_role, adaptive_scale=adaptive_scale
        ),
        line_height,
    )


def text_advance_px(text: str, *, font_px: float) -> float:
    """Estimate the rendered advance width of a single unwrapped run."""

    total = 0.0
    for character in " ".join(text.split()):
        if character.isspace():
            total += font_px * 0.33
        elif unicodedata.east_asian_width(character) in {"W", "F", "A"}:
            total += font_px
        elif character.isupper():
            total += font_px * 0.64
        elif character.isdigit():
            total += font_px * 0.56
        else:
            total += font_px * 0.54
    return total


def wrapped_line_count(text: str, *, width_px: float, font_px: float) -> int:
    """Count the line boxes the browser produces for a wrapped text node."""

    normalized = " ".join(text.split())
    if not normalized:
        return 1
    lines = 1
    occupied = 0.0
    space = font_px * 0.33
    for word in normalized.split(" "):
        word_width = text_advance_px(word, font_px=font_px)
        separator = space if occupied > 0 else 0.0
        if occupied > 0 and occupied + separator + word_width > width_px:
            lines += 1
            occupied = 0.0
            separator = 0.0
        if word_width <= width_px:
            occupied += separator + word_width
            continue
        remaining = word_width
        while remaining > width_px:
            if occupied > 0:
                lines += 1
                occupied = 0.0
            remaining -= width_px
            if remaining > 0:
                lines += 1
        occupied = remaining
    return lines


def text_node_height(text: str, *, width_px: float, metrics: TextMetrics) -> float:
    """Height of one emitted ``p``/``h2`` including its breathing padding."""

    return (
        wrapped_line_count(text, width_px=width_px, font_px=metrics.font_px)
        * metrics.font_px
        * metrics.line_height
        + TEXT_LINE_BOX_BREATHING_PX
    )


def footer_lane_item_height(*, adaptive_scale: float = 1.0) -> float:
    """Height one compact-footer text node occupies inside the fixed lane."""

    metrics = semantic_text_metrics(
        semantic_role="compact-footer",
        group_kind="compact-footer",
        fallback_role="body",
        declared_font_px=0.0,
        adaptive_scale=adaptive_scale,
    )
    return metrics.font_px * metrics.line_height + TEXT_LINE_BOX_BREATHING_PX


def image_height_px(unit: RenderUnit, *, group_kind: str = "") -> float:
    """Explicit ``height:`` the renderer writes for one image occurrence."""

    icon = unit.image_kind.strip().lower() == "icon"
    if group_kind.strip().lower() == "process-step":
        return FLOW_ICON_HEIGHT_PX if icon else FLOW_PROCESS_MEDIA_HEIGHT_PX
    return FLOW_ICON_HEIGHT_PX if icon else FLOW_MEDIA_HEIGHT_PX


def is_table_block(block: Sequence[RenderUnit]) -> bool:
    """Whether the renderer emits this block through the table grid path."""

    if not block:
        return False
    if block[0].group_kind.strip().lower() == "table-matrix":
        return True
    role = block[0].semantic_role.strip().lower()
    return role.startswith("table-") or role == "cell"


def table_matrix_shape(block: Sequence[RenderUnit]) -> tuple[int, int] | None:
    """Resolve the declared ``grid-row``/``grid-column`` matrix, if any."""

    has_metadata = any(
        item.table_row is not None or item.table_column is not None
        for item in block
    )
    if not has_metadata:
        return None
    if any(
        item.table_row is None or item.table_column is None for item in block
    ):
        raise RenderGeometryError("source_native_table_matrix_invalid")
    rows = max(
        int(item.table_row or 0) + item.table_row_span for item in block
    )
    columns = max(
        int(item.table_column or 0) + item.table_column_span for item in block
    )
    occupied: set[tuple[int, int]] = set()
    for item in block:
        row = int(item.table_row or 0)
        column = int(item.table_column or 0)
        for row_index in range(row, row + item.table_row_span):
            for column_index in range(column, column + item.table_column_span):
                cell = (row_index, column_index)
                if cell in occupied:
                    raise RenderGeometryError(
                        "source_native_table_matrix_invalid"
                    )
                occupied.add(cell)
    if rows <= 0 or columns <= 0:
        raise RenderGeometryError("source_native_table_matrix_invalid")
    return rows, columns


def auto_table_columns(cell_count: int) -> int:
    """Column count the renderer writes when no matrix metadata exists."""

    return max(2, min(4, math.ceil(max(1, cell_count) ** 0.5)))


def table_block_height(
    block: Sequence[RenderUnit],
    *,
    column_width: float,
    declared_font_px: float,
    adaptive_scale: float,
    fallback_role: str,
) -> tuple[float, int]:
    """Height and column count of one emitted table grid."""

    if not block:
        return 0.0, 1
    shape = table_matrix_shape(block)
    if shape is None:
        columns = auto_table_columns(len(block))
        rows = math.ceil(len(block) / columns)
    else:
        rows, columns = shape
    base_cell_width = max(
        1.0, (column_width - (columns - 1) * TABLE_COLUMN_GAP_PX) / columns
    )
    row_heights = [0.0] * rows
    for index, unit in enumerate(block):
        column_span = unit.table_column_span if shape is not None else 1
        row_span = unit.table_row_span if shape is not None else 1
        start = (
            int(unit.table_row or 0) if shape is not None else index // columns
        )
        cell_width = max(
            1.0,
            base_cell_width * column_span
            + TABLE_COLUMN_GAP_PX * (column_span - 1)
            - TABLE_CELL_HORIZONTAL_CHROME_PX,
        )
        # `line-height:1.08!important` on the cell overrides the text role.
        metrics = semantic_text_metrics(
            semantic_role=unit.semantic_role,
            group_kind=unit.group_kind,
            fallback_role=fallback_role,
            declared_font_px=declared_font_px,
            adaptive_scale=adaptive_scale,
        )
        cell_height = (
            wrapped_line_count(
                unit.text, width_px=cell_width, font_px=metrics.font_px
            )
            * metrics.font_px
            * TABLE_CELL_LINE_HEIGHT
            + TABLE_CELL_VERTICAL_CHROME_PX
        )
        per_row_height = cell_height / row_span
        for row_index in range(start, start + row_span):
            row_heights[row_index] = max(row_heights[row_index], per_row_height)
    return (
        sum(row_heights) + max(0, rows - 1) * TABLE_ROW_GAP_PX,
        columns,
    )


def _process_step_height(
    block: Sequence[RenderUnit],
    *,
    column_width: float,
    declared_font_px: float,
    adaptive_scale: float,
    fallback_role: str,
) -> float:
    content_width = max(1.0, column_width - CARD_HORIZONTAL_CHROME_PX)
    media_height = 0.0
    number_units: list[RenderUnit] = []
    header_units: list[RenderUnit] = []
    body_units: list[RenderUnit] = []
    for unit in block:
        if unit.kind == "image":
            media_height = max(
                media_height, image_height_px(unit, group_kind="process-step")
            )
            continue
        role = unit.semantic_role.strip().lower()
        if role in PROCESS_NUMBER_ROLES:
            number_units.append(unit)
        elif role in PROCESS_HEADER_ROLES:
            header_units.append(unit)
        else:
            body_units.append(unit)

    def metrics_for(unit: RenderUnit) -> TextMetrics:
        return semantic_text_metrics(
            semantic_role=unit.semantic_role,
            group_kind="process-step",
            fallback_role=fallback_role,
            declared_font_px=declared_font_px,
            adaptive_scale=adaptive_scale,
        )

    # Column one is an `auto` track, so it is sized by the widest step number
    # laid out at its own font size, and the 10px column gap follows it.
    number_column_px = 0.0
    header_heights: list[float] = []
    for unit in number_units:
        metrics = metrics_for(unit)
        number_column_px = max(
            number_column_px,
            text_advance_px(unit.text, font_px=metrics.font_px),
        )
        header_heights.append(
            metrics.font_px * metrics.line_height + TEXT_LINE_BOX_BREATHING_PX
        )
    header_width = max(
        1.0, content_width - number_column_px - PROCESS_COLUMN_GAP_PX
    )
    for unit in header_units:
        header_heights.append(
            text_node_height(
                unit.text, width_px=header_width, metrics=metrics_for(unit)
            )
        )
    body_height = sum(
        text_node_height(
            unit.text, width_px=content_width, metrics=metrics_for(unit)
        )
        for unit in body_units
    ) + max(0, len(body_units) - 1) * PROCESS_BODY_GAP_PX
    # CSS Grid charges the row gap between every declared track, so the media
    # header adds a third track and a second gap even when a track is empty.
    declared_rows = 3 if media_height > 0.0 else 2
    return (
        media_height
        + max(header_heights, default=0.0)
        + body_height
        + CARD_VERTICAL_CHROME_PX
        + (declared_rows - 1) * PROCESS_ROW_GAP_PX
    )


def semantic_block_height(
    block: Sequence[RenderUnit],
    *,
    column_width: float,
    declared_font_px: float,
    adaptive_scale: float,
    fallback_role: str,
    table_block: bool | None = None,
) -> float:
    """Rendered height of one emitted semantic group at a given column width.

    ``table_block`` lets a caller whose boundary classifier already segmented
    the block into table cells reuse that decision instead of re-deriving it.
    """

    if not block:
        return 0.0
    group_kind = block[0].group_kind.strip().lower()
    if group_kind == "compact-footer":
        return FOOTER_LANE_HEIGHT_PX
    if is_table_block(block) if table_block is None else table_block:
        return table_block_height(
            block,
            column_width=column_width,
            declared_font_px=declared_font_px,
            adaptive_scale=adaptive_scale,
            fallback_role=fallback_role,
        )[0]
    if group_kind == "process-step":
        return _process_step_height(
            block,
            column_width=column_width,
            declared_font_px=declared_font_px,
            adaptive_scale=adaptive_scale,
            fallback_role=fallback_role,
        )
    images = [unit for unit in block if unit.kind == "image"]
    texts = [unit for unit in block if unit.kind != "image"]
    if group_kind == "media-card":
        # Images occupy the fixed 52px grid column, so they never join the
        # text stack; they only raise the card's minimum row height.
        text_width = max(
            1.0,
            column_width
            - MEDIA_CARD_HORIZONTAL_CHROME_PX
            - MEDIA_CARD_ICON_COLUMN_PX
            - MEDIA_CARD_COLUMN_GAP_PX,
        )
        stack_gap = MEDIA_CARD_TEXT_GAP_PX
        vertical_chrome = MEDIA_CARD_VERTICAL_CHROME_PX
        image_height = 0.0
        gap_count = max(0, len(texts) - 1)
        floor = (
            FLOW_ICON_HEIGHT_PX + MEDIA_CARD_VERTICAL_CHROME_PX if images else 0.0
        )
    else:
        card_like = group_kind in CARD_GROUP_KINDS
        text_width = max(
            1.0,
            column_width - (CARD_HORIZONTAL_CHROME_PX if card_like else 0.0),
        )
        stack_gap = (
            TITLE_STACK_GAP_PX
            if group_kind == "title-stack"
            else CARD_STACK_GAP_PX
            if card_like
            else FLOW_STACK_GAP_PX
        )
        vertical_chrome = CARD_VERTICAL_CHROME_PX if card_like else 0.0
        # Images are flex children of the same stack as the text nodes.
        image_height = sum(
            image_height_px(unit, group_kind=group_kind) for unit in images
        )
        gap_count = max(0, len(block) - 1)
        floor = 0.0
    text_height = sum(
        text_node_height(
            unit.text,
            width_px=text_width,
            metrics=semantic_text_metrics(
                semantic_role=unit.semantic_role,
                group_kind=group_kind,
                fallback_role=fallback_role,
                declared_font_px=declared_font_px,
                adaptive_scale=adaptive_scale,
            ),
        )
        for unit in texts
    )
    return max(
        floor,
        text_height + image_height + stack_gap * gap_count + vertical_chrome,
    )
