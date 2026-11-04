"""A template's body component vocabulary: how its chips, labels, bars, cards and tables look.

Generated template decks keep the template's frame, but the body the model designs freely looked
generic next to the source's own body pages, which draw a recognisable set of components (numbered
chips, label pills, key-message bars, outlined cards, tinted panels, short heading marks, a table
header style) at a fixed ladder of text sizes. Fixed body layouts failed repeatedly, so this module
passes only the components' *style*: fill, stroke, corner radius, text size, weight and colour.
It never records where a component sits or how many there are; the author still chooses the layout.

Measured once at registration, deterministically and without a model call, from every source page
whose layout has a body frame: the slide's own shapes (groups flattened) inside that frame's body
region. Only components that recur (at least ``MIN_COUNT`` times on ``MIN_PAGES`` pages) are kept.
The result is stored on the package (``SvgTemplatePackage.component_style``) and rendered into the
template-mode author rules by :func:`component_style_rules` when ``SVG_TEMPLATE_COMPONENT_STYLE``
is on (see ``docs/template-component-style-20261104.md``).
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
from statistics import median

from lxml import etree

from models.svg_template_frame import (
    SvgTemplateComponent, SvgTemplateComponentStyle, SvgTemplateTableStyle, SvgTemplateTextStyle,
)
from services.source_content.pptx_extraction_constants import NS
from services.source_content.pptx_typography import placeholder_of
from services.svg_templates.frame_ooxml import group_leaves, shape_bounds

MIN_COUNT = 3
MIN_PAGES = 2
#: Body region tolerance: source shapes often touch the region's edge (a key-message bar
#: drawn right under the header band starts a few px above the computed region).
_TOLERANCE_PX = 12.0
_SKIPPED_PLACEHOLDERS = {"title", "ctrTitle", "subTitle", "sldNum", "ftr", "dt", "hdr"}
_NUMBER = re.compile(r"^(?:[0-9]{1,2}|[A-Za-z]|[Ⅰ-Ⅻ]|[IVXivx]{1,4})[.)]?$")
_SHAPES = {"rect": "rect", "flowChartProcess": "rect", "roundRect": "rounded", "flowChartAlternateProcess": "rounded",
           "ellipse": "circle", "flowChartConnector": "circle", "chevron": "chevron", "homePlate": "pentagon"}
_LINES = {"line", "straightConnector1"}
_KIND_ORDER = ("number_chip", "label", "bar", "step", "card", "panel", "accent_bar", "tile", "divider")
#: Variants kept per kind: the most frequent, and a second one when it is common too.
_VARIANTS = {"number_chip": 1, "label": 2, "bar": 2, "step": 1, "card": 1, "panel": 2,
             "accent_bar": 1, "tile": 1, "divider": 1}
_ERRORS = (ValueError, KeyError, TypeError, AttributeError, IndexError, ZeroDivisionError)


def _hex(value: str) -> str:
    return "none" if value == "none" else value.upper()


def _round(value: float, step: float = .5) -> float:
    return round(round(value / step) * step, 2)


def _weight(context, family: str, bold: bool) -> int:
    """The face the deck draws with, through the explicit substitutions (no side effect)."""
    record = context.font_substitutions.get(family.casefold())
    if record is not None:
        return record.target_weight
    return 700 if bold else 400


def _runs(container):
    for paragraph in container.iterfind(".//a:p", NS):
        for run in paragraph.iterfind("a:r", NS):
            text = run.find("a:t", NS)
            if text is not None and (text.text or "").strip():
                yield run, paragraph, text.text.strip()


def _run_style(context, text_context, run, paragraph, part):
    """``(size_px, weight, colour)`` of one run, or ``None`` when it does not resolve."""
    style = text_context.run_style(run, paragraph)
    family, size = style.get("fontFamily"), style.get("sizePt")
    if not family or not size:
        return None
    color = style.get("color")
    if color is None:
        chain = [run.find("a:rPr", NS), *text_context._paragraph_chain(paragraph)]
        paint = next((p.find("a:solidFill", NS) for p in chain
                      if p is not None and p.find("a:solidFill", NS) is not None), None)
        if paint is None:
            return None
        color = context.color(paint, part)
    color = ("#" + color.lstrip("#")).upper()
    if not re.fullmatch(r"#[0-9A-F]{6}", color):
        return None
    return float(size) * 12700 / context.scale, _weight(context, family, bool(style.get("bold"))), color


def _leaves(shape, context, part):
    if etree.QName(shape).localname == "grpSp":
        # Nested groups are walked by group_leaves; a leaf that failed to map is left out.
        for leaf, bounds, failure in group_leaves(shape, context.scale):
            if failure is None and bounds is not None:
                yield leaf, bounds
        return
    try:
        yield shape, shape_bounds(shape, context.typography, part, context.scale)
    except ValueError:
        return


def _inside(bounds, region) -> bool:
    return (bounds.x >= region.x - _TOLERANCE_PX and bounds.y >= region.y - _TOLERANCE_PX
            and bounds.x + bounds.width <= region.x + region.width + _TOLERANCE_PX
            and bounds.y + bounds.height <= region.y + region.height + _TOLERANCE_PX)


def _classify(shape_name, fill, stroke, text, width, height, region_width):
    """The component kind of one painted shape, or ``None`` for plain text and unknown geometry."""
    painted = fill != "none" or stroke != "none"
    short, long = min(width, height), max(width, height)
    if shape_name == "line":
        return "divider" if stroke != "none" and long >= 40 else None
    if not painted:
        return None
    if text:
        if shape_name in {"chevron", "pentagon"}:
            return "step"
        if fill != "none" and _NUMBER.match(text) and long <= 64:
            return "number_chip"
        if width >= region_width * .55 and height <= 110:
            return "bar"
        if height <= 40 and width < region_width * .5:
            return "label"
        return None
    if fill != "none" and short <= 2.5 and long >= 40:
        return "divider"
    if fill != "none" and short <= 10 and long <= 80 and long >= 2 * short:
        return "accent_bar"
    if fill != "none" and 20 <= short and long <= 90 and long <= short * 1.25:
        return "tile"
    if width >= 80 and height >= 40:
        return "card" if stroke != "none" else "panel"
    return None


def _cell_fill(context, cell, part):
    properties = cell.find("a:tcPr", NS)
    paint = properties.find("a:solidFill", NS) if properties is not None else None
    return _hex(context.color(paint, part)) if paint is not None else "none"


def _cell_rule(context, cell, part):
    properties = cell.find("a:tcPr", NS)
    for side in ("lnB", "lnT", "lnL", "lnR"):
        line = properties.find("a:" + side, NS) if properties is not None else None
        paint = line.find("a:solidFill", NS) if line is not None else None
        if paint is not None:
            return _hex(context.color(paint, part))
    return None


def _cell_text(context, text_context, cell, part):
    for run, paragraph, _ in _runs(cell):
        return _run_style(context, text_context, run, paragraph, part)
    return None


def _table(context, shape, part):
    table = shape.find(".//a:tbl", NS)
    rows = table.findall("a:tr", NS) if table is not None else []
    if len(rows) < 2:
        return None
    # A body cell is the second cell of a row: the first column is often a shaded row header.
    def body_cell(row):
        cells = row.findall("a:tc", NS)
        return cells[1] if len(cells) > 1 else (cells[0] if cells else None)
    header = rows[0].find("a:tc", NS)
    first, second = body_cell(rows[1]), body_cell(rows[2] if len(rows) > 2 else rows[1])
    if header is None or first is None or second is None:
        return None
    text_context = context.typography.shape_context(shape, part_name=part)
    header_fill, body_fill = _cell_fill(context, header, part), _cell_fill(context, first, part)
    band = _cell_fill(context, second, part)
    lead = rows[1].find("a:tc", NS)
    lead_fill = _cell_fill(context, lead, part) if lead is not None and lead is not first else body_fill
    return (header_fill, _cell_text(context, text_context, header, part), body_fill,
            band if band != body_fill else None, lead_fill if lead_fill != body_fill else None,
            _cell_rule(context, first, part), _cell_text(context, text_context, first, part))


def _text_style(item, share=None, colors=None) -> SvgTemplateTextStyle | None:
    if item is None:
        return None
    size, weight, color = item
    return SvgTemplateTextStyle(size_px=_round(size), weight=weight, colors=tuple(colors or (color,)), share=share)


def _common_text(style, sizes: Counter) -> SvgTemplateTextStyle | None:
    """``style`` is ``(weight, colour)``; the size is the most common one."""
    if style is None or not sizes:
        return None
    size = max(sizes.items(), key=lambda item: (item[1], item[0]))[0]
    return SvgTemplateTextStyle(size_px=size, weight=style[0], colors=(style[1],))


def _ladder(characters: Counter) -> tuple[SvgTemplateTextStyle, ...]:
    """Body text sizes, largest first: sizes within 8 % of a level's largest join it."""
    total = sum(characters.values())
    if not total:
        return ()
    by_size = defaultdict(Counter)
    for (size, weight, color), count in characters.items():
        by_size[_round(size)][(weight, color)] += count
    levels, current = [], None
    for size in sorted(by_size, reverse=True):
        if current is None or current["anchor"] > size * 1.08:
            current = {"anchor": size, "sizes": Counter(), "styles": Counter()}
            levels.append(current)
        current["sizes"][size] += sum(by_size[size].values())
        current["styles"].update(by_size[size])
    result = []
    for level in levels:
        count = sum(level["sizes"].values())
        if count / total < .04:
            continue
        size = max(level["sizes"].items(), key=lambda item: (item[1], item[0]))[0]
        weights = Counter()
        colors = Counter()
        for (weight, color), value in level["styles"].items():
            weights[weight] += value
            colors[color] += value
        ranked = sorted(colors.items(), key=lambda item: (-item[1], item[0]))
        shown = [ranked[0][0]] + [color for color, value in ranked[1:2] if value >= .3 * ranked[0][1]]
        weight = max(weights.items(), key=lambda item: (item[1], -item[0]))[0]
        result.append(SvgTemplateTextStyle(size_px=size, weight=weight, colors=tuple(shown),
                                           share=round(count / total, 2)))
    return tuple(result[:5])


