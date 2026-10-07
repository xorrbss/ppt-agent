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

from services.source_content.raster_layout_coherence import coherence_issue
from services.source_content.raster_layout_evidence import background_id, layout_advisories
from services.source_content.raster_layout_geometry import geometry_plan
from services.source_content.raster_layout_parts import page_parts
from services.source_content.raster_redesign_chrome import render_footer_members
from services.source_content.raster_redesign_fallback import (
    PAGE_CONTENT,
    advisories,
    apply_fallbacks,
    group_fallbacks,
    page_content_fallback,
    ranked_candidates,
    region_fallbacks,
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
INCOHERENT = "raster_redesign_incoherent:"
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
        return render_table(placed.table, placed.x, placed.y, id_prefix=f"rd-{tag}", family=FAMILY, escape=escape,
                            image_href=lambda region_id: raster_slot_id(page_number, region_id))
    image_w = max(1, math.floor(placed.w))
    image_h = max(1, math.floor(placed.h))
    x = placed.x + (placed.w - image_w) / 2
    if placed.kind == "crop" or placed.item.inline:  # source-geometry crop or pixel line: no frame
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
          compact=False, accept=None) -> list[str]:
    bottom = height - geo["footer"] - (8.0 if compact else 14.0)
    box = (geo["margin"], top, width - 2 * geo["margin"], bottom - top)
    if box[3] <= 0:
        raise ValueError("raster_redesign_content_overflow")
    body = []
    if items:
        plan = plan_content(
            items, box, source_size=(analysis.source.canvas_width_emu, analysis.source.canvas_height_emu),
            density=instruction_density(instruction), semantic=semantic, accept=accept,
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
    composition: str = "reflow"  # "reflow" | "geometry" | "page-content"
    incoherence: str | None = None  # the coherence rule a rejected reflow violated


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
    title, _, items = build_items(page)
    if title is None and not items:
        raise ValueError("raster_page_has_no_approved_content")
    parts = page_parts(analysis, page)
    title, members, items = parts.title, parts.footer, parts.items
    kickers, subtitles, sides, header_images = parts.kickers, parts.subtitles, parts.sides, parts.header_images
    chosen, candidates, semantic = [], None, has_groups(items)
    composition, incoherence, reason = "reflow", None, None
    args = (analysis, page_number, geo, width, height, instruction, title, header_images, kickers, subtitles, sides)
    while True:
        attempt = apply_fallbacks(items, chosen)
        # A reflow must keep the source's structure (sections, order, labels,
        # composites): only admissible compositions are chosen.
        accept = _coherence(analysis, page, chosen)
        try:
            header_xml, body = _compose(*args, attempt, semantic, accept)
            composition = "page-content" if chosen and chosen[0].group_id == PAGE_CONTENT else "reflow"
            break
        except ValueError as error:
            if str(error).startswith(INCOHERENT):
                incoherence = reason = str(error)[len(INCOHERENT):]
                header_xml, body, chosen, composition = _faithful(analysis, page, items, chosen, semantic, args)
                break
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
            # arrangement, block by block (editable where it fits, else each
            # group as its crop); only when even that is not legible, as one
            # crop of the content area (title stays editable).
            reason = "content_overflow"
            geometry = _geometry_compose(analysis, page, items, *args[1:5], *args[6:])
            if geometry is not None:
                (header_xml, body, chosen), composition = geometry, "geometry"
                break
            content = page_content_fallback(analysis, page)
            if content is None:
                raise
            chosen = [content]
    marker = background_id(composition, reason)  # receipt evidence of a fallback composition
    marker = f' id="{marker}"' if marker else ""
    xml = [
        f'<svg xmlns="http://www.w3.org/2000/svg" id="{RASTER_REDESIGN_ROOT_ID}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        f'<rect{marker} width="{width}" height="{height}" fill="{WHITE}"/>',
        *header_xml, *body, *_footer(page_number, width, height, geo, members), "</svg>",
    ]
    result = "".join(xml)
    from services.source_content.raster_target import require_raster_physical_readability

    require_raster_physical_readability(result, analysis.target_canvas)
    hrefs = re.findall(r'href="(source-slot-[^"]+)"', result)
    if len(hrefs) != len(set(hrefs)):
        raise ValueError("raster_redesign_region_drawn_twice")  # every region is drawn at most once
    if len(result.encode("utf-8")) >= 2 * 1024 * 1024:
        raise ValueError("raster_svg_size_limit")
    return RedesignRender(result, advisories(chosen) + layout_advisories(composition, reason), composition,
                          incoherence)


def _compose(analysis, page_number, geo, width, height, instruction,
             title, header_images, kickers, subtitles, sides, items, semantic=None, accept=None):
    """Regular header first, then a compact one; raises on overflow (or when nothing is admissible)."""
    for compact in (False, True):
        header_xml, header_bottom = render_header(
            title=title, images=header_images, kickers=kickers, subtitles=subtitles, sides=sides,
            page_number=page_number, width=width, margin=geo["margin"], top=geo["top"], compact=compact,
        )
        try:
            gap = geo["gap"] * (0.6 if compact else 1.0)  # compact header: tighter gap under the rule
            body = _body(analysis, items, page_number, header_bottom + gap, width, height, geo, instruction,
                         semantic, compact, accept)
            return header_xml, body
        except ValueError as error:
            if compact or str(error) != "raster_redesign_content_overflow":
                raise
    raise AssertionError("unreachable")


def _coherence(analysis, page, chosen):
    """``accept`` for plan_content: None when a composition keeps the source structure."""
    return lambda placements: coherence_issue(analysis, page, placements, chosen)


def _faithful(analysis, page, items, chosen, semantic, args):
    """No admissible reflow: the source arrangement, else one page-content crop, else the best reflow.

    Returns (header, body, chosen, composition).
    """
    geometry = _geometry_compose(analysis, page, items, *args[1:5], *args[6:])
    if geometry is not None:
        return (*geometry, "geometry")
    content = page_content_fallback(analysis, page)
    if content is not None:
        try:
            return (*_compose(*args, apply_fallbacks(items, [content]), semantic), [content], "page-content")
        except ValueError as error:
            if str(error) != "raster_redesign_content_overflow":
                raise
    return (*_compose(*args, apply_fallbacks(items, chosen), semantic), chosen, "reflow")


def _geometry_compose(analysis, page, items, page_number, geo, width, height,
                      title, header_images, kickers, subtitles, sides):
    """(header, body, chosen fallbacks) of the source-geometry composition, or None."""
    header_xml, header_bottom = render_header(
        title=title, images=header_images, kickers=kickers, subtitles=subtitles, sides=sides,
        page_number=page_number, width=width, margin=geo["margin"], top=geo["top"],
    )
    top, bottom = header_bottom + geo["gap"], height - geo["footer"] - 14.0
    plan = geometry_plan(page, items, (geo["margin"], top, width - 2 * geo["margin"], bottom - top))
    if plan is None:
        return None
    order = [f.region.region_id for f in (*group_fallbacks(page).values(), *region_fallbacks(page).values())]
    chosen = sorted(plan.chosen, key=lambda fallback: order.index(fallback.region.region_id))
    return header_xml, [xml for placed in plan.placements for xml in _content_xml(placed, page_number)], chosen


def redesign_layout_issue(analysis, page_number: int, *, instruction: str | None = None) -> str | None:
    """Preview whether a redesign page fits; returns the error code or None."""
    try:
        render_redesign_page_svg(analysis, page_number, instruction=instruction)
    except ValueError as error:
        return str(error)
    return None
