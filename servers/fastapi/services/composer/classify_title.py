"""Title and subtitle selection (rule 3) for the Composer classifier.

Placeholder type first, then the largest type size in the upper page, then --
when the manifest carries no sizes at all -- the first short one-line block.
The subtitle is the ``subTitle`` placeholder or the next-largest sized block
directly under the title that stands alone in its row.

A title candidate must carry words: a block with no letter at all, at most
``TITLE_MIN_CHARS - 1`` characters, or text shaped like a KPI callout
(``52%``, ``82주``) is never picked as title. Such a block is reported back
as a KPI id (see ``select_title``'s return) only when it was competitive for
the title spot -- large enough and high enough on the page -- so an unrelated
bare number elsewhere on the slide is left alone.
"""

from __future__ import annotations

from collections.abc import Sequence
import re

from services.composer.classify_bindings import TextBlock
from services.composer.classify_common import PageBox, overlap_ratio

TITLE_TOP_SHARE = 0.40
TITLE_MAX_CHARS = 160
TITLE_MIN_CHARS = 4
FALLBACK_TITLE_MAX_CHARS = 80
SUBTITLE_MAX_CHARS = 120
SUBTITLE_GAP_SHARE = 0.10

#: A title candidate narrower than this share of the page width may be
#: passed over for a wider, almost-as-large one (rule 3b).
NARROW_TITLE_SHARE = 0.60
WIDE_TITLE_SIZE_RATIO = 0.8

_TITLE_PLACEHOLDERS = frozenset({"title", "ctrTitle"})
_SUBTITLE_PLACEHOLDERS = frozenset({"subTitle"})

#: A bare number, optionally with a KPI unit (``52%``, ``82주``, ``39건``).
#: The unit is optional so plain digits (``2026``) match too -- they carry no
#: words either and must not become a title.
_KPI_UNIT_PATTERN = re.compile(r"^\s*[\d.,]+\s*(?:%|주|개|건|명|억|만|원|배|시간|일|x|X)?\s*$")
#: A signed percentage (``+18%``, ``-3.2%``), not covered by the pattern above
#: since it does not allow a leading sign.
_KPI_SIGNED_PERCENT_PATTERN = re.compile(r"^\s*[+-]?\d+(?:\.\d+)?%\s*$")


def select_title(
    blocks: Sequence[TextBlock], page: PageBox | None
) -> tuple[TextBlock | None, TextBlock | None, frozenset[str]]:
    """Rule 3: title by placeholder, then by size in the upper page, then by position.

    Returns ``(title, subtitle, kpi_ids)``. ``kpi_ids`` are blocks that were
    large and high enough to have won the title by size alone but were
    disqualified for carrying no words; the caller turns them into ``kpi``
    components instead of leaving them as plain paragraphs.
    """

    placeholder_title = _placeholder_block(blocks, _TITLE_PLACEHOLDERS)
    kpi_ids: frozenset[str] = frozenset()
    if placeholder_title is not None:
        title = placeholder_title
    else:
        zone = _zone_candidates(blocks, page)
        word_blocks = [block for block in zone if not _lacks_title_words(block)]
        kpi_blocks = [block for block in zone if _lacks_title_words(block)]
        title = _pick_title(word_blocks, page)
        kpi_ids = frozenset(block.block_id for block in kpi_blocks)
        if title is None and blocks and not any(block.dominant_size_pt for block in blocks):
            first = min(blocks, key=lambda block: block.order)
            single_line = first.line_count == 1 and first.projection.run_count == 1
            if first.char_count <= FALLBACK_TITLE_MAX_CHARS and single_line:
                title = first
    rest = [block for block in blocks if block is not title]
    subtitle = _placeholder_block(rest, _SUBTITLE_PLACEHOLDERS)
    if subtitle is None and title is not None:
        subtitle = _subtitle_below(title, rest, page)
    if subtitle is not None:
        kpi_ids = kpi_ids - {subtitle.block_id}
    return title, subtitle, kpi_ids


def _placeholder_block(blocks: Sequence[TextBlock], types: frozenset[str]) -> TextBlock | None:
    candidates = [block for block in blocks if block.placeholder_type in types]
    return min(candidates, key=_top_key, default=None)


def _top_key(block: TextBlock) -> tuple[float, int]:
    return (block.geometry.y if block.geometry is not None else float("inf"), block.order)


def _is_kpi_shaped(text: str) -> bool:
    return bool(_KPI_UNIT_PATTERN.match(text) or _KPI_SIGNED_PERCENT_PATTERN.match(text))


def _lacks_title_words(block: TextBlock) -> bool:
    """No letter at all, at most 3 characters, or a KPI-shaped number."""

    text = block.text.strip()
    if not any(char.isalpha() for char in text):
        return True
    if len(text) < TITLE_MIN_CHARS:
        return True
    return _is_kpi_shaped(text)


def _zone_candidates(blocks: Sequence[TextBlock], page: PageBox | None) -> list[TextBlock]:
    """Blocks large and high enough on the page to compete for the title spot."""

    return [
        block
        for block in blocks
        if block.dominant_size_pt
        and block.char_count <= TITLE_MAX_CHARS
        and (page is None or block.geometry is None or block.geometry.y / page.height <= TITLE_TOP_SHARE)
    ]


def _pick_title(candidates: Sequence[TextBlock], page: PageBox | None) -> TextBlock | None:
    """Largest size wins (ties -> smallest y); a much wider near-as-large block wins instead."""

    if not candidates:
        return None
    best = min(candidates, key=lambda block: (-block.dominant_size_pt, *_top_key(block)))
    if page is not None and best.geometry is not None and best.geometry.cx > 0 and page.width > 0:
        if best.geometry.cx / page.width < NARROW_TITLE_SHARE:
            wider = [
                block
                for block in candidates
                if block is not best
                and block.geometry is not None
                and block.geometry.cx > best.geometry.cx
                and block.dominant_size_pt >= WIDE_TITLE_SIZE_RATIO * best.dominant_size_pt
            ]
            if wider:
                best = min(wider, key=lambda block: (-block.dominant_size_pt, *_top_key(block)))
    return best


def _subtitle_below(title: TextBlock, blocks: Sequence[TextBlock], page: PageBox | None) -> TextBlock | None:
    """The next-largest sized block directly under the title, standing alone in its row."""

    if page is None or title.geometry is None:
        return None
    candidates: list[tuple[float, float, int, TextBlock]] = []
    for block in blocks:
        if block.geometry is None or not block.dominant_size_pt or block.char_count > SUBTITLE_MAX_CHARS:
            continue
        if title.dominant_size_pt and block.dominant_size_pt > title.dominant_size_pt:
            continue
        gap = (block.geometry.y - title.geometry.bottom) / page.height
        under = overlap_ratio(title.geometry.x, title.geometry.right, block.geometry.x, block.geometry.right) > 0
        if -0.02 <= gap <= SUBTITLE_GAP_SHARE and under and not _has_row_sibling(block, blocks):
            candidates.append((-block.dominant_size_pt, gap, block.order, block))
    return min(candidates, key=lambda item: item[:3])[3] if candidates else None


def _has_row_sibling(block: TextBlock, blocks: Sequence[TextBlock]) -> bool:
    """Column headers come in pairs on one row; a subtitle stands alone."""

    box = block.geometry
    return any(
        other is not block
        and other.geometry is not None
        and overlap_ratio(box.y, box.bottom, other.geometry.y, other.geometry.bottom, by="max") >= 0.5
        for other in blocks
    )


__all__ = ["select_title"]
