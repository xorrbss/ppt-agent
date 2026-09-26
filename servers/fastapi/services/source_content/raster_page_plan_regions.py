"""Data model for one planned page region and the plan of a page."""

from __future__ import annotations

from dataclasses import dataclass

from services.source_content.raster_captures import Box
from services.source_content.raster_redesign_ocr import RedesignWord


@dataclass(frozen=True)
class PixelCell:
    """An unverified cell of a native table kept as its source pixels
    (``raster_table_overlay``): position, crop box and the tokens it shows."""

    row: int
    col: int
    bbox: Box
    word_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class PlannedRegion:
    role: str
    bbox: Box
    word_indices: tuple[int, ...] = ()
    text: str | None = None
    table_cells: tuple[tuple[str, ...], ...] | None = None
    confidence: float = 1.0
    review_reason: str | None = None
    reason: str = ""
    # Low-confidence text kept as source pixels; ``text``/``table_cells``
    # carry the OCR reading only as a suggestion for optional review.
    editable_fallback: bool = False
    # Card/section membership and, for body text, how many leading lines
    # form the card heading.
    group_id: str | None = None
    heading_line_count: int = 0
    # Native tables only: unverified cells drawn from source pixels over the
    # table; their tokens stay in ``word_indices`` until the analysis is sealed.
    pixel_cells: tuple[PixelCell, ...] = ()


@dataclass(frozen=True)
class PagePlan:
    regions: tuple[PlannedRegion, ...]
    unpromoted: tuple[int, ...]
    # Token inventory when corrected readings were added (None: the OCR words).
    words: tuple[RedesignWord, ...] | None = None
    # Audit log of accepted text corrections: "before => after".
    corrections: tuple[str, ...] = ()
    structured_by: str | None = None
    # Page-level notes for review (e.g. "heuristic_plan:structuring_failed:TimeoutError").
    advisories: tuple[str, ...] = ()
