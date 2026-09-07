"""Measured type clamp: raise sub-floor text owners from the first render's DOM.

``enforce_authored_font_floor`` raises explicit ``font-size: Npx|pt``
declarations before the first render, but a size written as ``em``, ``rem``,
``%``, ``var()``, ``calc()``, the ``font`` shorthand or an SVG ``font-size``
attribute reaches Chromium untouched.  The first render's DOM metrics carry the
computed pixel size of every text owner, so the fix is deterministic: append an
inline ``font-size:<floor>px`` to each sub-floor owner (the last inline
declaration wins over any stylesheet rule that is not ``!important``) and
render once more.  Only when the second render still reports sub-floor text
does the slide enter the repair ladder, so no author call is spent on type
size (design Q2, ``docs/authored-sol-quality-first-design-20260908.md``).

Ownership and furniture follow ``inspect_fragmentation`` exactly: a page
number or a decoration/footer role is never clamped.  Elements are located by
their ``data-qid`` start tag, as ``utils/authored_fit_edits.py`` does, and the
document is never reserialized.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import re
from typing import Any, NamedTuple

from utils.authored_dom_quality import EXACT_RECONSTRUCTION
from utils.authored_dom_tags import StartTagCollector, attrs_dict
from utils.authored_fragmentation_gate import fit_font_floor_px, inspect_fragmentation
from utils.get_env_programme import (
    get_authored_fragmentation_max_text_owners,
    is_authored_fragmentation_gate_enabled,
)
from utils.runtime_settings import RuntimeSettings

#: Receipt key the clamp records under on the per-slide quality receipt.
MEASURED_TYPE_CLAMP_RECEIPT_KEY = "measured_type_clamp"

_STYLE_ATTR_PATTERN = re.compile(
    r"(\sstyle)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+)))?(?=\s|/?>)",
    re.IGNORECASE,
)
_TAG_END_PATTERN = re.compile(r"\s*/?>\s*$")
_ENTITY_PATTERN = re.compile(r"&#?\w+;")


class MeasuredTypeClampEdit(NamedTuple):
    """One owner raised from its computed size to the floor."""

    qid: str
    from_px: float
    to_px: float


@dataclass(frozen=True)
class ClampedRenderCapture:
    """The second render in the shape the pipeline reads a capture in.

    ``fitted_html`` is always the HTML of record: the clamped document, or the
    fitter's further edit of it when the second render fitted again.
    """

    png: bytes
    browser_metrics: Mapping[str, Any] | None
    fitted_html: str
    fit_report: Mapping[str, Any] | None


@dataclass(frozen=True)
class MeasuredTypeClampOutcome:
    """What one slide's render did about sub-floor type."""

    capture: Any
    edits: tuple[MeasuredTypeClampEdit, ...] = ()
    floor_px: float | None = None
    #: Text owners still below the floor after the second render.
    residual_sub_floor: int = 0
    rerendered: bool = False


def sub_floor_text_owners(
    browser_metrics: Mapping[str, Any] | None,
    *,
    floor_px: float,
    settings: RuntimeSettings | None = None,
) -> tuple[tuple[str, float], ...]:
    """``(qid, computed_px)`` of every counted text owner below ``floor_px``.

    Delegates to the gate so the ownership rule and the furniture exemption
    (page number, decoration, footer) are defined once.
    """

    report = inspect_fragmentation(
        browser_metrics,
        max_text_owners=get_authored_fragmentation_max_text_owners(settings),
        floor_px=int(floor_px),
    )
    return tuple((qid, px) for qid, px in report.sub_floor_owners if qid)


def _start_tags_by_qid(html: str) -> dict[str, tuple[int, str] | None]:
    """Map each ``data-qid`` to its start tag ``(offset, verbatim)``; ``None`` marks a duplicate."""

    parser = StartTagCollector()
    parser.feed(html)
    parser.close()
    located: dict[str, tuple[int, str] | None] = {}
    cursor = 0
    for _tag, raw_attrs, raw_tag in parser.tags:
        index = html.find(raw_tag, cursor)
        if index < 0:
            continue
        cursor = index + len(raw_tag)
        qid = attrs_dict(raw_attrs).get("data-qid", "").strip()
        if not qid:
            continue
        located[qid] = None if qid in located else (index, raw_tag)
    return located


def _split_declarations(style: str) -> list[str]:
    """Split inline CSS on ``;`` outside quotes, parentheses and entities."""

    declarations: list[str] = []
    depth = 0
    quote: str | None = None
    start = 0
    position = 0
    while position < len(style):
        char = style[position]
        if quote is not None:
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "&":
            entity = _ENTITY_PATTERN.match(style, position)
            if entity is not None:
                position = entity.end()
                continue
        elif char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            declarations.append(style[start:position])
            start = position + 1
        position += 1
    declarations.append(style[start:])
    return [item.strip() for item in declarations if item.strip()]


def _property_name(declaration: str) -> str:
    name, _separator, _value = declaration.partition(":")
    return name.strip().lower()


def floor_declaration(floor_px: float) -> str:
    """The exact inline declaration appended to a clamped owner."""

    return f"font-size:{floor_px:g}px"


def _style_with_floor(original: str, floor_px: float) -> str:
    kept = [
        item for item in _split_declarations(original) if _property_name(item) != "font-size"
    ]
    kept.append(floor_declaration(floor_px))
    return ";".join(kept)


