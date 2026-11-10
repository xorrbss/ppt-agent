"""Pure-helper tests for the authored fit replay harness (M3).

No Chromium and no database: the bucket rules, the current/legacy job rule,
the summary arithmetic behind the M5 decision inputs, and the external-image
skip are all total functions of already-measured results, which is exactly why
the design puts the classification in its own module. The one orchestration
test replaces the render-and-gate call with a stub.

``docs/authored-fit-magnitude-measurement-design-20260904.md`` sections 5-7.
"""

from __future__ import annotations


import pytest

from scripts import authored_fit_replay_buckets as buckets


def outcome(scale: float, passed: bool, scaled: int = 10, floored: int = 0):
    return buckets.ScaleOutcome(
        scale=scale,
        passed=passed,
        scaled_declarations=scaled,
        floored_declarations=floored,
    )


def classify(outcomes, worst_kind="text", flagged=True, in_ladder=True):
    return buckets.classify_bucket(
        flagged=flagged,
        in_ladder_population=in_ladder,
        worst_kind=worst_kind,
        outcomes=outcomes,
    )


# --------------------------------------------------------------------------
# 5.2 bucket table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "outcomes, expected",
    [
        ([outcome(0.95, True)], "fits_at_0.95"),
        ([outcome(0.95, False), outcome(0.90, True)], "fits_at_0.90"),
        (
            [outcome(0.95, False), outcome(0.90, False), outcome(0.85, True)],
            "fits_at_0.85",
        ),
        (
            [
                outcome(0.95, False),
                outcome(0.90, False),
                outcome(0.85, False),
                outcome(0.80, True),
            ],
            "fits_at_0.80",
        ),
    ],
)
def test_first_passing_scale_names_the_bucket(outcomes, expected):
    assert classify(outcomes) == expected


def test_floor_bound_when_half_the_body_declarations_hit_the_floor():
    # 5 scaled + 5 floored: the page fits only by sitting on the 15px floor.
    assert classify([outcome(0.90, True, scaled=5, floored=5)]) == "floor_bound"


def test_floor_share_just_below_the_threshold_is_a_real_fit():
    assert classify([outcome(0.90, True, scaled=6, floored=5)]) == "fits_at_0.90"


def test_non_text_when_no_scale_passes_and_a_figure_overflows():
    outcomes = [outcome(scale, False) for scale in (0.95, 0.90, 0.85, 0.80)]
    assert classify(outcomes, worst_kind="figure") == "non_text"
    assert classify(outcomes, worst_kind="container") == "non_text"


def test_unfixable_by_scale_when_text_overflow_survives_the_last_step():
    outcomes = [outcome(scale, False) for scale in (0.95, 0.90, 0.85, 0.80)]
    assert classify(outcomes, worst_kind="text") == "unfixable_by_scale"


def test_a_clean_slide_and_a_non_geometry_flag_stay_out_of_the_ladder():
    assert classify([], flagged=False) == "passes_at_1.0"
    assert classify([], flagged=True, in_ladder=False) == "non_geometry_flagged"


def test_no_scalable_declaration_is_not_attributed_to_the_floor():
    assert not buckets.is_floor_bound(outcome(0.90, True, scaled=0, floored=0))


# --------------------------------------------------------------------------
# worst geometry kind
# --------------------------------------------------------------------------


def test_worst_overflow_kind_picks_the_largest_ratio():
    issues = [
        {"category": "overflow", "magnitude": {"ratio_x": 1.05, "ratio_y": 1.0, "element_kind": "text"}},
        {"category": "overflow", "magnitude": {"ratio_x": 1.0, "ratio_y": 1.4, "element_kind": "container"}},
    ]
    assert buckets.worst_geometry_kind(issues) == "container"


def test_worst_kind_falls_back_to_off_canvas_then_clipping():
    off_canvas = [
        {"category": "off_canvas", "magnitude": {"overshoot_px_right": 30.0, "element_kind": "figure"}}
    ]
    assert buckets.worst_geometry_kind(off_canvas) == "figure"
    clipping = [
        {"category": "clipping", "magnitude": {"hidden_fraction": 0.4, "element_kind": "image"}}
    ]
    assert buckets.worst_geometry_kind(clipping) == "image"
    assert buckets.worst_geometry_kind([{"category": "contrast"}]) is None


