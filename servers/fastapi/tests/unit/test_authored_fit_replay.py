"""Pure-helper tests for the authored fit replay harness (M3).

No Chromium and no database: the bucket rules, the current/legacy job rule,
the summary arithmetic behind the M5 decision inputs, and the external-image
skip are all total functions of already-measured results, which is exactly why
the design puts the classification in its own module. The one orchestration
test replaces the render-and-gate call with a stub.

``docs/authored-fit-magnitude-measurement-design-20260904.md`` sections 5-7.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from scripts import authored_fit_replay as cli
from scripts import authored_fit_replay_buckets as buckets
from scripts import authored_fit_replay_gate as gate_lib
from scripts import authored_fit_replay_inputs as inputs_lib


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


def test_a_job_is_current_code_when_any_slide_carries_the_library_marker():
    from utils.authored_infographic.library import LIBRARY_MARKER

    quiet = "<html><body><h1>quiet page</h1></body></html>"
    figure = f"<html><head><style>{LIBRARY_MARKER}</style></head></html>"
    assert inputs_lib.is_current_code([quiet, figure]) is True
    assert inputs_lib.is_current_code([quiet, quiet]) is False
    assert inputs_lib.is_current_code([]) is False


def test_archetype_comes_from_the_library_root_class():
    assert inputs_lib.archetype_of('<div class="ig ig-roadmap n4">') == "roadmap"
    assert inputs_lib.archetype_of('<div class="ig-label">') is None


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


def test_external_image_references_are_detected_and_data_uris_are_not():
    html = (
        '<img src="/app_data/images/a.png">'
        '<img src="https://example.com/b.png">'
        '<img src="data:image/png;base64,AAAA">'
        '<link href="https://fonts.example/x.css" rel="stylesheet">'
    )
    refs = inputs_lib.external_image_refs(html)
    assert refs == ("/app_data/images/a.png", "https://example.com/b.png")
    assert inputs_lib.external_image_refs("<img src='data:image/png;base64,A'>") == ()


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


def _context():
    return inputs_lib.JobContext(
        job_id=uuid.UUID("8a138084-abae-4ee2-ab3c-73686a8f2313"),
        presentation_id=uuid.UUID("513d0056-ae08-4b1b-abbb-822060381ebe"),
        state="failed",
        created_at=None,
        profile=_FakeProfile(),
        capacity_plan=_FakePlan(),
        reference_mode="style_reference",
        roles=("COVER", "PILLARS"),
        strict_structural_off_canvas=False,
        code_version="current",
    )


def test_collect_slides_skips_a_slide_with_an_external_image_reference(monkeypatch, tmp_path):
    context = _context()
    clean = inputs_lib.SlideInput(
        job=context,
        slide_index=0,
        role="COVER",
        source=inputs_lib.CHECKPOINT_AUTHOR,
        html="<html><img src='data:image/png;base64,A'></html>",
        archetype=None,
    )
    dirty = inputs_lib.SlideInput(
        job=context,
        slide_index=1,
        role="PILLARS",
        source=inputs_lib.CHECKPOINT_AUTHOR,
        html='<html><img src="/app_data/images/x.png"></html>',
        archetype=None,
    )

    async def fake_select_jobs(_session, **_kwargs):
        return [object()]

    async def fake_build_context(_session, _job):
        return context

    async def fake_checkpoint_slides(_session, _context):
        return [clean, dirty]

    monkeypatch.setattr(inputs_lib, "select_jobs", fake_select_jobs)
    monkeypatch.setattr(inputs_lib, "build_job_context", fake_build_context)
    monkeypatch.setattr(inputs_lib, "load_checkpoint_slides", fake_checkpoint_slides)
    monkeypatch.setattr(inputs_lib, "is_current_code", lambda _htmls: True)

    state = gate_lib.ReplayState(
        out_dir=tmp_path, png_dir=tmp_path / "png", scales=(0.95,)
    )
    state.png_dir.mkdir(parents=True, exist_ok=True)
    args = cli.parse_args(
        [
            "--database-url", "sqlite+aiosqlite:///x.db",
            "--app-data", str(tmp_path),
            "--job-id", "8a138084-abae-4ee2-ab3c-73686a8f2313",
            "--out", str(tmp_path),
        ]
    )
    slides = asyncio.run(cli.collect_slides(object(), args, state))

    assert [item.slide_index for item in slides] == [0]
    assert len(state.skipped) == 1
    assert state.skipped[0]["reason"] == "external_image_reference"
    assert state.skipped[0]["detail"] == "/app_data/images/x.png"


# --------------------------------------------------------------------------
# orchestration with the render-and-gate call stubbed out
# --------------------------------------------------------------------------


def _gate_result(*, issues, violations=(), blocking=0):
    return gate_lib.GateResult(
        violations=tuple(violations),
        issues=list(issues),
        fit_summary={"content_bbox_fill": 0.6, "worst_overflow_ratio": 1.2},
        telemetry_status="available",
        blocking_issue_count=blocking,
        render_millis=1,
        png=b"png-bytes",
    )


def test_replay_slide_stops_at_the_first_passing_scale(monkeypatch, tmp_path):
    context = _context()
    slide = inputs_lib.SlideInput(
        job=context,
        slide_index=1,
        role="PILLARS",
        source=inputs_lib.CHECKPOINT_AUTHOR,
        html="<html><body><p style='font-size:20px'>x</p></body></html>",
        archetype=None,
    )
    overflow = {
        "category": "overflow",
        "magnitude": {"ratio_x": 1.0, "ratio_y": 1.3, "element_kind": "text"},
    }
    calls: list[float] = []

    async def fake_evaluate(html, _context, _index):
        # The 1.0 render misses; the first ladder rung already fits.
        if not calls:
            calls.append(1.0)
            return _gate_result(issues=[overflow], blocking=1)
        calls.append(len(calls))
        return _gate_result(issues=[])

    monkeypatch.setattr(gate_lib, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        inputs_lib, "prepare_for_render", lambda html, **_kwargs: html
    )

    state = gate_lib.ReplayState(
        out_dir=tmp_path, png_dir=tmp_path / "png", scales=(0.95, 0.90)
    )
    state.png_dir.mkdir(parents=True, exist_ok=True)
    row_out = asyncio.run(gate_lib.replay_slide(state, slide))

    assert row_out["bucket"] == "fits_at_0.95"
    assert row_out["first_passing_scale"] == 0.95
    assert len(row_out["scale_candidates"]) == 1
    assert row_out["worst_overflow_kind"] == "text"
    assert row_out["geometry_blocked"] is True
    assert sorted(path.name for path in (tmp_path / "png").iterdir()) == [
        "8a138084-01-0.95.png",
        "8a138084-01-1.00.png",
    ]
    # The row must be JSON-serialisable: it becomes one line of slides.jsonl.
    assert json.loads(json.dumps(row_out))["slide_index"] == 1


def test_replay_slide_leaves_a_non_geometry_flag_out_of_the_ladder(monkeypatch, tmp_path):
    context = _context()
    slide = inputs_lib.SlideInput(
        job=context,
        slide_index=0,
        role="COVER",
        source=inputs_lib.CHECKPOINT_AUTHOR,
        html="<html></html>",
        archetype=None,
    )
    renders: list[str] = []

    async def fake_evaluate(html, _context, _index):
        renders.append(html)
        return _gate_result(issues=[{"category": "contrast"}], blocking=0)

    monkeypatch.setattr(gate_lib, "evaluate", fake_evaluate)
    monkeypatch.setattr(
        inputs_lib, "prepare_for_render", lambda html, **_kwargs: html
    )
    state = gate_lib.ReplayState(
        out_dir=tmp_path, png_dir=tmp_path / "png", scales=(0.95, 0.90)
    )
    state.png_dir.mkdir(parents=True, exist_ok=True)
    row_out = asyncio.run(gate_lib.replay_slide(state, slide))

    assert row_out["flagged"] is True
    assert row_out["bucket"] == "non_geometry_flagged"
    assert row_out["scale_candidates"] == []
    assert len(renders) == 1


def test_parse_scales_rejects_a_value_outside_the_unit_interval():
    assert cli.parse_scales("0.95,0.90") == (0.95, 0.90)
    with pytest.raises(inputs_lib.ReplayInputError):
        cli.parse_scales("1.5")
    with pytest.raises(inputs_lib.ReplayInputError):
        cli.parse_scales("")


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


@pytest.mark.parametrize(
    "error_code, blocked, expected",
    [
        ("authored_composition_safety_gate_failed", [15], "consistent"),
        ("authored_composition_safety_gate_failed", [], "inconsistent"),
        ("http_error_500", [], "consistent"),
        ("public_deadline_exceeded", [7], "not comparable (deck failed before a gate verdict)"),
    ],
)
def test_circumstantial_parity_labels_a_deck_level_cross_check(error_code, blocked, expected):
    from scripts.authored_fit_replay_parity import _circumstantial_row

    context = _context()
    context.last_error_code = error_code
    rows = [
        {
            "job_id": str(context.job_id),
            "slide_index": index,
            "geometry_blocked": True,
        }
        for index in blocked
    ]
    assert _circumstantial_row(context, rows)["agreement"] == expected
