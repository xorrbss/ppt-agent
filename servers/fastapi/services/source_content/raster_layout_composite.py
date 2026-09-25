"""Composite pictures: source tiles that touch are laid out as one picture.

Screenshot mock-ups are often analysed as several touching or overlapping
crops. A reflow that places them one by one tears the composite apart. Before
planning, each cluster of touching pictures of one section (at least one of
them a real tile, ``TILE_MIN_AREA`` of the page) becomes one virtual picture
with the cluster's union box; after planning it expands back into its member
crops at one scale, in their source arrangement, drawn without frames
(``Placed.kind == "crop"``). The crops themselves are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.source_content.raster_contracts import RasterBox
from services.source_content.raster_layout_coherence import TILE_MIN_AREA, TOUCH, _gap
from services.source_content.raster_redesign_tree import Item, Placed

PREFIX = "composite-"


@dataclass(frozen=True)
class CompositeRegion:
    """Duck-typed region of a composite: the union of its members (never a crop slot)."""

    region_id: str
    bbox: RasterBox
    source_pixel_bbox: tuple[int, int, int, int]
    group_id: str | None
    reading_order: float | None
    members: tuple  # member Items
    role: str = "screenshot"
    text: None = None
    table_cells: None = None
    review_reason: None = None
    editable_fallback: bool = False
    ocr_token_indices: tuple = ()


def _box(item):
    b = item.region.bbox
    return b.x, b.y, b.x + b.width, b.y + b.height


def _clusters(pictures, page_w):
    parent = list(range(len(pictures)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(pictures):
        for j in range(i + 1, len(pictures)):
            b = pictures[j]
            if getattr(a.region, "group_id", None) == getattr(b.region, "group_id", None) \
                    and _gap(_box(a), _box(b)) <= TOUCH * page_w:
                parent[find(i)] = find(j)
    clusters: dict[int, list] = {}
    for index, item in enumerate(pictures):
        clusters.setdefault(find(index), []).append(item)
    return [members for members in clusters.values() if len(members) > 1]


def _virtual(members) -> Item:
    boxes = [m.region.bbox for m in members]
    x0, y0 = min(b.x for b in boxes), min(b.y for b in boxes)
    x1, y1 = max(b.x + b.width for b in boxes), max(b.y + b.height for b in boxes)
    pixels = [m.region.source_pixel_bbox for m in members]
    px0, py0 = min(p[0] for p in pixels), min(p[1] for p in pixels)
    px1, py1 = max(p[0] + p[2] for p in pixels), max(p[1] + p[3] for p in pixels)
    orders = [o for m in members if (o := getattr(m.region, "reading_order", None)) is not None]
    region = CompositeRegion(
        region_id=PREFIX + members[0].region.region_id,
        bbox=RasterBox(x=x0, y=y0, width=x1 - x0, height=y1 - y0),
        source_pixel_bbox=(int(px0), int(py0), int(px1 - px0), int(py1 - py0)),
        group_id=getattr(members[0].region, "group_id", None),
        reading_order=min(orders) if orders else None,
        members=tuple(members),
    )
    return Item("image", region, aspect=(py1 - py0) / max(px1 - px0, 1), native_height=float(py1 - py0),
                min_scale=max(m.min_scale for m in members))


def composite_clusters(items, source_size) -> list[list]:
    """Clusters of touching pictures of one section with at least one real tile (see module docstring)."""
    page_w, page_h = source_size
    pictures = [item for item in items if item.kind == "image" and not item.inline
                and getattr(item.region, "source_pixel_bbox", None)]
    return [members for members in _clusters(pictures, page_w)
            if any((_box(m)[2] - _box(m)[0]) * (_box(m)[3] - _box(m)[1]) >= TILE_MIN_AREA * page_w * page_h
                   for m in members)]


def merge_composites(items, source_size) -> list:
    """Items with each composite cluster replaced by one virtual picture (at its first member's place)."""
    clusters = composite_clusters(items, source_size)
    if not clusters:
        return list(items)
    owner = {id(member): cluster for cluster in clusters for member in cluster}
    result, done = [], set()
    for item in items:
        cluster = owner.get(id(item))
        if cluster is None:
            result.append(item)
        elif id(cluster) not in done:
            done.add(id(cluster))
            result.append(_virtual(cluster))
    return result


def expand_composites(placements) -> list:
    """Replace each placed composite by its members, at one scale, in source arrangement."""
    result = []
    for placed in placements:
        region = getattr(placed.item, "region", None) if placed.item is not None else None
        if not isinstance(region, CompositeRegion):
            result.append(placed)
            continue
        ux, uy = region.source_pixel_bbox[:2]
        scale = placed.w / max(region.source_pixel_bbox[2], 1)
        for member in region.members:
            mx, my, mw, mh = member.region.source_pixel_bbox
            result.append(Placed("crop", placed.x + (mx - ux) * scale, placed.y + (my - uy) * scale,
                                 mw * scale, mh * scale, member))
    return result
