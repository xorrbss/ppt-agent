"""Canvas-scaled outline content budgets.

Scales the outline stage's prompt targets, floor, ceiling, and schema
``max_length`` by a canvas's usable-area factor relative to the 16:9
baseline. See ``docs/canvas-expansion-content-budget-spec-20260929.md``
(rev 2) for the derivation; the numbers here are that spec's contract.

Not wired into the outline generator yet; this module is standalone and
imported by nothing outside its own tests.
"""

from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from types import MappingProxyType

from utils.get_dynamic_models import _OUTLINE_CONTENT_BOUNDS
from utils.llm_calls.outline_density import DENSITY_CEILINGS_BY_VERBOSITY

BASELINE_SAFE_AREA = 1152 * 592

VERBOSITIES = ("concise", "standard", "text-heavy")

#: (target_chars, target_words, (bullets_lo, bullets_hi)) per verbosity, as
#: stated in the outline system prompt (no importable constant exists for
#: these; they are literals in `generate_presentation_outlines.py`).
PROMPT_TARGETS = MappingProxyType(
    {
        "concise": (250, 80, (3, 4)),
        "standard": (500, 150, (4, 6)),
        "text-heavy": (800, 250, (5, 7)),
    }
)


def canvas_factor(width_px: int, height_px: int, margin_px: int) -> Decimal:
    usable_area = Decimal(width_px - 2 * margin_px) * Decimal(height_px - 2 * margin_px)
    ratio = usable_area / Decimal(BASELINE_SAFE_AREA)
    f = min(Decimal(1), ratio)
    return f.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def scaled(base: int, f: Decimal) -> int:
    return int((Decimal(base) * f + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


@dataclass(frozen=True)
class CanvasBudget:
    canvas_id: str
    factor: Decimal
    floor: int
    target_chars: MappingProxyType
    target_words: MappingProxyType
    bullets: MappingProxyType
    ceiling: MappingProxyType
    schema_max_length: MappingProxyType


def budget_for_canvas(
    canvas_id: str,
    width_px: int,
    height_px: int,
    margin_px: int,
    *,
    floor_chars: int,
) -> CanvasBudget:
    f = canvas_factor(width_px, height_px, margin_px)
    if f < Decimal("0.5"):
        raise ValueError("canvas_budget_factor_too_small")

    target_chars: dict[str, int] = {}
    target_words: dict[str, int] = {}
    bullets: dict[str, tuple[int, int]] = {}
    ceiling: dict[str, int] = {}
    schema_max_length: dict[str, int] = {}

    for verbosity in VERBOSITIES:
        chars, words, (lo, hi) = PROMPT_TARGETS[verbosity]
        target_chars[verbosity] = scaled(chars, f)
        target_words[verbosity] = scaled(words, f)
        bullets[verbosity] = (max(2, scaled(lo, f)), max(3, scaled(hi, f)))
        ceiling[verbosity] = scaled(DENSITY_CEILINGS_BY_VERBOSITY[verbosity], f)
        _, max_length = _OUTLINE_CONTENT_BOUNDS[verbosity]
        schema_max_length[verbosity] = scaled(max_length, f)

    return CanvasBudget(
        canvas_id=canvas_id,
        factor=f,
        floor=scaled(floor_chars, f),
        target_chars=MappingProxyType(target_chars),
        target_words=MappingProxyType(target_words),
        bullets=MappingProxyType(bullets),
        ceiling=MappingProxyType(ceiling),
        schema_max_length=MappingProxyType(schema_max_length),
    )
