"""How a template composes its body pages: a composition grammar and exemplar outlines.

Generated template decks keep the template's frame and (with ``SVG_TEMPLATE_COMPONENT_STYLE``) its
component looks, but their bodies stayed sparse: one repeated component, 10-20 text items, large type,
the lower half empty, where the template's own body pages combine a key-message bar, two or three
numbered sections each holding a different component, 40-80 text items and little empty space.
Component looks alone do not say that. This module measures it, deterministically and without a model
call, at registration (from every source page whose layout has a body frame, as
``component_style.py`` does):

* the **grammar**: distributions over the template's content pages (sections per page, share of pages
  opening with a key-message bar or numbered sub-headings, content kinds per page, text items, the
  share of the body covered and how far down it reaches, body text sizes);
* **exemplars**: a few source body pages chosen to cover the content kinds the template draws
  (table, chart, flow, hierarchy, KPI, cards, picture, list), each kept as a structural outline:
  element kind, box relative to the body region, paint, and for a text only its size, weight, colour,
  paragraph and character counts.

**No source characters are stored.** The stored models (``SvgTemplateBodyComposition``) have no
free-text field: kinds and chart types are literals, colours are validated hex, everything else is a
number. So a template's own words (a customer's text) cannot reach a prompt through this material;
an exemplar shows what a page contains and how full it is, never what it says.

At generation (``SVG_TEMPLATE_BODY_EXEMPLARS``): :func:`composition_rules` renders the grammar into
the deck-invariant author rules, and :func:`page_exemplars` picks one or two exemplars per body page
by matching the outline page's content kinds (:func:`content_kinds`, a cheap deterministic
classifier) and renders them within a token budget. Both are guidance: the author still designs the
page freely and is told not to copy positions, counts or arrangement.
"""
from __future__ import annotations

from collections import Counter
import math
import re
from statistics import median

from lxml import etree

from models.svg_template_frame import (
    SvgTemplateBodyComposition, SvgTemplateBodyExemplar, SvgTemplateCompositionGrammar,
    SvgTemplateExemplarItem, SvgTemplateExemplarText,
)
from services.source_content.pptx_extraction_constants import NS
from services.source_content.pptx_package_parts import _relationship_map
from services.source_content.pptx_typography import placeholder_of
from services.svg_templates import component_style as style
from services.svg_templates.frame_ooxml import group_leaves, shape_bounds

#: Exemplars kept on a package, and per content kind.
MAX_EXEMPLARS = 10
_PER_KIND = 2
#: Items kept per exemplar (reading order); the rest is summarised as a count.
MAX_ITEMS = 120
#: Prompt budget per rendered exemplar, in estimated tokens (:func:`estimate_tokens`).
EXEMPLAR_TOKEN_BUDGET = 2500
#: A group with no text and at most this size is one figure (an icon drawn from several shapes).
_ICON_PX = 120.0
_ARROWS = {"rightArrow", "leftArrow", "upArrow", "downArrow", "leftRightArrow", "upDownArrow", "bentArrow",
           "uturnArrow", "notchedRightArrow", "stripedRightArrow", "curvedRightArrow", "curvedDownArrow",
           "bentUpArrow", "leftUpArrow", "circularArrow"}
_STEP_PRESETS = {"chevron", "homePlate"}
_KIND_OF = {"number_chip": "chip", "label": "label", "bar": "bar", "step": "step", "card": "card",
            "panel": "panel", "accent_bar": "mark", "tile": "tile", "divider": "divider"}
#: How much a shared content kind counts when an exemplar is matched to a page.
_KIND_WEIGHT = {"table": 3.0, "chart": 3.0, "flow": 3.0, "hierarchy": 3.0, "kpi": 2.0, "cards": 1.0,
                "picture": .5, "list": .5}
_CHARTS = {"barChart": "bar", "bar3DChart": "bar", "lineChart": "line", "line3DChart": "line",
           "pieChart": "pie", "pie3DChart": "pie", "ofPieChart": "pie", "doughnutChart": "doughnut",
           "areaChart": "area", "area3DChart": "area", "scatterChart": "scatter", "bubbleChart": "scatter",
           "radarChart": "radar"}
_HEADING_NUMBER = re.compile(r"^\s*(?:0?[1-9]|[1-9][0-9])(?:[.)]|\s)\s*\S")


# --- registration: measure the source pages ------------------------------------------------------


def _paragraphs(shape) -> int:
    return sum(1 for paragraph in shape.iterfind(".//a:p", NS)
               if any((node.text or "").strip() for node in paragraph.iterfind(".//a:t", NS)))


def _dominant(styles):
    """``(size_px, weight, colour)`` carrying the most characters."""
    if not styles:
        return None
    weights = Counter()
    for item, length in styles:
        weights[item] += length
    return max(weights.items(), key=lambda entry: (entry[1], entry[0][0]))[0]


