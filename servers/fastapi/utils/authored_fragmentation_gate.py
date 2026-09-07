"""Fragmentation gate for authored slides: text-owner count and a type floor.

Measured on one fixture and one code line (2026-09-08): two models put the
same ~400-450 visible characters on a slide.  The one that split them across
80-96 text-owning elements at 14-15px pushed ink outside 17 cards, 9 of which
the in-render fitter could not rescue; the one that used 46-47 owners at 18px
pushed ink out of 2.  The product decision is therefore to gate the *shape* of
the copy and never its amount: a violation is repaired by merging boxes, not
by shrinking type and not by cutting CONTENT.

The gate is independent of the composition envelope
(``AUTHORED_COMPOSITION_ENVELOPE_ENABLED``).  It binds no character count and
runs whether or not the envelope does.  Its violations ride the envelope's
road -- ``capacity_repair_instructions`` -> the one bounded repair round in
``services/authored_presentation_generation.py`` -- and never enter the final
publish gate, so they cannot block a deck on their own.

The in-render fitter shares the floor: with the gate on it stops shrinking at
``AUTHORED_TYPE_FLOOR_PX`` and reports ``unfixable:floor``, which this gate
turns into a merge-boxes instruction (``fit_floor``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from services.authored_vnext.capability_registry import AUTHORED_FONT_FLOOR_PX
from utils.authored_dom_footer_zone import _looks_like_page_number
from utils.authored_dom_quality import normalize_browser_metrics
from utils.get_env_programme import (
    get_authored_fragmentation_max_text_owners,
    get_authored_type_floor_px,
    is_authored_fragmentation_gate_enabled,
)
from utils.runtime_settings import RuntimeSettings

#: Issue category the gate reports under; routed like ``capacity`` failures.
FRAGMENTATION_CATEGORY = "fragmentation"
TEXT_OWNERS_VIOLATION = "text_owners"
TYPE_FLOOR_VIOLATION = "type_floor"
FIT_FLOOR_VIOLATION = "fit_floor"
#: Fitter card outcome meaning "shrinking to the floor was not enough".
FIT_UNFIXABLE_FLOOR_OUTCOME = "unfixable:floor"

#: Roles that own text without being copy: never counted, never floored.
_FURNITURE_ROLES = frozenset(
    {"decoration", "page_number", "page-number", "pagenumber", "footer", "furniture"}
)
_VIOLATION_PATTERN = re.compile(
    rf"({TEXT_OWNERS_VIOLATION}|{TYPE_FLOOR_VIOLATION}|{FIT_FLOOR_VIOLATION}):(\d+)([<>])(\d+)"
)
_MAX_INSTRUCTION_CHARS = 200


@dataclass(frozen=True)
class FragmentationReport:
    """What the gate measured on one rendered slide."""

    text_owners: int
    max_text_owners: int
    #: ``(qid, font_px)`` of every counted owner rendered below the floor.
    sub_floor_owners: tuple[tuple[str, float], ...]
    #: Smallest counted owner type in px; ``None`` when nothing was measured.
    min_font_px: float | None
    floor_px: int
    #: Cards the fitter gave up on at the floor (``unfixable:floor``).
    fit_floor_cards: int
    violations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["sub_floor_owners"] = [
            {"qid": qid, "font_px": font_px} for qid, font_px in self.sub_floor_owners
        ]
        return payload


def _rendered_text(item: Mapping[str, Any]) -> str:
    value = item.get("rendered_text", item.get("renderedText", item.get("text", "")))
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _owns_text(item: Mapping[str, Any], text: str) -> bool:
    owned = item.get("has_owned_text", item.get("hasOwnedText"))
    if isinstance(owned, bool):
        return owned and bool(text)
    return bool(text)


def _role(item: Mapping[str, Any]) -> str:
    raw = item.get("semantic_role", item.get("semanticRole", item.get("role", "")))
    return str(raw or "").strip().lower()


def _font_px(item: Mapping[str, Any]) -> float | None:
    value = item.get("font_size", item.get("fontSize"))
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    size = float(value)
    return size if size > 0 and size == size else None


def _is_furniture(item: Mapping[str, Any], role: str, viewport: Mapping[str, Any]) -> bool:
    if role in _FURNITURE_ROLES:
        return True
    return _looks_like_page_number(
        item,
        viewport_width=float(viewport.get("width", 1280) or 1280),
        viewport_height=float(viewport.get("height", 720) or 720),
    )


def fit_floor_unfixable_cards(fit_report: Mapping[str, Any] | None) -> int:
    """Count the cards a fit report gave up on at the font floor."""
    if not isinstance(fit_report, Mapping):
        return 0
    cards = fit_report.get("cards")
    if not isinstance(cards, Sequence) or isinstance(cards, (str, bytes)):
        return 0
    return sum(
        1
        for card in cards
        if isinstance(card, Mapping)
        and str(card.get("outcome", "")).startswith(FIT_UNFIXABLE_FLOOR_OUTCOME)
    )


def inspect_fragmentation(
    browser_metrics: Mapping[str, Any] | None,
    *,
    max_text_owners: int,
    floor_px: int,
    fit_report: Mapping[str, Any] | None = None,
) -> FragmentationReport:
    """Measure text owners and sub-floor type on one slide's browser metrics.

    A text owner is a measured element that owns non-empty rendered text and
    is not furniture (decoration, footer, page number).  Font sizes are the
    computed px the probe reported.  Nothing here reads a character count.
    """

    metrics = normalize_browser_metrics(browser_metrics)
    viewport = metrics.get("viewport", {})
    owners = 0
    sub_floor: list[tuple[str, float]] = []
    min_font: float | None = None
    for item in metrics.get("elements", ()):
        if not isinstance(item, Mapping):
            continue
        text = _rendered_text(item)
        if not _owns_text(item, text):
            continue
        role = _role(item)
        if _is_furniture(item, role, viewport):
            continue
        owners += 1
        font = _font_px(item)
        if font is None:
            continue
        min_font = font if min_font is None else min(min_font, font)
        if font < floor_px:
            sub_floor.append((str(item.get("qid", "") or ""), font))
    fit_floor_cards = fit_floor_unfixable_cards(fit_report)
    violations: list[str] = []
    if owners > max_text_owners:
        violations.append(f"{TEXT_OWNERS_VIOLATION}:{owners}>{max_text_owners}")
    if sub_floor:
        violations.append(f"{TYPE_FLOOR_VIOLATION}:{len(sub_floor)}<{floor_px}")
    if fit_floor_cards:
        violations.append(f"{FIT_FLOOR_VIOLATION}:{fit_floor_cards}>0")
    return FragmentationReport(
        text_owners=owners,
        max_text_owners=max_text_owners,
        sub_floor_owners=tuple(sub_floor),
        min_font_px=min_font,
        floor_px=floor_px,
        fit_floor_cards=fit_floor_cards,
        violations=tuple(violations),
    )


def fragmentation_violations(
    browser_metrics: Mapping[str, Any] | None,
    *,
    fit_report: Mapping[str, Any] | None = None,
    settings: RuntimeSettings | None = None,
) -> tuple[str, ...]:
    """The gate's violations for one slide, or nothing while the knob is off."""

    if not is_authored_fragmentation_gate_enabled(settings):
        return ()
    return inspect_fragmentation(
        browser_metrics,
        max_text_owners=get_authored_fragmentation_max_text_owners(settings),
        floor_px=get_authored_type_floor_px(settings),
        fit_report=fit_report,
    ).violations


