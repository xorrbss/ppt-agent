"""Content model and candidate layout search for raster redesign pages.

The planner never drops or rewrites content. It enumerates a bounded set of
compositions (stacked bands, text/capture split, source-column sections,
card grids, column flow), keeps only those that fit above the 9.5 pt floor,
and picks the best score.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from services.source_content.raster_contracts import RasterBox
from services.source_content.raster_redesign_dedupe import suppressed_regions
from services.source_content.raster_redesign_sections import build_sections, xy_tree
from services.source_content.raster_redesign_semantic import (
    blocks,
    build_semantic,
    card_runs,
    has_groups,
    natural_columns,
    semantic_configs,
)
from services.source_content.raster_redesign_table import layout_table
from services.source_content.raster_redesign_text import (
    GLYPH_ASCENT,
    GLYPH_DESCENT,
    visual_width,
    column_height,
    layout_paragraphs,
    split_lines_into_columns,
    split_paragraphs,
)
from services.source_content.raster_redesign_tree import height as tree_height
from services.source_content.raster_redesign_tree import (
    Fixed,
    ImageRow,
    Item,
    Placed,
    Row,
    Stack,
    image_area,
    place,
    scale_compatible,
    solve,
)
from services.source_content.raster_svg_layout import MIN_FONT_UNITS

NAVY, COBALT, SLATE = "#081D49", "#0866FF", "#536785"
BODY_TEXT = "#1B2F52"
CARD_PAD, CARD_ACCENT = 14.0, 4.0
BAND_GAP, GRID_GAP_X, GRID_GAP_Y = 10.0, 14.0, 8.0
# Sizes are emitted with two decimals, so the floor step is rounded up.
FLOOR_FONT = math.ceil(MIN_FONT_UNITS * 100) / 100
TEXT_FONTS = (25.4, 22.6, 19.8, 16.9, 15.5, 14.1, FLOOR_FONT)
TABLE_FONTS = (19.8, 16.9, 15.5, 14.1, FLOOR_FONT)
IMAGE_ROLES = frozenset({"screenshot", "logo"})
# An unresolved table kept as pixels must stay at least as legible as the source.
TABLE_CROP_MIN_SCALE = 0.6

__all__ = ["Item", "Placed", "Plan", "build_items", "plan_content", "redesign_crop_regions", "region_is_crop"]


@dataclass(frozen=True)
class TitlePixels:
    """The page title carried as pixels (drawn in the title band, never as content)."""

    items: tuple
    text = None
    region = None

    @property
    def bbox(self) -> RasterBox:
        """Union source box of the title crops (anchors header-zone placement)."""
        boxes = [item.region.bbox for item in self.items]
        x, y = min(b.x for b in boxes), min(b.y for b in boxes)
        return RasterBox(x=x, y=y, width=max(b.x + b.width for b in boxes) - x,
                         height=max(b.y + b.height for b in boxes) - y)


@dataclass
class Plan:
    placements: list[Placed]
    score: float
    used_height: float
    name: str


def _order_key(region):
    order = getattr(region, "reading_order", None)
    return (0 if order is not None else 1, order if order is not None else 0,
            region.bbox.y, region.bbox.x, region.z_order)


def ordered_regions(page) -> list:
    return sorted(page.regions, key=_order_key)


def _table_rows(region):
    rows = getattr(region, "table_cells", None)
    return tuple(tuple(row) for row in rows) if rows else None


def region_is_crop(region) -> bool:
    """Whether a redesign region is rendered as its reviewed source crop."""
    if region.role in IMAGE_ROLES:
        return True
    if region.role == "table":
        return _table_rows(region) is None
    if region.role == "unknown":
        return not (region.text or "").strip() and region.crop_artifact_ref is not None
    return False


def redesign_crop_regions(page) -> list:
    """Regions that need a raster crop slot on a redesign page (never the reference)."""
    carried = suppressed_regions(page)
    return [
        region for region in ordered_regions(page)
        if region.role != "decoration" and region_is_crop(region) and region.region_id not in carried
    ]


def _aspect(region) -> float:
    pixel = getattr(region, "source_pixel_bbox", None)
    if pixel is not None:
        return pixel[3] / pixel[2]
    return region.bbox.height / region.bbox.width


PIXEL_LINE_REASONS = frozenset({"low_confidence_text_pixels", "crop_text_boundary_line"})
# Crops that are not a line/paragraph of flowing text (boundary cuts of whole cards/areas).
NOT_FLOW_REASONS = frozenset({"crop_text_boundary", "crop_text_boundary_card", "table_cell_ocr_low_confidence"})


def is_pixel_line(region) -> bool:
    if region.role != "screenshot" or not getattr(region, "editable_fallback", False):
        return False
    reason = getattr(region, "review_reason", None)
    if reason in PIXEL_LINE_REASONS:
        return True
    if reason == "uncovered_ink":  # word/glyph ink: text only inside the title group
        return getattr(region, "group_id", None) == "title"
    # Any other editable-fallback crop of a paragraph/line (e.g. an atomic pixel
    # paragraph) flows inline, unless it is a card/area/table boundary crop.
    return reason not in NOT_FLOW_REASONS and bool(getattr(region, "ocr_token_indices", None))


def _text_px(page, region) -> float | None:
    """Median OCR glyph height of a region's tokens, in source pixels."""
    inventory = getattr(page, "ocr_inventory", ()) or ()
    indices = [i for i in (getattr(region, "ocr_token_indices", None) or ()) if i < len(inventory)]
    px = getattr(region, "source_pixel_bbox", None)
    if not indices or not px or not px[3]:
        return None
    emu_per_px = region.bbox.height / px[3]
    heights = sorted(inventory[i].bbox.height / emu_per_px for i in indices)
    return heights[len(heights) // 2] or None


def _pixel_min_scale(region) -> float:
    """Unresolved tables and uncovered-ink crops carry text as pixels: keep them legible."""
    if region.role == "table" or getattr(region, "review_reason", None) == "uncovered_ink":
        return TABLE_CROP_MIN_SCALE
    return 0.0


def _native_height(region) -> float | None:
    pixel = getattr(region, "source_pixel_bbox", None)
    return float(pixel[3]) if pixel is not None else None


def build_items(page):
    """Split a page into header title, header logos and ordered content items."""

    title, logos, items, title_pixels = None, [], [], []
    carried = suppressed_regions(page)
    for region in ordered_regions(page):
        role = region.role
        if role == "decoration" or region.region_id in carried:
            continue  # decoration, or already carried by a containing crop (drawn once)
        text = (region.text or "").strip()
        if role == "title" and title is None and text:
            title = region
        elif region_is_crop(region):
            if role == "screenshot" and getattr(region, "group_id", None) == "title":
                title_pixels.append(region)  # title-line pieces lead the content as one line
                continue
            items.append(Item("image", region, aspect=_aspect(region), native_height=_native_height(region),
                              min_scale=_pixel_min_scale(region), inline=is_pixel_line(region),
                              text_px=_text_px(page, region) if is_pixel_line(region) else None))
        elif role == "table":
            items.append(Item("table", region, rows=_table_rows(region)))
        elif text:
            items.append(Item("text", region, paragraphs=tuple(
                split_paragraphs(text, getattr(region, "heading_line_count", None)),
            )))
        else:
            raise ValueError("raster_redesign_region_unrenderable")
    if title is None and title_pixels:
        # A title kept as pixels (paragraph-atomic crop) belongs in the title band.
        title = TitlePixels(tuple(Item("image", r, aspect=_aspect(r), native_height=_native_height(r),
                                       inline=True, text_px=_text_px(page, r)) for r in title_pixels))
        return title, logos, items
    # Safety: pixel pieces beside an editable title (a second title line) lead the content
    # as one inline line, in reading order, instead of scattered word tiles.
    lead = [Item("image", r, aspect=_aspect(r), native_height=_native_height(r), min_scale=0.0, inline=True,
                 text_px=None) for r in title_pixels]
    return title, logos, [*lead, *items]


class _Cache:
    def __init__(self):
        self.text, self.table = {}, {}

    def paragraphs(self, item, width, size):
        key = (id(item), round(width, 2), size)
        if key not in self.text:
            try:
                self.text[key] = layout_paragraphs(
                    list(item.paragraphs), width, size, body_color=BODY_TEXT, heading_color=NAVY,
                )
            except ValueError:
                self.text[key] = None
        return self.text[key]

    def grid(self, item, width, size):
        key = (id(item), round(width, 2), size)
        if key not in self.table:
            self.table[key] = layout_table(item.rows, width, size)
        return self.table[key]


def _bands(items):
    bands: list[tuple[str, list[Item]]] = []
    for item in items:
        if bands and bands[-1][0] == item.kind and item.kind != "table":
            bands[-1][1].append(item)
        else:
            bands.append((item.kind, [item]))
    return bands


def _text_leaf(items, x, w, f, k, cache, *, cards: bool):
    if len(items) == 1 or not cards:
        if len(items) > 1:
            return None
        column_width = (w - GRID_GAP_X * (k - 1)) / k
        laid = cache.paragraphs(items[0], column_width, f)
        if laid is None:
            return None
        parts = split_lines_into_columns(laid[0], k)
        placed = [Placed("text", x + i * (column_width + GRID_GAP_X), 0.0, column_width,
                         column_height(part), items[0], lines=part, part=i)
                  for i, part in enumerate(parts)]
        return Fixed(max(p.h for p in placed), placed)
    if k > len(items):
        return None
    card_width = (w - GRID_GAP_X * (k - 1)) / k
    inner = card_width - CARD_PAD * 2 - CARD_ACCENT
    placed, cursor = [], 0.0
    for start in range(0, len(items), k):
        row = items[start:start + k]
        laid = [cache.paragraphs(item, inner, f) for item in row]
        if any(value is None for value in laid):
            return None
        row_height = max(value[1] for value in laid) + CARD_PAD * 2
        for i, (item, value) in enumerate(zip(row, laid, strict=True)):
            cx = x + i * (card_width + GRID_GAP_X)
            placed.append(Placed("card", cx, cursor, card_width, row_height, item))
            placed.append(Placed("text", cx + CARD_ACCENT + CARD_PAD, cursor + CARD_PAD, inner,
                                 value[1], item, lines=value[0]))
        cursor += row_height + GRID_GAP_Y
    return Fixed(cursor - GRID_GAP_Y, placed)


def _image_rows(group, x, w, per_row):
    """Screens in rows of ``per_row``; runs of icons packed into compact rows."""
    rows, run = [], []
    for is_icon, members in _runs(group):
        if not is_icon:
            rows.extend(ImageRow(members[i:i + per_row], x, w) for i in range(0, len(members), per_row))
            continue
        used = 0.0
        for icon in members:
            need = icon.natural_width + (6.0 if run else 0.0)
            if run and (used + need > w or not scale_compatible([*run, icon])):
                rows.append(ImageRow(run, x, w))
                run, used, need = [], 0.0, icon.natural_width
            run.append(icon)
            used += need
        if run:
            rows.append(ImageRow(run, x, w))
            run = []
    return rows


def _runs(group):
    runs: list[tuple[bool, list]] = []
    for item in group:
        if runs and runs[-1][0] == item.is_icon:
            runs[-1][1].append(item)
        else:
            runs.append((item.is_icon, [item]))
    return runs


def _stack(items, x, w, f, tf, k, per_row, cache, *, cards=True):
    children = []
    for kind, group in _bands(items):
        if kind == "text":
            if not cards and len(group) > 1:
                leaves = [_text_leaf([item], x, w, f, 1, cache, cards=False) for item in group]
                if any(leaf is None for leaf in leaves):
                    return None
                children.append(Stack(leaves, GRID_GAP_Y))
                continue
            leaf = _text_leaf(group, x, w, f, k, cache, cards=cards)
        elif kind == "table":
            grid = cache.grid(group[0], w, tf)
            leaf = None if grid is None else Fixed(
                grid.height, [Placed("table", x, 0.0, w, grid.height, group[0], table=grid)],
            )
        else:
            leaf = Stack(_image_rows(group, x, w, per_row), 10.0)
        if leaf is None:
            return None
        children.append(leaf)
    return Stack(children, BAND_GAP)


def _configs(items):
    images = [item for item in items if item.kind == "image"]
    texts = [item for item in items if item.kind != "image"]
    configs = [("sections", 1, 1, None)]
    for k in (1, 2, 3):
        for per_row in range(1, min(3, max(1, len(images))) + 1):
            configs.append(("stack", k, per_row, None))
    if texts and images:
        for ratio in (0.36, 0.44, 0.52, 0.6):
            for per_row in (1, 2):
                if per_row <= len(images):
                    configs.append(("split", 1, per_row, ratio))
    return configs


def _build(mode, k, per_row, ratio, items, box, f, tf, cache, source_width):
    x, _, w, _ = box
    if mode.startswith("semantic"):
        return build_semantic(mode, k, per_row, ratio, items, x, w, f, tf, cache)
    if mode == "sections":
        tree = xy_tree(items, source_width[0], source_width[1], lambda item: _order_key(item.region))
        return build_sections(
            tree, x, w, lambda group, gx, gw: _stack(group, gx, gw, f, tf, 1, min(3, len(group)), cache, cards=False),
        )
    if mode == "stack":
        return _stack(items, x, w, f, tf, k, per_row, cache)
    texts = [item for item in items if item.kind != "image"]
    images = [item for item in items if item.kind == "image"]
    left_w = (w - 28.0) * ratio
    if left_w / f < 14:
        return None
    left = _stack(texts, x, left_w, f, tf, 1, 1, cache)
    right = _stack(images, x + left_w + 28.0, w - 28.0 - left_w, f, tf, 1, per_row, cache)
    return None if left is None or right is None else Row([left, right])


def _split_imbalance(node, t, available) -> float:
    """A split whose columns end far apart leaves a hole; penalise the gap."""
    left, right = (tree_height(child, t) for child in node.children)
    return 0.8 * abs(left - right) / available


def _text_boxes(placed):
    """Glyph boxes (Chrome metrics) of every line of one text placement."""
    for line in placed.lines:
        left = placed.x + line.indent
        yield (left, placed.y + line.baseline - GLYPH_ASCENT * line.size,
               left + visual_width(line.text) * line.size, placed.y + line.baseline + GLYPH_DESCENT * line.size)


def _overlap(a, b, tolerance=0.5) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > tolerance and min(a[3], b[3]) - max(a[1], b[1]) > tolerance


def layout_violation(placements) -> str | None:
    """Layout invariants checked before any SVG is emitted.

    * no image (with its 4-unit frame) intersects an editable text line or a table;
    * every text line lies inside the card that contains its block origin.
    """
    frame = 4.0
    images = [(p.x - (0.0 if p.item.inline else frame), p.y - (0.0 if p.item.inline else frame),
               p.x + p.w + (0.0 if p.item.inline else frame), p.y + p.h + (0.0 if p.item.inline else frame))
              for p in placements if p.kind == "image"]
    texts = [box for p in placements if p.kind == "text" for box in _text_boxes(p)]
    tables = [(p.x, p.y, p.x + p.w, p.y + p.h) for p in placements if p.kind == "table"]
    if any(_overlap(image, other) for image in images for other in (*texts, *tables)):
        return "image_over_text"
    cards = [(p.x, p.y, p.x + p.w, p.y + p.h) for p in placements if p.kind in {"card", "panel"}]
    for placed in placements:
        if placed.kind != "text":
            continue
        owner = next((c for c in cards if c[0] <= placed.x <= c[2] and c[1] <= placed.y <= c[3]), None)
        if owner is None:
            continue
        for box in _text_boxes(placed):
            if box[0] < owner[0] - 0.5 or box[2] > owner[2] + 0.5 or box[3] > owner[3] + 0.5:
                return "text_outside_card"
    return None


def _grid_penalty(runs, columns) -> float:
    """Prefer each card run's natural grid; penalise orphan cards in a last row."""
    penalty = 0.0
    for count in runs:
        if count < 2:
            continue
        used = min(columns, count)
        penalty += 0.15 * abs(used - natural_columns(count)) + (0.1 if count % used else 0.0)
    return penalty


def plan_content(items, box, *, source_size: tuple[float, float], density: str = "regular",
                 semantic: bool | None = None, accept=None) -> Plan:
    """Choose the best fitting composition for ``items`` inside ``box``.

    ``accept(placements)`` returns None for an admissible composition or the
    reason it is not (the source-coherence check). When compositions fit but
    none is admissible, raises ``raster_redesign_incoherent:<reason>``.
    """

    from services.source_content.raster_layout_composite import expand_composites, merge_composites

    x, y, w, h = box
    items = merge_composites(items, source_size)  # touching source tiles stay one picture
    cache = _Cache()
    has_text = any(item.kind == "text" for item in items)
    has_table = any(item.kind == "table" for item in items)
    single_text = sum(item.kind == "text" for item in items) == 1
    fonts = TEXT_FONTS if density != "compact" else tuple(s for s in TEXT_FONTS if s <= 16.9)
    best: Plan | None = None
    rejected = None
    # A structured page stays semantic even after every group became pixels.
    semantic = has_groups(items) if semantic is None else semantic
    configs = semantic_configs(items) if semantic else _configs(items)
    runs = card_runs(blocks(items)) if semantic else []
    for f in fonts if has_text else (TEXT_FONTS[3],):
        for tf in TABLE_FONTS if has_table else (TABLE_FONTS[0],):
            for mode, k, per_row, ratio in configs:
                if k > 1 and not has_text and not semantic:
                    continue
                node = _build(mode, k, per_row, ratio, items, box, f, tf, cache, source_size)
                t = None if node is None else solve(node, h)
                if t is None:
                    continue
                placements = place(node, 0.0, t)
                if layout_violation(placements):
                    continue  # never emit an image over text or text outside its card
                used = max((p.y + p.h for p in placements), default=0.0)
                widest = max((p.w for p in placements if p.kind == "text"), default=0.0)
                score = (
                    (3.0 * f / TEXT_FONTS[0] if has_text else 0.0)
                    + (1.0 * tf / TABLE_FONTS[0] if has_table else 0.0)
                    + 2.6 * image_area(node, t) / (w * h)
                    + 0.3 * min(1.0, used / h)
                    - (0.35 if widest / f > 50 else 0.0)
                    - (0.12 * (k - 1) if single_text and k > 1 and not semantic else 0.0)
                    + (0.35 if mode == "sections" else 0.0)
                    # Semantic cards: prefer the natural grid and roomy padding.
                    - (_grid_penalty(runs, k) + (0.05 if per_row < 14.0 else 0.0) if semantic else 0.0)
                    - (_split_imbalance(node, t, h) if mode == "semantic-split" else 0.0)
                )
                if best is None or score > best.score + 1e-9:
                    issue = accept(expand_composites(placements)) if accept is not None else None
                    if issue is not None:
                        rejected = rejected or issue
                        continue
                    best = Plan(placements, score, used, f"{mode}-k{k}-r{per_row}-{ratio}-f{f:.1f}-t{tf:.1f}")
    if best is None:
        raise ValueError(f"raster_redesign_incoherent:{rejected}" if rejected else "raster_redesign_content_overflow")
    best.placements = expand_composites(best.placements)
    for placed in best.placements:
        placed.y += y
    return best
