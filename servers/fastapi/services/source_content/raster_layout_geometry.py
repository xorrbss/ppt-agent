"""Source-geometry composition: the last resort before one page-content crop.

When no reflowed composition fits, even with every group as pixels, the page
used to keep its whole content area as one picture. This keeps the source's
own arrangement instead, block by block, at the uniform scale that picture
would have had (so it fits whenever the picture fits):

* a group is laid out in its scaled source boxes: editable when every text
  member fits its box at or above the 9.5 pt floor and every capture stays
  legible, otherwise as its group crop (``dense_group_pixel_fallback``);
* an ungrouped text or table is editable in its box, otherwise a crop of its
  own box; an ungrouped capture is its own crop in its box;
* an editable block whose text would touch any other placement (a crop that
  already shows those pixels, or other text) becomes pixels as well, so no
  text is drawn over a picture and nothing is shown twice.

Crops are drawn without frames (the source arrangement is seamless), the
largest first; editable text is drawn last.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.source_content.raster_redesign_fallback import NAMED_GROUPS, group_fallbacks, region_fallbacks
from services.source_content.raster_redesign_pixel_lines import FLOOR_FONT, line_scale
from services.source_content.raster_redesign_plan import (
    BODY_TEXT,
    NAVY,
    TABLE_FONTS,
    TEXT_FONTS,
    _overlap,
    _text_boxes,
)
from services.source_content.raster_redesign_table import layout_table
from services.source_content.raster_redesign_text import layout_paragraphs
from services.source_content.raster_redesign_tree import Item, Placed

# Laid-out text may use this much more height than its source box (our line height is looser).
TEXT_SLACK = 1.15


@dataclass
class GeometryPlan:
    placements: list[Placed]
    chosen: list  # FallbackGroup crops used, for advisories
    scale: float  # target units per source EMU


@dataclass
class _Block:
    key: str
    items: list
    fallback: object | None  # FallbackGroup, or None when every member is already a crop
    pixels: bool = False
    placements: list[Placed] = field(default_factory=list)


def _blocks(page, items) -> list[_Block]:
    groups, regions = group_fallbacks(page), region_fallbacks(page)
    blocks: dict[str, _Block] = {}
    for item in items:
        group = getattr(item.region, "group_id", None)
        if group and group not in NAMED_GROUPS:
            key = f"group:{group}"
            fallback = groups.get(group)
        else:
            key = f"region:{item.region.region_id}"
            fallback = regions.get(item.region.region_id) if item.kind != "image" else None
        blocks.setdefault(key, _Block(key, [], fallback)).items.append(item)
    return list(blocks.values())


def _union(boxes):
    x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
    return x0, y0, max(b.x + b.width for b in boxes) - x0, max(b.y + b.height for b in boxes) - y0


def _units_per_px(region, scale) -> float | None:
    px = getattr(region, "source_pixel_bbox", None)
    return scale * region.bbox.height / px[3] if px and px[3] else None


def _legible(item, scale) -> bool:
    units_per_px = _units_per_px(item.region, scale)
    if units_per_px is None:
        return False
    minimum = line_scale(item, FLOOR_FONT)[2] if item.inline else item.min_scale
    return units_per_px + 1e-6 >= minimum


def _fit_text(item, w, h):
    for size in TEXT_FONTS:
        try:
            lines, height = layout_paragraphs(list(item.paragraphs), w, size, body_color=BODY_TEXT, heading_color=NAVY)
        except ValueError:
            continue
        if height <= h * TEXT_SLACK:
            return lines, height
    return None


def _fit_table(item, w, h):
    for size in TABLE_FONTS:
        grid = layout_table(item.rows, w, size)
        if grid is not None and grid.height <= h * TEXT_SLACK:
            return grid
    return None


def _editable(block, rect, scale) -> list[Placed] | None:
    placements = []
    for item in block.items:
        x, y, w, h = rect(item.region.bbox)
        if item.kind == "text":
            fitted = _fit_text(item, w, h)
            if fitted is None:
                return None
            placements.append(Placed("text", x, y, w, fitted[1], item, lines=fitted[0]))
        elif item.kind == "table":
            grid = _fit_table(item, w, h)
            if grid is None:
                return None
            placements.append(Placed("table", x, y, w, grid.height, item, table=grid))
        elif _legible(item, scale):
            placements.append(Placed("crop", x, y, w, h, item))
        else:
            return None
    return placements


def _pixels(block, rect, scale) -> list[Placed] | None:
    if block.fallback is not None:
        region = block.fallback.region
        item = Item("image", region, min_scale=block.fallback.min_scale)
        return [Placed("crop", *rect(region.bbox), item)] if _legible(item, scale) else None
    if any(item.kind != "image" or not _legible(item, scale) for item in block.items):
        return None
    return [Placed("crop", *rect(item.region.bbox), item) for item in block.items]


def _boxes(placed):
    if placed.kind == "text":
        return list(_text_boxes(placed))
    return [(placed.x, placed.y, placed.x + placed.w, placed.y + placed.h)]


def _clash(blocks, bottom) -> _Block | None:
    """The first editable block whose text or table touches another placement or leaves the box."""
    for block in blocks:
        if block.pixels:
            continue
        for placed in block.placements:
            if placed.kind not in {"text", "table"}:
                continue
            mine = _boxes(placed)
            if any(box[3] > bottom + 0.5 for box in mine):
                return block
            others = [box for other in blocks for item in other.placements if item is not placed
                      for box in _boxes(item)]
            if any(_overlap(a, b) for a in mine for b in others):
                return block
    return None


def geometry_plan(page, items, box) -> GeometryPlan | None:
    """Place ``items`` in their source arrangement inside ``box`` (x, y, w, h); None if not legible.

    Like the page-content crop, it needs at least two items: one region that
    does not fit is not re-laid out as a picture of itself.
    """
    if len(items) < 2 or any(not getattr(item.region, "source_pixel_bbox", None) for item in items):
        return None
    ux, uy, uw, uh = _union([item.region.bbox for item in items])
    bx, by, bw, bh = box
    if uw <= 0 or uh <= 0 or bw <= 0 or bh <= 0:
        return None
    scale = min(bw / uw, bh / uh)
    left = bx + (bw - uw * scale) / 2

    def rect(source):
        return left + (source.x - ux) * scale, by + (source.y - uy) * scale, source.width * scale, source.height * scale

    blocks = _blocks(page, items)
    for _ in range(len(blocks) + 1):
        carried = set()
        for block in blocks:
            placements = None if block.pixels else _editable(block, rect, scale)
            if placements is None:
                block.pixels = True
                placements = _pixels(block, rect, scale)
                if placements is None:
                    return None
                if block.fallback is not None:
                    carried.update(set(block.fallback.member_ids) - {item.region.region_id for item in block.items})
            block.placements = placements
        live = [block for block in blocks
                if not all(item.region.region_id in carried for item in block.items)]
        clash = _clash(live, by + bh)
        if clash is None:
            crops = sorted((p for block in live for p in block.placements if p.kind == "crop"),
                           key=lambda placed: -placed.w * placed.h)
            rest = [p for block in live for p in block.placements if p.kind != "crop"]
            chosen = [block.fallback for block in live if block.pixels and block.fallback is not None]
            return GeometryPlan([*crops, *rest], chosen, scale)
        clash.pixels = True
    return None
