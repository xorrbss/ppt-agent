"""Uniform text-scale applier for authored slide HTML.

Rewrites absolute ``font-size``/``line-height`` pixel values by a factor, in
both inline ``style`` attributes and rule bodies inside ``<style>`` blocks
written by the model. It is a pure string transform — no DOM, no browser —
used by the A4 fit ladder's rung 2 (uniform text scale-down) and by the
offline replay script that measures how far a failing slide misses (see
``docs/authored-fit-magnitude-measurement-design-20260904.md`` section 4).

Two things are deliberately left alone, because they are not text at all:

* Anything belonging to an infographic figure — an element carrying the
  ``ig`` class or an ``ig-*`` part class, and any ``<style>`` rule whose
  selector mentions ``.ig``/``.ig-``. Figure density is the ladder's rung 1,
  not this rung's job.
* The server-injected library stylesheet itself
  (``utils/authored_infographic/library.py``). It carries no attribute
  marker on its ``<style>`` tag, but its CSS text always starts with the
  literal rule ``LIBRARY_MARKER`` regardless of brand (colours and the font
  stack are the only per-brand tokens in it); a ``<style>`` block containing
  that marker is recognised as the library block and skipped whole.

Percent, ``em``, ``rem``, ``vw``/``vh``, ``clamp()``/``calc()`` and keyword
values are left untouched and counted as unscaled — only a bare
``<number>px`` value is rewritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from utils.authored_infographic.library import LIBRARY_MARKER

__all__ = ["ScaledHtml", "scale_authored_text"]


@dataclass(frozen=True)
class ScaledHtml:
    """Result of one scaling pass."""

    html: str
    scaled_declarations: int
    floored_declarations: int
    unscaled_declarations: int
    skipped_figure_declarations: int


class _Counters:
    """Mutable tally threaded through one rewrite pass."""

    __slots__ = ("scaled", "floored", "unscaled", "skipped")

    def __init__(self) -> None:
        self.scaled = 0
        self.floored = 0
        self.unscaled = 0
        self.skipped = 0


# ---------------------------------------------------------------------------
# Declaration-level parsing, shared by inline style attributes and <style>
# rule bodies. Both call sites pass an already-isolated declarations string
# (an attribute value, or one rule's body with its braces stripped), so a
# bare ``;`` or end-of-string is always the right terminator.
# ---------------------------------------------------------------------------

_DECL_RE = re.compile(
    r"(?<![\w-])(?P<prop>font-size|line-height)(?![\w-])(?P<sep>\s*:\s*)(?P<value>[^;}]+)",
    re.IGNORECASE,
)
_BANG_RE = re.compile(r"^(?P<num>.*?)\s*(?P<bang>!\s*important)\s*$", re.IGNORECASE)
_PX_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)px$", re.IGNORECASE)


def _split_important(raw: str) -> tuple[str, bool]:
    match = _BANG_RE.match(raw.strip())
    if match:
        return match.group("num").strip(), True
    return raw.strip(), False


def _px_value(value: str) -> float | None:
    match = _PX_RE.match(value.strip())
    return float(match.group(1)) if match else None


def _format_number(value: float) -> str:
    rounded = round(value, 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def _count_declarations(text: str, counters: _Counters) -> None:
    """Tally every font-size/line-height declaration in a skip zone."""
    for _ in _DECL_RE.finditer(text):
        counters.skipped += 1


def _rewrite_declarations(text: str, factor: float, floor_px: float, counters: _Counters) -> str:
    def repl(match: re.Match[str]) -> str:
        prop = match.group("prop")
        raw = match.group("value")
        num_part, important = _split_important(raw)
        px = _px_value(num_part)
        if px is None:
            counters.unscaled += 1
            return match.group(0)

        prop_lower = prop.lower()
        if prop_lower == "font-size":
            if px < floor_px:
                # Already below the design floor before scaling: the author
                # chose a value the floor already permits, so scaling it
                # further down is not this applier's call to make.
                counters.unscaled += 1
                return match.group(0)
            scaled = px * factor
            if scaled < floor_px:
                new_value = floor_px
                counters.floored += 1
            else:
                new_value = scaled
                counters.scaled += 1
        else:  # line-height: no floor, always scales
            new_value = px * factor
            counters.scaled += 1

        sep = match.group("sep")
        suffix = " !important" if important else ""
        return f"{prop}{sep}{_format_number(new_value)}px{suffix}"

    return _DECL_RE.sub(repl, text)


# ---------------------------------------------------------------------------
# <style> block handling.
# ---------------------------------------------------------------------------

_STYLE_BLOCK_RE = re.compile(
    r"^(?P<open><style\b[^>]*>)(?P<body>.*)(?P<close></style\s*>)$",
    re.IGNORECASE | re.DOTALL,
)
_RULE_RE = re.compile(r"(?P<selector>[^{}]+)\{(?P<body>[^{}]*)\}", re.DOTALL)
_FIGURE_SELECTOR_RE = re.compile(r"\.ig(?:-[\w-]*)?(?=[^\w-]|$)")


def _is_figure_selector(selector: str) -> bool:
    return _FIGURE_SELECTOR_RE.search(selector) is not None


def _is_library_css(body: str) -> bool:
    return LIBRARY_MARKER in body


def _process_style_block(block: str, factor: float, floor_px: float, counters: _Counters) -> str:
    match = _STYLE_BLOCK_RE.match(block)
    if match is None:
        # Not a well-formed single <style>...</style> span; leave untouched
        # rather than guess at a rewrite.
        return block

    body = match.group("body")
    if _is_library_css(body):
        _count_declarations(body, counters)
        return block

    out: list[str] = []
    pos = 0
    for rule in _RULE_RE.finditer(body):
        out.append(body[pos : rule.start()])
        pos = rule.end()
        selector = rule.group("selector")
        rule_body = rule.group("body")
        if _is_figure_selector(selector):
            _count_declarations(rule_body, counters)
            out.append(rule.group(0))
        else:
            new_body = _rewrite_declarations(rule_body, factor, floor_px, counters)
            out.append(f"{selector}{{{new_body}}}")
    out.append(body[pos:])

    return f"{match.group('open')}{''.join(out)}{match.group('close')}"


# ---------------------------------------------------------------------------
# Inline style attribute handling.
# ---------------------------------------------------------------------------

_CLASS_ATTR_RE = re.compile(
    r"(?<![\w-])class\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)
_STYLE_ATTR_RE = re.compile(
    r"(?<![\w-])style\s*=\s*(?P<quote>[\"'])(?P<value>.*?)(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)


def _is_figure_tag(tag: str) -> bool:
    match = _CLASS_ATTR_RE.search(tag)
    if match is None:
        return False
    classes = match.group("value").split()
    return any(cls == "ig" or cls.startswith("ig-") for cls in classes)


def _process_tag(tag: str, factor: float, floor_px: float, counters: _Counters) -> str:
    style_match = _STYLE_ATTR_RE.search(tag)
    if style_match is None:
        return tag

    value = style_match.group("value")
    if _is_figure_tag(tag):
        _count_declarations(value, counters)
        return tag

    new_value = _rewrite_declarations(value, factor, floor_px, counters)
    if new_value == value:
        return tag
    start, end = style_match.span("value")
    return f"{tag[:start]}{new_value}{tag[end:]}"


# ---------------------------------------------------------------------------
# Top-level HTML walk: script blocks are copied verbatim, style blocks and
# element tags are routed to the handlers above, everything else (text
# nodes, closing tags, comments) is copied through untouched.
# ---------------------------------------------------------------------------

_BLOCK_RE = re.compile(
    r"(?P<script><script\b[^>]*>.*?</script\s*>)"
    r"|(?P<style><style\b[^>]*>.*?</style\s*>)"
    r"|(?P<tag><[a-zA-Z][\w:-]*\b[^>]*?/?>)",
    re.IGNORECASE | re.DOTALL,
)


def _rewrite_html(html: str, factor: float, floor_px: float, counters: _Counters) -> str:
    out: list[str] = []
    pos = 0
    for match in _BLOCK_RE.finditer(html):
        out.append(html[pos : match.start()])
        pos = match.end()
        if match.group("script") is not None:
            out.append(match.group("script"))
        elif match.group("style") is not None:
            out.append(_process_style_block(match.group("style"), factor, floor_px, counters))
        else:
            out.append(_process_tag(match.group("tag"), factor, floor_px, counters))
    out.append(html[pos:])
    return "".join(out)


def scale_authored_text(html: str, factor: float, *, floor_px: float = 15.0) -> ScaledHtml:
    """Scale every body ``font-size``/``line-height`` px declaration by ``factor``.

    Figure (``.ig``/``ig-*``) markup and the injected library stylesheet are
    left untouched and reported separately via ``skipped_figure_declarations``.
    ``factor == 1.0`` returns ``html`` unchanged byte-for-byte (rounding a
    value by a no-op factor can still reformat it, e.g. ``20.0px`` ->
    ``20px``, which would violate identity), while still computing counts.
    """
    counters = _Counters()
    rewritten = _rewrite_html(html, factor, floor_px, counters)
    return ScaledHtml(
        html=html if factor == 1.0 else rewritten,
        scaled_declarations=counters.scaled,
        floored_declarations=counters.floored,
        unscaled_declarations=counters.unscaled,
        skipped_figure_declarations=counters.skipped,
    )
