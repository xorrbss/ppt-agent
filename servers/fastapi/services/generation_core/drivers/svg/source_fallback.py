"""The verbatim page a source-reflow deck publishes when a page is out of attempts.

A source-reflow page may not be re-authored: every source text, table cell,
image and shape must appear exactly as the source manifest seals it, which
``services/svg_source_layout.verify_source_receipt`` proves. When the last
authored render of such a page lost content (``quality/page_failure_kind.py``)
or never rendered, the terminal rescue (``terminal.py``) publishes this page
instead, flagged ``author:source_verbatim_fallback``: a deterministic, plain
arrangement of that page's own source units in reading order.

* every text block and table cell is one ``<text>`` whose characters are the
  sealed source text, byte for byte -- wrapped into ``<tspan>`` lines at
  whitespace (a word wider than a line breaks mid-word), never shortened;
* tables keep their rows, columns and merges on one consistent cell grid, the
  native-table group the publication projects into an editable table;
* source images keep their slot, contained in a box of their source aspect;
* source shapes keep their identity as light outlines at their source position.

When the content does not fit at a readable size the text is set smaller and
flowed into more columns; when it does not fit at all the overflow stays on the
page, measured and flagged -- nothing is dropped. The page is bound through
``bind_whole_page_source_svg``, the same binder an authored whole-page source
page goes through, so publication and export read it as any other source page.

Only the whole-page source contract (``svg-source-whole-page/v1``) is served,
and a page carrying a chart is not: a chart needs a native declaration this
plain layout does not author. Either raises, and the rescue falls back to its
failure placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET

from services.generation_core.drivers.svg.fallback import _char_em, _ink_on, _mix
from services.svg_contract import SVG_NS

WHOLE_PAGE_CONTRACT = {"version": "svg-source-whole-page/v1"}
#: v2: text ink and table fills follow the page background (v1 drew fixed dark
#: ink and white cells). The label is only recorded in the attempt receipt.
SOURCE_FALLBACK_VERSION = "svg-source-verbatim-fallback/v2"

#: Body sizes tried in order. The readable ones (at or above the deck's floor)
#: come first; the smaller tail keeps every word on a page the flag already
#: marks for review.
_FONT_STEPS_PX = (24, 22, 20, 18, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4)
_COLUMNS = (1, 2, 3)
_LINE_HEIGHT = 1.6
_TITLE_SCALE = 1.5
_SPACE_EM = 0.3
_CELL_PADDING_PX = 6
_ITEM_GAP_EM = 0.8
_IMAGE_MAX_SHARE = 0.45
#: Preferred ink; ``_ink_on`` replaces it when it does not read on the page background.
_PREFERRED_INK = "#172033"
_SHAPE_STROKE = "#CBD5E1"
#: Shares of the ink mixed into the page background for the table header and its outline.
_TABLE_HEADER_MIX = 0.08
_TABLE_STROKE_MIX = 0.4
_TITLE_ROLES = frozenset({"title", "ctrTitle"})


def _font_family(snapshot) -> str:
    from utils.svg_template_fonts import FONT_FAMILY

    template = snapshot.get("source_template") or {}
    body = (template.get("fonts") or {}).get("body") or {}
    return body.get("family") or FONT_FAMILY


def _background(snapshot) -> str:
    tokens = (snapshot.get("source_template") or {}).get("tokens") or {}
    value = tokens.get("background")
    if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{3}", value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return value.upper()
    return "#FFFFFF"


def _ink(background: str) -> str:
    """Text colour that reads (4.5:1) on ``background``."""

    return _ink_on(background, _PREFERRED_INK)


def line_slices(text: str, max_em: float) -> list[tuple[int, int]]:
    """``(start, end)`` ranges that concatenate back to ``text`` exactly.

    A line ends after a source line feed (kept in that line), after the last
    whitespace that keeps the line within ``max_em``, or -- for one word wider
    than the line -- mid-word. Whitespace never forces a break, so trailing
    spaces stay on their line.
    """

    if not text:
        return [(0, 0)]
    slices, start, size = [], 0, len(text)
    while start < size:
        end, width, last_break = start, 0.0, None
        while end < size:
            ch = text[end]
            if ch == "\n":
                end += 1
                break
            if ch.isspace():
                width += _SPACE_EM
                end += 1
                last_break = end
                continue
            advance = _char_em(ch)
            if end > start and width + advance > max_em:
                if last_break is not None:
                    end = last_break
                break
            width += advance
            end += 1
        slices.append((start, end))
        start = end
    return slices


@dataclass
class _Box:
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top


def _content_box(snapshot, page_index, count, width, height, header_page) -> _Box:
    margin = round(min(width, height) * 0.06)
    box = _Box(margin, margin, width - margin, height - margin)
    header = snapshot.get("common_header") or {}
    if (header.get("version") == "svg-common-header/v2" and isinstance(header_page, dict)
            and header_page.get("applied") and isinstance(header.get("body_box"), dict)):
        body = header["body_box"]
        box = _Box(max(box.left, body["x"]), max(box.top, body["y"]),
                   min(box.right, body["x"] + body["width"]), min(box.bottom, body["y"] + body["height"]))
    furniture = ((snapshot.get("source_template") or {}).get("system_furniture") or {})
    for reservation in furniture.get("reservations", []):
        visibility = reservation.get("visibility")
        if (visibility == "hidden" or (visibility == "exceptCover" and page_index == 0)
                or (visibility == "coverOnly" and page_index != 0)):
            continue
        top, bottom = reservation["y"], reservation["y"] + reservation["height"]
        gap = 12
        if (top + bottom) / 2 < height / 2:
            box.top = max(box.top, bottom + gap)
        else:
            box.bottom = min(box.bottom, top - gap)
    if box.width <= 0 or box.height <= 0:
        raise ValueError("svg_source_fallback_no_body_space")
    return box


def _node(parent, name, **attrs):
    return ET.SubElement(parent, f"{{{SVG_NS}}}{name}",
                         {key.replace("_", "-"): _number(value) for key, value in attrs.items()})


def _number(value):
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def _text(parent, element_id, text, *, x, y, width, font_px, family, fill, weight=None):
    """One source text, wrapped; returns the height it used."""

    line_height = font_px * _LINE_HEIGHT
    slices = line_slices(text, max(1.0, width / font_px))
    attrs = dict(id=element_id, x=x, y=y + font_px, font_family=family, font_size=font_px, fill=fill)
    if weight:
        attrs["font_weight"] = weight
    node = _node(parent, "text", **attrs)
    first_start, first_end = slices[0]
    node.text = text[first_start:first_end]
    for line, (start, end) in enumerate(slices[1:], 1):
        tspan = _node(node, "tspan", x=x, y=y + font_px + line * line_height)
        tspan.text = text[start:end]
    return len(slices) * line_height


def _text_height(text, width, font_px):
    return len(line_slices(text, max(1.0, width / font_px))) * font_px * _LINE_HEIGHT


def _table_grid(block, width, font_px):
    """``(column widths, row heights)`` that hold every cell's wrapped text."""
    from services.svg_source_layout import _cells

    rows, cells = _cells(block)
    columns = len(rows[0])
    weights = block.payload.get("columnWidthsEmu")
    if (not isinstance(weights, list) or len(weights) != columns
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) or item <= 0 for item in weights)):
        weights = [1] * columns
    total = float(sum(weights))
    widths = [width * item / total for item in weights]
    line_height = font_px * _LINE_HEIGHT
    minimum = 2 * _CELL_PADDING_PX + line_height
    heights = [minimum] * len(rows)
    spanned = []
    for row, column, row_span, column_span, cell in cells:
        cell_width = sum(widths[column:column + column_span]) - 2 * _CELL_PADDING_PX
        need = 2 * _CELL_PADDING_PX + _text_height(cell["text"], max(1.0, cell_width), font_px)
        if row_span == 1:
            heights[row] = max(heights[row], need)
        else:
            spanned.append((row, row_span, need))
    for row, row_span, need in spanned:
        deficit = need - sum(heights[row:row + row_span])
        if deficit > 0:
            heights[row + row_span - 1] += deficit
    return widths, heights, cells


