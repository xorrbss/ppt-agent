"""Render-level exactly-once rules for redesign pages.

A region whose source area lies inside another drawn crop is already carried
by that crop's pixels. Drawing it again duplicates content (a title, a button,
a sentence shown twice). Such a region is suppressed when this is lossless:

* a pixel region (crop) inside a crop: its pixels are part of the container;
* an editable text region inside a crop: only when the container's OCR tokens
  include all of the text region's tokens (the ledger says the crop carries it).

Suppression is a pure function of the analysis, so layout, asset slots and
receipts agree on it.
"""

from __future__ import annotations

CONTAINED_RATIO = 0.8
ADVISORY = "region_carried_by_crop"
_PIXEL_ROLES = frozenset({"screenshot", "logo", "table"})


def _inside(inner, outer) -> bool:
    ix, iy, iw, ih = inner
    ox, oy, ow, oh = outer
    width = min(ix + iw, ox + ow) - max(ix, ox)
    height = min(iy + ih, oy + oh) - max(iy, oy)
    return width > 0 and height > 0 and width * height >= CONTAINED_RATIO * iw * ih


def _is_crop(region) -> bool:
    return region.role in _PIXEL_ROLES and not getattr(region, "table_cells", None)


def suppressed_regions(page) -> dict[str, str]:
    """Region id -> id of the crop that carries it (never itself suppressed)."""
    from services.source_content.raster_table_overlay import overlay_links

    cells = overlay_links(page.regions)  # drawn by their table only, never carried nor carrying
    regions = [r for r in page.regions if r.role != "decoration" and getattr(r, "source_pixel_bbox", None)
               and r.region_id not in cells]
    crops = sorted((r for r in regions if _is_crop(r)),
                   key=lambda r: r.source_pixel_bbox[2] * r.source_pixel_bbox[3], reverse=True)
    carried: dict[str, str] = {}
    for container in crops:
        if container.region_id in carried:
            continue
        box = container.source_pixel_bbox
        tokens = set(getattr(container, "ocr_token_indices", None) or ())
        for region in regions:
            if region is container or region.region_id in carried or region.region_id == container.region_id:
                continue
            if region.source_pixel_bbox[2] * region.source_pixel_bbox[3] > box[2] * box[3]:
                continue
            if not _inside(region.source_pixel_bbox, box):
                continue
            if _is_crop(region):
                carried[region.region_id] = container.region_id
            elif region.role in {"title", "body"}:
                own = set(getattr(region, "ocr_token_indices", None) or ())
                if own and own <= tokens:
                    carried[region.region_id] = container.region_id
    return carried


def carried_advisories(page) -> tuple[dict, ...]:
    carried = suppressed_regions(page)
    by_container: dict[str, list[str]] = {}
    for region_id, container in carried.items():
        by_container.setdefault(container, []).append(region_id)
    return tuple({"code": ADVISORY, "regionId": container, "replacedRegionIds": sorted(ids)}
                 for container, ids in sorted(by_container.items()))


def same_line(a, b) -> bool:
    """Two source boxes share one text line box (vertical overlap >= half the shorter)."""
    ay, ah = a[1], a[3]
    by, bh = b[1], b[3]
    return min(ay + ah, by + bh) - max(ay, by) >= 0.5 * min(ah, bh)
