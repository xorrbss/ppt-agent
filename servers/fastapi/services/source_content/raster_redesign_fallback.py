"""Dense-page fallback: render the fewest groups as lossless source-pixel crops.

Used only after every editable composition fails at the 9.5 pt floor. A
group (card) is replaced by one crop of its union source box, shown at >=0.6x
native pixels so its text is at least as legible as the source. Groups are
chosen by estimated height saved per editable character lost, one at a time,
until the page fits. Each choice is reported as the advisory
``dense_group_pixel_fallback`` so evidence and UI can count it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from services.source_content.raster_contracts import RasterBox, RasterRegion
from services.source_content.raster_redesign_text import LINE_HEIGHT, layout_paragraphs
from services.source_content.raster_redesign_tree import Item

ADVISORY = "dense_group_pixel_fallback"
FALLBACK_MIN_SCALE = 0.6
NAMED_GROUPS = frozenset({"header", "footer", "title"})  # never replaced by a group crop


@dataclass(frozen=True)
class FallbackGroup:
    group_id: str
    region: object  # the crop region (explicit from the analysis, or derived)
    member_ids: tuple[str, ...]
    min_scale: float = 0.0  # 0 means FALLBACK_MIN_SCALE


def fallback_region_id(page_number: int, group_id: str) -> str:
    return f"p{page_number}-group-{re.sub(r'[^A-Za-z0-9_-]', '_', str(group_id))}-pixels"


def group_fallbacks(page) -> dict[str, FallbackGroup]:
    """Every content group's potential pixel fallback, deterministic from the analysis.

    The crop region is a validated ``RasterRegion`` whose source box is the
    union of the members' boxes. It carries the reference image's provenance
    (occurrence, media part, source digest) because its pixels are cut from
    the page reference image at publish time; it has no stored crop artifact.
    """
    members: dict[str, list] = {}
    for region in page.regions:
        group = getattr(region, "group_id", None)
        if group and group not in NAMED_GROUPS and region.role != "decoration":
            members.setdefault(group, []).append(region)
    reference = getattr(page, "reference_region", None)
    result = {}
    for group, regions in members.items():
        if not any(region.role in {"title", "body", "table", "unknown"} for region in regions):
            continue
        boxes = [region.source_pixel_bbox for region in regions if getattr(region, "source_pixel_bbox", None)]
        if len(boxes) != len(regions):
            continue
        # The crop stays a member of its group, so a reflow keeps it at the group's place.
        region = _union_region(page.page_number, group, regions, boxes, reference, group_id=group)
        inside = [other for other in page.regions if _contained(other, region.source_pixel_bbox)]
        ids = tuple(r.region_id for r in [*regions, *inside])
        result[group] = FallbackGroup(group, region, ids, legible_min_scale(page, ids))
    return result


def _union_region(page_number, group, regions, boxes, reference, group_id=None) -> RasterRegion:
    left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
    right, bottom = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    emu = [region.bbox for region in regions]
    ex, ey = min(b.x for b in emu), min(b.y for b in emu)
    return RasterRegion(
        region_id=fallback_region_id(page_number, group), page_number=page_number, role="screenshot",
        bbox=RasterBox(x=ex, y=ey, width=max(b.x + b.width for b in emu) - ex,
                       height=max(b.y + b.height for b in emu) - ey),
        z_order=0, review_required=False, group_id=group_id,
        source_occurrence_id=getattr(reference, "source_occurrence_id", None),
        source_media_part=getattr(reference, "source_media_part", None),
        crop_source_sha256=getattr(reference, "crop_source_sha256", None),
        source_pixel_bbox=(int(left), int(top), int(right - left), int(bottom - top)),
    )

def _contained(region, box, ratio: float = 0.85) -> bool:
    if getattr(region, "group_id", None):
        return False
    if region.role not in {"screenshot", "logo", "table"} or not getattr(region, "source_pixel_bbox", None):
        return False
    x, y, w, h = region.source_pixel_bbox
    bx, by, bw, bh = box
    overlap_w = min(x + w, bx + bw) - max(x, bx)
    overlap_h = min(y + h, by + bh) - max(y, by)
    return overlap_w > 0 and overlap_h > 0 and overlap_w * overlap_h >= ratio * w * h


def _editable_chars(regions) -> int:
    total = 0
    for region in regions:
        cells = getattr(region, "table_cells", None)
        value = "".join("".join(row) for row in cells) if cells else (region.text or "")
        total += len(re.sub(r"\s+", "", value))
    return max(1, total)


PAGE_CONTENT = "page-content"


def page_content_fallback(analysis, page) -> FallbackGroup | None:
    """Last resort: the whole content area (below the title band) as one source crop.

    It keeps the source's own dense arrangement, which is the most compact
    lossless form. Title, header/footer groups and header-zone text stay editable.
    """
    from services.source_content.raster_layout_parts import page_parts

    parts = page_parts(analysis, page)  # the same zones as the layout: each region drawn once
    header_images = parts.header_images
    regions = [item.region for item in parts.items]
    if len(regions) < 2 or any(not getattr(region, "source_pixel_bbox", None) for region in regions):
        return None
    region = _union_region(page.page_number, PAGE_CONTENT, regions,
                           [region.source_pixel_bbox for region in regions], getattr(page, "reference_region", None))
    own = {r.region_id for r in regions}
    own |= {item.region.region_id for item in header_images}
    inside = [other for other in page.regions
              if other.region_id not in own and _contained(other, region.source_pixel_bbox)]
    ids = tuple(r.region_id for r in [*regions, *inside])
    return FallbackGroup(PAGE_CONTENT, region, ids, legible_min_scale(page, ids))


REGION_PREFIX = "region-"


def region_fallbacks(page) -> dict[str, FallbackGroup]:
    """A pixel fallback for each editable region outside any content group, and each decoration.

    Used by the source-geometry composition: an ungrouped text or table that
    cannot stay editable in its source box is shown as a crop of its own box,
    and decoration keeps its place in the source arrangement (as it would
    inside a page-content crop). Keyed by region id; crops contained in the
    box are carried (drawn once).
    """
    reference = getattr(page, "reference_region", None)
    result = {}
    for region in page.regions:
        group = getattr(region, "group_id", None)
        box = getattr(region, "source_pixel_bbox", None)
        if not box:
            continue
        if region.role != "decoration" and (
            region.role not in {"body", "table", "unknown"} or (group and group not in NAMED_GROUPS - {"title"})
            or not (region.text or getattr(region, "table_cells", None))
        ):
            continue
        key = f"{REGION_PREFIX}{region.region_id}"
        crop = _union_region(page.page_number, key, [region], [box], reference)
        inside = [] if region.role == "decoration" else [
            other for other in page.regions if other is not region and _contained(other, crop.source_pixel_bbox)
        ]
        ids = (region.region_id, *(other.region_id for other in inside))
        result[region.region_id] = FallbackGroup(key, crop, ids, legible_min_scale(page, ids))
    return result


def available_fallbacks(analysis, page) -> list[FallbackGroup]:
    """Every pixel fallback the layout may use on this page (groups, regions, then page content)."""
    result = [*group_fallbacks(page).values(), *region_fallbacks(page).values()]
    content = page_content_fallback(analysis, page)
    return [*result, content] if content is not None else result


def legible_min_scale(page, member_ids) -> float:
    """Smallest crop scale keeping the members' OCR text at the 9.5 pt-equivalent size.

    Uses the 25th-percentile OCR token height (source px) as the text size;
    never above the fixed FALLBACK_MIN_SCALE, which keeps text at least as
    legible as the source when the source text itself is small.
    """
    from services.source_content.raster_svg_layout import MIN_FONT_UNITS

    inventory = getattr(page, "ocr_inventory", ()) or ()
    ids = set(member_ids)
    heights = []
    emu_per_px = None
    for region in page.regions:
        if region.region_id not in ids or not getattr(region, "ocr_token_indices", None):
            continue
        px, emu = getattr(region, "source_pixel_bbox", None), region.bbox
        if px and px[3] and emu_per_px is None:
            emu_per_px = emu.height / px[3]
        heights.extend(inventory[i].bbox.height for i in region.ocr_token_indices if i < len(inventory))
    if not heights or not emu_per_px:
        return FALLBACK_MIN_SCALE
    heights.sort()
    token_px = heights[len(heights) // 4] / emu_per_px
    return min(FALLBACK_MIN_SCALE, MIN_FONT_UNITS / max(token_px, 1.0))


def ranked_candidates(page, items, floor_font: float) -> list[FallbackGroup]:
    """Groups ordered by estimated height saved per editable character lost."""
    fallbacks = group_fallbacks(page)
    scored = []
    for group, fallback in fallbacks.items():
        members = [item for item in items if getattr(item.region, "group_id", None) == group]
        if not members:
            continue
        px = fallback.region.source_pixel_bbox
        crop_w, crop_h = px[2] * FALLBACK_MIN_SCALE, px[3] * FALLBACK_MIN_SCALE
        text_h = 0.0
        for item in members:
            if item.kind == "text" and item.paragraphs:
                try:
                    text_h += layout_paragraphs(list(item.paragraphs), max(crop_w, 40.0), floor_font,
                                                body_color="#000000", heading_color="#000000")[1]
                except ValueError:
                    text_h += floor_font * LINE_HEIGHT * len(item.paragraphs)
            elif item.kind == "table":
                text_h += floor_font * LINE_HEIGHT * (len(item.rows) + 1)
            elif item.kind == "image":
                text_h += (item.native_height or 0.0) * 0.5
        saved = text_h - crop_h
        chars = _editable_chars([item.region for item in members])
        scored.append((saved / chars, -px[1], fallback))
    scored.sort(key=lambda entry: (entry[0], entry[1]), reverse=True)
    return [entry[2] for entry in scored]


def apply_fallbacks(items, chosen: list[FallbackGroup]):
    """Replace each chosen group's members with its crop, at the first member's position."""
    owner = {member: fallback for fallback in chosen for member in fallback.member_ids}
    result, placed = [], set()
    for item in items:
        fallback = owner.get(item.region.region_id)
        if fallback is not None:
            group = fallback.group_id
            if group not in placed:
                region = fallback.region
                px = region.source_pixel_bbox
                result.append(Item("image", region, aspect=px[3] / px[2], native_height=float(px[3]),
                                   min_scale=fallback.min_scale or FALLBACK_MIN_SCALE))
                placed.add(group)
            continue
        result.append(item)
    return result


def advisories(chosen: list[FallbackGroup]) -> tuple[dict, ...]:
    return tuple(
        {"code": ADVISORY, "groupId": fallback.group_id, "regionId": fallback.region.region_id,
         "replacedRegionIds": list(fallback.member_ids)}
        for fallback in chosen
    )


def advisories_from_svg(analysis, page, svg: str) -> tuple[dict, ...]:
    """Receipt advisories recovered from a canonical redesign SVG (deterministic)."""
    import xml.etree.ElementTree as ET

    from services.raster_source_assets import raster_slot_id

    used = {node.attrib.get("href") for node in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}image")}
    chosen = [fallback for fallback in (*group_fallbacks(page).values(), *region_fallbacks(page).values())
              if raster_slot_id(page.page_number, fallback.region.region_id) in used]
    if raster_slot_id(page.page_number, fallback_region_id(page.page_number, PAGE_CONTENT)) in used:
        content = page_content_fallback(analysis, page)  # only built when the SVG actually uses it
        chosen += [content] if content is not None else []
    from services.source_content.raster_layout_evidence import layout_advisories_from_svg

    return advisories(chosen) + layout_advisories_from_svg(svg)