"""Named page groups: ``header`` (subtitle under the title) and ``footer``.

A ``footer`` group renders as a thin footer line: its text left-aligned and
the system page number on the right, never as a card. Anything that cannot sit
legibly on that line (too long, or an image that would need shrinking below
its legibility floor) returns to the content flow instead of being dropped.
"""

from __future__ import annotations

import math

from services.source_content.raster_redesign_header import FAMILY, SLATE, element_tag
from services.source_content.raster_redesign_text import visual_width
from services.source_content.raster_svg_layout import MIN_FONT_UNITS

FOOTER_FONT = math.ceil(MIN_FONT_UNITS * 100) / 100
FOOTER_IMAGE_MAX_HEIGHT = 24.0
FOOTER_GAP = 16.0
PAGE_NUMBER_ROOM = 70.0


def split_named_groups(items):
    """(header items, footer items, rest) by the analysis group ids."""
    header, footer, rest = [], [], []
    for item in items:
        group = getattr(item.region, "group_id", None)
        (header if group == "header" else footer if group == "footer" else rest).append(item)
    return header, footer, rest


def _footer_members(items):
    members = []
    for item in items:
        if item.kind == "text":
            text = " ".join(" ".join(line.split()) for line in (item.region.text or "").splitlines() if line.strip())
            members.append(("text", item, visual_width(text) * FOOTER_FONT, text))
        elif item.kind == "image" and item.native_height:
            height = min(FOOTER_IMAGE_MAX_HEIGHT, item.native_height * 1.25)
            if item.min_scale and height < item.min_scale * item.native_height:
                return None
            members.append(("image", item, height / item.aspect, height))
        else:
            return None
    return members


def footer_layout(items, width, margin):
    """Members for the footer line, or None when they must go to the content."""
    if not items:
        return []
    members = _footer_members(items)
    if members is None:
        return None
    total = sum(member[2] for member in members) + FOOTER_GAP * (len(members) - 1)
    return members if total <= width - 2 * margin - PAGE_NUMBER_ROOM else None


def render_footer_members(members, page_number, baseline, margin):
    from xml.sax.saxutils import escape

    from services.raster_source_assets import raster_slot_id

    xml, left = [], margin
    for kind, item, member_width, value in members:
        if kind == "text":
            xml.append(
                f'<text id="rd-{element_tag(item.region.region_id)}-0" x="{left:.2f}" y="{baseline:.2f}" '
                f'font-family="{FAMILY}" font-size="{FOOTER_FONT:.2f}" fill="{SLATE}">{escape(value)}</text>'
            )
        else:
            image_w, image_h = max(1, math.floor(member_width)), max(1, math.floor(value))
            xml.append(
                f'<image x="{left:.2f}" y="{baseline - image_h + 4:.2f}" width="{image_w}" height="{image_h}" '
                f'href="{raster_slot_id(page_number, item.region.region_id)}" preserveAspectRatio="xMidYMid meet"/>'
            )
        left += member_width + FOOTER_GAP
    return xml
