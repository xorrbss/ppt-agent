"""Start-tag scanning primitives shared by the authored-DOM preflight modules.

``utils/authored_dom_quality.py`` inspects authored slide HTML and
``utils/authored_qid_namespace.py`` stamps and validates its ``data-qid``
namespace. Both walk the same start tags, normalise the same attribute text
and need the same answer to "is this element something a patch may target?",
so those primitives live here instead of one module reaching into the other.

Every helper here is deliberately parse-only: tags are collected with their
verbatim source token (``get_starttag_text()``) so callers can splice the
original bytes rather than reserialize a tree.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from html import unescape
from html.parser import HTMLParser

NON_RENDERED_TAGS = {
    "html",
    "head",
    "body",
    "base",
    "link",
    "meta",
    "title",
    "style",
    "script",
    "template",
    "noscript",
    "defs",
    "symbol",
    "clipPath".lower(),
}
# Most HTML void elements do not own editable content.  ``img`` is the deliberate
# exception: it is a first-class visual target for fit/crop repair, while still
# never being pushed onto the text parser stack.  In particular, treating ``br``
# as a normal opening tag would make following text belong to a phantom break.
# Keep this local copy rather than importing the patch engine: DOM inspection is a
# one-way dependency used by the patch trust boundary.
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_WS = re.compile(r"\s+")


class StartTagCollector(HTMLParser):
    """Collect every start tag as ``(tag, attrs, verbatim source token)``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, list[tuple[str, str | None]], str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag.lower(), attrs, self.get_starttag_text()))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag.lower(), attrs, self.get_starttag_text()))


def normalize_dom_text(value: object, *, limit: int | None = None) -> str:
    text = _WS.sub(" ", unescape(str(value or ""))).strip()
    return text[:limit] if limit is not None else text


def attrs_dict(attrs: Iterable[tuple[str, str | None]]) -> dict[str, str]:
    return {
        str(name).lower(): "" if value is None else str(value) for name, value in attrs
    }


def is_edit_target(tag: str, attrs: Mapping[str, str]) -> bool:
    """Avoid document plumbing but include every visible element that a patch may target."""
    if tag in NON_RENDERED_TAGS or (tag in VOID_TAGS and tag != "img") or tag in {
        "g",
        "path",
        "rect",
        "circle",
        "line",
        "polygon",
        "polyline",
    }:
        return False
    if attrs.get("aria-hidden", "").lower() == "true" or "data-qid-ignore" in attrs:
        return False
    # Preserve the historical migration contract: qid-less images are passive.
    # An explicitly addressed image is safe to expose for fit/crop operations.
    if tag == "img":
        return bool(attrs.get("data-qid", "").strip())
    return not (
        attrs.get("aria-hidden", "").lower() == "true" or "data-qid-ignore" in attrs
    )


__all__ = [
    "NON_RENDERED_TAGS",
    "VOID_TAGS",
    "StartTagCollector",
    "attrs_dict",
    "is_edit_target",
    "normalize_dom_text",
]