def extract_component_style(context, slides, regions: dict) -> SvgTemplateComponentStyle | None:
    """The recurring body components of ``slides`` (slide parts) whose layout is in ``regions``.

    ``regions`` maps a slide layout part to the body region of its body frame. A shape that does
    not resolve (unsupported paint, a rotated group, an unresolved colour) is left out; nothing
    is guessed. ``None`` when no page qualifies or nothing recurs.
    """
    characters: Counter = Counter()
    variants: dict[tuple, dict] = {}
    tables: dict[tuple, dict] = {}
    sampled = 0
    for page, part in enumerate(slides):
        region = regions.get(context.related(part, "/slideLayout"))
        if region is None:
            continue
        sampled += 1
        context.source_slide_part = part
        items = []
        root = context.root(part)
        tree = root.find("p:cSld/p:spTree", NS)
        for top in (tree if tree is not None else ()):
            local = etree.QName(top).localname
            if local in {"nvGrpSpPr", "grpSpPr", "extLst"}:
                continue
            placeholder = placeholder_of(top)
            if placeholder is not None and placeholder.type in _SKIPPED_PLACEHOLDERS:
                continue
            marker = top.find(".//p:cNvPr", NS)
            if marker is not None and marker.get("hidden", "0") in {"1", "true"}:
                continue
            for shape, bounds in _leaves(top, context, part):
                if not _inside(bounds, region):
                    continue
                try:
                    item = _measure(context, shape, bounds, part, page, tables)
                except _ERRORS:
                    continue
                if item is not None:
                    items.append(item)
        _attach_overlay_text(items)
        for item in items:
            _register(item, page, region.width, characters, variants)
    if not sampled:
        return None
    components = []
    for kind in _KIND_ORDER:
        # Spread over pages first: a component the deck uses on many pages is its vocabulary,
        # one drawn many times on a single page is that page's content.
        ranked = sorted(((key, value) for key, value in variants.items() if key[0] == kind),
                        key=lambda item: (-len(item[1]["pages"]), -item[1]["count"], repr(item[0])))
        kept = [(key, value) for key, value in ranked
                if value["count"] >= MIN_COUNT and len(value["pages"]) >= MIN_PAGES]
        for index, (key, value) in enumerate(kept[:_VARIANTS[kind]]):
            if index and len(value["pages"]) < .5 * len(kept[0][1]["pages"]):
                break
            _, shape_name, fill, stroke, stroke_width, radius, text = key
            sized = kind in {"number_chip", "tile", "accent_bar"}
            tall = sized or kind in {"label", "bar", "step"}
            components.append(SvgTemplateComponent(
                kind=kind, shape=shape_name, fill=fill, stroke=stroke, stroke_width_px=stroke_width,
                radius_px=radius, width_px=_round(median(value["widths"]), 1) if sized else None,
                height_px=_round(median(value["heights"]), 1) if tall else None,
                text=_text_style(text), count=value["count"], pages=len(value["pages"])))
    table_styles = []
    for key, value in sorted(tables.items(), key=lambda item: (-len(item[1]["pages"]), -item[1]["count"],
                                                               repr(item[0])))[:2]:
        if value["count"] < 2 or (table_styles and len(value["pages"]) < .5 * table_styles[0].pages):
            break
        header_fill, header_color, body_fill, band, lead, rule = key
        body_color = (max(value["body_styles"].items(), key=lambda item: (item[1], repr(item[0])))[0]
                      if value["body_styles"] else None)
        table_styles.append(SvgTemplateTableStyle(
            header_fill=header_fill, header_text=_common_text(header_color, value["header_sizes"]),
            body_fill=body_fill, band_fill=band, row_header_fill=lead, rule_color=rule,
            body_text=_common_text(body_color, value["body_sizes"]),
            count=value["count"], pages=len(value["pages"])))
    ladder = _ladder(characters)
    if not components and not table_styles and not ladder:
        return None
    return SvgTemplateComponentStyle(pages_sampled=sampled, type_ladder=ladder,
                                     components=tuple(components), tables=tuple(table_styles))


