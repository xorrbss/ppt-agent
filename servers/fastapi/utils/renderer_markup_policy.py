"""Which HTML/SVG a renderer-isolated authored slide may contain.

Split out of ``renderer_isolation.py`` alongside the CSS policy so both lists
can be read and reviewed as lists. The *values* these attributes may carry stay
in the validator — paint attributes are still restricted to local fragment ids,
opacities to numbers, and so on. This module only decides what may appear.

The SVG surface is expressed as one shared presentation set applied to every
shape element, plus that element's own geometry. It was previously written out
per element and had drifted: ``transform`` was admitted on ``g``, ``path`` and
``text`` but not on ``rect``, ``circle`` or ``polygon``, so a deck that
translated a bar in a chart failed while the same declaration on a group
passed. Sharing the set is what stops that drift returning.
"""

from __future__ import annotations

from typing import Mapping

ALLOWED_TAGS = {
    "html", "head", "title", "meta", "body", "main", "section", "article",
    "header", "footer", "nav", "aside", "div", "span", "p", "h1", "h2",
    "h3", "h4", "h5", "h6", "ul", "ol", "li", "dl", "dt", "dd",
    "table", "caption", "colgroup", "col", "thead", "tbody", "tfoot", "tr",
    "th", "td", "strong", "b", "em", "i", "small", "sub", "sup", "br",
    "hr", "img", "figure", "figcaption", "blockquote", "style", "svg", "g", "path",
    "rect", "circle", "ellipse", "line", "polyline", "polygon", "text", "tspan",
    "defs", "lineargradient", "radialgradient", "stop", "clippath", "mask",
}
VOID_TAGS = {"meta", "br", "hr", "img", "col"}
DROP_TAGS = {
    "script", "iframe", "frame", "frameset", "object", "embed", "applet",
    "link", "base", "form", "input", "button", "textarea", "select", "option",
    "video", "audio", "source", "track", "canvas", "template", "math",
    "foreignobject", "use",
}
GLOBAL_ATTRIBUTES = {
    "id", "class", "role", "aria-label", "aria-hidden", "lang", "dir", "style",
    "title", "data-qid", "data-qid-ignore", "data-allow-overlap",
    # Inert browser-readback classification. Capacity and receipt gates validate
    # the value; admitting the attribute keeps model-declared and deterministic
    # post-processed semantic roles on the same renderer-safe path.
    "data-semantic-role",
    # Inert, server-stamped source identity used to join the admitted manifest
    # block to Chromium DOM geometry/readback evidence. The isolation parser
    # still applies its ordinary size, NUL, traversal, and duplicate checks.
    "data-source-block-id",
    # Inert export-planning hint occasionally emitted by Authored.  It carries
    # no URL or executable value; Hybrid still derives authoritative coverage
    # from the admitted DOM instead of trusting this hint.
    "data-raster-fallback",
    # Inert authored-header markers. They carry no behaviour and no URL; the
    # deck's header contract validator is what constrains their values.
    "data-header-part", "data-requirement-ids", "data-header-band",
}

#: Presentation attributes every SVG shape may carry. Values remain validated
#: by the isolation parser: paint attributes only reach local fragment ids,
#: opacities must be numbers, dasharrays and joins must match their grammars.
_SVG_PRESENTATION = {
    "transform", "fill", "stroke", "stroke-width", "fill-opacity",
    "stroke-opacity", "opacity", "stroke-linecap", "stroke-linejoin",
    "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit",
    "vector-effect", "clip-path", "mask", "paint-order", "shape-rendering",
}
#: Type attributes shared by <text> and <tspan>. A diagram labels its own axes,
#: and a Korean chart commonly sets these per span rather than in CSS.
_SVG_TYPESETTING = {
    "x", "y", "dx", "dy", "text-anchor", "dominant-baseline",
    "alignment-baseline", "font-size", "font-family", "font-weight",
    "font-style", "letter-spacing", "word-spacing", "textlength",
    "lengthadjust", "writing-mode",
}


def _svg(*extra: str) -> set[str]:
    return _SVG_PRESENTATION | set(extra)


TAG_ATTRIBUTES: Mapping[str, set[str]] = {
    "meta": {"charset"},
    "img": {"src", "alt", "width", "height", "loading", "decoding"},
    "th": {"colspan", "rowspan", "scope"},
    "td": {"colspan", "rowspan"},
    "col": {"span"},
    "svg": _svg(
        "viewbox", "width", "height", "x", "y", "preserveaspectratio",
        "focusable", "xmlns", "overflow",
    ),
    "g": _svg(),
    "path": _svg("d", "fill-rule", "clip-rule"),
    "rect": _svg("x", "y", "width", "height", "rx", "ry"),
    "circle": _svg("cx", "cy", "r"),
    "ellipse": _svg("cx", "cy", "rx", "ry"),
    "line": _svg("x1", "y1", "x2", "y2"),
    "polyline": _svg("points"),
    "polygon": _svg("points", "fill-rule", "clip-rule"),
    "text": _svg(*_SVG_TYPESETTING),
    "tspan": _svg(*_SVG_TYPESETTING),
    "lineargradient": {
        "id", "x1", "y1", "x2", "y2", "gradientunits", "gradienttransform",
        "spreadmethod",
    },
    "radialgradient": {
        "id", "cx", "cy", "r", "fr", "fx", "fy", "gradientunits",
        "gradienttransform", "spreadmethod",
    },
    "stop": {"offset", "stop-color", "stop-opacity"},
    "clippath": {"id", "transform", "clippathunits"},
    "mask": {
        "id", "x", "y", "width", "height", "maskunits", "maskcontentunits",
    },
}


__all__ = [
    "ALLOWED_TAGS",
    "DROP_TAGS",
    "GLOBAL_ATTRIBUTES",
    "TAG_ATTRIBUTES",
    "VOID_TAGS",
]
