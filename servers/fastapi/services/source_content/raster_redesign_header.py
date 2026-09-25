"""Header band of a redesign page: kicker, title, subtitle, logos and side text.

Source slides in this family carry a small label above the title, a one-line
subtitle below it and a logo with a short motto at the top right. Keeping
those in the header (instead of the content area) gives the content more room
while each keeps its own editable text object and its characters verbatim.
"""

from __future__ import annotations

import hashlib
import math
from xml.sax.saxutils import escape

from services.source_content.raster_contracts import RasterBox
from services.source_content.raster_redesign_table import svg_line, svg_rect
from services.source_content.raster_redesign_text import Paragraph, layout_paragraphs, split_paragraphs, visual_width

FAMILY = "Noto Sans KR"
NAVY, COBALT, SLATE = "#081D49", "#0866FF", "#536785"
SURFACE, RULE = "#EEF7FF", "#CBDFF4"
TITLE_FONTS = (30.0, 27.0, 24.0, 21.0, 18.4)
COMPACT_TITLE_FONTS = (24.0, 21.0, 18.4)  # dense pages: header yields room to content
KICKER_FONT, SUBTITLE_FONT, SIDE_FONT = 14.1, 18.4, 14.1
LOGO_HEIGHT, LOGO_MAX_WIDTH, SIDE_MAX_WIDTH = 38.0, 170.0, 200.0
CLUSTER_MAX_WIDTH = 440.0
# services/svg_contract admits ``dy`` on <tspan> for pages whose root id is
# RASTER_REDESIGN_ROOT_ID, and the SVG export worker merges such paragraphs.
PARAGRAPH_TSPAN_DY = True


def element_tag(region_id: str) -> str:
    return hashlib.sha256(region_id.encode("utf-8")).hexdigest()[:10]


def text_xml(element_id, x, y, lines, *, bold_all=False, paragraph_dy=None):
    """One <text> per block; each wrapped line is a positioned <tspan>.

    ``paragraph_dy`` (default ``PARAGRAPH_TSPAN_DY``) emits ``x`` + ``dy`` lines,
    which the vendored converter merges into ONE multi-paragraph text frame
    when run with ``merge_paragraphs=True``. The ``y`` form is what the SVG
    contract admits today; the converter then emits one text box per line.
    """
    use_dy = PARAGRAPH_TSPAN_DY if paragraph_dy is None else paragraph_dy
    use_dy = use_dy and all(line.indent == 0.0 for line in lines)
    starts = [index for index, line in enumerate(lines) if index == 0 or line.paragraph_start]
    if use_dy and len(starts) > 1 and len(starts) == len(lines):
        # No soft-wrapped line: the converter would take the paragraph spacing
        # as its base line height and merge every paragraph into one line.
        # Emit one <text> per paragraph instead (each stays its own frame).
        return "".join(
            text_xml(f"{element_id}-p{index}", x, y, [line], bold_all=bold_all, paragraph_dy=True)
            for index, line in enumerate(lines)
        )
    spans, previous = [], None
    for line in lines:
        weight = "700" if (line.bold or bold_all) else "400"
        baseline = y + line.baseline
        if use_dy:
            position = f'x="{x:.2f}"' + (f' dy="{baseline - previous:.2f}"' if previous is not None else "")
        else:
            position = f'x="{x + line.indent:.2f}" y="{baseline:.2f}"'
        previous = baseline
        spans.append(
            f'<tspan {position} font-size="{line.size:.2f}" '
            f'font-weight="{weight}" fill="{line.color}">{escape(line.text)}</tspan>'
        )
    first = lines[0]
    return (
        f'<text id="{element_id}" x="{x + first.indent:.2f}" y="{y + first.baseline:.2f}" '
        f'font-family="{FAMILY}" font-size="{first.size:.2f}" fill="{first.color}">{"".join(spans)}</text>'
    )


def _flat(text):
    return [Paragraph(item.text, marker=item.marker, tight=True) for item in split_paragraphs(text or "")]