def _measure(context, shape, bounds, part, page, tables):
    """One body shape as an item to classify (tables are recorded directly), or ``None``."""
    local = etree.QName(shape).localname
    if local == "graphicFrame":
        measured = _table(context, shape, part)
        if measured is not None:
            # Tables are told apart by colours and weights; their text sizes are pooled.
            header_fill, header_text, body_fill, band, lead, rule, body_text = measured
            key = (header_fill, header_text and header_text[1:], body_fill, band, lead, rule)
            entry = tables.setdefault(key, {"count": 0, "pages": set(), "header_sizes": Counter(),
                                            "body_sizes": Counter(), "body_styles": Counter()})
            entry["count"] += 1
            entry["pages"].add(page)
            for name, text in (("header_sizes", header_text), ("body_sizes", body_text)):
                if text is not None:
                    entry[name][_round(text[0])] += 1
            if body_text is not None:
                entry["body_styles"][body_text[1:]] += 1
        return None
    if local not in {"sp", "cxnSp"}:
        return None
    geometry = shape.find("p:spPr/a:prstGeom", NS)
    preset = geometry.get("prst") if geometry is not None else None
    shape_name = "line" if local == "cxnSp" or preset in _LINES else _SHAPES.get(preset)
    fill = "none" if shape_name == "line" else _hex(context.fill(shape, part)[0])
    stroke, line = context.fill(shape, part, "line")
    stroke = _hex(stroke)
    text_context = context.typography.shape_context(shape, part_name=part)
    styles = []
    for run, paragraph, value in _runs(shape):
        style = _run_style(context, text_context, run, paragraph, part)
        if style is not None:
            styles.append((style, len(value)))
    item = {"shape_name": shape_name, "fill": fill, "stroke": stroke, "bounds": bounds,
            "text": "".join(value[2] for value in _runs(shape)).strip(), "styles": styles,
            "stroke_width": 0.0, "radius": 0.0, "overlay": False}
    if shape_name is None:
        return item  # unknown geometry: its text still counts for the ladder when unpainted
    if shape_name == "line":
        ends = [line.find("a:" + name, NS) for name in ("headEnd", "tailEnd")] if line is not None else []
        if any(end is not None and end.get("type", "none") != "none" for end in ends):
            return None  # an arrow: a connector, not a divider
    if stroke != "none" and line is not None:
        item["stroke_width"] = max(.5, _round(float(line.get("w", "12700")) / context.scale, .5))
    if shape_name == "rounded":
        guide = geometry.find("a:avLst/a:gd[@name='adj']", NS)
        formula = guide.get("fmla", "val 16667") if guide is not None else "val 16667"
        share = min(50000, max(0, int(formula[4:]))) / 100000 if formula.startswith("val ") else 16667 / 100000
        item["radius"] = min(bounds.width, bounds.height) * share
        if share >= .45:
            item["shape_name"] = "pill"
    elif shape_name == "circle":
        item["radius"] = min(bounds.width, bounds.height) / 2
    return item


