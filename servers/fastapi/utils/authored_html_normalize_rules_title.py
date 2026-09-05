"""Title-role ``<br>`` normalisation for authored HTML (QUAL-12).

Models sometimes force a line break inside a Korean slide title with ``<br>``
or ``<br/>``, which splits the title at a point the model chose rather than
where the renderer's wrapping would put it. This rule replaces every ``<br>``
inside a *title-role* element with a single space so the title wraps on its
own. It is one rule of ``utils/authored_html_normalize.py`` and follows the
same contract: a document without a matching finding is returned
byte-identical, tags are located by exact byte span through
``html.parser.HTMLParser`` and never reserialized, and on anything the
parser cannot resolve cleanly (unbalanced tags) the document is left alone.

How a title is recognised
-------------------------
There is no single ``data-role="title"`` marker. The markers real authored
slides carry, and that the rest of the pipeline already keys on, are:

* ``data-header-part="title"`` -- the header-contract marker the slide
  prompt asks for (``utils/authored_header_prompt.py``; vocabulary in
  ``models/authored_header_contract.py``).
* ``data-semantic-role="title"`` -- emitted by the placeholder and composer
  builders (``services/authored_author_stage_placeholder.py``,
  ``services/composer/render_cover.py``).
* A ``data-qid`` in the primary title vocabulary (``title``, ``text.title``,
  ``cover.title``, ``header.title``, ``slide.title``, their ``.``-prefixed
  descendants, and ``*.title.text``).
* An ``<h1>`` or ``<h2>`` element.

This is the same set ``_is_primary_title_candidate`` in
``services/authored_composition_policy.py`` uses to find a slide's primary
title, so the normaliser and the composition policy agree on what a title is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

from utils.renderer_markup_policy import VOID_TAGS

TITLE_LINE_BREAK_RULE_ID = "title_role_line_break_replaced"

_TITLE_TAGS = frozenset({"h1", "h2"})
_PRIMARY_TITLE_QIDS = ("cover.title", "header.title", "slide.title")

#: A ``<br>`` / ``<br/>`` / ``<br />`` tag with the whitespace around it, so
#: ``제목<br>\n  부제`` becomes ``제목 부제`` rather than ``제목 \n  부제``.
_LINE_BREAK = re.compile(r"(?i)\s*<br\s*/?>\s*")


def is_title_role(tag: str, attrs: dict[str, str]) -> bool:
    """Mirror of ``authored_composition_policy._is_primary_title_candidate``."""
    if tag in _TITLE_TAGS:
        return True
    if attrs.get("data-header-part", "").strip().lower() == "title":
        return True
    if attrs.get("data-semantic-role", "").strip().lower() == "title":
        return True
    qid = attrs.get("data-qid", "").strip().lower()
    if not qid:
        return False
    return (
        qid in {"title", "text.title", *_PRIMARY_TITLE_QIDS}
        or qid.endswith(".title.text")
        or any(qid.startswith(f"{prefix}.") for prefix in _PRIMARY_TITLE_QIDS)
    )


@dataclass(frozen=True, slots=True)
class _TitleSpan:
    tag: str
    content_start: int
    content_end: int


@dataclass
class _OpenFrame:
    tag: str
    content_start: int
    title: bool


class _TitleScanParser(HTMLParser):
    """Locate the inner byte span (between start and end tag) of title elements."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0]
        self._line_offsets.extend(match.end() for match in re.finditer(r"\n", source))
        self._stack: list[_OpenFrame] = []
        self.spans: list[_TitleSpan] = []
        self.malformed = False

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in VOID_TAGS:
            return
        raw = self.get_starttag_text()
        if raw is None:
            self.malformed = True
            return
        attributes = {(name or "").lower(): value or "" for name, value in attrs}
        self._stack.append(
            _OpenFrame(
                tag=tag,
                content_start=self._offset() + len(raw),
                title=is_title_role(tag, attributes),
            )
        )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack or self._stack[-1].tag != tag:
            self.malformed = True
            return
        frame = self._stack.pop()
        if frame.title:
            self.spans.append(
                _TitleSpan(
                    tag=frame.tag,
                    content_start=frame.content_start,
                    content_end=self._offset(),
                )
            )


def _title_spans(html: str) -> list[_TitleSpan]:
    parser = _TitleScanParser(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []
    if parser.malformed or parser._stack:
        return []
    return parser.spans


def apply_title_line_break_rule(html: str) -> tuple[str, dict | None]:
    """Replace ``<br>`` inside title-role elements with one space.

    Returns the rewritten document and the receipt entry, or the input
    unchanged and ``None`` when no title carries a line break. The cheap
    ``<br`` substring check keeps the parser off documents that cannot match.
    """
    if "<br" not in html.lower():
        return html, None

    spans = _title_spans(html)
    if not spans:
        return html, None

    # Outermost spans win: a nested title (an h1 inside a marked title
    # container) lies inside its parent's span and is rewritten with it.
    spans.sort(key=lambda span: (span.content_start, -span.content_end))
    edits: list[tuple[int, int, str]] = []
    total = 0
    tags: set[str] = set()
    covered_through = -1
    for span in spans:
        if span.content_start < covered_through:
            continue
        covered_through = span.content_end
        segment = html[span.content_start : span.content_end]
        replaced, count = _LINE_BREAK.subn(" ", segment)
        if not count:
            continue
        edits.append((span.content_start, span.content_end, replaced))
        total += count
        tags.add(span.tag)

    if not edits:
        return html, None

    result = html
    for start, end, replacement in sorted(edits, key=lambda edit: edit[0], reverse=True):
        result = result[:start] + replacement + result[end:]

    return result, {
        "rule": TITLE_LINE_BREAK_RULE_ID,
        "detail": ", ".join(sorted(tags)),
        "count": total,
    }


__all__ = [
    "TITLE_LINE_BREAK_RULE_ID",
    "apply_title_line_break_rule",
    "is_title_role",
]
