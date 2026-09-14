"""Semantic bundle drafts for Composer slides, read back from the rendered HTML.

The renderer writes ``data-qid`` and ``data-semantic-role`` on every visible
element, so the declared semantic source is a projection of the HTML rather
than a second authority.  The element shapes mirror ``_semantic_draft`` in the
Deck IR product adapter: text carries one run per element, and an element
without text is declared as a rectangle shape.

Run style (size, weight, colour) is read back from the same HTML, from the
exact class/style attributes ``render_css.py`` and ``render_text.py`` wrote,
so the declared style can never disagree with what the renderer actually
painted.  ``render_css.TEXT_COLOR_CLASS_TOKENS`` and ``BOLD_CLASS_NAMES`` are
the single source of truth for the class -> paint mapping; this module only
imports them, it never re-derives them, so the read-back cannot drift from
the write side the way a second hardcoded copy would.

Composer archetypes nest qid-bearing elements (bands contain stacks, stacks
contain blocks), and the authored-hybrid exporter reconciles the declared graph
against what Chromium observes: ``browser-source.ts`` reports each element's
``parentElementId`` as ``element.parentElement.closest("[data-qid]")``.  A draft
that declared every element as a root therefore failed
``authored_semantic_ownership_mismatch`` on the first nested slide, so the
ownership edges are projected from the same HTML nesting the browser walks.
"""

from __future__ import annotations

import math
import re
from html.parser import HTMLParser
from typing import Any

from services.composer.contracts import QID_ATTRIBUTE, SEMANTIC_ROLE_ATTRIBUTE
from services.composer.plan import ComposerTokens
from services.composer.render_css import (
    BOLD_CLASS_NAMES,
    DEFAULT_TEXT_COLOR_TOKEN,
    TEXT_COLOR_CLASS_TOKENS,
)

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
_SKIP_TEXT_TAGS = frozenset({"script", "style", "template"})
_CJK = re.compile(r"[\u3000-\u9fff\uac00-\ud7af]")
_MAX_RUN_CHARS = 16384

_FONT_SIZE_RE = re.compile(r"font-size\s*:\s*([0-9.]+)px")
_FONT_WEIGHT_RE = re.compile(r"font-weight\s*:\s*(\d+)")

#: Declared size/weight for a qid element whose own style carries no
#: ``font-size`` (e.g. a table cell that inherits its size from an ancestor
#: rather than setting it inline): the pre-existing placeholder values, kept
#: only as a last resort for markup this module cannot see through, never as
#: a substitute for reading a size/weight that *is* present.
_FALLBACK_FONT_SIZE_PT = 18.0
_FALLBACK_WEIGHT = 400

#: ``browser-source.ts:367`` exactly: ``Math.round(value * 1e6) / 1e6``.
#: Python's ``round`` uses round-half-to-even; this instead rounds half away
#: from zero (same as ``Math.round`` for the always-positive px values here),
#: so a px value that lands exactly on a 7th-decimal boundary cannot disagree
#: with the browser's own conversion at the clamp.
def _round6(value: float) -> float:
    return math.floor(value * 1_000_000 + 0.5) / 1_000_000


def _declared_size_pt(style: str) -> float:
    """``fontSizePt = max(9, round(px * 0.75))``, mirroring ``browser-source.ts``."""

    match = _FONT_SIZE_RE.search(style)
    if not match:
        return _FALLBACK_FONT_SIZE_PT
    return max(9.0, _round6(float(match.group(1)) * 0.75))


def _declared_weight(classes: list[str], style: str) -> int:
    """700 when a bold-forcing class or an inline bold declaration says so.

    ``render_text.render_bound_text`` only adds ``font-weight:700`` inline
    when every run of the *source* paragraph was itself bold; a title (or any
    other ``BOLD_CLASS_NAMES`` element) is bold from its CSS class alone, with
    no inline echo, so the class check is not optional -- reading inline
    style only would still declare 400 for an unbolded-source title even
    though the browser computes 700 for it.
    """

    if any(name in BOLD_CLASS_NAMES for name in classes):
        return 700
    match = _FONT_WEIGHT_RE.search(style)
    return 700 if match and match.group(1) == "700" else _FALLBACK_WEIGHT


def _declared_color(classes: list[str], tokens: ComposerTokens) -> str:
    """The token a class-bearing element paints text with, last match wins.

    Never keyed by ``data-semantic-role``: ``.cx-marker`` and ``.cx-kpi``
    carry role ``decoration``/``body`` while painting in ``accent_text``, so a
    role-keyed table would resolve them to the wrong token.
    """

    token_name = DEFAULT_TEXT_COLOR_TOKEN
    class_set = set(classes)
    for css_class, token in TEXT_COLOR_CLASS_TOKENS.items():
        if css_class in class_set:
            token_name = token
    return str(getattr(tokens, token_name))