def _table(parent, fact, block, *, x, y, width, font_px, family, background):
    widths, heights, cells = _table_grid(block, width, font_px)
    xs = [x]
    for item in widths:
        xs.append(xs[-1] + item)
    ys = [y]
    for item in heights:
        ys.append(ys[-1] + item)
    group = _node(parent, "g", id=fact["element_id"])
    ink = _ink(background)
    header_fill = _mix(background, ink, _TABLE_HEADER_MIX)
    stroke = _mix(background, ink, _TABLE_STROKE_MIX)
    for row, column, row_span, column_span, _cell in cells:
        _node(group, "rect", x=xs[column], y=ys[row], width=xs[column + column_span] - xs[column],
              height=ys[row + row_span] - ys[row], fill=header_fill if row == 0 else background,
              stroke=stroke, stroke_width=1)
    for (row, column, _row_span, column_span, cell), cell_fact in zip(cells, fact["cells"], strict=True):
        cell_width = xs[column + column_span] - xs[column] - 2 * _CELL_PADDING_PX
        _text(group, cell_fact["element_id"], cell["text"], x=xs[column] + _CELL_PADDING_PX,
              y=ys[row] + _CELL_PADDING_PX, width=max(1.0, cell_width), font_px=font_px, family=family,
              fill=_ink(header_fill if row == 0 else background), weight="700" if row == 0 else None)
    return ys[-1] - y


