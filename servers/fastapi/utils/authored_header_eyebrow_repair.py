"""Deterministic fix for one header contract violation: a dropped chapter prefix.

The bench found a patched slide that lost its chapter numeral from the header
eyebrow after a repair round -- the eyebrow kept its section name but the
deck's ``"Ⅱ. 제안개요 > "`` opener was gone. ``authored_header_repair`` fixes
mechanical drift it can see from the contract alone (an out-of-bounds band, a
duplicated part, an unpinned body); it has no view of the chapter plan, so a
missing prefix is not something it can fix. This sibling module is that one
missing fix, kept separate rather than added to ``authored_header_repair``
because that module is already at the file-size ratchet's line budget.

Like ``authored_header_repair``, this edits the exact byte span of the
eyebrow's own content -- located via ``HTMLParser`` line/column offsets, never
a reserialised tree -- so nothing outside that span can move by even one
byte.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re

from utils.authored_header_validation import HEADER_PART_ATTRIBUTE, VOID_HTML_TAGS

_TAG_STRIP = re.compile(r"<[^>]*>")


def _collapse_whitespace(text: str) -> str:
    return " ".join((text or "").split())


@dataclass(frozen=True, slots=True)
class _EyebrowSpan:
    #: Offset right after the eyebrow element's own opening tag: where its
    #: content begins, and where an insertion belongs.
    content_start: int
    #: Offset right before the eyebrow element's own closing tag.
    content_end: int
    #: ``data-qid`` on the eyebrow element itself, if it has one.
    qid: str | None


@dataclass
class _OpenFrame:
    tag: str
    is_eyebrow: bool
    qid: str | None
    content_start: int


class _EyebrowScanParser(HTMLParser):
    """Locate every ``data-header-part="eyebrow"`` element's content span.

    Mirrors ``authored_header_repair``'s own scanner: exact byte offsets from
    ``HTMLParser``'s line/column position, never a reserialised tree.
    """

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_offsets = [0, *(m.end() for m in re.finditer(r"\n", source))]
        self._stack: list[_OpenFrame] = []
        self.spans: list[_EyebrowSpan] = []
        self.malformed = False

    def _offset(self) -> int:
        line, column = self.getpos()
        return self._line_offsets[line - 1] + column

    @staticmethod
    def _read(attrs: list[tuple[str, str | None]], name: str) -> str | None:
        for attr_name, value in attrs:
            if attr_name.lower() == name:
                return value
        return None

    def _is_eyebrow(self, attrs: list[tuple[str, str | None]]) -> bool:
        part = self._read(attrs, HEADER_PART_ATTRIBUTE)
        return (part or "").strip().lower() == "eyebrow"

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        raw = self.get_starttag_text()
        if raw is None:
            self.malformed = True
            return
        is_eyebrow = self._is_eyebrow(attrs)
        content_start = self._offset() + len(raw)
        if tag.lower() in VOID_HTML_TAGS:
            if is_eyebrow:
                # A void element carries no text; an eyebrow that is one is not
                # something this repair can insert into.
                self.malformed = True
            return
        qid = self._read(attrs, "data-qid")
        self._stack.append(_OpenFrame(tag.lower(), is_eyebrow, qid, content_start))

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if self._is_eyebrow(attrs):
            self.malformed = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if not self._stack or self._stack[-1].tag != tag:
            self.malformed = True
            return
        frame = self._stack.pop()
        if frame.is_eyebrow:
            self.spans.append(
                _EyebrowSpan(frame.content_start, self._offset(), frame.qid)
            )


def _eyebrow_spans(html: str) -> list[_EyebrowSpan] | None:
    parser = _EyebrowScanParser(html)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return None
    if parser.malformed or parser._stack:
        return None
    return parser.spans


def repair_eyebrow_prefix(
    html: str,
    *,
    chapter_prefix: str,
    eyebrow_qid_or_locator: str | None = None,
) -> tuple[str, bool]:
    """Re-insert the deck's chapter prefix at the start of the eyebrow.

    ``eyebrow_qid_or_locator``, when given, is the eyebrow element's own
    ``data-qid`` (as ``authored_header_restyle.header_qids`` reports it): the
    edit is refused unless it names exactly the one eyebrow element found.
    Left ``None``, the edit proceeds only when the document has exactly one
    eyebrow element -- the ordinary case, since a duplicated eyebrow is
    already a separate violation the caller routes to re-authoring instead.

    Returns the possibly-edited HTML and whether it changed. Nothing is
    changed -- ``html`` is returned as given -- when the eyebrow cannot be
    located unambiguously, or when its visible text already starts with the
    prefix (so calling this twice in a row is a no-op the second time).
    """
    spans = _eyebrow_spans(html)
    if not spans:
        return html, False
    if eyebrow_qid_or_locator is not None:
        matches = [span for span in spans if span.qid == eyebrow_qid_or_locator]
    else:
        matches = spans
    if len(matches) != 1:
        return html, False
    span = matches[0]

    current = html[span.content_start : span.content_end]
    visible = _collapse_whitespace(_TAG_STRIP.sub("", current))
    expected = _collapse_whitespace(chapter_prefix)
    if not expected or visible.startswith(expected):
        return html, False

    if visible:
        # ``.rstrip()`` then a single space normalises whatever trailing
        # whitespace ``chapter_prefix`` itself carries (the chapter plan's
        # separator, e.g. ``"Ⅱ. 제안개요 > "``, already ends in one) to
        # exactly one space before the existing text, never a doubled one.
        insertion = chapter_prefix.rstrip() + " "
    else:
        # The eyebrow is empty (or whitespace/markup only): the prefix is the
        # entire eyebrow, with none of its own trailing separator space added
        # back on -- there is no section name yet for it to separate from.
        insertion = chapter_prefix

    new_html = html[: span.content_start] + insertion + html[span.content_start :]
    return new_html, True


__all__ = ["repair_eyebrow_prefix"]
