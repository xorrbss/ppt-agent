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

from dataclasses import dataclass

from services.source_content.raster_redesign_chrome import footer_layout, split_named_groups
from services.source_content.raster_redesign_header import place_header_group, split_header_items
from services.source_content.raster_redesign_plan import build_items

# Without a title region the usual title band ends at this fraction of the page height.
TITLE_BAND_BOTTOM = 0.125


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
    members = footer_layout(footer, analysis.target_canvas.width_units, page_margin(analysis))
    if members is None:  # the footer does not fit on one line: keep it as content
        content, members = [*content, *footer], []
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