def _painted(item) -> bool:
    return item["fill"] != "none" or item["stroke"] != "none"


def _attach_overlay_text(items) -> None:
    """A text box drawn over a short painted shape is that shape's text (a bar or a chip whose
    words sit in a separate box). Its text then describes the component, not the ladder."""
    for index, item in enumerate(items):
        if (not _painted(item) or item["text"] or item["shape_name"] in {None, "line"}
                or item["bounds"].height > 110):
            continue
        box = item["bounds"]
        for other in items[index + 1:]:
            if _painted(other) or not other["styles"] or other["overlay"]:
                continue
            inner = other["bounds"]
            cx, cy = inner.x + inner.width / 2, inner.y + inner.height / 2
            if (box.x <= cx <= box.x + box.width and box.y <= cy <= box.y + box.height
                    and inner.height <= box.height + 2):
                item["text"], item["styles"] = other["text"], other["styles"]
                other["overlay"] = True
                break


def _register(item, page, region_width, characters, variants) -> None:
    if not _painted(item) and not item["overlay"]:
        # The ladder is the plain body text; text on a painted shape belongs to its component.
        for style, length in item["styles"]:
            characters[style] += length
    if item["shape_name"] is None or item["overlay"]:
        return
    bounds = item["bounds"]
    kind = _classify(item["shape_name"], item["fill"], item["stroke"], item["text"],
                     bounds.width, bounds.height, region_width)
    if kind is None:
        return
    shape_name, fill, stroke, stroke_width = item["shape_name"], item["fill"], item["stroke"], item["stroke_width"]
    if kind == "divider":
        shape_name, fill = "line", "none"
        if stroke == "none":  # a hairline rectangle
            stroke, stroke_width = item["fill"], _round(min(bounds.width, bounds.height), .5)
    first = item["styles"][0][0] if item["text"] and item["styles"] else None
    text_key = (_round(first[0]), first[1], first[2]) if first else None
    key = (kind, shape_name, fill, stroke, stroke_width, _round(item["radius"], 1), text_key)
    entry = variants.setdefault(key, {"count": 0, "pages": set(), "widths": [], "heights": []})
    entry["count"] += 1
    entry["pages"].add(page)
    entry["widths"].append(bounds.width)
    entry["heights"].append(bounds.height)


