"""Pinned html5lib 1.1 adapter for authored HTML tree construction.

``document`` parses with the HTML parser's document algorithm and always
serializes as an explicit standards-mode document. ``fragment`` parses in a
HTML ``div`` context. HTML namespace prefixes are disabled for ordinary HTML
elements (``namespaceHTMLElements=False``); foreign SVG/MathML namespaces stay
intact. These choices are deliberate API, not html5lib defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from xml.etree.ElementTree import Element

import html5lib

NormalizationProfile = Literal["document", "fragment"]
HTML5LIB_VERSION = "1.1"


@dataclass(frozen=True, slots=True)
class Html5ParseError:
    code: str
    data: dict[str, object]


@dataclass(frozen=True, slots=True)
class ParsedAuthoredHtml:
    root: Element
    profile: NormalizationProfile
    errors: tuple[Html5ParseError, ...]


def parse_authored_html(html: str, *, profile: NormalizationProfile) -> ParsedAuthoredHtml:
    """Build a browser-equivalent HTML5 tree under the pinned profile."""

    if profile not in {"document", "fragment"}:
        raise ValueError(f"unknown authored HTML normalization profile: {profile!r}")
    parser = html5lib.HTMLParser(
        tree=html5lib.getTreeBuilder("etree"),
        strict=False,
        namespaceHTMLElements=False,
    )
    if profile == "document":
        root = parser.parse(html)
    else:
        root = parser.parseFragment(html, container="div")
    errors = tuple(Html5ParseError(code=code, data=dict(data)) for _position, code, data in parser.errors)
    return ParsedAuthoredHtml(root=root, profile=profile, errors=errors)


__all__ = [
    "HTML5LIB_VERSION",
    "Html5ParseError",
    "NormalizationProfile",
    "ParsedAuthoredHtml",
    "parse_authored_html",
]
