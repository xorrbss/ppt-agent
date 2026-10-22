"""Shared text style for SVG author output: declare once on the root svg, expand on receipt.

The canonical page contract (``services/svg_contract.py``) requires ``font-family``,
``font-size`` and ``fill`` on every ``<text>`` and rejects ``font-*`` on ``<svg>``/``<g>``.
That makes the model repeat the same font and size on ~58 texts per page. This module lets
the model write the shared style once on the root ``<svg>`` and restores the canonical form
immediately after the response is parsed, before anything validates, binds or stores it.

Semantics are SVG presentation-attribute inheritance, restricted:

* the nearest ``svg``/``g`` ancestor that declares a property wins, and a property written on
  the ``<text>`` itself always wins (even an invalid value, which the unchanged validator then
  rejects);
* values are copied verbatim; nothing is computed, so ``14px`` or ``inherit`` are still
  rejected by the unchanged validator at the text;
* push-down targets ``<text>`` only: ``<tspan>`` inherits through its text and shapes are never
  touched;
* ``font-family``, ``font-size``, ``font-weight`` and ``text-anchor`` are stripped from every
  ``svg``/``g`` (the contract does not admit them there); ``fill`` is stripped from the root
  only, so a ``<g fill>`` keeps its normal meaning for the shapes it wraps;
* a required attribute that no ancestor declares stays missing, so the existing
  ``svg_contract_explicit_text_attributes_required`` still reports it.

A page written in today's explicit form is a byte-for-byte no-op for the expansion.
The ``SVG_AUTHOR_RULES`` constant is never modified: ``text_defaults_rules`` rewrites the
per-call rules string only (the constant is a durable digest input of the header-design call
and a golden fixture).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter

from services.svg_contract import SVG_NS

TEXT_DEFAULTS_VERSION = "svg-text-defaults/v1"
TEXT_DEFAULT_PROPS_T0 = ("font-family",)
TEXT_DEFAULT_PROPS_T1 = ("font-family", "font-size")
TEXT_DEFAULT_PROPS_T2 = ("font-family", "font-size", "fill")
TEXT_DEFAULTS_REPAIR_SENTENCE = (
    "previous_svg declares its shared text style on the root svg; keep that convention"
)

_NS = "{" + SVG_NS + "}"
# Properties the contract admits on <text> but rejects on svg/g.
_TEXT_ONLY = ("font-family", "font-size", "font-weight", "text-anchor")
# Everything a text may inherit; fill is the one property also legal on svg/g today.
_INHERITED = _TEXT_ONLY + ("fill",)
# Materialized attributes land where models usually write them: `id x y font-family ...`.
_ORDER = ("id", "x", "y", "font-family", "font-size", "font-weight", "fill", "text-anchor")
_RANK = {name: index for index, name in enumerate(_ORDER)}

_TEMPLATE_ANCHOR = (
    'Return <svg xmlns="http://www.w3.org/2000/svg" width="W" height="H" viewBox="0 0 W H">.'
)
_ATTRIBUTES_ANCHOR = (
    "Use explicit finite unitless coordinates. Set font-family,font-size,fill,x,y on EACH text."
)
_TEMPLATE_PREFIX = _TEMPLATE_ANCHOR[: -len(">.")]
# props -> (extra root attributes in the template, attributes still set on each text,
#           what the root declares, inherited names, attributes written only on a difference)
_RULE_VARIANTS = {
    TEXT_DEFAULT_PROPS_T0: (
        ' font-family="F"',
        "x,y,font-size,fill",
        "F is tokens.body_font",
        "F",
        "font-family",
    ),
    TEXT_DEFAULT_PROPS_T1: (
        ' font-family="F" font-size="S"',
        "x,y,fill",
        "F is tokens.body_font and S the most common text size on this page",
        "F and S",
        "font-family or font-size",
    ),
    TEXT_DEFAULT_PROPS_T2: (
        ' font-family="F" font-size="S" fill="C"',
        "x,y",
        "F is tokens.body_font, S the most common text size on this page, "
        "C the most common text colour",
        "F, S and C",
        "font-family, font-size or fill",
    ),
}


def _tag(node) -> str:
    tag = node.tag
    if not isinstance(tag, str):  # comments / processing instructions built by hand
        return ""
    return tag[len(_NS):] if tag.startswith(_NS) else tag


def _set_ordered(node, name: str, value: str) -> None:
    items = list(node.attrib.items())
    position = 0
    for index, (key, _) in enumerate(items):
        if _RANK.get(key, len(_ORDER)) < _RANK[name]:
            position = index + 1
    items.insert(position, (name, value))
    node.attrib.clear()
    node.attrib.update(items)


def expand_text_defaults(root) -> dict[str, int]:
    """Materialize inherited text properties on every <text>, strip the declarations (in place).

    Returns ``{"texts_filled", "containers_stripped"}``: how many ``<text>`` elements received at
    least one attribute, and how many ``svg``/``g`` elements lost at least one declaration. Both
    are 0 for every page written in today's explicit form. Iterative, so a hostile nesting depth
    cannot hit the recursion limit; never raises on model output."""
    texts_filled = containers_stripped = 0
    stack = [(root, {})]
    while stack:
        node, inherited = stack.pop()
        name = _tag(node)
        if name in ("svg", "g"):
            current = dict(inherited)
            for prop in _INHERITED:
                if prop in node.attrib:
                    current[prop] = node.attrib[prop]
            stripped = False
            for prop in _TEXT_ONLY:
                stripped = node.attrib.pop(prop, None) is not None or stripped
            if node is root:
                stripped = node.attrib.pop("fill", None) is not None or stripped
            containers_stripped += stripped
            stack.extend((child, current) for child in reversed(list(node)))
        elif name == "text":
            filled = False
            for prop in _INHERITED:
                if prop not in node.attrib and prop in inherited:
                    _set_ordered(node, prop, inherited[prop])
                    filled = True
            texts_filled += filled
    return {"texts_filled": texts_filled, "containers_stripped": containers_stripped}


def _length(name: str, value: str) -> int:
    return 1 + len(name) + 2 + len(value) + 1


def _protected(node, parents) -> bool:
    """Server-owned furniture (``svg-system-*``) stays verbose in the model view."""
    while node is not None:
        if (node.get("id") or "").startswith("svg-system-"):
            return True
        node = parents.get(node)
    return False


def _declared_above(node, prop: str, parents) -> bool:
    ancestor = parents.get(node)
    while ancestor is not None:
        if prop in ancestor.attrib:
            return True
        ancestor = parents.get(ancestor)
    return False


def _flatten(root) -> list:
    """Order-insensitive-attribute, iterative encoding of a tree (equal lists = equal trees)."""
    flat, stack = [], [root]
    while stack:
        node = stack.pop()
        flat.append((node.tag, tuple(sorted(node.attrib.items())), node.text, node.tail, len(node)))
        stack.extend(reversed(list(node)))
    return flat


def compact_text_defaults(svg: str, props) -> str:
    """Model view of a canonical page: the shared text style declared once on the root <svg>.

    Per property the most common value among model-authored texts (ties: smaller string) moves
    to the root when that saves characters. Server-owned ``svg-system-*`` texts and texts whose
    nearer ``<g>`` already declares the property are left explicit. Returns ``svg`` unchanged
    unless the view provably expands back to a tree identical to the input; never raises."""
    wanted = [prop for prop in dict.fromkeys(props or ()) if prop in _INHERITED]
    if not wanted or not isinstance(svg, str):
        return svg
    try:
        root = ET.fromstring(svg)
        original = _flatten(root)
        parents = {child: parent for parent in root.iter() for child in parent}
        texts = [n for n in root.iter() if _tag(n) == "text" and not _protected(n, parents)]
        declared = {}
        for prop in wanted:
            if prop in root.attrib:
                continue
            pool = [t for t in texts if prop in t.attrib and not _declared_above(t, prop, parents)]
            counts = Counter(t.attrib[prop] for t in pool)
            if not counts:
                continue
            value = sorted(counts, key=lambda v: (-counts[v], v))[0]
            if counts[value] * _length(prop, value) - _length(prop, value) <= 0:
                continue
            declared[prop] = value
            for text in pool:
                if text.attrib[prop] == value:
                    del text.attrib[prop]
        if not declared:
            return svg
        for prop, value in declared.items():
            root.set(prop, value)
        view = ET.tostring(root, encoding="unicode")
        check = ET.fromstring(view)
        expand_text_defaults(check)
        return view if _flatten(check) == original else svg
    except Exception:  # an outbound view must never break the call it decorates
        return svg


def text_defaults_rules(rules: str, props) -> str:
    """Rewrite the two svg author rule lines for a root-declared text style.

    ``rules`` is the per-call rules string, never the ``SVG_AUTHOR_RULES`` constant. Raises
    ``ValueError("svg_output_view_rule_anchor_missing")`` when either sentence it replaces is
    absent (a future edit of the rule text, or a second application), so it can never turn
    into a silent no-op."""
    variant = _RULE_VARIANTS.get(tuple(props))
    if variant is None:
        raise ValueError("svg_output_view_text_defaults_props_unsupported")
    if _TEMPLATE_ANCHOR not in rules or _ATTRIBUTES_ANCHOR not in rules:
        raise ValueError("svg_output_view_rule_anchor_missing")
    root_attributes, each_text, declares, names, written = variant
    template = _TEMPLATE_PREFIX + root_attributes + ">."
    attributes = (
        f"Use explicit finite unitless coordinates. Set {each_text} on EACH text. "
        f"The root svg declares the page's shared text style once: {declares}. "
        f"Every text inherits {names} as in SVG; write {written} on a text only when it "
        "differs from the root. After inheritance every text must have font-family, "
        "font-size and fill. Never declare font-weight or text-anchor on svg or g. "
        "The inherited style applies to text only; shapes always carry their own fill "
        "and stroke."
    )
    return rules.replace(_TEMPLATE_ANCHOR, template).replace(_ATTRIBUTES_ANCHOR, attributes)