def fragmentation_repair_instruction(role: str, violation: str) -> str | None:
    """One bounded repair instruction for a gate violation, else ``None``.

    Every sentence says "merge boxes" and forbids the two escapes the gate
    exists to close: shrinking type and cutting CONTENT.
    """

    match = _VIOLATION_PATTERN.fullmatch(str(violation or "").strip())
    if match is None:
        return None
    slide_role = (role or "SLIDE").strip().upper() or "SLIDE"
    name, measured, _sign, bound = match.groups()
    if name == TEXT_OWNERS_VIOLATION:
        detail = (
            f"{slide_role} has {measured} text-owning elements (limit {bound}). Merge "
            "labels, kickers and small boxes into fewer, larger text blocks; keep "
            "CONTENT; do not shrink type."
        )
    elif name == TYPE_FLOOR_VIOLATION:
        detail = (
            f"{slide_role} has {measured} text element(s) below {bound}px. Raise type to "
            f"at least {bound}px; make room by merging boxes, not by cutting CONTENT or "
            "shrinking other type."
        )
    else:
        detail = (
            f"{slide_role}: {measured} card(s) still overflow at the type floor. Merge "
            "their labels and boxes into fewer, larger blocks; keep CONTENT; do not "
            "shrink type."
        )
    return detail[:_MAX_INSTRUCTION_CHARS]


def fragmentation_prompt_clause(settings: RuntimeSettings | None = None) -> str:
    """The one countable design-system clause; empty while the knob is off.

    Empty keeps ``build_design_system`` byte-identical to the pre-gate prompt;
    the frozen ``PRE_BUNDLE_TYPE_SCALES`` baseline is never edited for this.
    """

    if not is_authored_fragmentation_gate_enabled(settings):
        return ""
    owners = get_authored_fragmentation_max_text_owners(settings)
    floor = get_authored_type_floor_px(settings)
    return (
        f"\nFragmentation: use at most {owners} text-owning elements on a slide; body "
        f"type never below {floor}px; when copy does not fit, merge boxes rather than "
        "shrinking type."
    )


def fit_font_floor_px(settings: RuntimeSettings | None = None) -> float:
    """The floor the in-render fitter shrinks to: the gate floor when the gate
    is on, the registry's ``AUTHORED_FONT_FLOOR_PX`` otherwise."""

    if is_authored_fragmentation_gate_enabled(settings):
        return float(get_authored_type_floor_px(settings))
    return float(AUTHORED_FONT_FLOOR_PX)


__all__ = [
    "FIT_FLOOR_VIOLATION",
    "FIT_UNFIXABLE_FLOOR_OUTCOME",
    "FRAGMENTATION_CATEGORY",
    "FragmentationReport",
    "TEXT_OWNERS_VIOLATION",
    "TYPE_FLOOR_VIOLATION",
    "fit_floor_unfixable_cards",
    "fit_font_floor_px",
    "fragmentation_prompt_clause",
    "fragmentation_repair_instruction",
    "fragmentation_violations",
    "inspect_fragmentation",
]