def _text(styles, text: str, paragraphs: int) -> SvgTemplateExemplarText | None:
    dominant = _dominant(styles)
    if dominant is None or not text:
        return None
    size, weight, color = dominant
    return SvgTemplateExemplarText(size_px=style._round(size), weight=weight, color=color,
                                   paragraphs=max(1, min(200, paragraphs)), chars=max(1, min(20000, len(text))),
                                   numbered=bool(style._NUMBER.match(text)),
                                   lead_number=bool(not style._NUMBER.match(text) and _HEADING_NUMBER.match(text)))


def _arrow_line(shape) -> bool:
    line = shape.find("p:spPr/a:ln", NS)
    ends = [line.find("a:" + name, NS) for name in ("headEnd", "tailEnd")] if line is not None else []
    return any(end is not None and end.get("type", "none") != "none" for end in ends)


def _chart_type(context, shape, part) -> str:
    chart = shape.find(".//c:chart", NS)
    rid = chart.get(f"{{{NS['r']}}}id") if chart is not None else None
    target = _relationship_map(context.package, part).get(rid, (None, ""))[0] if rid else None
    if not target:
        return "other"
    plot = context.root(target).find(".//c:plotArea", NS)
    for node in plot if plot is not None else ():
        name = _CHARTS.get(etree.QName(node).localname)
        if name:
            return name
    return "other"


def _table_item(context, shape, part, box):
    table = shape.find(".//a:tbl", NS)
    rows = table.findall("a:tr", NS) if table is not None else []
    if not rows:
        return None, 0
    columns = len(table.findall("a:tblGrid/a:gridCol", NS)) or max(len(row.findall("a:tc", NS)) for row in rows)
    text_context = context.typography.shape_context(shape, part_name=part)

    def cell_styles(cells):
        styles, lengths = [], []
        for cell in cells:
            words = "".join(value for _, _, value in style._runs(cell))
            if not words:
                continue
            lengths.append(len(words))
            for run, paragraph, value in style._runs(cell):
                measured = style._run_style(context, text_context, run, paragraph, part)
                if measured is not None:
                    styles.append((measured, len(value)))
        return styles, lengths

    header_cells = rows[0].findall("a:tc", NS)
    body_cells = [cell for row in rows[1:] for cell in row.findall("a:tc", NS)]
    header_styles, header_lengths = cell_styles(header_cells)
    body_styles, body_lengths = cell_styles(body_cells)
    header_fill = style._cell_fill(context, header_cells[0], part) if header_cells else "none"

    def text_of(styles, lengths):
        dominant = _dominant(styles)
        if dominant is None or not lengths:
            return None
        return SvgTemplateExemplarText(size_px=style._round(dominant[0]), weight=dominant[1], color=dominant[2],
                                       paragraphs=1, chars=max(1, round(median(lengths))))

    item = dict(kind="table", box=box, rows=min(200, len(rows)), cols=max(1, min(60, columns)),
                header_fill=None if header_fill == "none" else header_fill,
                header_text=text_of(header_styles, header_lengths), text=text_of(body_styles, body_lengths))
    return item, len(header_lengths) + len(body_lengths)


def _sp_item(context, shape, part, bounds):
    """A shape or connector as an outline item (``None`` for an invisible one)."""
    local = etree.QName(shape).localname
    geometry = shape.find("p:spPr/a:prstGeom", NS)
    preset = geometry.get("prst") if geometry is not None else None
    if (local == "cxnSp" or preset in style._LINES) and _arrow_line(shape):
        return {"kind": "arrow", "box": (bounds.x, bounds.y, bounds.width, bounds.height)}
    measured = style._measure(context, shape, bounds, part, 0, {})
    if measured is None:
        return None
    measured["paragraphs"] = _paragraphs(shape)
    measured["preset"] = preset
    measured["custom"] = geometry is None and shape.find("p:spPr/a:custGeom", NS) is not None
    return measured


def _top_items(context, top, part, region, page_tables):
    """Outline items of one top-level shape tree element (groups flattened or merged)."""
    local = etree.QName(top).localname
    if local == "grpSp":
        leaves = [(leaf, bounds) for leaf, bounds, failure in group_leaves(top, context.scale)
                  if failure is None and bounds is not None and style._inside(bounds, region)]
        if not leaves:
            return []
        if not any(any(True for _ in style._runs(leaf)) for leaf, _ in leaves) and len(leaves) >= 2:
            x0 = min(b.x for _, b in leaves)
            y0 = min(b.y for _, b in leaves)
            x1 = max(b.x + b.width for _, b in leaves)
            y1 = max(b.y + b.height for _, b in leaves)
            if x1 - x0 <= _ICON_PX and y1 - y0 <= _ICON_PX:
                return [{"kind": "icon", "box": (x0, y0, x1 - x0, y1 - y0)}]
        result = []
        for leaf, bounds in leaves:
            result.extend(_leaf_items(context, leaf, part, bounds, region, page_tables))
        return result
    try:
        bounds = shape_bounds(top, context.typography, part, context.scale)
    except ValueError:
        return []
    if not style._inside(bounds, region):
        return []
    return _leaf_items(context, top, part, bounds, region, page_tables)


