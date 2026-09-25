"""One partition of a redesign page into header band, footer line and content.

The layout and the page-content fallback both use this partition, so every
region lives in exactly one zone: it is never drawn in the header and again
inside a content crop.

Header-zone invariant: the analysis' ``header`` group is chrome only while it
sits above the content. A member whose vertical centre lies below the top of
the content (the topmost content item below the title) is content, whatever
its group label, and joins the content flow at its reading-order position.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from services.source_content.raster_redesign_chrome import footer_layout, split_named_groups
from services.source_content.raster_redesign_header import place_header_group, split_header_items
from services.source_content.raster_redesign_plan import build_items

# Without a title region the usual title band ends at this fraction of the page height.
TITLE_BAND_BOTTOM = 0.125
# Ungrouped items below all other content and inside this bottom fraction of the page are footer.
FOOTER_BAND = 0.12
# Pixels standing in for text are shown at most this much larger than on the source page.
TEXT_PIXEL_UPSCALE = 1.25


@dataclass(frozen=True)
class PageParts:
    title: object
    kickers: list
    subtitles: list
    sides: list
    header_images: list
    footer: list  # footer-line members (see ``footer_layout``); empty when the footer joined the content
    items: list  # content items in reading order


def page_margin(analysis) -> float:
    width, height = analysis.target_canvas.width_units, analysis.target_canvas.height_units
    return 48.0 * min(width, height) / 840.0


def header_strays(title, header, content, source_size) -> list:
    """Header-group members lying inside the content area (see module docstring)."""
    bottom = title.bbox.y + title.bbox.height if title is not None else source_size[1] * TITLE_BAND_BOTTOM
    tops = [item.region.bbox.y for item in content if item.region.bbox.y >= bottom]
    if not tops:
        return []
    content_top = min(tops)
    return [item for item in header if item.region.bbox.y + item.region.bbox.height / 2 > content_top]


def footer_strays(content, source_size) -> list:
    """Ungrouped pixel crops that sit in the bottom band below every other content item.

    They are the source's own footer (a running label, a page number kept as
    pixels, e.g. ink no structured region carried), which the analysis could
    not group. The footer line takes them when they fit on it, otherwise they
    return to the end of the content. Text and pixel lines keep their analysis
    placement.
    """
    band = source_size[1] * (1 - FOOTER_BAND)
    low = [item for item in content if getattr(item.region, "group_id", None) is None
           and item.kind == "image" and not item.inline and item.region.bbox.y >= band]
    if not low:
        return []
    ids = {id(item) for item in low}
    above = [item.region.bbox.y + item.region.bbox.height for item in content if id(item) not in ids]
    floor = max(above, default=0.0)
    return [item for item in low if item.region.bbox.y >= floor]


def _text_pixel_cap(item, analysis):
    """Cap a crop that stands in for text at ``TEXT_PIXEL_UPSCALE`` x its source-page size.

    A small label kept as pixels must not become a magnified box: the page
    scale is the content width over the source width (units per source px).
    """
    region = item.region
    px = getattr(region, "source_pixel_bbox", None)
    if item.kind != "image" or item.inline or not getattr(region, "editable_fallback", False) or not px or not px[2]:
        return item
    source_px_width = analysis.source.canvas_width_emu * px[2] / region.bbox.width
    content_width = analysis.target_canvas.width_units - 2 * page_margin(analysis)
    return replace(item, max_scale=max(item.min_scale, TEXT_PIXEL_UPSCALE * content_width / source_px_width))


def page_parts(analysis, page) -> PageParts:
    size = (analysis.source.canvas_width_emu, analysis.source.canvas_height_emu)
    title, logos, items = build_items(page)
    ordered = [*logos, *items]
    position = {id(item): index for index, item in enumerate(ordered)}
    header, footer, rest = split_named_groups(ordered)
    kickers, subtitles, sides, header_images, content = split_header_items(title, rest, size)
    strays = {id(item) for item in header_strays(title, header, content, size)}
    if strays:
        header = [item for item in header if id(item) not in strays]
        content = sorted([*content, *(item for item in ordered if id(item) in strays)],
                         key=lambda item: position[id(item)])
    strays = {id(item) for item in footer_strays(content, size)}
    if strays:
        footer = sorted([*footer, *(item for item in content if id(item) in strays)],
                        key=lambda item: position[id(item)])
        content = [item for item in content if id(item) not in strays]
    members = footer_layout(footer, analysis.target_canvas.width_units, page_margin(analysis))
    if members is None:  # the footer does not fit on one line: keep it as content
        content, members = [*content, *footer], []
    content = [_text_pixel_cap(item, analysis) for item in content]
    # The header group is placed by source position like any header-zone item
    # (kicker above, brand beside, subtitle below); nothing else leaves the header.
    h_kickers, h_subtitles, h_sides, h_images, _ = place_header_group(title, header, size)
    return PageParts(
        title=title,
        kickers=h_kickers + kickers,
        subtitles=h_subtitles + subtitles,
        sides=h_sides + sides,
        header_images=header_images + h_images,
        footer=members,
        items=content,
    )
