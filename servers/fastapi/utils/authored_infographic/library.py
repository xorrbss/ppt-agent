"""The rendered stylesheet, its class vocabulary, and how it enters a slide.

The library is injected server-side into the model's self-contained slide
document, after the model's own ``<style>`` so the library wins ties, and
before renderer admission so the injected CSS is validated like everything
else. Injection is idempotent: a document that already carries the library
(a persisted slide re-entering through remake or review) is left alone.
"""

from __future__ import annotations

import re
from functools import lru_cache

from utils.authored_infographic.css_base import BASE_CSS
from utils.authored_infographic.css_compact import COMPACT_CSS
from utils.authored_infographic.css_data import DATA_CSS
from utils.authored_infographic.css_fill import FILL_CSS
from utils.authored_infographic.css_flow import FLOW_CSS
from utils.authored_infographic.css_list import LIST_CSS
from utils.authored_infographic.css_structure import STRUCTURE_CSS
from utils.authored_infographic.tokens import (
    InfographicTokens,
    render_css,
    tokens_for_brand,
)

#: ``COMPACT_CSS`` states how a figure fits the shortest box it is given and
#: ``FILL_CSS`` how it fills the tallest, so both come last, in that order:
#: each must win the ties its selectors do not already outrank, and a cap that
#: fills a tall box has to outrank the compaction of a short one.
LIBRARY_TEMPLATE = (
    BASE_CSS + FLOW_CSS + STRUCTURE_CSS + DATA_CSS + LIST_CSS + COMPACT_CSS + FILL_CSS
)

#: The rule every rendered library carries first; its presence is what marks a
#: document as already injected.
LIBRARY_MARKER = ".ig-lib-v1{display:none}"

_CLASS_TOKEN = re.compile(r"\.([A-Za-z_][\w-]*)")
_HEAD_CLOSE = re.compile(r"</head\s*>", re.IGNORECASE)
_BODY_OPEN = re.compile(r"<body\b", re.IGNORECASE)


@lru_cache(maxsize=1)
def library_classes() -> frozenset[str]:
    """Every class name the library styles — the only classes a figure may use."""
    return frozenset(_CLASS_TOKEN.findall(LIBRARY_TEMPLATE))


def library_css(brand: object) -> str:
    """The stylesheet for one deck, with every token rendered as a literal."""
    return render_css(LIBRARY_TEMPLATE, tokens_for_brand(brand))


def library_css_for_tokens(tokens: InfographicTokens) -> str:
    return render_css(LIBRARY_TEMPLATE, tokens)


def has_library(html: str) -> bool:
    return LIBRARY_MARKER in (html or "")


def inject_library_css(html: str, css: str) -> str:
    """Add the library stylesheet to a slide document exactly once.

    The block goes at the end of ``<head>`` — after the model's own styles, so
    an accidental ``.ig-label`` rule of theirs cannot override the library at
    equal specificity — or, for a headless document, just before ``<body>``.
    """
    if has_library(html):
        return html
    block = f"<style>{css}</style>"
    match = _HEAD_CLOSE.search(html)
    if match is not None:
        return html[: match.start()] + block + html[match.start() :]
    match = _BODY_OPEN.search(html)
    if match is not None:
        return html[: match.start()] + f"<head>{block}</head>" + html[match.start() :]
    raise ValueError("slide document has neither a head nor a body to receive the library")


__all__ = [
    "LIBRARY_MARKER",
    "LIBRARY_TEMPLATE",
    "has_library",
    "inject_library_css",
    "library_classes",
    "library_css",
    "library_css_for_tokens",
]