def _leaf_items(context, shape, part, bounds, region, page_tables):
    local = etree.QName(shape).localname
    box = (bounds.x, bounds.y, bounds.width, bounds.height)
    if local == "graphicFrame":
        data = shape.find(".//a:graphicData", NS)
        uri = (data.get("uri") or "") if data is not None else ""
        if uri.endswith("/table"):
            item, cells = _table_item(context, shape, part, box)
            if item is not None:
                page_tables.append(cells)
                return [item]
            return []
        if uri.endswith("/chart"):
            return [{"kind": "chart", "box": box, "chart": _chart_type(context, shape, part)}]
        return [{"kind": "shape", "box": box}]
    if local == "pic":
        small = bounds.width <= 72 and bounds.height <= 72
        return [{"kind": "icon" if small else "picture", "box": box}]
    if local not in {"sp", "cxnSp"}:
        return []
    item = _sp_item(context, shape, part, bounds)
    return [item] if item is not None else []


def _finish(raw, region_width):
    """Classify measured shapes into outline items (after overlay text was attached)."""
    items = []
    for item in raw:
        if "shape_name" not in item:
            items.append(item)
            continue
        if item.get("overlay"):
            continue
        x, y, w, h = item["box"] if "box" in item else (item["bounds"].x, item["bounds"].y,
                                                        item["bounds"].width, item["bounds"].height)
        text = _text(item["styles"], item["text"], item.get("paragraphs") or 1)
        painted = style._painted(item)
        fill = None if item["fill"] == "none" else item["fill"]
        stroke = None if item["stroke"] == "none" else item["stroke"]
        preset = item.get("preset")
        if preset in _ARROWS:
            kind = "arrow"
        elif preset in _STEP_PRESETS and (text is not None or painted):
            kind = "step"
        else:
            kind = None
            if item["shape_name"] is not None:
                found = style._classify(item["shape_name"], item["fill"], item["stroke"], item["text"], w, h,
                                        region_width)
                kind = _KIND_OF.get(found)
                if item["shape_name"] == "line" and kind is None:
                    kind = "line" if stroke else None
            if kind is None:
                if text is not None:
                    kind = ("card" if stroke else "panel") if painted else "text"
                elif painted and max(w, h) >= 6:
                    kind = "line" if min(w, h) <= 3 else "shape"
        if kind is None:
            continue
        items.append({"kind": kind, "box": (x, y, w, h), "fill": fill if kind not in {"line", "divider", "arrow"} else None,
                      "stroke": stroke, "text": text})
    return _merge_small_shapes(items)


def _merge_small_shapes(items):
    """Small untexted free shapes that touch are one figure (an illustration of many paths)."""
    small = [index for index, item in enumerate(items)
             if item["kind"] == "shape" and item["box"][2] <= _ICON_PX and item["box"][3] <= _ICON_PX]
    if len(small) < 2:
        return items
    parent = {index: index for index in small}

    def find(index):
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for position, first in enumerate(small):
        ax, ay, aw, ah = items[first]["box"]
        for second in small[position + 1:]:
            bx, by, bw, bh = items[second]["box"]
            if ax - 4 <= bx + bw and bx - 4 <= ax + aw and ay - 4 <= by + bh and by - 4 <= ay + ah:
                parent[find(first)] = find(second)
    clusters: dict[int, list[int]] = {}
    for index in small:
        clusters.setdefault(find(index), []).append(index)
    merged, dropped = [], set(small)
    for members in clusters.values():
        x0 = min(items[i]["box"][0] for i in members)
        y0 = min(items[i]["box"][1] for i in members)
        x1 = max(items[i]["box"][0] + items[i]["box"][2] for i in members)
        y1 = max(items[i]["box"][1] + items[i]["box"][3] for i in members)
        kind = "icon" if len(members) > 1 and x1 - x0 <= _ICON_PX * 1.5 and y1 - y0 <= _ICON_PX * 1.5 else "shape"
        merged.append((min(members), {"kind": kind, "box": (x0, y0, x1 - x0, y1 - y0), "fill": None,
                                      "stroke": None, "text": None}))
    result = [item for index, item in enumerate(items) if index not in dropped]
    for _, item in sorted(merged, key=lambda entry: entry[0]):
        result.append(item)
    return result


def _page(context, part, region):
    """``(items, table_text_cells)`` of one source page's body, or ``None``."""
    context.source_slide_part = part
    tree = context.root(part).find("p:cSld/p:spTree", NS)
    raw, tables = [], []
    for top in (tree if tree is not None else ()):
        local = etree.QName(top).localname
        if local in {"nvGrpSpPr", "grpSpPr", "extLst"}:
            continue
        placeholder = placeholder_of(top)
        if placeholder is not None and placeholder.type in style._SKIPPED_PLACEHOLDERS:
            continue
        marker = top.find(".//p:cNvPr", NS)
        if marker is not None and marker.get("hidden", "0") in {"1", "true"}:
            continue
        try:
            raw.extend(_top_items(context, top, part, region, tables))
        except style._ERRORS:
            continue
    measured = [item for item in raw if "shape_name" in item]
    style._attach_overlay_text(measured)
    for item in measured:
        item["box"] = (item["bounds"].x, item["bounds"].y, item["bounds"].width, item["bounds"].height)
    return _finish(raw, region.width), sum(tables)