# --------------------------------------------------------------------------
# 6.4 current/legacy job rule
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 5.3 summary arithmetic and the section 7 decision inputs
# --------------------------------------------------------------------------


def row(bucket, *, code_version="current", fill=0.7, kind="text", source="checkpoint_author"):
    return {
        "code_version": code_version,
        "source": source,
        "bucket": bucket,
        "worst_overflow_kind": kind,
        "fit_summary": {"content_bbox_fill": fill},
    }


def test_decision_inputs_are_shares_of_the_ladder_population():
    population = [
        "fits_at_0.95",
        "fits_at_0.90",
        "fits_at_0.85",
        "floor_bound",
        "non_text",
        "non_text",
        "unfixable_by_scale",
        "unfixable_by_scale",
        # Neither of these counts toward S or N.
        "passes_at_1.0",
        "non_geometry_flagged",
    ]
    result = buckets.decision_inputs(population)
    assert result["ladder_population"] == 8
    assert result["fits_by_scale"] == 3
    assert result["S"] == pytest.approx(3 / 8)
    assert result["non_text"] == 2
    assert result["N"] == pytest.approx(2 / 8)
    assert result["floor_bound"] == 1
    assert result["unfixable_by_scale"] == 2


def test_decision_inputs_on_an_empty_population_report_no_share():
    result = buckets.decision_inputs(["passes_at_1.0"])
    assert result["ladder_population"] == 0
    assert result["S"] is None and result["N"] is None


def test_summary_separates_legacy_and_measures_under_fill():
    rows = [
        row("fits_at_0.95"),
        row("non_text", kind="figure"),
        row("passes_at_1.0", fill=0.42),
        row("passes_at_1.0", fill=0.80),
        row("unfixable_by_scale", code_version="legacy"),
    ]
    summary = buckets.build_summary(rows)
    assert summary["slides"] == 5
    assert summary["by_code_version"] == {"current": 4, "legacy": 1}
    assert summary["buckets_legacy"] == {"unfixable_by_scale": 1}
    assert summary["overflow_kinds_current"] == {"figure": 1, "text": 1}
    # Legacy never reaches the decision inputs.
    assert summary["decision_inputs_current"]["ladder_population"] == 2
    assert summary["decision_inputs_current"]["S"] == pytest.approx(0.5)
    assert summary["decision_inputs_current"]["N"] == pytest.approx(0.5)
    under_fill = summary["under_fill"]
    assert under_fill["population"] == 2
    assert under_fill["below_0.55"] == 1
    assert under_fill["grow_step_indicated"] is True
    assert sum(item["count"] for item in under_fill["histogram"]) == 2


def test_under_fill_histogram_bins_are_a_tenth_wide():
    histogram = buckets.under_fill_histogram([0.0, 0.05, 0.55, 1.0])
    counts = {item["bin"]: item["count"] for item in histogram}
    assert counts["0.0-0.1"] == 2
    assert counts["0.5-0.6"] == 1
    # A full canvas belongs to the last bin, not to an eleventh one.
    assert counts["0.9-1.0"] == 1
    assert len(histogram) == 10


# --------------------------------------------------------------------------
# 5.1 external image references
# --------------------------------------------------------------------------


class _FakeCapacity:
    role = "PILLARS"
    title_max_chars = 58
    body_max_chars = 380
    max_visible_text_chars = 460
    max_images = 8
    max_qids = 40
    min_font_px = 15


class _FakePlan:
    slides = (_FakeCapacity(), _FakeCapacity())

    def at(self, index):
        return self.slides[index]


class _FakeProfile:
    profile_id = "ordinary"


# --------------------------------------------------------------------------
# orchestration with the render-and-gate call stubbed out
# --------------------------------------------------------------------------


def test_kind_label_names_the_category_when_no_magnitude_exists():
    overlap_only = {
        "worst_overflow_kind": None,
        "blocking_categories": ["overlap"],
    }
    assert buckets.kind_label(overlap_only) == "no_element_kind:overlap"
    assert buckets.kind_label({"worst_overflow_kind": "figure"}) == "figure"
    assert buckets.kind_label({"worst_overflow_kind": None}) == "none"


def test_blocking_categories_keeps_only_publication_blockers():
    issues = [
        {"category": "contrast"},
        {"category": "overlap"},
        {"category": "off_canvas"},
        {"category": "font"},
    ]
    assert buckets.blocking_categories(issues) == ["off_canvas", "overlap"]
