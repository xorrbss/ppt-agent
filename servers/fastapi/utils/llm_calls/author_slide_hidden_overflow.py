"""Static rejection of CSS declarations that hide authored content instead of
resizing it to fit the fixed 1280x720 frame.

``-webkit-line-clamp``/``line-clamp``, ``text-overflow: ellipsis`` and
``overflow``/``overflow-x``/``overflow-y: auto|scroll`` let a slide "fit" by
truncating or scrolling text the reader (and the hybrid-PPTX exporter, which
measures the DOM once) never sees. The measured Chromium gate in
``authored_dom_quality.py`` already flags this, but only after a full render
+ repair cycle. Rejecting it here, at authoring time, turns that into an
immediate local retry instead.

``overflow: hidden`` is intentionally left alone: a clipped decorative
container is a legitimate composition choice and the measured gate still
judges any resulting clipping of real content. ``text-overflow: clip`` is the
property's initial value and hides nothing beyond what ``overflow: hidden``
already does, so it is not matched either. ``overflow-wrap`` and
``overflow: visible`` are unrelated properties/values and are never matched.

The scan is for the model's own CSS only. The server-owned infographic
library stylesheet (``utils/authored_infographic/library.py``) is injected
into the document before this validation runs and legitimately uses
``text-overflow: ellipsis`` on fixed single-line labels; its ``<style>`` block
is recognised by ``LIBRARY_MARKER`` and skipped.

This module only scans; it never rewrites the HTML. See
``utils/renderer_css_policy.py`` for why the admission allowlist itself stays
value-blind and is not the place to add this check -- that policy also
re-validates already-stored decks, and tightening it there would reject
historical slides.
"""

from __future__ import annotations

import re

from utils.authored_infographic.library import LIBRARY_MARKER
from utils.renderer_worker_policy import RendererPolicyError

#: Substring every raised message here starts with, so callers (the author-deck
#: retry classifier) can recognise this rejection without re-deriving it.
HIDDEN_OVERFLOW_ERROR_PREFIX = "authored_hidden_overflow_declaration"

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_STYLE_BLOCK_RE = re.compile(
    r"<style\b[^>]*>(?P<css>.*?)</style\s*>", re.IGNORECASE | re.DOTALL
)
_INLINE_STYLE_RE = re.compile(
    r"\bstyle\s*=\s*(?P<quote>['\"])(?P<css>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
# Quoted CSS string literals (e.g. a `content: "..."` value), stripped before
# matching so a declaration-shaped substring inside one is never mistaken for
# a real declaration.
_CSS_STRING_RE = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'')

# Property name, then its value up to the next declaration/rule boundary. The
# leading negative lookbehind keeps the bare `line-clamp`/`overflow`
# alternatives from also matching inside their longer relatives
# (`-webkit-line-clamp`, `overflow-x`, `overflow-y`, `overflow-wrap`): a match
# can only start where the property name is not itself preceded by a word
# character or hyphen, and (for `overflow`) must be followed by a colon, which
# `overflow-wrap:` never is.
_DECLARATION_RE = re.compile(
    r"(?<![\w-])"
    r"(?P<property>-webkit-line-clamp|line-clamp|text-overflow|overflow-x|overflow-y|overflow)"
    r"\s*:\s*"
    r"(?P<value>[^;{}]*)",
    re.IGNORECASE,
)

_HIDING_TOKENS = frozenset({"auto", "scroll"})
_HIDING_TEXT_OVERFLOW_VALUES = frozenset({"ellipsis"})


def _strip_important(value: str) -> str:
    cleaned = re.sub(r"!\s*important\s*$", "", value.strip(), flags=re.IGNORECASE)
    return cleaned.strip()


def _is_hiding_declaration(property_name: str, raw_value: str) -> bool:
    prop = property_name.strip().lower()
    value = _strip_important(raw_value).lower()
    if not value:
        return False
    if prop in ("-webkit-line-clamp", "line-clamp"):
        return True
    if prop == "text-overflow":
        return value in _HIDING_TEXT_OVERFLOW_VALUES
    if prop in ("overflow", "overflow-x", "overflow-y"):
        return any(token in _HIDING_TOKENS for token in value.split())
    return False


def _first_hiding_declaration(css: str) -> str | None:
    """Return the first forbidden ``property: value`` found in one CSS fragment."""

    cleaned = _CSS_STRING_RE.sub(" ", css)
    cleaned = _CSS_COMMENT_RE.sub(" ", cleaned)
    for match in _DECLARATION_RE.finditer(cleaned):
        if _is_hiding_declaration(match.group("property"), match.group("value")):
            return f"{match.group('property').strip()}: {_strip_important(match.group('value'))}"
    return None


def reject_hidden_overflow_declarations(html: str) -> None:
    """Raise ``RendererPolicyError`` if the slide hides content instead of fitting it.

    Scans every ``<style>`` block and ``style=""`` attribute (HTML comments are
    excluded first, so a commented-out block is never scanned; the injected
    infographic library block is skipped) for ``-webkit-line-clamp``/
    ``line-clamp`` (any value), ``text-overflow: ellipsis``, or
    ``overflow``/``overflow-x``/``overflow-y: auto|scroll``.
    """

    source = _HTML_COMMENT_RE.sub(" ", html or "")
    css_fragments = [
        m.group("css")
        for m in _STYLE_BLOCK_RE.finditer(source)
        if LIBRARY_MARKER not in m.group("css")
    ]
    css_fragments.extend(m.group("css") for m in _INLINE_STYLE_RE.finditer(source))
    for css in css_fragments:
        declaration = _first_hiding_declaration(css)
        if declaration is not None:
            raise RendererPolicyError(
                f"{HIDDEN_OVERFLOW_ERROR_PREFIX}: {declaration} -- content must never be "
                "hidden to make it fit the 1280x720 frame; reduce density (shorter copy, "
                "fewer items) or re-lay out the slide so everything is fully visible instead."
            )


__all__ = ["HIDDEN_OVERFLOW_ERROR_PREFIX", "reject_hidden_overflow_declarations"]