def split_header_items(title, items, source_size=None):
    """Move header-zone items out of the content list.

    Returns (kickers, subtitles, sides, header_images, rest). Only images that
    sit beside the title in the source (e.g. the brand logo) join the header;
    other icons stay in the content next to their labels.
    """

    kickers, subtitles, sides, images, rest = [], [], [], [], []
    if title is None and source_size is None:
        return kickers, subtitles, sides, images, list(items)
    # Without a title region, anchor on the source's usual title band.
    box = title.bbox if title is not None else RasterBox(
        x=0.0, y=source_size[1] * 0.045, width=source_size[0] * 0.55, height=source_size[1] * 0.08,
    )
    for item in items:
        region = item.region
        r = region.bbox
        lines = [line for line in (region.text or "").splitlines() if line.strip()]
        short = item.kind == "text" and 1 <= len(lines) <= 4 and visual_width(" ".join(lines)) <= 70
        beside = r.x >= box.x + box.width and r.y < box.y + box.height
        above = r.y + r.height <= box.y + box.height * 0.1 and r.x < box.x + box.width
        # A subtitle is one source line directly under the title; multi-line
        # blocks there are content (e.g. the first row of step cards).
        below = (box.y + box.height * 0.5 <= r.y <= box.y + box.height * 1.4
                 and r.x < box.x + box.width and not subtitles and len(lines) == 1)
        in_band = r.y + r.height <= box.y + box.height * 1.2
        if short and above:
            kickers.append(item)
        elif short and beside and in_band:
            sides.append(item)
        elif item.kind == "image" and beside and in_band and len(images) < 2:
            images.append(item)
        elif short and below:
            subtitles.append(item)
        else:
            rest.append(item)
    return kickers, subtitles, sides, images, rest

def place_header_group(title, items, source_size):
    """Place the analysis' ``header`` group by source position; nothing leaves the header.

    Right-half items (brand block, motto) join the top-right cluster; items
    above the title are kickers; the rest are subtitles under the title. A
    text line kept as pixels (``inline``) is a text line, not a logo: on the
    left it is a kicker or subtitle drawn at text scale.
    """
    kickers, subtitles, sides, images = [], [], [], []
    title_top = title.bbox.y if title is not None else source_size[1] * 0.045
    for item in items:
        box = item.region.bbox
        right_half = box.x + box.width / 2 > source_size[0] * 0.6
        if right_half:
            (sides if item.kind == "text" else images).append(item)
        elif item.kind != "text" and not item.inline:
            images.append(item)
        elif box.y + box.height <= title_top + 2:
            kickers.append(item)
        else:
            subtitles.append(item)
    return kickers, subtitles, sides, images, []


def _title_lines(title, width, fonts=TITLE_FONTS):
    paragraphs = _flat(title.text)
    for size in fonts:
        lines, height = layout_paragraphs(paragraphs, width, size, body_color=NAVY, heading_color=NAVY)
        if len(lines) <= 2 or size == fonts[-1]:
            return lines, height
    raise AssertionError("unreachable")


def _cluster_members(images, sides):
    from services.source_content.raster_redesign_pixel_lines import line_scale

    members = []
    for item in images:
        aspect = item.aspect
        if item.inline:  # a text line kept as pixels: text-sized, never shrunk to a logo
            native, scale, _ = line_scale(item, SIDE_FONT)
            height = native * scale
            members.append(("image", item, max(1, math.floor(height / aspect)), max(1, math.floor(height)), None))
            continue
        height = min(LOGO_HEIGHT, (item.native_height or LOGO_HEIGHT) * 1.25)
        height = max(height, item.min_scale * (item.native_height or 0.0))  # pixel text stays legible
        width = max(1, math.floor(min(LOGO_MAX_WIDTH, height / aspect)))
        members.append(("image", item, width, max(1, math.floor(width * aspect)), None))
    for item in sides:
        lines, height = layout_paragraphs(_flat(item.region.text), SIDE_MAX_WIDTH, SIDE_FONT,
                                          body_color=SLATE, heading_color=SLATE)
        width = max(line.indent + visual_width(line.text) * line.size for line in lines)
        members.append(("text", item, width, height, lines))
    return sorted(members, key=lambda member: member[1].region.bbox.x)


def _right_cluster(images, sides, page_number, right, top):
    """Brand cluster at the top right: side by side in source order, or stacked if too wide."""
    from services.raster_source_assets import raster_slot_id

    members = _cluster_members(images, sides)
    horizontal = sum(m[2] for m in members) + 12.0 * max(0, len(members) - 1) <= CLUSTER_MAX_WIDTH
    xml, cursor, cluster_width, bottom = [], top, 0.0, top
    left = right - (sum(m[2] for m in members) + 12.0 * max(0, len(members) - 1)) if horizontal else None
    for kind, item, width, height, lines in members:
        x = left if horizontal else right - width
        y = top if horizontal else cursor
        if kind == "image":
            xml.append(
                f'<image x="{x:.2f}" y="{y:.2f}" width="{width}" height="{height}" '
                f'href="{raster_slot_id(page_number, item.region.region_id)}" preserveAspectRatio="xMidYMid meet"/>'
            )
        else:
            xml.append(text_xml(f"rd-{element_tag(item.region.region_id)}-0", x, y, lines))
        bottom = max(bottom, y + height)
        if horizontal:
            left += width + 12.0
        else:
            cursor += height + 6.0
        cluster_width = max(cluster_width, width)
    if horizontal:
        cluster_width = sum(m[2] for m in members) + 12.0 * max(0, len(members) - 1)
    return xml, bottom, cluster_width