def _relative(item, region) -> SvgTemplateExemplarItem:
    """The item's box relative to the body region, clipped to it (sources overhang it by a few px)."""
    x, y, w, h = item["box"]
    left, top = max(0.0, x - region.x), max(0.0, y - region.y)
    right = min(region.width, x - region.x + w)
    bottom = min(region.height, y - region.y + h)
    return SvgTemplateExemplarItem(
        kind=item["kind"], x=round(left), y=round(top), w=max(0, round(right - left)), h=max(0, round(bottom - top)),
        fill=item.get("fill"), stroke=item.get("stroke"), text=item.get("text"),
        rows=item.get("rows"), cols=item.get("cols"), header_fill=item.get("header_fill"),
        header_text=item.get("header_text"), chart=item.get("chart"))


def _heads(items, texts, usual: float) -> list[tuple[int, int]]:
    """Numbered sub-headings ``(x, y)``: a number (chip or bare number text) with heading words right
    beside it on its row, or a heading text that starts with a number ("01 ...") set bold or larger
    than the body text. A bare number with no words beside it (a chart value, a step's digit) is not
    a heading."""
    heads = []
    for item in items:
        number = item.kind == "chip" or (item.kind == "text" and item.text and item.text.numbered)
        if number:
            middle = item.y + item.h / 2
            if any(other is not item and not other.text.numbered and other.text.chars >= 4
                   and item.x + item.w - 6 <= other.x <= item.x + item.w + 80
                   and abs(other.y + other.h / 2 - middle) <= max(item.h, 12) for other in texts):
                heads.append((item.x, item.y))
        elif (item.kind == "text" and item.text and item.text.lead_number
              and (item.text.weight >= 600 or (usual and item.text.size_px >= usual * 1.1))):
            heads.append((item.x, item.y))
    return heads


def _section_heads(heads, height: float) -> int:
    """Sections started by numbered heads ``(x, y)``.

    Two heads on one row are side-by-side sections, three or more the numbers of a sequence (one
    section). Heads in one column count apart only when at least 12 % of the body height separates
    them; closer ones number the items of a list (one section).
    """
    rows = Counter(round(y / 12) for _, y in heads)
    standalone = [(x, y) for x, y in heads if rows[round(y / 12)] <= 2]
    sections = sum(1 for count in rows.values() if count > 2)
    columns: dict[int, list[float]] = {}
    for x, y in standalone:
        columns.setdefault(round(x / 16), []).append(y)
    for ys in columns.values():
        ys.sort()
        sections += 1 + sum(1 for above, below in zip(ys, ys[1:], strict=False) if below - above >= .12 * height)
    return sections


def page_features(items, table_cells: int, width: float, height: float) -> dict:
    """What one body page contains (``items`` relative to its body region)."""
    texts = [item for item in items if item.text is not None and item.kind != "table"]
    text_items = len(texts) + table_cells
    counts = Counter(item.kind for item in items)
    sizes = [item.text.size_px for item in texts if not item.text.numbered]
    usual = median(sizes) if sizes else 0
    kinds = []
    if counts["table"]:
        kinds.append("table")
    if counts["chart"]:
        kinds.append("chart")
    if counts["step"] >= 2 or counts["arrow"] >= 2:
        kinds.append("flow")
    boxes = [item for item in items if item.kind in {"card", "panel", "label", "chip", "tile"} and item.text]
    short_lines = [item for item in items if item.kind == "line" and max(item.w, item.h) < width * .5]
    if len(short_lines) >= 3 and len(boxes) >= 4:
        kinds.append("hierarchy")
    figures = [item for item in texts if item.text.chars <= 12 and usual and item.text.size_px >= usual * 1.35
               and not item.text.numbered]
    if len(figures) >= 2:
        kinds.append("kpi")
    containers = [item for item in items if item.kind in {"card", "panel"} and item.w >= 80 and item.h >= 40]
    if len(containers) >= 3:
        kinds.append("cards")
    if any(item.kind == "picture" and item.w >= 120 and item.h >= 80 for item in items):
        kinds.append("picture")
    if not kinds:
        kinds.append("list")
    numbered = _section_heads(_heads(items, texts, usual), height)
    marks = sorted({round(item.y / 12) for item in items if item.kind == "mark"})
    sections = numbered if numbered >= 2 else (len(marks) if len(marks) >= 2 else 1)
    key_bar = any(item.kind == "bar" and item.y <= height * .15 for item in items)
    # Ink: text, tables, charts, figures and step shapes (container interiors are not ink).
    columns, rows = 48, 32
    covered = set()
    for item in items:
        if item.kind not in {"text", "table", "chart", "picture", "icon", "step", "chip", "bar", "label", "tile",
                             "shape", "arrow"} and item.text is None:
            continue
        x0 = max(0, int(item.x / width * columns))
        x1 = min(columns, math.ceil((item.x + item.w) / width * columns))
        y0 = max(0, int(item.y / height * rows))
        y1 = min(rows, math.ceil((item.y + item.h) / height * rows))
        covered.update((cx, cy) for cx in range(x0, x1) for cy in range(y0, y1))
    bottom = max((item.y + item.h for item in items), default=0) / height if height else 0
    return {"text_items": text_items, "kinds": tuple(kinds), "sections": min(20, sections),
            "numbered": numbered >= 2, "key_bar": key_bar, "ink": len(covered) / (columns * rows),
            "bottom": max(0.0, min(1.5, bottom))}


