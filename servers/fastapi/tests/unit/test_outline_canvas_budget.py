"""Pins docs/canvas-expansion-content-budget-spec-20260929.md (rev 2) §3-4.

Standalone: `outline_canvas_budget` is not wired into the outline generator
yet, so these tests exercise the module directly against the spec's tables.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from utils.llm_calls import generate_presentation_outlines
from utils.llm_calls.outline_canvas_budget import (
    PROMPT_TARGETS,
    VERBOSITIES,
    budget_for_canvas,
    canvas_factor,
)

FLOOR_CHARS = 350

CANVASES = (
    ("wide-16x9", 1280, 720, 64, Decimal("1.00")),
    ("a4-landscape", 1188, 840, 60, Decimal("1.00")),
    ("a4-portrait", 840, 1188, 60, Decimal("1.00")),
    ("tall-9x16", 720, 1280, 64, Decimal("1.00")),
    ("tall-9x13", 720, 1040, 64, Decimal("0.79")),
    ("square-1x1", 720, 720, 64, Decimal("0.51")),
    ("ultrawide-21x9", 1680, 720, 64, Decimal("1.00")),
)

BASE_TARGET_CHARS = {"concise": 250, "standard": 500, "text-heavy": 800}
BASE_TARGET_WORDS = {"concise": 80, "standard": 150, "text-heavy": 250}
BASE_BULLETS = {"concise": (3, 4), "standard": (4, 6), "text-heavy": (5, 7)}
BASE_CEILING = {"concise": 400, "standard": 700, "text-heavy": 1100}
BASE_SCHEMA_MAX_LENGTH = {"concise": 400, "standard": 1000, "text-heavy": 2500}

EXPECTED_1X1 = {
    "target_chars": {"concise": 128, "standard": 255, "text-heavy": 408},
    "target_words": {"concise": 41, "standard": 77, "text-heavy": 128},
    "bullets": {"concise": (2, 3), "standard": (2, 3), "text-heavy": (3, 4)},
    "floor": 179,
    "ceiling": {"concise": 204, "standard": 357, "text-heavy": 561},
    "schema_max_length": {"concise": 204, "standard": 510, "text-heavy": 1275},
}

EXPECTED_9X13 = {
    "target_chars": {"concise": 198, "standard": 395, "text-heavy": 632},
    "target_words": {"concise": 63, "standard": 119, "text-heavy": 198},
    "bullets": {"concise": (2, 3), "standard": (3, 5), "text-heavy": (4, 6)},
    "floor": 277,
    "ceiling": {"concise": 316, "standard": 553, "text-heavy": 869},
    "schema_max_length": {"concise": 316, "standard": 790, "text-heavy": 1975},
}


def test_canvas_factor_matches_the_spec_table():
    for canvas_id, width, height, margin, expected_f in CANVASES:
        assert canvas_factor(width, height, margin) == expected_f, canvas_id


@pytest.mark.parametrize(
    ("canvas_id", "width", "height", "margin", "expected"),
    [
        ("square-1x1", 720, 720, 64, EXPECTED_1X1),
        ("tall-9x13", 720, 1040, 64, EXPECTED_9X13),
    ],
)
def test_scaled_values_match_the_spec_table(canvas_id, width, height, margin, expected):
    budget = budget_for_canvas(canvas_id, width, height, margin, floor_chars=FLOOR_CHARS)
    assert dict(budget.target_chars) == expected["target_chars"]
    assert dict(budget.target_words) == expected["target_words"]
    assert dict(budget.bullets) == expected["bullets"]
    assert budget.floor == expected["floor"]
    assert dict(budget.ceiling) == expected["ceiling"]
    assert dict(budget.schema_max_length) == expected["schema_max_length"]


@pytest.mark.parametrize(
    ("canvas_id", "width", "height", "margin"),
    [
        ("wide-16x9", 1280, 720, 64),
        ("a4-landscape", 1188, 840, 60),
        ("a4-portrait", 840, 1188, 60),
        ("tall-9x16", 720, 1280, 64),
        ("ultrawide-21x9", 1680, 720, 64),
    ],
)
def test_f_equal_one_canvases_return_todays_constants_exactly(canvas_id, width, height, margin):
    budget = budget_for_canvas(canvas_id, width, height, margin, floor_chars=FLOOR_CHARS)
    assert budget.factor == Decimal("1.00")
    assert dict(budget.target_chars) == BASE_TARGET_CHARS
    assert dict(budget.target_words) == BASE_TARGET_WORDS
    assert dict(budget.bullets) == BASE_BULLETS
    assert budget.floor == FLOOR_CHARS
    assert dict(budget.ceiling) == BASE_CEILING
    assert dict(budget.schema_max_length) == BASE_SCHEMA_MAX_LENGTH


def test_invariants_hold_over_every_canvas_and_verbosity():
    for canvas_id, width, height, margin, _f in CANVASES:
        budget = budget_for_canvas(canvas_id, width, height, margin, floor_chars=FLOOR_CHARS)
        for verbosity in VERBOSITIES:
            assert budget.floor < budget.ceiling[verbosity], (canvas_id, verbosity)
            assert (
                budget.target_chars[verbosity]
                <= budget.ceiling[verbosity]
                <= budget.schema_max_length[verbosity]
            ), (canvas_id, verbosity)
            lo, hi = budget.bullets[verbosity]
            assert lo <= hi, (canvas_id, verbosity)


def test_a_canvas_below_half_factor_raises():
    assert canvas_factor(600, 400, 64) < Decimal("0.5")
    with pytest.raises(ValueError, match="canvas_budget_factor_too_small"):
        budget_for_canvas("too-small", 600, 400, 64, floor_chars=FLOOR_CHARS)


def test_prompt_targets_match_the_outline_system_prompt(monkeypatch):
    monkeypatch.setattr(
        generate_presentation_outlines,
        "is_outline_density_ceiling_enabled",
        lambda *args, **kwargs: False,
    )
    for verbosity in VERBOSITIES:
        chars, words, (lo, hi) = PROMPT_TARGETS[verbosity]
        prompt = generate_presentation_outlines.get_system_prompt(verbosity, True, False)
        assert f"about {words} words" in prompt
        assert f"about {chars} characters" in prompt
        assert f"{lo} to {hi} bullets" in prompt
