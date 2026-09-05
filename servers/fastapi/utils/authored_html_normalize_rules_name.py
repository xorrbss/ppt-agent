"""``name``-attribute normalisation for authored HTML.

``renderer_markup_policy`` refuses a ``name`` attribute outright, yet on the
tag surface the policy allows it carries no behaviour at all: the tags that
would make ``name`` load-bearing -- ``form``, ``input``, ``iframe`` -- are
already in ``DROP_TAGS``. So this rule always strips the attribute and never
the element, and a deck is not lost over a leftover ``name="..."``.

It is one rule of ``utils/authored_html_normalize.py`` and follows the same
contract: a document without a ``name`` attribute is returned byte-identical
(guarded by a plain substring short-circuit before the parser runs), tags are
located by exact byte span through ``html.parser.HTMLParser`` and never
reserialized, and on anything the parser cannot resolve the document is left
alone for admission to reject on its own terms.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

from utils.authored_html_normalize_edits import apply_edits

NAME_ATTRIBUTE_RULE_ID = "html_attribute_stripped"

#: A ``name`` attribute on a start tag, with or without a value. Requires
#: leading whitespace so a value merely ending in ``name`` is never matched,
#: and a real attribute-name boundary so ``data-name`` is not either.
_NAME_ATTRIBUTE = re.compile(r"""(?i)\s+name\b(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]*))?""")


class _NameTagScanParser(HTMLParser):
    """Byte spans of start tags carrying a literal ``name`` attribute -- the
    same ``get_starttag_text()``-plus-position technique as
    ``_HiddenScanParser``, no element span needed since ``name`` is stripped
    from the tag, never the element."""

    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self._line_offsets = [0]
        self._line_offsets.extend(m.end() for m in re.finditer(r"\n", source))
        self.tags: list[tuple[int, int, str]] = []

    def _record(self, attrs: list[tuple[str, str | None]]) -> None:
        if not any((name or "").lower() == "name" for name, _ in attrs):
            return
        raw = self.get_starttag_text()
        if raw is None:
            return
        line, column = self.getpos()
        start = self._line_offsets[line - 1] + column
        self.tags.append((start, start + len(raw), raw))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._record(attrs)


def _normalize_name_attributes(html: str) -> tuple[str, int]:
    parser = _NameTagScanParser(html)
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return html, 0
    edits = [
        (start, end, new_raw)
        for start, end, raw in parser.tags
        for new_raw in [_NAME_ATTRIBUTE.sub("", raw, count=1)]
        if new_raw != raw
    ]
    return (apply_edits(html, edits), len(edits)) if edits else (html, 0)


def apply_name_attribute_rule(html: str) -> tuple[str, dict | None]:
    """Strip every ``name`` attribute, returning the normalisation entry.

    The entry is ``None`` -- and ``html`` byte-identical -- when the document
    carries no ``name`` attribute.
    """

    # Cheap short-circuit: never invoke the parser on a document that cannot
    # possibly carry a `name` attribute, so the common case stays a plain
    # string scan and the result stays byte-identical.
    if "name" not in html.lower():
        return html, None
    result, count = _normalize_name_attributes(html)
    if not count:
        return result, None
    return result, {"rule": NAME_ATTRIBUTE_RULE_ID, "detail": "name", "count": count}


__all__ = ["NAME_ATTRIBUTE_RULE_ID", "apply_name_attribute_rule"]