def _quartiles(values):
    ordered = sorted(values)
    if not ordered:
        return (0, 0, 0)

    def at(share):
        return ordered[min(len(ordered) - 1, int(round(share * (len(ordered) - 1))))]
    return (int(round(at(.25))), int(round(median(ordered))), int(round(at(.75))))


def _weighted_sizes(pages):
    sizes = []
    for items in pages:
        for item in items:
            if item.kind == "text" and item.text and not item.text.numbered:
                sizes.append((item.text.size_px, item.text.chars))
            if item.kind == "table" and item.text:
                sizes.append((item.text.size_px, item.text.chars * max(1, (item.rows or 2) - 1) * (item.cols or 1)))
    if not sizes:
        return (0.0, 0.0, 0.0)
    sizes.sort()
    total = sum(weight for _, weight in sizes)

    def at(share):
        running = 0
        for size, weight in sizes:
            running += weight
            if running >= share * total:
                return size
        return sizes[-1][0]
    return (at(.9), at(.5), at(.1))


def _signature(features, items) -> tuple:
    return (features["kinds"], features["sections"], features["text_items"] // 4,
            tuple(sorted(Counter(item.kind for item in items).items())))


def extract_body_composition(context, slides, regions: dict) -> SvgTemplateBodyComposition | None:
    """The composition grammar and exemplar outlines of ``slides`` whose layout is in ``regions``.

    ``regions`` maps a slide layout part to its body frame's region (as for
    :func:`component_style.extract_component_style`). ``None`` when no page has body content.
    """
    pages = []
    for index, part in enumerate(slides):
        region = regions.get(context.related(part, "/slideLayout"))
        if region is None or region.width <= 0 or region.height <= 0:
            continue
        try:
            raw, table_cells = _page(context, part, region)
        except style._ERRORS:
            continue
        items = sorted((_relative(item, region) for item in raw), key=lambda item: (item.y, item.x, item.kind))
        if not items:
            continue
        pages.append((index, region, items, page_features(items, table_cells, region.width, region.height)))
    if not pages:
        return None
    typical = median(features["text_items"] for *_, features in pages)
    content = [page for page in pages if page[3]["text_items"] >= max(4, .3 * typical)] or pages
    shares = Counter(kind for *_, features in content for kind in features["kinds"])
    count = len(content)
    grammar = SvgTemplateCompositionGrammar(
        pages_sampled=count,
        key_message_bar=round(sum(f["key_bar"] for *_, f in content) / count, 2),
        numbered_headings=round(sum(f["numbered"] for *_, f in content) / count, 2),
        sections=_quartiles([f["sections"] for *_, f in content]),
        content_kinds_per_page=_quartiles([len([k for k in f["kinds"] if k != "list"]) for *_, f in content]),
        text_items=_quartiles([f["text_items"] for *_, f in content]),
        ink_coverage=round(median(f["ink"] for *_, f in content), 2),
        bottom_reach=round(median(f["bottom"] for *_, f in content), 2),
        kind_shares=tuple((kind, round(value / count, 2)) for kind, value in
                          sorted(shares.items(), key=lambda entry: (-entry[1], entry[0]))),
        text_sizes=_weighted_sizes([items for _, _, items, _ in content]),
    )
    return SvgTemplateBodyComposition(grammar=grammar, exemplars=_choose_exemplars(content, grammar))


#: Content kinds that name a page's main components; cards, pictures and lists accompany them.
_MAJOR = ("table", "chart", "flow", "hierarchy", "kpi")


def _combination(kinds) -> tuple[str, ...]:
    major = tuple(kind for kind in _MAJOR if kind in kinds)
    return major or (("cards",) if "cards" in kinds else ("list",))


