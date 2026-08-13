from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.maximum_quality_repair_benchmark import compare_observations, load_observation


def test_realistic_80_slide_fixture_compares_render_cost_and_resolution_rate():
    fixture = Path(__file__).parents[1] / "fixtures" / "maximum_quality_repair_benchmark_80.json"
    data = json.loads(fixture.read_text(encoding="utf-8"))

    comparison = compare_observations(data["baseline"], data["maximum_repair"])

    assert comparison["slide_count"] == 80
    assert comparison["delta"] == {
        "full_render_count": -62,
        "resolved_issue_count": 13,
        "resolution_rate": 0.135416,
    }
    assert comparison["acceptance"]["passed"] is True
    assert load_observation(fixture, "baseline")["slide_count"] == 80


def test_benchmark_rejects_deck_mismatch_and_hard_gate_regression():
    baseline = {
        "slide_count": 2, "detailed_issue_count": 2, "resolved_issue_count": 1,
        "remaining_issue_count": 1, "fixed_count": 1, "full_render_count": 4,
        "hard_violation_count": 0, "content_loss_count": 0,
    }
    mismatched = {**baseline, "slide_count": 3}
    with pytest.raises(ValueError, match="same deck"):
        compare_observations(baseline, mismatched)

    unsafe = {**baseline, "hard_violation_count": 1}
    assert compare_observations(baseline, unsafe)["acceptance"]["passed"] is False
