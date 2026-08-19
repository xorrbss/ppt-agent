"""Fit one deck's requirement chip group onto a single row.

The width arithmetic lives beside the contract rather than inside it because it
is an estimate — glyph advances without a font — and the contract itself must
stay a description of decisions, not of measurements. A ``+N`` chip that wraps
onto a second row makes the header taller on some slides than others, which is
the exact inconsistency the contract exists to remove.
"""

from __future__ import annotations

import math

from models.authored_header_contract import (
    SLIDE_WIDTH_PX,
    AuthoredHeaderContract,
    HeaderRequirementSpec,
)

#: Requirement identifiers are ``PREFIX-000`` shaped; the overflow chip is ``+N``.
_REQUIREMENT_ID_CHARS = 7
_OVERFLOW_CHIP_CHARS = 3
#: Average advance of an uppercase alphanumeric glyph, as a fraction of font size.
_GLYPH_ADVANCE_RATIO = 0.62


def _chip_width_px(spec: HeaderRequirementSpec, characters: int) -> int:
    """Estimate one chip's rendered width, including its 1px border on each side."""
    text = math.ceil(characters * _GLYPH_ADVANCE_RATIO * spec.font_size_px)
    return spec.padding_x_px * 2 + 2 + text


def requirement_row_width_px(contract: "AuthoredHeaderContract") -> int:
    """Width the requirement group needs to stay on exactly one row."""
    spec = contract.requirements
    if not spec.enabled or spec.style != "chip":
        return 0
    width = spec.max_visible * _chip_width_px(spec, _REQUIREMENT_ID_CHARS)
    gaps = spec.max_visible - 1
    if spec.overflow == "plusN":
        width += _chip_width_px(spec, _OVERFLOW_CHIP_CHARS)
        gaps += 1
    return width + max(0, gaps) * spec.gap_px


def fit_requirement_row(contract: "AuthoredHeaderContract") -> "AuthoredHeaderContract":
    """Widen the right slot, then trim visible chips, until one row fits.

    A ``+N`` chip that wraps onto a second row makes the header taller on some
    slides than others, which is the exact inconsistency the contract exists to
    remove. Widening first keeps as many identifiers visible as possible.
    """
    if not contract.requirements.enabled or contract.requirements.style != "chip":
        return contract

    payload = contract.model_dump()
    widest = min(
        560,
        SLIDE_WIDTH_PX - 2 * contract.margin_x_px - contract.title.max_width_px,
    )
    working = contract
    needed = requirement_row_width_px(working)
    if needed > working.right_slot.width_px and widest >= needed:
        payload["right_slot"]["width_px"] = max(160, needed)
        return AuthoredHeaderContract.model_validate(payload)

    while needed > working.right_slot.width_px and payload["requirements"][
        "max_visible"
    ] > 1:
        payload["requirements"]["max_visible"] -= 1
        working = AuthoredHeaderContract.model_validate(payload)
        needed = requirement_row_width_px(working)
    return working



__all__ = ["fit_requirement_row", "requirement_row_width_px"]