def _choose_exemplars(pages, grammar) -> tuple[SvgTemplateBodyExemplar, ...]:
    """One page for each combination of main content kinds the template draws, most frequent first.

    A combination is the set of main kinds on a page (table, chart, flow, hierarchy, KPI), or cards
    or list when it has none. Its representative: a page that fits the token budget uncut, then the
    text amount nearest the template's upper quartile (dense but typical), then more sections, then
    source order. A combination seen on one page only is taken after the recurring ones; a content
    kind on at least 5 % of the pages but in no chosen page gets its best page last. Near-identical
    pages count once.
    """
    upper = grammar.text_items[2]
    candidates = []
    for index, region, items, features in pages:
        exemplar = SvgTemplateBodyExemplar(
            source_page=index, region_width=max(1, round(region.width)), region_height=max(1, round(region.height)),
            kinds=features["kinds"], sections=features["sections"], text_items=features["text_items"],
            items=tuple(items[:MAX_ITEMS]), region_x=round(region.x), region_y=round(region.y))
        fits = len(items) <= MAX_ITEMS and estimate_tokens(render_exemplar(exemplar)) <= EXEMPLAR_TOKEN_BUDGET
        candidates.append((exemplar, fits, _signature(features, items)))

    def rank(entry):
        exemplar, fits, _ = entry
        return (not fits, abs(exemplar.text_items - upper), -exemplar.sections, exemplar.source_page)

    frequency = Counter(_combination(exemplar.kinds) for exemplar, _, _ in candidates)
    order = sorted(frequency, key=lambda combo: (frequency[combo] < 2, -frequency[combo], -len(combo), combo))
    chosen, signatures = [], set()

    def take(pool):
        for exemplar, _fits, signature in sorted(pool, key=rank):
            if signature in signatures or any(item.source_page == exemplar.source_page for item in chosen):
                continue
            chosen.append(exemplar)
            signatures.add(signature)
            return
    for combo in order:
        if len(chosen) >= MAX_EXEMPLARS:
            break
        take([entry for entry in candidates if _combination(entry[0].kinds) == combo])
    for kind, share in grammar.kind_shares:
        if len(chosen) >= MAX_EXEMPLARS:
            break
        if share >= .05 and not any(kind in item.kinds for item in chosen):
            take([entry for entry in candidates if kind in entry[0].kinds])
    return tuple(sorted(chosen, key=lambda item: item.source_page))


# --- generation: classify a page, pick and render exemplars -------------------------------------