def _paint(fill: str, stroke: str, width: float) -> str:
    parts = ["fill " + fill if fill != "none" else "no fill"]
    parts.append(f"stroke {stroke} {width:g}px" if stroke != "none" else "no stroke")
    return ", ".join(parts)


def _text(style: dict) -> str:
    return f"{style['size_px']:g}px weight {style['weight']} {' or '.join(style['colors'])}"


def _geometry(item: dict) -> str:
    name = item["shape"]
    if name == "rounded":
        return f"rounded rectangle, corner radius {item['radius_px']:g}px"
    if name == "pill":
        return "pill (fully rounded ends)"
    if name == "rect":
        return "rectangle, square corners"
    return {"circle": "circle", "chevron": "chevron", "pentagon": "pentagon arrow", "line": "line"}[name]


def _size(item: dict) -> str:
    if item.get("width_px") and item.get("height_px"):
        return f", about {item['width_px']:g}x{item['height_px']:g}px"
    if item.get("height_px"):
        return f", about {item['height_px']:g}px high"
    return ""


_LABELS = {
    "number_chip": "Number chip (a step, item or rank number)",
    "label": "Label or tag",
    "bar": "Title bar or key-message bar across the content width",
    "step": "Step shape in a sequence",
    "card": "Outlined card",
    "panel": "Tinted panel (a background area that groups content)",
    "accent_bar": "Short accent mark beside a sub-heading",
    "tile": "Tile or disc behind an icon or a short mark",
    "divider": "Divider line",
}


