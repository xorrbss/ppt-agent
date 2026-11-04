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


def push_down_container_font_attributes(root) -> int:
    """Move font attributes an author wrote on ``svg``/``g`` onto the texts they govern (in place).

    The contract admits ``font-family``, ``font-size``, ``font-weight`` and
    ``text-anchor`` only on ``<text>``/``<tspan>``. A model occasionally writes
    them once on a container, which SVG inheritance gives exactly one meaning;
    the page used to be rejected for it, and on a common-header representative
    page that rejection failed the whole deck. Those four attributes are
    materialised on every ``<text>`` below that does not set its own and are
    stripped from the containers. ``fill`` is never touched (it is legal on
    containers and also paints shapes). Every page accepted before this
    existed is a byte-for-byte no-op, and a stored raw response replays
    identically because the producer and ``parse_author_response`` both call
    this. Returns how many ``<text>`` elements received an attribute.
    """
    filled = 0
    stack = [(root, {})]
    while stack:
        node, inherited = stack.pop()
        name = _tag(node)
        if name in ("svg", "g"):
            current = dict(inherited)
            for prop in _TEXT_ONLY:
                value = node.attrib.pop(prop, None)
                if value is not None:
                    current[prop] = value
            stack.extend((child, current) for child in reversed(list(node)))
        elif name == "text":
            changed = False
            for prop in _TEXT_ONLY:
                if prop not in node.attrib and prop in inherited:
                    _set_ordered(node, prop, inherited[prop])
                    changed = True
            filled += changed
    return filled


_UNPUSHABLE_FILLS = frozenset({"", "none", "transparent", "currentcolor", "inherit"})


def _pushable_fill(value: str) -> bool:
    text = value.strip().lower()
    return text not in _UNPUSHABLE_FILLS and not text.startswith("url(")


#: Elements whose zero size SVG defines as "rendering disabled" (rect and image width/height,
#: circle r).
_ZERO_SIZE_ATTRIBUTES = {"rect": ("width", "height"), "circle": ("r",), "image": ("width", "height")}


def drop_zero_size_shapes(root) -> int:
    """Remove every ``<rect>`` or ``<image>`` with a zero width or height and every
    ``<circle>`` with a zero radius (in place); return how many were removed.

    SVG renders nothing for them, not even a stroke ("a computed value of zero disables
    rendering of the element"), but the contract requires positive sizes
    (``svg_contract_positive_required``) because the PPTX converter cannot draw a zero-size
    shape. Measured 2026-11-04 on 250 template pages: 4 first drafts were rejected only for
    such a shape (``height="0"`` spacers and accent bars) and needed a whole new draft. On 24
    later template decks 3 more first drafts held an offered icon as
    ``<image href="source-slot-..." width="0" height="0">``, which the icon flattener leaves
    for the validator (``svg_source_image_unknown_slot``). A dropped icon counts as not shown,
    so a required one still fails (``svg_required_source_image_missing``, or the unused
    vector icon check). A negative or non-numeric size is left for the validator. No page
    accepted before this existed holds one, so every accepted page is a no-op; the producer and
    ``parse_author_response`` both call this, and the code fingerprint in
    ``services/svg_partial_recovery.py`` lists this file (see :func:`push_down_container_fill`).
    """
    removed = 0
    for parent in list(root.iter()):
        for child in list(parent):
            names = _ZERO_SIZE_ATTRIBUTES.get(_tag(child))
            if not names or len(child):
                continue
            try:
                zero = any(float(child.get(name, "nan")) == 0 for name in names)
            except ValueError:
                continue
            if zero:
                parent.remove(child)
                removed += 1
    return removed


#: Text this small draws no legible glyph (a ``font-size="1"`` spacer dot is 0.3 px wide).
_PLACEHOLDER_FONT_SIZE_PX = 2.0


def _inherited(node, name: str, parents: dict, default: str | None) -> str | None:
    while node is not None:
        if name in node.attrib:
            return node.attrib[name].strip()
        node = parents.get(node)
    return default


def _number_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def _paints_nothing(node, parents: dict) -> bool:
    """No fill and no stroke, own or inherited (SVG defaults: fill black, stroke none)."""
    return (_inherited(node, "fill", parents, "black") == "none"
            and _inherited(node, "stroke", parents, "none") == "none")


def drop_unrendered_marks(root) -> int:
    """Remove marks that SVG draws nothing for and that carry no content (in place).

    * a ``<path>`` with neither fill nor stroke, own or inherited: its geometry is never
      painted, so its ``d`` does not matter (one first draft wrote
      ``<path d="M940 312 A0 0 0 0 1 940 312" fill="none"/>`` in a donut chart, rejected as
      ``svg_contract_linear_path_only``);
    * an empty ``<style>`` or ``<defs>`` (no child, no text): one draft wrote ``<style></style>``
      (``svg_contract_unsupported_element``);
    * a ``<text>`` without any letter or digit (a spacer such as ``.``) that is invisible or
      illegible: no fill and no stroke (``svg_contract_invisible_text``), or, when it holds a
      non-space character, ``opacity`` or ``fill-opacity`` 0 on the text itself
      (``text_hidden``) or a font-size of at most 2 px (``small_body_text``). Measured
      2026-11-04 on 24 template decks (240 pages): 4 first drafts failed only for such a ``.``
      placeholder.

    Nothing visible changes, and no word or number can be removed (a text with a letter or
    digit is never touched, so an invisible real text still fails as before). A non-numeric
    size or opacity is left for the validator. Called by the producer and
    ``parse_author_response`` right after :func:`drop_zero_size_shapes`. Every page accepted
    before this existed is a no-op: each mark removed here failed the contract or the
    browser checks (the whitespace-only spacers with a fill that accepted pages hold, such as
    ``<text font-size="1" fill="#0E1116"> </text>``, are kept).
    """
    parents = {child: parent for parent in root.iter() for child in parent}
    removed = 0
    for node in list(root.iter()):
        parent = parents.get(node)
        if parent is None:
            continue
        name = _tag(node)
        drop = False
        if name == "path":
            drop = not len(node) and _paints_nothing(node, parents)
        elif name in ("style", "defs"):
            drop = not len(node) and not (node.text or "").strip()
        elif name == "text":
            content = "".join(node.itertext())
            if not any(char.isalnum() for char in content):
                size = _number_or_none(_inherited(node, "font-size", parents, None))
                opacities = [_number_or_none(node.get(key)) for key in ("opacity", "fill-opacity")]
                # A whitespace-only text already passes when it has a fill (it is not body
                # paint, see apply_template_body_quality); only its contract failure is new here.
                drop = _paints_nothing(node, parents) or bool(content.strip()) and (
                    any(value == 0 for value in opacities)
                    or (size is not None and 0 < size <= _PLACEHOLDER_FONT_SIZE_PX))
        if drop:
            parent.remove(node)
            removed += 1
    return removed


