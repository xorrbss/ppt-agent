"""Build ordered layout items (title, logos, content) from a redesign page's
planned regions, and classify which regions are rendered as reviewed source
crops versus editable content.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.source_content.raster_contracts import RasterBox
from services.source_content.raster_redesign_dedupe import suppressed_regions
from services.source_content.raster_redesign_plan_constants import IMAGE_ROLES, TABLE_CROP_MIN_SCALE
from services.source_content.raster_redesign_text import split_paragraphs
from services.source_content.raster_redesign_tree import Item


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
