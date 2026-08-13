"""Deterministic conservative title-fit for the canonical explicit lines."""

from __future__ import annotations

import unicodedata

from models.authored_vnext.master.contract import (
    DeckMasterContract,
    MasterLayout,
    PowerPointMargins,
    TextStyle,
)
from models.authored_vnext.master.slide import ExplicitTitle, TitleFitResult


_CLOSING = frozenset("」』）)]｝】〉》〕〗〙〛、。，．,.!?！？:：;；…")
_OPENING = frozenset("「『（([｛【〈《〔〖〘〚")


def _layout(contract: DeckMasterContract, layout_id: str) -> MasterLayout:
    for layout in (
        contract.layouts.cover,
        contract.layouts.section,
        contract.layouts.standard,
        contract.layouts.long_title,
    ):
        if layout.id == layout_id:
            return layout
    raise ValueError("layout is not registered by the master")


def _title_style(contract: DeckMasterContract, layout: MasterLayout) -> tuple[TextStyle, object]:
    title_slots = [slot for slot in layout.slots if slot.role == "title"]
    if len(title_slots) != 1:
        raise ValueError("layout must contain exactly one title slot")
    return contract.semantic_styles.title, title_slots[0]


def _glyph_em_width(character: str) -> float:
    if character.isspace():
        return 0.32
    if unicodedata.east_asian_width(character) in {"F", "W"}:
        return 1.0
    category = unicodedata.category(character)
    if category.startswith("M"):
        return 0.0
    if category.startswith("P"):
        return 0.42
    if character.isupper():
        return 0.68
    if character.isdigit():
        return 0.58
    return 0.56


def _line_width_px(line: str, font_pt: float) -> float:
    return sum(_glyph_em_width(character) for character in line) * font_pt * (96 / 72)


def _kinsoku_violations(lines: list[str]) -> list[str]:
    violations: list[str] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and stripped[0] in _CLOSING:
            violations.append(f"line[{index}].leadingClosingPunctuation")
        if stripped and stripped[-1] in _OPENING:
            violations.append(f"line[{index}].trailingOpeningPunctuation")
    return violations


def fit_title(
    contract: DeckMasterContract,
    layout_id: str,
    title: ExplicitTitle,
) -> TitleFitResult:
    layout = _layout(contract, layout_id)
    style, slot = _title_style(contract, layout)
    margins = style.margins
    horizontal_margin_px = (margins.left_pt + margins.right_pt) * (96 / 72)
    vertical_margin_px = (margins.top_pt + margins.bottom_pt) * (96 / 72)
    available_width = max(0.0, slot.bounds.width - horizontal_margin_px)
    available_height = max(0.0, slot.bounds.height - vertical_margin_px)
    max_lines = (
        contract.fit_policy.long_title_max_lines
        if layout.kind == "longTitle"
        else contract.fit_policy.standard_max_lines
    )
    allowed_lines = min(max_lines, style.max_lines)
    chosen = min(style.font_pt, contract.fit_policy.preferred_font_pt)
    minimum = max(style.min_font_pt, contract.fit_policy.minimum_font_pt)
    while chosen >= minimum:
        width_ok = all(
            _line_width_px(line, chosen) <= available_width for line in title.lines
        )
        height = len(title.lines) * chosen * (96 / 72) * style.line_height
        if width_ok and height <= available_height:
            break
        chosen -= 0.5
    violations = _kinsoku_violations(title.lines)
    if len(title.lines) > allowed_lines:
        violations.append("lineCountExceeded")
    if chosen < minimum:
        violations.append("textBoundsExceeded")
        chosen = minimum
    return TitleFitResult(
        layout_id=layout.id,
        explicit_lines=title.lines,
        font_family=contract.font_manifest.windows_fallback,
        font_manifest_digest=contract.font_manifest.digest,
        chosen_font_pt=chosen,
        margins=PowerPointMargins(
            left_pt=margins.left_pt,
            right_pt=margins.right_pt,
            top_pt=margins.top_pt,
            bottom_pt=margins.bottom_pt,
        ),
        clipped=bool(violations),
        violations=violations,
    )
