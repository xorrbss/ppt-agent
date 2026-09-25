"""Screenshot-blue redesign of a reviewed raster page into editable SVG.

Unlike ``full_page_preserve`` (one picture in a frame) and ``reconstruct``
(source geometry replayed), this composes a new A4 page: a header with the
editable title, a content area chosen from the content mix, and a footer with
the page number. Text and structured tables stay editable; only screenshot,
logo and unstructured-table regions are placed as their reviewed crops via
raster slot ids. The full-page reference image is never rendered.

The page count, page order and every source character are preserved. When a
composition cannot fit above the 9.5 pt floor the call raises instead of
dropping content.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

from services.source_content.raster_redesign_chrome import footer_layout, render_footer_members, split_named_groups
from services.source_content.raster_redesign_fallback import (
    PAGE_CONTENT,
    advisories,
    apply_fallbacks,
    page_content_fallback,
    ranked_candidates,
)
from services.source_content.raster_redesign_semantic import has_groups

from services.source_content.raster_redesign_header import (
    COBALT,
    FAMILY,
    RULE,
    SLATE,
    SURFACE,
    element_tag,
    render_header,
    place_header_group,
    split_header_items,
    text_xml,
)
from services.source_content.raster_redesign_plan import (
    FLOOR_FONT,
    Placed,
    build_items,
    plan_content,
    redesign_crop_regions,
)
from services.source_content.raster_redesign_table import render_table, svg_line, svg_rect
from services.svg_contract import PAGE_NUMBER_ID, RASTER_REDESIGN_ROOT_ID

__all__ = ["RedesignRender", "render_redesign_page", "render_redesign_page_svg", "redesign_crop_regions",
           "redesign_layout_issue", "instruction_density"]

WHITE, PANEL = "#FFFFFF", "#F7FBFF"
PAGE_NUMBER_FONT = 14.0
_COMPACT = re.compile(r"compact|dense|concise|촘촘|간결|압축|빽빽", re.IGNORECASE)


def instruction_density(instruction: str | None) -> str:
    """Map a free-form instruction to a safe style knob; it never drops content."""
    return "compact" if instruction and _COMPACT.search(instruction) else "regular"


def _geometry(width: int, height: int):
    scale = min(width, height) / 840.0
    return dict(margin=48.0 * scale, top=26.0 * scale, footer=44.0 * scale, gap=20.0 * scale)


def _footer(page_number, width, height, geo, members):
    margin, footer = geo["margin"], geo["footer"]
    y = height - footer
    baseline = height - footer / 2 + 5
    return [
        svg_line(margin, y, width - margin, y, RULE),
        *render_footer_members(members, page_number, baseline, margin),
        f'<text id="{PAGE_NUMBER_ID}" x="{width - margin:.2f}" y="{baseline:.2f}" '
        f'font-family="{FAMILY}" font-size="{PAGE_NUMBER_FONT:.2f}" fill="{SLATE}" text-anchor="end">'
        f'{str(page_number).zfill(2)}</text>',
    ]

def _content_xml(placed: Placed, page_number: int) -> list[str]:
    from services.raster_source_assets import raster_slot_id

    if placed.kind == "panel":
        return [svg_rect(placed.x, placed.y, placed.w, placed.h, PANEL, f' rx="6" stroke="{RULE}" stroke-width="1"')]
    if placed.kind == "card":
        return [
            svg_rect(placed.x, placed.y, placed.w, placed.h, SURFACE, ' rx="6"'),
            svg_rect(placed.x, placed.y, 4, placed.h, COBALT),
        ]
    tag = element_tag(placed.item.region.region_id)
    if placed.kind == "text":
        return [text_xml(f"rd-{tag}-{placed.part}", placed.x, placed.y, placed.lines)] if placed.lines else []
    if placed.kind == "table":
        return render_table(placed.table, placed.x, placed.y, id_prefix=f"rd-{tag}", family=FAMILY, escape=escape)
    image_w = max(1, math.floor(placed.w))
    image_h = max(1, math.floor(placed.h))
    x = placed.x + (placed.w - image_w) / 2
    if placed.item.inline:  # pixel line: part of the text flow, no frame
        return [
            f'<image x="{placed.x:.2f}" y="{placed.y:.2f}" width="{image_w}" height="{image_h}" '
            f'href="{raster_slot_id(page_number, placed.item.region.region_id)}" preserveAspectRatio="xMidYMid meet"/>'
        ]
    return [
        svg_rect(x - 4, placed.y - 4, image_w + 8, image_h + 8, SURFACE, f' stroke="{RULE}" stroke-width="1"'),
        f'<image x="{x:.2f}" y="{placed.y:.2f}" width="{image_w}" height="{image_h}" '
        f'href="{raster_slot_id(page_number, placed.item.region.region_id)}" preserveAspectRatio="xMidYMid meet"/>',
    ]


def _body(analysis, items, page_number, top, width, height, geo, instruction, semantic=None,
          compact=False) -> list[str]:
    bottom = height - geo["footer"] - (8.0 if compact else 14.0)
    box = (geo["margin"], top, width - 2 * geo["margin"], bottom - top)
    if box[3] <= 0:
        raise ValueError("raster_redesign_content_overflow")
    body = []
    if items:
        plan = plan_content(
            items, box, source_size=(analysis.source.canvas_width_emu, analysis.source.canvas_height_emu),
            density=instruction_density(instruction), semantic=semantic,
        )
        for placed in plan.placements:
            if placed.y + placed.h > bottom + 0.5 or placed.x + placed.w > width - geo["margin"] + 0.5:
                raise ValueError("raster_redesign_content_overflow")
            body.extend(_content_xml(placed, page_number))
    return body


@dataclass(frozen=True)
class RedesignRender:
    svg: str
    advisories: tuple[dict, ...] = ()  # e.g. dense_group_pixel_fallback per replaced group


def render_redesign_page_svg(analysis, page_number: int, *, instruction: str | None = None) -> str:
    """Compose one redesign page as screenshot-blue A4 SVG (see render_redesign_page)."""
    return render_redesign_page(analysis, page_number, instruction=instruction).svg


def render_redesign_page(analysis, page_number: int, *, instruction: str | None = None) -> RedesignRender:
    """Compose one redesign page and report layout advisories.

    Caller (``render_raster_page_svg``) owns approval checks; this function
    owns layout only. Order of fit strategies: editable compositions with the
    regular then compact header; then the fewest groups as >=0.6x source-pixel
    crops (advisory ``dense_group_pixel_fallback``); otherwise ``ValueError``.
    """

    if page_number < 1 or page_number > len(analysis.pages):
        raise ValueError("raster_page_number_invalid")
    page = analysis.pages[page_number - 1]
    width, height = analysis.target_canvas.width_units, analysis.target_canvas.height_units
    geo = _geometry(width, height)
    title, logos, items = build_items(page)
    if title is None and not items:
        raise ValueError("raster_page_has_no_approved_content")
    header_group, footer_group, items = split_named_groups([*logos, *items])
    members = footer_layout(footer_group, width, geo["margin"])
    if members is None:  # the footer does not fit on one line: keep it as content
        items, members = [*items, *footer_group], []
    kickers, subtitles, sides, header_images, items = split_header_items(
        title, items, (analysis.source.canvas_width_emu, analysis.source.canvas_height_emu),
    )
    # The analysis' header group is placed by source position like any header
    # zone item (kicker above, brand beside, subtitle below); nothing leaves the header.
    h_kickers, h_subtitles, h_sides, h_images, h_rest = place_header_group(
        title, header_group, (analysis.source.canvas_width_emu, analysis.source.canvas_height_emu),
    )
    kickers = h_kickers + kickers
    sides = h_sides + sides
    subtitles = h_subtitles + [item for item in h_rest if item.kind == "text"] + subtitles
    header_images += h_images + [item for item in h_rest if item.kind == "image"]
    chosen, candidates, semantic = [], None, has_groups(items)
    while True:
        attempt = apply_fallbacks(items, chosen)
        try:
            header_xml, body = _compose(analysis, attempt, page_number, geo, width, height, instruction,
                                        title, header_images, kickers, subtitles, sides, semantic)
            break
        except ValueError as error:
            if str(error) != "raster_redesign_content_overflow":
                raise
            if candidates is None:
                candidates = ranked_candidates(page, items, FLOOR_FONT)
            if chosen and chosen[0].group_id == PAGE_CONTENT:
                raise
            if len(chosen) < len(candidates):
                chosen.append(candidates[len(chosen)])
                continue
            # Every group as pixels still overflows: keep the source's own dense
            # arrangement as one crop of the content area (title stays editable).
            content = page_content_fallback(analysis, page)
            if content is None:
                raise
            chosen = [content]
    xml = [
        f'<svg xmlns="http://www.w3.org/2000/svg" id="{RASTER_REDESIGN_ROOT_ID}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{WHITE}"/>',
        *header_xml, *body, *_footer(page_number, width, height, geo, members), "</svg>",
    ]
    result = "".join(xml)
    hrefs = re.findall(r'href="(source-slot-[^"]+)"', result)
    if len(hrefs) != len(set(hrefs)):
        raise ValueError("raster_redesign_region_drawn_twice")  # every region is drawn at most once
    if len(result.encode("utf-8")) >= 2 * 1024 * 1024:
        raise ValueError("raster_svg_size_limit")
    return RedesignRender(result, advisories(chosen))


def _compose(analysis, items, page_number, geo, width, height, instruction,
             title, header_images, kickers, subtitles, sides, semantic=None):
    """Regular header first, then a compact one; raises on overflow."""
    for compact in (False, True):
        header_xml, header_bottom = render_header(
            title=title, images=header_images, kickers=kickers, subtitles=subtitles, sides=sides,
            page_number=page_number, width=width, margin=geo["margin"], top=geo["top"], compact=compact,
        )
        try:
            gap = geo["gap"] * (0.6 if compact else 1.0)  # compact header: tighter gap under the rule
            body = _body(analysis, items, page_number, header_bottom + gap, width, height, geo, instruction,
                         semantic, compact)
            return header_xml, body
        except ValueError as error:
            if compact or str(error) != "raster_redesign_content_overflow":
                raise
    raise AssertionError("unreachable")

def redesign_layout_issue(analysis, page_number: int, *, instruction: str | None = None) -> str | None:
    """Preview whether a redesign page fits; returns the error code or None."""
    try:
        render_redesign_page_svg(analysis, page_number, instruction=instruction)
    except ValueError as error:
        return str(error)
    return None