def _image_size(block, width, max_height):
    geometry = block.payload.get("geometry") or {}
    cx, cy = geometry.get("cx"), geometry.get("cy")
    aspect = cx / cy if isinstance(cx, (int, float)) and isinstance(cy, (int, float)) and cx > 0 and cy > 0 else 4 / 3
    image_width = min(width, max_height * aspect)
    return max(1, int(image_width)), max(1, int(image_width / aspect))


def _shape(parent, fact, block, *, scale_x, scale_y, fallback_box):
    from services.source_content.pptx_custom_geometry import flatten_custom_geometry

    geometry = block.payload.get("geometry") or {}
    values = [geometry.get(key) for key in ("x", "y", "cx", "cy")]
    if all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values):
        x, y, w, h = values[0] * scale_x, values[1] * scale_y, values[2] * scale_x, values[3] * scale_y
    else:
        x, y, w, h = fallback_box.left, fallback_box.top, 8.0, 8.0
    preset = block.payload.get("preset")
    custom = block.payload.get("customGeometry")
    element_id = fact["element_id"]
    if custom and w > 0 and h > 0:
        _node(parent, "path", id=element_id, d=flatten_custom_geometry(custom, x=x, y=y, width=w, height=h),
              fill="none", stroke=_SHAPE_STROKE, stroke_width=1)
    elif preset in {"line", "straightConnector1"} or block.semantic_role == "connector" or w <= 0 or h <= 0:
        x1, x2 = (x + w, x) if geometry.get("flipH") else (x, x + w)
        y1, y2 = (y + h, y) if geometry.get("flipV") else (y, y + h)
        _node(parent, "line", id=element_id, x1=x1, y1=y1, x2=x2, y2=y2, stroke=_SHAPE_STROKE, stroke_width=1)
    elif preset == "ellipse":
        _node(parent, "ellipse", id=element_id, cx=x + w / 2, cy=y + h / 2, rx=w / 2, ry=h / 2,
              fill="none", stroke=_SHAPE_STROKE, stroke_width=1)
    else:
        _node(parent, "rect", id=element_id, x=x, y=y, width=w, height=h, rx=8 if preset == "roundRect" else 0,
              fill="none", stroke=_SHAPE_STROKE, stroke_width=1)


def _table_font(font_px):
    return max(1, round(font_px * 0.9))