def _raised(text: dict | None, floor: float) -> tuple[dict | None, float]:
    """``text`` at no less than ``floor`` px, and the factor its size grew by."""
    if not text or text["size_px"] >= floor:
        return text, 1.0
    raised = math.ceil(floor * 2 - 1e-9) / 2
    return {**text, "size_px": raised}, raised / text["size_px"]


def _ladder_at_floor(levels: list[dict], floor: float) -> list[dict]:
    """Levels below the floor are drawn at it, so they merge into one level there; the merged
    level keeps the weight and colours of its most used member."""
    result = []
    for level in levels:
        level, _ = _raised(level, floor)
        if result and result[-1]["size_px"] == level["size_px"]:
            previous = result[-1]
            main, other = (level, previous) if (level["share"] or 0) > (previous["share"] or 0) else (previous, level)
            colors = list(dict.fromkeys((*main["colors"], *other["colors"])))[:2]
            result[-1] = {**main, "colors": colors, "share": round((main["share"] or 0) + (other["share"] or 0), 2)}
            continue
        result.append(level)
    return result


def component_style_rules(style: dict | None, *, body_floor: float | None = None) -> str:
    """The author-rule paragraph for a stored component style; empty when there is none.

    Text the source sets below ``body_floor`` (the page's body text minimum, CSS px) is given
    at the floor, and a component around such text grows by the same factor, so the guidance
    never asks for text the readability checks reject.
    """
    if not style:
        return ""
    style = SvgTemplateComponentStyle.model_validate(style).model_dump(mode="json")
    floor = float(body_floor or 0)
    lines = []
    if style["type_ladder"]:
        levels = "; ".join(_text(level) for level in _ladder_at_floor(style["type_ladder"], floor))
        lines.append(f"- Body text sizes, largest first: {levels}. Use these sizes, weights and colours for"
                     " the matching levels (sub-heading, body, caption).")
    for item in style["components"]:
        text, factor = _raised(item.get("text"), floor)
        item = {**item, **{key: _round(item[key] * factor, 1) for key in ("width_px", "height_px") if item.get(key)}}
        words = f"; text {_text(text)}" if text else ""
        lines.append(f"- {_LABELS[item['kind']]}: {_geometry(item)}, "
                     f"{_paint(item['fill'], item['stroke'], item['stroke_width_px'])}{_size(item)}{words}.")
    for table in style["tables"]:
        parts = [f"header row fill {table['header_fill']}" if table["header_fill"] != "none" else "header row without fill"]
        if table.get("header_text"):
            parts.append(f"header text {_text(_raised(table['header_text'], floor)[0])}")
        parts.append(f"body cells fill {table['body_fill']}" if table["body_fill"] != "none" else "body cells without fill")
        if table.get("band_fill"):
            parts.append(f"alternate rows {table['band_fill']}")
        if table.get("row_header_fill"):
            parts.append(f"first column fill {table['row_header_fill']}")
        if table.get("rule_color"):
            parts.append(f"cell rules {table['rule_color']}")
        if table.get("body_text"):
            parts.append(f"cell text {_text(_raised(table['body_text'], floor)[0])}")
        lines.append("- Table: " + ", ".join(parts) + ".")
    if not lines:
        return ""
    return ("\nTEMPLATE COMPONENT STYLE (measured from this template's own body pages; style only):"
            " the layout stays free. Choose positions, sizes of areas, counts and arrangement from this page's"
            " content, and use a component only when the content calls for it; never add one to imitate the"
            " template. When you draw one of these components, give it this look:\n" + "\n".join(lines) + "\n")