class _QidCollector(HTMLParser):
    """Collect ``(qid, role, class, style, text)`` in document order.

    Text goes to the innermost owner; ``class``/``style`` are kept verbatim
    so the declared run style can be read back from the exact attributes
    ``render_text.py`` and ``render_table.py`` wrote, instead of a second,
    independently-maintained copy of what they mean.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, Any]] = []
        self._stack: list[tuple[str, int | None]] = []
        self._skip_depth = 0

    def _enclosing_owner(self) -> int | None:
        """The innermost open qid element, which is what ``closest`` resolves to."""

        for _, owner in reversed(self._stack):
            if owner is not None:
                return owner
        return None

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> int | None:
        mapping = {name.lower(): (value or "") for name, value in attrs}
        qid = mapping.get(QID_ATTRIBUTE, "").strip()
        if not qid:
            return None
        role = mapping.get(SEMANTIC_ROLE_ATTRIBUTE, "").strip() or ("image" if tag == "img" else "body")
        parent = self._enclosing_owner()
        index = len(self.elements)
        self.elements.append(
            {
                "qid": qid,
                "role": role[:128],
                "tag": tag,
                "text": [],
                "parent": parent,
                "children": [],
                "classes": mapping.get("class", "").split(),
                "style": mapping.get("style", ""),
            }
        )
        if parent is not None:
            self.elements[parent]["children"].append(index)
        return index

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TEXT_TAGS:
            self._skip_depth += 1
        owner = self._open(tag, attrs)
        if tag not in _VOID_TAGS:
            self._stack.append((tag, owner))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag.lower(), attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TEXT_TAGS and self._skip_depth:
            self._skip_depth -= 1
        for position in range(len(self._stack) - 1, -1, -1):
            if self._stack[position][0] == tag:
                del self._stack[position:]
                return

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        owner = self._enclosing_owner()
        if owner is not None:
            self.elements[owner]["text"].append(data)


def collect_qid_elements(html: str) -> list[dict[str, Any]]:
    """Project the qid elements of one slide with their ownership edges."""

    parser = _QidCollector()
    parser.feed(html)
    parser.close()
    collected = parser.elements
    return [
        {
            "qid": item["qid"],
            "role": item["role"],
            "tag": item["tag"],
            "text": " ".join("".join(item["text"]).split()),
            "parent": None if item["parent"] is None else collected[item["parent"]]["qid"],
            "children": [collected[child]["qid"] for child in item["children"]],
            "classes": item["classes"],
            "style": item["style"],
        }
        for item in collected
    ]


def semantic_draft_from_html(
    html: str,
    *,
    language: str,
    font_family: str,
    font_face: str,
    tokens: ComposerTokens,
) -> dict[str, Any]:
    """Build the ``semanticSource`` draft ``publish_authored_semantic_bundle_graph`` seals.

    ``fontSizePt``, ``weight`` and ``color`` are read back from the class and
    inline ``style`` the renderer actually wrote for each element
    (``_declared_size_pt``, ``_declared_weight``, ``_declared_color``), not a
    fixed placeholder: a declared run that disagrees with what the exporter
    measures fails the exporter's identity check.
    """

    if re.search(r'data-source-fit-policy=[\"\']composer-source-fit/v2[\"\']', html):
        from services.composer.publish_source_semantic import source_semantic_draft
        return source_semantic_draft(html, language=language, font_family=font_family, font_face=font_face, tokens=tokens)

    elements: list[dict[str, Any]] = []
    for index, item in enumerate(collect_qid_elements(html)):
        text = item["text"]
        if not text:
            elements.append(
                {
                    "elementId": item["qid"],
                    "kind": "shape",
                    "semanticRole": item["role"],
                    "intendedOrder": index,
                    "parentId": item["parent"],
                    "childIds": list(item["children"]),
                    "payload": {"preset": "rectangle"},
                }
            )
            continue
        classes = item["classes"]
        style = item["style"]
        elements.append(
            {
                "elementId": item["qid"],
                "kind": "text",
                "semanticRole": item["role"],
                "intendedOrder": index,
                "parentId": item["parent"],
                "childIds": list(item["children"]),
                "payload": {
                    "paragraphs": [
                        {
                            "runs": [
                                {
                                    "text": text[:_MAX_RUN_CHARS],
                                    "language": language or "ko",
                                    "script": "cjk" if _CJK.search(text) else "latin",
                                    "fontFamily": font_family,
                                    "fontFace": font_face,
                                    "fontSizePt": _declared_size_pt(style),
                                    "weight": _declared_weight(classes, style),
                                    "color": _declared_color(classes, tokens),
                                }
                            ]
                        }
                    ]
                },
            }
        )
    return {
        "semanticSource": {
            "elements": elements,
            "identityRegistry": {
                "activeQids": sorted(str(item["elementId"]) for item in elements),
                "retiredQids": [],
            },
        }
    }


__all__ = ["collect_qid_elements", "semantic_draft_from_html"]