def _same_baseline_text_key(node, parents: dict) -> tuple[str, float] | None:
    """``(compact text, absolute baseline y)`` of a ``<text>``, or ``None`` when unknown."""
    import re

    y = _number_or_none(node.get("y"))
    if y is None:
        return None
    current = node
    while current is not None:
        transform = current.get("transform")
        if transform:
            match = re.fullmatch(r"\s*translate\(\s*([-+0-9.eE]+)(?:[\s,]+([-+0-9.eE]+))?\s*\)\s*", transform)
            shift = _number_or_none(match.group(2)) if match and match.group(2) else (0.0 if match else None)
            if shift is None:
                return None
            y += shift
        current = parents.get(current)
    text = "".join("".join(node.itertext()).split())
    return (text, y) if text else None


def drop_duplicate_native_table_headers(root) -> int:
    """Remove header labels written a second time outside their native table (in place).

    A ``data-native-table`` group draws its header cells itself. Measured 2026-11-04 on 24
    template decks: 3 first drafts (3 of the 9 ``text_overlap`` failures) drew a header band
    outside the group with its labels, then the same labels again inside the group on the
    same baseline (exactly on top, or 4-6 px to the side), so each label overlapped its own
    copy. A ``<text>`` outside every native table group is removed when its text equals a
    header-row cell of a table (``headerRows``, default 1) and a ``<text>`` inside that group
    with the same text sits on the same baseline (within 1 px, ``translate`` included): the
    label is still drawn by the table, which the PPTX export turns into the native table. A
    label on another baseline, or with any other wording, is left as it is. Runs before the
    declarations are extracted, in the producer and in ``parse_author_response`` alike.
    """
    import json

    parents = {child: parent for parent in root.iter() for child in parent}
    groups = [node for node in root.iter() if _tag(node) == "g" and "data-native-table" in node.attrib]
    if not groups:
        return 0
    inside: set = set()
    header_marks: list[tuple[str, float]] = []
    for group in groups:
        texts = [node for node in group.iter() if _tag(node) == "text"]
        inside.update(texts)
        try:
            declaration = json.loads(group.attrib["data-native-table"])
            rows = declaration["rows"]
            header_rows = int(declaration.get("headerRows", 1))
            headers = {"".join(str(cell).split()) for row in rows[:max(0, header_rows)] for cell in row}
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        for node in texts:
            key = _same_baseline_text_key(node, parents)
            if key is not None and key[0] in headers:
                header_marks.append(key)
    removed = 0
    for node in list(root.iter()):
        if _tag(node) != "text" or node in inside:
            continue
        key = _same_baseline_text_key(node, parents)
        if key is not None and any(key[0] == text and abs(key[1] - y) <= 1.0 for text, y in header_marks):
            parents[node].remove(node)
            removed += 1
    return removed


def push_down_container_fill(root) -> int:
    """Give each <text> that has no fill the fill it inherits from its nearest svg/g (in place).

    A model often writes the page's text colour once on a group and leaves it off the texts,
    which is how SVG inheritance renders it, but the contract wants ``fill`` on every
    ``<text>``. Measured on a real template deck (2026-11-04): after
    :func:`push_down_container_font_attributes`, all 15 of the 20 failing author attempts that
    still failed (``svg_contract_explicit_text_attributes_required``) lacked only ``fill``,
    57 to 68 texts on a page, and the repair attempt repeated it. The nearest declaration
    wins, so an inner ``none`` or paint server (``url(...)``) shadows an outer colour and
    pushes nothing. The container keeps its own fill (it also paints shapes), so rendering is
    unchanged. A page whose texts all carry a fill is a no-op, so no page accepted before this
    existed changes. The producer and ``parse_author_response`` both call this, so a response
    replays the same way under the same code. A response rejected before this function existed
    (a text without ``fill`` that a group supplied) may now be accepted when replayed; the code
    fingerprint in ``services/svg_partial_recovery.py``, which lists this file, refuses a partial
    recovery across this change. Returns how many <text> elements received a fill.
    """
    filled = 0
    stack = [(root, None)]
    while stack:
        node, inherited = stack.pop()
        name = _tag(node)
        if name in ("svg", "g"):
            current = inherited
            if "fill" in node.attrib:
                current = node.attrib["fill"] if _pushable_fill(node.attrib["fill"]) else None
            stack.extend((child, current) for child in reversed(list(node)))
        elif name == "text" and "fill" not in node.attrib and inherited is not None:
            _set_ordered(node, "fill", inherited)
            filled += 1
    return filled


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
