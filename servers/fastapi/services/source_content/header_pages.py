"""Which source pages carry the deck's header furniture.

A restyle keeps a deck's header rhythm, but only for the pages that have one.
An ordinary content page carries the deck's running header -- one block whose
text repeats across the deck -- or its own page number.  Front and back matter
(a cover, a closing page) carry neither: their source is a title, a statement
and a footer, and requiring a header band on them rejects the composition the
page actually is.

The judgement is made from the pinned manifest alone, so it is deterministic
and identical on every attempt and every replay.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import re

from services.source_content.source_runs import normalize_source_text

#: A block is the deck's running header when its text repeats on at least this
#: share of pages.  Half is deliberately generous: a deck that changes its
#: header at a chapter boundary still has one, and a page that happens to share
#: a sentence with one neighbour does not become a header because of it.
_RUNNING_HEADER_SHARE = 0.5

_PAGE_NUMBER = re.compile(r"0*\d{1,3}")


def header_absent_pages(
    bindings: Sequence[Sequence[tuple[str, str]]] | None,
) -> frozenset[int]:
    """Indices of source pages carrying neither a running header nor a number."""

    if not bindings:
        return frozenset()
    counts: Counter[str] = Counter()
    for page in bindings:
        for _, text in page:
            normalized = normalize_source_text(text)
            if normalized:
                counts[normalized] += 1
    threshold = max(2, int(len(bindings) * _RUNNING_HEADER_SHARE))
    running = {text for text, count in counts.items() if count >= threshold}
    absent: set[int] = set()
    for index, page in enumerate(bindings):
        carries = False
        for _, text in page:
            normalized = normalize_source_text(text)
            if normalized in running:
                carries = True
                break
            if _PAGE_NUMBER.fullmatch(normalized) and int(normalized) == index + 1:
                carries = True
                break
        if not carries:
            absent.add(index)
    return frozenset(absent)


__all__ = ["header_absent_pages"]