_TABLE_LINE = re.compile(r"^\s*\|.*\|\s*$")
#: A figure with a unit, a decimal or a thousands separator (a bare year or count is not a data point).
_FIGURE = re.compile(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?\s*(%|％|퍼센트|억\s*원|억|만\s*원|만|천|원|건|명|배|점|시간|개월|"
                     r"[kKmMgGtT][bB]|[kK]?[wW]h|MW|ms|pt|bps|ppm)(?![A-Za-z])")
_PLAIN_FIGURE = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+\.\d+")
_PERIOD = re.compile(r"(?:\d{1,2}\s*월(?!\s*\d{1,2}\s*일)|[1-4]\s*분기|\bQ[1-4]\b|\bH[12]\b|FY\s?\d{2,4}|"
                     r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b)", re.IGNORECASE)
_KPI_WORDS = re.compile(r"(목표|현재|달성|개선|감소|증가|단축|KPI|SLA|지표|성과|\btarget\b|\bbaseline\b|\bKPI)",
                        re.IGNORECASE)
_FLOW_WORDS = re.compile(r"(단계|절차|프로세스|순서|로드맵|착수|→|->|⇒|▶|\bsteps?\b|\bphases?\b|\bprocess\b|"
                         r"\bstages?\b|\broadmap\b|\bworkflow\b|\bmilestones?\b)", re.IGNORECASE)
_HIERARCHY_WORDS = re.compile(r"(조직|계층|레이어|아키텍처|구성도|본부|팀장|PMO|\bPM\b|R&R|역할|"
                              r"\borgani[sz]ation\b|\bhierarchy\b|\barchitecture\b|\blayers?\b|\broles?\b)",
                              re.IGNORECASE)
_LIST_LINE = re.compile(r"^\s*(?:[-*•·▪◦]|\d{1,2}[.)])\s+\S")
_NUMBERED_LINE = re.compile(r"^\s*\d{1,2}[.)]\s+\S")
_HEADING_LINE = re.compile(r"^\s*(?:#{2,6}\s+\S|\*\*[^*]{2,80}\*\*\s*:?\s*$)")


def _figures(line: str) -> list[str]:
    """The data figures of a line: their units (``""`` for a unitless decimal or grouped number)."""
    found = [(match.start(), match.group(1).replace(" ", "")) for match in _FIGURE.finditer(line)]
    taken = {start for start, _ in found}
    found += [(match.start(), "") for match in _PLAIN_FIGURE.finditer(line) if match.start() not in taken]
    return [unit for _, unit in sorted(found)]


def content_kinds(content: str) -> tuple[tuple[str, ...], int]:
    """``(kinds, sections)`` of an outline page's markdown, by cheap deterministic signals.

    Tables are Markdown table rows (or tab-separated rows); a chart is a series (a line with four or
    more figures of one unit, or three or more periods with four or more figures); KPIs are three or
    more figures with units next to target or result words; flows are stage words, arrows or a
    numbered sequence; hierarchies are organisation, role and layer words; cards are three or more
    list items or sub-headings. ``sections`` counts sub-headings (at least 1).
    """
    lines = [line for line in (content or "").splitlines() if line.strip()]
    body = lines[1:] if lines and lines[0].lstrip().startswith("#") else lines
    kinds = []
    if (sum(1 for line in body if _TABLE_LINE.match(line)) >= 2
            or sum(1 for line in body if line.count("\t") >= 2) >= 2):
        kinds.append("table")
    plain = [line for line in body if not _TABLE_LINE.match(line)]
    figures = [_figures(line) for line in plain]
    series = any(max(Counter(units).values(), default=0) >= 4 for units in figures)
    periods = sum(len(_PERIOD.findall(line)) for line, units in zip(plain, figures, strict=True) if units)
    if series or (periods >= 3 and sum(len(units) for units in figures) >= 4):
        kinds.append("chart")
    text = "\n".join(body)
    with_units = sum(1 for units in figures for unit in units if unit)
    if with_units >= 3 and len(_KPI_WORDS.findall(text)) >= 2:
        kinds.append("kpi")
    if len(_FLOW_WORDS.findall(text)) >= 2 or sum(1 for line in body if _NUMBERED_LINE.match(line)) >= 3:
        kinds.append("flow")
    if len(_HIERARCHY_WORDS.findall(text)) >= 2:
        kinds.append("hierarchy")
    headings = sum(1 for line in body if _HEADING_LINE.match(line))
    if sum(1 for line in body if _LIST_LINE.match(line)) >= 3 or headings >= 2:
        kinds.append("cards")
    if not kinds:
        kinds.append("list")
    return tuple(kinds), max(1, headings)


def choose_exemplars(composition: dict | None, content: str, *, limit: int = 2) -> list[dict]:
    """One or two stored exemplars for a page, best match of the page's content kinds first.

    The second is added only when it shows a kind of the page the first does not. Deterministic:
    the same content and template give the same exemplars.
    """
    if not composition or not composition.get("exemplars"):
        return []
    wanted, sections = content_kinds(content)
    pool = list(composition["exemplars"])

    def score(exemplar, kinds):
        shared = sum(_KIND_WEIGHT[kind] for kind in kinds if kind in exemplar["kinds"])
        extra = sum(_KIND_WEIGHT[kind] for kind in exemplar["kinds"] if kind not in wanted)
        return (shared - .35 * extra - .25 * abs(exemplar["sections"] - sections), exemplar["text_items"],
                -exemplar["source_page"])

    first = max(pool, key=lambda exemplar: score(exemplar, wanted))
    result = [first]
    rest = [kind for kind in wanted if kind not in first["kinds"]]
    if limit > 1 and rest:
        others = [exemplar for exemplar in pool if exemplar is not first and any(kind in exemplar["kinds"] for kind in rest)]
        if others:
            result.append(max(others, key=lambda exemplar: score(exemplar, rest)))
    return result


def estimate_tokens(text: str) -> int:
    """A conservative token estimate for outline text (digits and hex colours split finely)."""
    return math.ceil(len(text) / 2.8)


def _size(text: dict, floor: float) -> tuple[float, int]:
    """Size shown, and the characters that fit the same box at that size."""
    size = float(text["size_px"])
    if floor and size < floor:
        raised = math.ceil(floor * 2 - 1e-9) / 2
        return raised, max(1, int(text["chars"] * (size / raised) ** 2))
    return size, int(text["chars"])


def _words(text: dict | None, floor: float, *, counts: bool = True) -> str:
    if not text:
        return ""
    size, chars = _size(text, floor)
    if text.get("numbered"):
        return f" {size:g}/{text['weight']} {text['color']} number"
    if text.get("lead_number"):
        chars = f"number+{chars}ch"
    else:
        chars = f"{chars}ch"
    amount = (f" {text['paragraphs']}p" if text.get("paragraphs", 1) > 1 else "") + f" {chars}" if counts else ""
    return f" {size:g}/{text['weight']} {text['color']}{amount}"


def _line(item: dict, floor: float) -> str:
    head = f"{item['kind']} {item['x']},{item['y']} {item['w']}x{item['h']}"
    if item["kind"] == "table":
        head += f" {item['rows']}x{item['cols']}"
        if item.get("header_fill"):
            head += f" head {item['header_fill']}"
        if item.get("header_text"):
            head += _words(item["header_text"], floor, counts=False)
        if item.get("text"):
            head += " | cells" + _words(item["text"], floor)
        return head
    if item["kind"] == "chart":
        return head + f" {item.get('chart') or 'other'}"
    paint = ""
    if item.get("fill"):
        paint += f" fill {item['fill']}"
    if item.get("stroke"):
        paint += f" stroke {item['stroke']}"
    if item.get("text"):
        return head + paint + (" |" if paint else "") + _words(item["text"], floor)
    return head + paint


def render_exemplar(exemplar, *, floor: float = 0.0, budget: int = EXEMPLAR_TOKEN_BUDGET) -> str:
    """The outline text of one exemplar, cut to ``budget`` estimated tokens.

    Text below ``floor`` (the page's body text minimum) is shown at the floor, with the characters
    that fit the same box at that size. When over budget, plain lines and dividers go first, then
    the last items (summarised as a count).
    """
    data = exemplar.model_dump(mode="json") if hasattr(exemplar, "model_dump") else exemplar
    items = list(data["items"])
    kinds = ", ".join(data["kinds"])
    text_items = data["text_items"]
    header = (f"body {data['region_width']}x{data['region_height']}px; content: {kinds}; "
              f"{data['sections']} section{'s' if data['sections'] != 1 else ''}; {text_items} text items")
    lines = [_line(item, floor) for item in items]

    def joined(rows, omitted=0):
        return header + "\n" + "\n".join(rows) + (f"\n(+{omitted} more items)" if omitted else "")

    text = joined(lines)
    if estimate_tokens(text) <= budget:
        return text
    kept = [line for item, line in zip(items, lines, strict=True) if item["kind"] not in {"line", "divider"}]
    omitted = len(lines) - len(kept)
    while kept and estimate_tokens(joined(kept, omitted)) > budget:
        kept.pop()
        omitted += 1
    return joined(kept, omitted)


def page_exemplars(composition: dict | None, content: str, *, floor: float = 0.0) -> list[str]:
    """Rendered exemplars for one body page (empty when the template has none)."""
    return [render_exemplar(exemplar, floor=floor) for exemplar in choose_exemplars(composition, content)]


def _percent(value: float) -> str:
    return f"{round(value * 100):d}%"


#: The density line's closing advice. ``SVG_TEMPLATE_VISUAL_GUIDANCE`` replaces "enlarge the
#: components": measured 2026-11-05 on template A, drafts read it as larger type (figures of 40-50px
#: in half-empty cards; the judge's empty share 30-62 % against 10-37 % on the original pages).
FILL_BY_ENLARGING = (" when the facts are few, enlarge the components rather than leaving the lower half"
                     " empty.")
FILL_BY_EXPLAINING = (" When the facts are few, give each item its full explanation from the source and the"
                      " template's secondary components (a caption row, a note line, an icon beside the"
                      " label, a compact key figure next to its explanation) rather than larger type or"
                      " taller empty cards; a figure is at most about three times the body size.")


def composition_rules(composition: dict | None, *, body_floor: float | None = None,
                      exemplars: bool = True, visual_guidance: bool = False) -> str:
    """The author-rule paragraph for a stored composition grammar; empty when there is none.
    ``visual_guidance`` (``SVG_TEMPLATE_VISUAL_GUIDANCE``) swaps the density line's closing advice."""
    if not composition:
        return ""
    grammar = SvgTemplateBodyComposition.model_validate(composition).grammar
    floor = float(body_floor or 0)

    def shown(size):
        return max(size, math.ceil(floor * 2 - 1e-9) / 2) if floor else size
    low, mid, high = grammar.sections
    kinds_low, kinds_mid, kinds_high = grammar.content_kinds_per_page
    items_low, items_mid, items_high = grammar.text_items
    big, usual, small = grammar.text_sizes
    named = ", ".join(f"{kind} {_percent(share)}" for kind, share in grammar.kind_shares if kind != "list")
    lines = [
        f"- Sections: a body page holds {mid} section{'s' if mid != 1 else ''} (quartiles {low}-{high})"
        + (f"; {_percent(grammar.numbered_headings)} of pages start each section with a numbered sub-heading"
           if grammar.numbered_headings >= .2 else "")
        + (f"; {_percent(grammar.key_message_bar)} open the body with a key-message bar across the content width"
           " right under the header, stating the page's conclusion" if grammar.key_message_bar >= .2 else "")
        + ".",
        f"- Mix: a page combines {kinds_mid} different content kinds (quartiles {kinds_low}-{kinds_high})"
        + (f"; across pages: {named}" if named else "")
        + ". Give each section the component its own content calls for (a table for compared attributes,"
          " steps for a sequence, a hierarchy for an organisation or layers, a chart or figures for numbers,"
          " cards for parallel points) instead of repeating one component down the page.",
        f"- Density: {items_mid} text items per page (quartiles {items_low}-{items_high}), body text about"
        f" {shown(usual):g}px (from {shown(small):g} to {shown(big):g}px); elements cover {_percent(grammar.ink_coverage)}"
        f" of the body area and reach {_percent(min(1.0, grammar.bottom_reach))} of its height. Fill body_region from"
        " top to bottom at a comparable density using every fact, label and figure of this page (state each"
        " point fully; give labels a short explanation from the source);"
        + (FILL_BY_EXPLAINING if visual_guidance else FILL_BY_ENLARGING) + " Never invent facts to fill space.",
    ]
    if exemplars:
        lines.append(
            "- template_exemplars, when given, outline one or two of this template's own body pages whose"
            " content resembles this page's: one element per line in reading order, kind, x,y relative to"
            " the body's top-left, width x height in px, paint, then text size/weight colour and amount"
            " (p paragraphs, ch characters that fit; the words are left out; number = an item number, number+ = a heading that starts with one); picture"
            " and icon mark the source's images, where this page uses its own offered images or icons, or"
            " content. They show how this template divides and fills a page; never copy their positions,"
            " counts or arrangement, and never take content from them: compose this page from its own content.")
    return ("\nTEMPLATE PAGE COMPOSITION (measured over " f"{grammar.pages_sampled} of this template's body pages;"
            " how it fills a page, not a layout to copy):\n" + "\n".join(lines) + "\n")