def title_image_xml(placements, top, page_number):
    from services.raster_source_assets import raster_slot_id

    return [
        f'<image x="{p.x:.2f}" y="{top + p.y:.2f}" '
        f'width="{max(1, math.floor(p.w))}" height="{max(1, math.floor(p.h))}" '
        f'href="{raster_slot_id(page_number, p.item.region.region_id)}" preserveAspectRatio="xMidYMid meet"/>'
        for p in placements
    ]


def _pixel_line_xml(item, x, width, font, top, page_number):
    """(svg fragments, height) of one header text line kept as pixels, at ``font`` text scale."""
    from services.source_content.raster_redesign_pixel_lines import pixel_line

    block = pixel_line([item], x, width, font)
    if block is None:
        raise ValueError("raster_redesign_content_overflow")
    return title_image_xml(block.placements, top, page_number), block.height


def render_header(*, title, images, kickers, subtitles, sides, page_number, width, margin, top, compact=False):
    """Return (svg fragments, header bottom y)."""

    xml = [f'<polygon points="{width - 250:.0f},0 {width},0 {width},118" fill="{SURFACE}"/>']
    cluster, cluster_bottom, cluster_width = _right_cluster(images, sides, page_number, width - margin, top)
    xml.extend(cluster)
    text_width = width - 2 * margin - max(cluster_width, 150.0) - 42.0
    cursor = top
    for item in kickers:
        if item.kind == "image":  # a kicker kept as pixels
            fragment, height = _pixel_line_xml(item, margin, text_width, KICKER_FONT, cursor, page_number)
            xml.extend(fragment)
            cursor += height + (3.0 if compact else 6.0)
            continue
        lines, height = layout_paragraphs(_flat(item.region.text), text_width, KICKER_FONT,
                                          body_color=COBALT, heading_color=COBALT)
        xml.append(text_xml(f"rd-{element_tag(item.region.region_id)}-0", margin, cursor, lines, bold_all=True))
        cursor += height + (3.0 if compact else 6.0)
    if title is not None and getattr(title, "items", None):
        # Title carried as pixels: one inline block at title-text scale in the title band.
        from services.source_content.raster_redesign_pixel_lines import pixel_line

        block = pixel_line(list(title.items), margin + 18.0, text_width - 18.0,
                           (COMPACT_TITLE_FONTS if compact else TITLE_FONTS)[0])
        if block is None:
            raise ValueError("raster_redesign_content_overflow")
        xml.append(svg_rect(margin, cursor + 4, 5, max(24.0, block.height - 8), COBALT))
        xml.extend(title_image_xml(block.placements, cursor, page_number))
        cursor += block.height
    elif title is not None:
        lines, title_height = _title_lines(title, text_width - 18.0, COMPACT_TITLE_FONTS if compact else TITLE_FONTS)
        xml.append(svg_rect(margin, cursor + 4, 5, max(24.0, title_height - 8), COBALT))
        xml.append(text_xml("rd-title", margin + 18.0, cursor, lines, bold_all=True))
        cursor += title_height
    for item in subtitles:
        font = 15.5 if compact else SUBTITLE_FONT
        cursor += 4.0 if compact else 8.0
        if item.kind == "image":  # a subtitle kept as pixels: drawn at subtitle text scale
            fragment, height = _pixel_line_xml(item, margin, text_width, font, cursor, page_number)
            xml.extend(fragment)
            cursor += height
            continue
        lines, height = layout_paragraphs(_flat(item.region.text), text_width, font,
                                          body_color=SLATE, heading_color=SLATE)
        xml.append(text_xml(f"rd-{element_tag(item.region.region_id)}-0", margin, cursor, lines))
        cursor += height
    bottom = max(top + 62.0, cursor + (8.0 if compact else 14.0), cluster_bottom + 8.0)
    xml.append(svg_line(margin, bottom, width - margin, bottom, RULE))
    xml.append(svg_rect(margin, bottom - 1.5, 84, 3, COBALT))
    return xml, bottom
