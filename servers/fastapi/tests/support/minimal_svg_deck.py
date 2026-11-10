"""The smallest svg deck that publishes a revision.

Generic workspace and publication tests used an authored deck as their
neutral fixture. The authored driver was removed on 2026-11-10, so a new deck
is svg; publishing an svg revision re-stamps page numbers from the deck's
style snapshot and needs at least one svg page.
"""

from __future__ import annotations

import uuid

from models.sql.slide import SlideModel
from services.svg_contract import source_digest
from services.svg_page_publication import set_svg_page

MINIMAL_SVG_STYLE_SNAPSHOT = {"tokens": {"body_font": "Noto Sans KR", "primary": "#2563EB"}}
MINIMAL_SVG_PAGE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
    '<text x="10" y="20" font-family="Noto Sans KR" font-size="16" fill="#000000">body</text></svg>'
)


def minimal_svg_theme() -> dict:
    return {"mode": "svg", "style": "test", "svg": {"styleSnapshot": dict(MINIMAL_SVG_STYLE_SNAPSHOT)}}


def minimal_svg_slide(presentation_id: uuid.UUID, index: int = 0, **fields) -> SlideModel:
    slide = SlideModel(
        presentation=presentation_id,
        layout_group="custom",
        layout="content",
        index=index,
        content={"title": f"slide-{index}"},
        **fields,
    )
    set_svg_page(
        slide,
        svg_content=MINIMAL_SVG_PAGE,
        content=slide.content,
        quality={"source_digest": source_digest(MINIMAL_SVG_PAGE)},
    )
    slide.source_format = "svg"
    return slide


__all__ = [
    "MINIMAL_SVG_PAGE",
    "MINIMAL_SVG_STYLE_SNAPSHOT",
    "minimal_svg_slide",
    "minimal_svg_theme",
]
