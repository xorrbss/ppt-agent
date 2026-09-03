"""Furniture detection: page numbers, footers, running headers, dates, marks.

Furniture is text the source repeats or pins to the page edge; the Composer
renders it in the template's bands, never as body content. Every rule is a
pure function of the block, the page box and the deck context.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

from services.composer.classify_bindings import TextBlock
from services.composer.classify_common import PageBox
from services.composer.plan import FurnitureKind
from services.source_content.source_runs import normalize_source_text

_PLACEHOLDER_FURNITURE: dict[str, FurnitureKind] = {
    "ftr": "footer",
    "sldNum": "page_number",
    "dt": "date",
}

#: Page-number forms that are unambiguous wherever they sit on the page.
_PAGE_NUMBER_ANYWHERE = (
    re.compile(r"^\s*\d+\s*/\s*\d+\s*$"),
    re.compile(r"^\s*\d+\s*of\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:p|pg|page)\.?\s*\d+\s*$", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*(?:페이지|쪽)\s*$"),
)
#: A bare number is a page number only at the page edge or when set small; in
#: the middle of the page it is a step badge or a KPI the restyle must keep.
_BARE_NUMBER = re.compile(r"^\s*-?\s*\d+\s*-?\s*$")
_CONFIDENTIAL = re.compile(
    r"^[\s\[\(【<]*(?:strictly\s+|company\s+|internal\s+)?confidential"
    r"(?:\s*(?:&|and)\s*proprietary)?[\s\]\)】>.]*$"
    r"|^[\s\[\(【<]*(?:사내\s*)?(?:대외비|기밀|비밀)(?:\s*문서|\s*자료)?[\s\]\)】>.]*$",
    re.IGNORECASE,
)
_DATE = re.compile(
    r"^\s*\d{4}\s*[.\-/년]\s*\d{1,2}\s*[.\-/월]\s*\d{1,2}\s*일?\s*\.?\s*$"
    r"|^\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,?\s*\d{4})?\s*$"
    r"|^\s*\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(?:\s+\d{4})?\s*$",
    re.IGNORECASE,
)

_BOTTOM_BAND = 0.92
_EDGE_TOP = 0.10
_EDGE_BOTTOM = 0.85
_HEADER_BAND = 0.15
_SHORT_FOOTER_CHARS = 60
_LARGE_NUMBER_PT = 18.0
_SMALL_NUMBER_PT = 14.0


def classify_furniture(
    block: TextBlock, page: PageBox | None, deck_context: Mapping[str, Any]
) -> FurnitureKind | None:
    """Return the furniture kind of a text block, or ``None`` for content.

    Rules, in order: placeholder type (``ftr``/``sldNum``/``dt``), a
    confidentiality mark, a page-number pattern, text repeated across the deck,
    and a short block whose bottom sits in the lowest 8% of the page. Edge
    rules need a captured page size; unit extents have no real bottom edge.
    """

    placeholder = _PLACEHOLDER_FURNITURE.get(block.placeholder_type or "")
    if placeholder is not None:
        return placeholder
    text = block.text
    if _CONFIDENTIAL.match(text):
        return "confidential"
    if is_page_number(block, page):
        return "page_number"
    repeated = {normalize_source_text(item) for item in deck_context.get("repeated_texts") or ()}
    if text in repeated:
        return _repeated_kind(block, page)
    if (
        _edge_known(block, page)
        and block.geometry.bottom / page.height > _BOTTOM_BAND
        and block.char_count <= _SHORT_FOOTER_CHARS
    ):
        return "date" if _DATE.match(text) else "footer"
    return None


def _edge_known(block: TextBlock, page: PageBox | None) -> bool:
    return page is not None and page.captured and block.geometry is not None


def is_page_number(block: TextBlock, page: PageBox | None) -> bool:
    text = block.text
    if block.dominant_size_pt is not None and block.dominant_size_pt >= _LARGE_NUMBER_PT:
        return False
    if any(pattern.match(text) for pattern in _PAGE_NUMBER_ANYWHERE):
        return True
    if _BARE_NUMBER.match(text) is None:
        return False
    if _edge_known(block, page):
        top = block.geometry.y / page.height
        bottom = block.geometry.bottom / page.height
        return bottom > _EDGE_BOTTOM or top < _EDGE_TOP
    return block.dominant_size_pt is None or block.dominant_size_pt <= _SMALL_NUMBER_PT


def _repeated_kind(block: TextBlock, page: PageBox | None) -> FurnitureKind:
    if _DATE.match(block.text):
        return "date"
    if _edge_known(block, page) and block.geometry.bottom / page.height <= _HEADER_BAND:
        return "running_header"
    return "footer"


__all__ = ["classify_furniture", "is_page_number"]
