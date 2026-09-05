"""Pure grid-layout arithmetic for the authored-run contact sheet.

No Pillow, no filesystem access here: given (possibly missing) per-cell
pixel dimensions and a target thumbnail width, decide each row's height and
how many rows fit on one page before the sheet must be split into
``contact-sheet-1.png``, ``contact-sheet-2.png`` etc. See
``authored_run_contact_sheet_render.py`` for the actual drawing and
``authored_run_contact_sheet_metrics.py`` for the metrics-table arithmetic.
"""

from __future__ import annotations


def scale_to_width(orig_width: int, orig_height: int, target_width: int) -> int:
    """Height a ``target_width``-wide thumbnail keeps for an image of size
    ``(orig_width, orig_height)``, preserving aspect ratio."""
    if orig_width <= 0:
        return target_width
    return max(1, round(orig_height * target_width / orig_width))


def compute_row_height(
    cell_heights: list[int | None],
    *,
    placeholder_height: int,
    label_padding: int = 0,
) -> int:
    """Height of one contact-sheet row: the tallest present cell (or the
    placeholder height when every cell in the row is a gap), plus a fixed
    padding for the row label / spacing between rows.
    """
    present = [h for h in cell_heights if h is not None]
    tallest = max(present) if present else placeholder_height
    return tallest + label_padding


def plan_pages(
    row_heights: list[int],
    *,
    header_height: int,
    max_height: int,
) -> list[tuple[int, int]]:
    """Split rows into pages so each page (header + its rows) stays under
    ``max_height`` pixels tall wherever possible.

    Returns half-open ``(start, end)`` row-index ranges, one per page. A
    single row taller than the budget on its own still gets its own page --
    it is never dropped or silently truncated. An empty ``row_heights``
    yields one empty page, so callers can always render at least one file.
    """
    if not row_heights:
        return [(0, 0)]

    pages: list[tuple[int, int]] = []
    start = 0
    running = header_height
    for i, height in enumerate(row_heights):
        projected = running + height
        if i > start and projected > max_height:
            pages.append((start, i))
            start = i
            running = header_height + height
        else:
            running = projected
    pages.append((start, len(row_heights)))
    return pages
