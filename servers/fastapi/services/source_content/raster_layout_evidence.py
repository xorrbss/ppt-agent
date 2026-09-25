"""Receipt evidence of a redesign page's composition (which layout the page ended in, and why).

A page ends in one of three compositions: the reflow (the layout engine),
the source-geometry arrangement, or one page-content crop. The last two are
fallbacks; the reason is either ``content_overflow`` (no reflow fits) or the
coherence rule a fitting reflow violated (see ``raster_layout_coherence``).

The composition is written into the canonical SVG as the id of the page
background, so receipt advisories stay a pure function of the SVG:

* geometry: ``{"code": "source_geometry_layout", "groupId": "source-geometry", "reason": ...}``
* page-content: ``{"code": "page_content_layout", "reason": ...}`` (next to the
  existing ``page-content`` group advisory, which is not duplicated)
"""

from __future__ import annotations

import re

GEOMETRY, PAGE_CONTENT, REFLOW = "geometry", "page-content", "reflow"
OVERFLOW = "content_overflow"
GEOMETRY_GROUP = "source-geometry"
_PREFIX = "rd-layout-"
_MARKER = re.compile(rf'id="{_PREFIX}(geometry|page-content)--([a-z_]+)"')


def background_id(composition: str, reason: str | None) -> str | None:
    """Id for the page background rect, or None for a reflow page."""
    if composition == REFLOW:
        return None
    return f"{_PREFIX}{composition}--{reason or OVERFLOW}"


def layout_advisories(composition: str, reason: str | None) -> tuple[dict, ...]:
    if composition == GEOMETRY:
        return ({"code": "source_geometry_layout", "groupId": GEOMETRY_GROUP, "reason": reason or OVERFLOW},)
    if composition == PAGE_CONTENT:
        return ({"code": "page_content_layout", "reason": reason or OVERFLOW},)
    return ()


def layout_advisories_from_svg(svg: str) -> tuple[dict, ...]:
    match = _MARKER.search(svg)
    return layout_advisories(match[1], match[2]) if match else ()