def _measure(items, box, font_px, columns):
    """Whether ``items`` fit ``columns`` columns of ``box`` at ``font_px``."""

    gap = _ITEM_GAP_EM * font_px
    column_width = (box.width - (columns - 1) * 2 * font_px) / columns
    used, column = 0.0, 0
    for kind, block, _fact in items:
        if kind == "text":
            size = font_px * _TITLE_SCALE if block.semantic_role in _TITLE_ROLES else font_px
            lines = len(line_slices(block.payload["text"], max(1.0, column_width / size)))
            remaining = lines * size * _LINE_HEIGHT
            while remaining > 0:
                space = box.height - used
                if space >= remaining:
                    used += remaining + gap
                    remaining = 0
                    break
                fits = int(max(0.0, space) // (size * _LINE_HEIGHT))
                remaining -= fits * size * _LINE_HEIGHT
                column += 1
                used = 0.0
                if column >= columns:
                    return False
            continue
        if kind == "table":
            height = sum(_table_grid(block, column_width, _table_font(font_px))[1])
        else:
            height = _image_size(block, column_width, box.height * _IMAGE_MAX_SHARE)[1]
        if used and used + height > box.height:
            column += 1
            used = 0.0
            if column >= columns:
                return False
        if height > box.height:
            return False
        used += height + gap
    return True


def _choose(items, box, minimum_font_px):
    readable = [size for size in _FONT_STEPS_PX if size >= minimum_font_px]
    small = [size for size in _FONT_STEPS_PX if size < minimum_font_px]
    configurations = [(size, columns) for size in readable + small for columns in _COLUMNS]
    for font_px, columns in configurations:
        if _measure(items, box, font_px, columns):
            return font_px, columns
    return configurations[-1]


def _place(root, items, box, font_px, columns, family, background):
    gap = _ITEM_GAP_EM * font_px
    column_gap = 2 * font_px
    column_width = (box.width - (columns - 1) * column_gap) / columns
    column, y = 0, box.top

    def advance():
        nonlocal column, y
        if column + 1 < columns:
            column += 1
            y = box.top
            return True
        return False

    for kind, block, fact in items:
        left = box.left + column * (column_width + column_gap)
        if kind == "text":
            title = block.semantic_role in _TITLE_ROLES
            size = round(font_px * _TITLE_SCALE) if title else font_px
            text = block.payload["text"]
            slices = line_slices(text, max(1.0, column_width / size))
            line_height = size * _LINE_HEIGHT
            node = None
            for start, end in slices:
                if y + line_height > box.bottom and y > box.top and advance():
                    left = box.left + column * (column_width + column_gap)
                if node is None:
                    attrs = dict(id=fact["element_id"], x=left, y=y + size, font_family=family,
                                 font_size=size, fill=_ink(background))
                    if title:
                        attrs["font_weight"] = "700"
                    node = _node(root, "text", **attrs)
                    node.text = text[start:end]
                else:
                    _node(node, "tspan", x=left, y=y + size).text = text[start:end]
                y += line_height
            y += gap
            continue
        if kind == "table":
            table_font = _table_font(font_px)
            height = sum(_table_grid(block, column_width, table_font)[1])
            if y > box.top and y + height > box.bottom and advance():
                left = box.left + column * (column_width + column_gap)
            y += _table(root, fact, block, x=left, y=y, width=column_width, font_px=table_font, family=family,
                       background=background)
            y += gap
            continue
        image_width, image_height = _image_size(block, column_width, box.height * _IMAGE_MAX_SHARE)
        if y > box.top and y + image_height > box.bottom and advance():
            left = box.left + column * (column_width + column_gap)
        _node(root, "image", id=fact["element_id"], x=round(left), y=round(y), width=image_width,
              height=image_height, href=fact["slot_id"], preserveAspectRatio="xMidYMid meet")
        y = round(y) + image_height + gap


def source_page_title(page) -> str:
    """The page's title text (title-role block, else its first text), or ``""``."""

    texts = [block for block in page.blocks if block.kind == "text"
             and str(block.payload.get("text", "")).strip()]
    titled = [block for block in texts if block.semantic_role in _TITLE_ROLES]
    chosen = (titled or texts or [None])[0]
    return str(chosen.payload["text"]).strip() if chosen is not None else ""


def source_fallback_page(manifest, page_index, snapshot, *, image_slots, width, height,
                         minimum_font_px, validated_pages=None, header_page=None):
    """``(svg, source_preservation receipt)`` for the verbatim fallback of one source page.

    Raises ``ValueError`` when the page cannot be laid out verbatim here (another
    source contract, a chart) or does not bind -- never returns a partial page.
    """
    from services.generation_core.authoring.author_source_svg_layout import _whole_page_facts
    from services.svg_source_layout import bind_whole_page_source_svg, source_pages

    if snapshot.get("source_authoring_contract") != WHOLE_PAGE_CONTRACT:
        raise ValueError("svg_source_fallback_contract_unsupported")
    pages = (source_pages(manifest, chart_contract=snapshot.get("source_chart_contract"))
             if validated_pages is None else validated_pages)
    page = pages[page_index]
    if any(block.kind == "chart" for block in page.blocks):
        raise ValueError("svg_source_fallback_chart_unsupported")
    facts = {fact["source_id"]: fact for fact in _whole_page_facts(page, image_slots)}
    family = _font_family(snapshot)
    box = _content_box(snapshot, page_index, len(pages), width, height, header_page)

    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": str(width), "height": str(height),
                                           "viewBox": f"0 0 {width} {height}"})
    background = _background(snapshot)
    _node(root, "rect", x=0, y=0, width=width, height=height, fill=background)
    blocks = sorted((block for block in page.blocks if block.kind != "source_note"),
                    key=lambda block: block.order)
    shapes = [block for block in blocks if block.kind == "shape"]
    if shapes:
        size = manifest.slide_size
        extents = [block.payload.get("geometry") or {} for block in shapes]
        source_width = size.cx if size is not None else max(
            [g.get("x", 0) + g.get("cx", 0) for g in extents if isinstance(g.get("cx"), (int, float))] or [width])
        source_height = size.cy if size is not None else max(
            [g.get("y", 0) + g.get("cy", 0) for g in extents if isinstance(g.get("cy"), (int, float))] or [height])
        for block in shapes:
            _shape(root, facts[block.source_block_id], block, scale_x=width / max(1, source_width),
                   scale_y=height / max(1, source_height), fallback_box=box)
    items = [(block.kind, block, facts[block.source_block_id]) for block in blocks
             if block.kind in {"text", "table", "image"}]
    font_px, columns = _choose(items, box, minimum_font_px)
    _place(root, items, box, font_px, columns, family, background)
    svg = ET.tostring(root, encoding="unicode")
    return bind_whole_page_source_svg(svg, manifest, page_index, facts=list(facts.values()),
        width=width, height=height, style_snapshot=snapshot, minimum_font_px=minimum_font_px,
        _validated_pages=pages)


__all__ = [
    "SOURCE_FALLBACK_VERSION", "WHOLE_PAGE_CONTRACT", "line_slices", "source_fallback_page",
    "source_page_title",
]
