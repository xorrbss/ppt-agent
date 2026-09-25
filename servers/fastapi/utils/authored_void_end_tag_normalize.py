"""Drop browser-ignored closing tags for HTML void elements.

HTML parsing treats an end tag such as ``</img>`` or ``</br>`` as a parse
error and ignores it. Authored intake mirrors that deterministic browser
behaviour before renderer admission, while leaving every non-void end tag
untouched for the renderer's fail-closed balance checks.

The parser is used only to locate genuine end-tag tokens. Exact source spans
are deleted in reverse order, so comments, style text, and all unrelated bytes
remain unchanged.
"""

from __future__ import annotations

import re
from collections import Counter
from html.parser import HTMLParser

from utils.authored_dom_tags import VOID_TAGS
from utils.authored_html_normalize_edits import apply_edits


class _VoidEndTagParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._line_offsets = [0]
        self._line_offsets.extend(match.end() for match in re.finditer(r"\n", source))
        self.spans: list[tuple[int, int, str]] = []

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag not in VOID_TAGS:
            return
        line, column = self.getpos()
        start = self._line_offsets[line - 1] + column
        match = re.match(
            rf"</\s*{re.escape(normalized_tag)}\s*>",
            self._source[start:],
            flags=re.IGNORECASE,
        )
        if match is not None:
            self.spans.append((start, start + match.end(), normalized_tag))


def drop_void_element_end_tags(html: str) -> tuple[str, list[dict]]:
    """Return ``html`` without genuine void-element end tags and a receipt."""

    if not isinstance(html, str) or not html or "</" not in html:
        return html, []
    parser = _VoidEndTagParser(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html, []
    if not parser.spans:
        return html, []
    counts = Counter(tag for _start, _end, tag in parser.spans)
    result = apply_edits(html, [(start, end, "") for start, end, _tag in parser.spans])
    receipts = [
        {"rule": "html_void_end_tag_dropped", "detail": tag, "count": counts[tag]}
        for tag in sorted(counts)
    ]
    return result, receipts


def html5_void_end_tag_receipts(errors: tuple[object, ...]) -> list[dict]:
    """Map html5lib void-end-tag corrections to bounded compatibility receipts."""

    counts: Counter[tuple[str, str]] = Counter()
    for error in errors:
        code = str(getattr(error, "code", ""))
        data = getattr(error, "data", {})
        if code == "unexpected-end-tag":
            tag = str(data.get("name", "")).lower()
            if tag in VOID_TAGS:
                counts[("html_void_end_tag_dropped", tag)] += 1
        elif code == "unexpected-end-tag-treated-as":
            tag = str(data.get("originalName", "")).lower()
            if tag in VOID_TAGS:
                counts[("html_void_end_tag_reparsed", tag)] += 1
    return [
        {"rule": rule, "detail": tag, "count": count}
        for (rule, tag), count in sorted(counts.items())
    ]


__all__ = ["drop_void_element_end_tags", "html5_void_end_tag_receipts"]