def _escape_attribute_value(value: str) -> str:
    return value.replace('"', "&quot;")


def _start_tag_with_floor(raw_tag: str, floor_px: float) -> str | None:
    """``raw_tag`` with its inline ``font-size`` replaced by (or set to) the floor."""

    match = _STYLE_ATTR_PATTERN.search(raw_tag)
    if match is None:
        end = _TAG_END_PATTERN.search(raw_tag)
        if end is None:
            return None
        value = _escape_attribute_value(floor_declaration(floor_px))
        return f'{raw_tag[: end.start()]} style="{value}"{raw_tag[end.start() :]}'
    original = next((group for group in match.groups()[1:] if group is not None), "")
    value = _escape_attribute_value(_style_with_floor(original, floor_px))
    return raw_tag[: match.start()] + f' style="{value}"' + raw_tag[match.end() :]


def clamp_measured_type(
    html: str,
    browser_metrics: Mapping[str, Any] | None,
    *,
    floor_px: float,
    settings: RuntimeSettings | None = None,
) -> tuple[str, tuple[MeasuredTypeClampEdit, ...]]:
    """Set ``font-size:<floor>px`` inline on every sub-floor text owner.

    Returns the rewritten document and one edit per owner it changed.  An
    owner whose qid is missing or duplicated in ``html`` is left alone: the
    metrics then describe a different document and the gate path decides.
    """

    if not isinstance(html, str):
        raise TypeError("authored HTML must be a string")
    owners = sub_floor_text_owners(browser_metrics, floor_px=floor_px, settings=settings)
    if not owners:
        return html, ()
    located = _start_tags_by_qid(html)
    replacements: list[tuple[int, int, str]] = []
    edits: list[MeasuredTypeClampEdit] = []
    for qid, font_px in owners:
        target = located.get(qid)
        if target is None:
            continue
        offset, raw_tag = target
        rewritten = _start_tag_with_floor(raw_tag, floor_px)
        if rewritten is None or rewritten == raw_tag:
            continue
        replacements.append((offset, offset + len(raw_tag), rewritten))
        edits.append(MeasuredTypeClampEdit(qid, font_px, float(floor_px)))
    result = html
    for start, end, replacement in sorted(replacements, reverse=True):
        result = result[:start] + replacement + result[end:]
    return result, tuple(edits)


async def render_with_measured_type_clamp(
    html: str,
    *,
    reference_mode: str,
    render: Callable[..., Awaitable[Any]],
    settings: RuntimeSettings | None = None,
) -> MeasuredTypeClampOutcome:
    """Render ``html`` with the fitter; clamp sub-floor owners and render once more.

    Exactly one extra render per affected slide, never a loop: the second
    render's capture is returned whether or not it still reports sub-floor
    text, and the existing gate path judges it.  With the gate off, or in
    exact-reconstruction mode, the first capture is returned untouched.
    """

    capture = await render(html, fit=True)
    if reference_mode == EXACT_RECONSTRUCTION or not is_authored_fragmentation_gate_enabled(
        settings
    ):
        return MeasuredTypeClampOutcome(capture=capture)
    floor_px = fit_font_floor_px(settings)
    source = getattr(capture, "fitted_html", None) or html
    rewritten, edits = clamp_measured_type(
        source, getattr(capture, "browser_metrics", None), floor_px=floor_px, settings=settings
    )
    if not edits:
        return MeasuredTypeClampOutcome(capture=capture, floor_px=floor_px)
    second = await render(rewritten, fit=True)
    metrics = getattr(second, "browser_metrics", None)
    clamped = ClampedRenderCapture(
        png=second.png,
        browser_metrics=metrics,
        fitted_html=getattr(second, "fitted_html", None) or rewritten,
        fit_report=getattr(second, "fit_report", None),
    )
    return MeasuredTypeClampOutcome(
        capture=clamped,
        edits=edits,
        floor_px=floor_px,
        residual_sub_floor=len(
            sub_floor_text_owners(metrics, floor_px=floor_px, settings=settings)
        ),
        rerendered=True,
    )


def merge_measured_type_clamp_receipt(
    receipt: Mapping[str, Any] | None,
    outcome: MeasuredTypeClampOutcome | None,
) -> Mapping[str, Any] | None:
    """Record the clamp on a slide's quality receipt; unchanged when it did nothing."""

    if outcome is None or not outcome.edits:
        return receipt
    return {
        **dict(receipt or {}),
        MEASURED_TYPE_CLAMP_RECEIPT_KEY: {
            "floor_px": outcome.floor_px,
            "edit_count": len(outcome.edits),
            "edits": [
                {"qid": edit.qid, "from_px": edit.from_px, "to_px": edit.to_px}
                for edit in outcome.edits
            ],
            "residual_sub_floor": outcome.residual_sub_floor,
            "rerendered": outcome.rerendered,
        },
    }


__all__ = [
    "MEASURED_TYPE_CLAMP_RECEIPT_KEY",
    "ClampedRenderCapture",
    "MeasuredTypeClampEdit",
    "MeasuredTypeClampOutcome",
    "clamp_measured_type",
    "floor_declaration",
    "merge_measured_type_clamp_receipt",
    "render_with_measured_type_clamp",
    "sub_floor_text_owners",
]
