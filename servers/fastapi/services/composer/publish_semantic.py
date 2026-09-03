"""Semantic bundle drafts for Composer slides, read back from the rendered HTML.

The renderer writes ``data-qid`` and ``data-semantic-role`` on every visible
element, so the declared semantic source is a projection of the HTML rather
than a second authority.  The element shapes mirror ``_semantic_draft`` in the
Deck IR product adapter: text carries one run per element, and an element
without text is declared as a rectangle shape.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from services.composer.contracts import QID_ATTRIBUTE, SEMANTIC_ROLE_ATTRIBUTE

_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
)
_SKIP_TEXT_TAGS = frozenset({"script", "style", "template"})
_CJK = re.compile(r"[\u3000-\u9fff\uac00-\ud7af]")
_MAX_RUN_CHARS = 16384


class _QidCollector(HTMLParser):
    """Collect ``(qid, role, text)`` in document order; text goes to the innermost owner."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict[str, Any]] = []
        self._stack: list[tuple[str, int | None]] = []
        self._skip_depth = 0

    def _open(self, tag: str, attrs: list[tuple[str, str | None]]) -> int | None:
        mapping = {name.lower(): (value or "") for name, value in attrs}
        qid = mapping.get(QID_ATTRIBUTE, "").strip()
        if not qid:
            return None
        role = mapping.get(SEMANTIC_ROLE_ATTRIBUTE, "").strip() or ("image" if tag == "img" else "body")
        self.elements.append({"qid": qid, "role": role[:128], "tag": tag, "text": []})
        return len(self.elements) - 1

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
        for _, owner in reversed(self._stack):
            if owner is not None:
                self.elements[owner]["text"].append(data)
                return


def collect_qid_elements(html: str) -> list[dict[str, Any]]:
    parser = _QidCollector()
    parser.feed(html)
    parser.close()
    return [
        {"qid": item["qid"], "role": item["role"], "tag": item["tag"], "text": " ".join("".join(item["text"]).split())}
        for item in parser.elements
    ]


def semantic_draft_from_html(
    html: str,
    *,
    language: str,
    font_family: str,
    font_face: str,
) -> dict[str, Any]:
    """Build the ``semanticSource`` draft ``publish_authored_semantic_bundle_graph`` seals."""

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
                    "payload": {"preset": "rectangle"},
                }
            )
            continue
        elements.append(
            {
                "elementId": item["qid"],
                "kind": "text",
                "semanticRole": item["role"],
                "intendedOrder": index,
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
                                    "fontSizePt": 18,
                                    "weight": 400,
                                    "color": "#111111",
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
