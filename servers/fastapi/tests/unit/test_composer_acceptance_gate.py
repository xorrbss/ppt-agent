"""Scoring tests for the Composer promotion-gate harness (A1-A7).

Pure fixtures only: no Chromium, no database, no live backend. Exercises
``scripts/composer_acceptance_gate_score.py`` -- thresholds, verdict
aggregation and ``not_evaluated`` handling -- plus the offline
report.json -> RunEvidence transform in
``scripts/composer_acceptance_gate.py`` (``_slides_from_report``,
``collect_offline_run``).
"""

from __future__ import annotations

from pathlib import Path

from scripts.composer_acceptance_gate import _slides_from_report, collect_offline_run
from scripts.composer_acceptance_gate_score import (
    A1_MIN_DISTINCT_DECKS,
    A1_MIN_RUNS,
    A1_MIN_UNSEEN_DECKS,
    FAIL,
    NOT_EVALUATED,
    PASS,
    RunEvidence,
    SlideEvidence,
    build_gate_report,
    format_table,
    score_a1,
    score_a2,
    score_a3,
    score_a4,
    score_a5,
    score_a6,
    score_a7,
)


def _run(**overrides) -> RunEvidence:
    defaults = dict(
        label="deck-35", source="offline", evidence_path="artifacts/deck-35/report.json",
        deck_key="deck-35", page_count=35, wall_ms=48_000.0, provider_calls=0,
        readback_passed=True, checked_block_count=289, missing_block_count=0,
        slides=(
            SlideEvidence(index=0, rung=0),
            SlideEvidence(index=1, rung=1),
        ),
    )
    defaults.update(overrides)
    return RunEvidence(**defaults)


# --- A1 --------------------------------------------------------------------------


def test_a1_not_evaluated_without_live_runs():
    result = score_a1(())
    assert result.verdict == NOT_EVALUATED
    assert result.measured == "n/a"


def test_a1_fails_on_undersized_sample_even_at_100_percent():
    runs = [_run(label=f"r{i}", source="live", deck_key=f"deck-{i % 3}", op_status="published") for i in range(5)]
    result = score_a1(runs)
    assert result.verdict == FAIL
    assert "sample" in result.notes[0]


def test_a1_passes_at_threshold():
    decks = [f"deck-{i}" for i in range(A1_MIN_DISTINCT_DECKS)]
    unseen = set(decks[:A1_MIN_UNSEEN_DECKS])
    runs = [
        _run(label=f"r{i}", source="live", deck_key=decks[i % len(decks)], unseen=decks[i % len(decks)] in unseen,
             op_status="published")
        for i in range(A1_MIN_RUNS)
    ]
    result = score_a1(runs)
    assert result.verdict == PASS
    assert "20/20" in result.measured


def test_a1_fails_below_success_rate_even_with_full_sample():
    decks = [f"deck-{i}" for i in range(A1_MIN_DISTINCT_DECKS)]
    runs = [
        _run(label=f"r{i}", source="live", deck_key=decks[i % len(decks)], unseen=i < A1_MIN_UNSEEN_DECKS,
             op_status="published" if i > 5 else "failed")  # 14/20 published, 70% < 95%
        for i in range(A1_MIN_RUNS)
    ]
    result = score_a1(runs)
    assert result.verdict == FAIL


# --- A2 --------------------------------------------------------------------------


def test_a2_not_evaluated_without_readback():
    result = score_a2([_run(readback_passed=None, checked_block_count=None)])
    assert result.verdict == NOT_EVALUATED


def test_a2_passes_at_100_percent_every_run():
    runs = [_run(label="deck-35", checked_block_count=289), _run(label="deck-43", checked_block_count=965)]
    result = score_a2(runs)
    assert result.verdict == PASS
    assert "deck-35: 289/289" in result.measured
    assert "deck-43: 965/965" in result.measured


def test_a2_fails_if_any_run_has_missing_blocks():
    runs = [_run(label="deck-35", checked_block_count=289, missing_block_count=0),
            _run(label="deck-43", checked_block_count=965, missing_block_count=3)]
    result = score_a2(runs)
    assert result.verdict == FAIL


def test_a2_fails_on_readback_error_with_no_block_count():
    runs = [_run(readback_passed=False, checked_block_count=None)]
    result = score_a2(runs)
    assert result.verdict == FAIL
    assert "readback error" in result.measured


# --- A3 --------------------------------------------------------------------------


def test_a3_not_evaluated_without_fitted_slides():
    result = score_a3([_run(slides=())])
    assert result.verdict == NOT_EVALUATED


def test_a3_passes_with_zero_blocking_defects_at_rungs_0_to_4():
    runs = [_run(slides=(SlideEvidence(index=0, rung=0), SlideEvidence(index=1, rung=4)))]
    result = score_a3(runs)
    assert result.verdict == PASS


def test_a3_fails_on_a_blocking_defect_at_rung_2():
    runs = [_run(slides=(SlideEvidence(index=0, rung=2, blocking_issues=("clipping: title",)),))]
    result = score_a3(runs)
    assert result.verdict == FAIL
    assert "deck-35#0" in result.notes[0]


def test_a3_ignores_rung_5_slides():
    """Rung 5's own residue is A4's concern, never A3's -- even if it slipped through unfiltered."""

    runs = [_run(slides=(SlideEvidence(index=0, rung=5, blocking_issues=("overlap: figure",)),))]
    result = score_a3(runs)
    assert result.verdict == NOT_EVALUATED  # no rung-0..4 slide observed at all


# --- A4 --------------------------------------------------------------------------


def test_a4_not_evaluated_without_rung_5_slides():
    result = score_a4([_run(slides=(SlideEvidence(index=0, rung=0),))])
    assert result.verdict == NOT_EVALUATED


def test_a4_passes_only_when_rung_5_slides_carry_no_dom_issue_at_all():
    runs = [_run(slides=(SlideEvidence(index=4, rung=5),))]
    result = score_a4(runs)
    assert result.verdict == PASS
    assert "deck-35#4" in result.notes[1]


def test_a4_fails_on_a_non_overlap_blocking_issue_at_rung_5():
    runs = [_run(slides=(SlideEvidence(index=4, rung=5, blocking_issues=("off_canvas: caption",)),))]
    result = score_a4(runs)
    assert result.verdict == FAIL


def test_a4_never_auto_passes_on_the_overlap_category_alone():
    """The category-only heuristic is not trustworthy: an 'overlap'-tagged residual issue can be a
    renderer defect rather than a genuine source-page overlap, so it must still fail here and be
    named for a human eyeball, not silently accepted."""

    runs = [_run(slides=(SlideEvidence(index=4, rung=5, residual_issues=("overlap: figure",)),))]
    result = score_a4(runs)
    assert result.verdict == FAIL
    assert "deck-35#4" in result.notes[-1]


# --- A5 --------------------------------------------------------------------------


def test_a5_not_evaluated_when_every_run_exceeds_the_page_cap():
    result = score_a5([_run(page_count=51)])
    assert result.verdict == NOT_EVALUATED


def test_a5_passes_under_five_minutes_with_zero_provider_calls():
    result = score_a5([_run(wall_ms=48_000.0, provider_calls=0)])
    assert result.verdict == PASS


def test_a5_fails_over_five_minutes():
    result = score_a5([_run(wall_ms=301_000.0, provider_calls=0)])
    assert result.verdict == FAIL


def test_a5_fails_on_a_nonzero_provider_call():
    result = score_a5([_run(wall_ms=1_000.0, provider_calls=1)])
    assert result.verdict == FAIL


def test_a5_fails_when_provider_calls_unmeasured():
    """Requirement: measured, not assumed -- an unknown count is never a pass."""

    result = score_a5([_run(wall_ms=1_000.0, provider_calls=None)])
    assert result.verdict == FAIL
    assert "unmeasured" in result.measured


# --- A6 / A7: always not_evaluated, never a default pass --------------------------


def test_a6_and_a7_are_always_not_evaluated():
    assert score_a6().verdict == NOT_EVALUATED
    assert score_a7().verdict == NOT_EVALUATED
    assert "docs/" in score_a6().notes[0]
    assert "docs/" in score_a7().notes[0]


# --- Aggregate report --------------------------------------------------------------


def test_overall_blocked_when_any_automated_criterion_fails():
    offline_runs = [_run(slides=(SlideEvidence(index=0, rung=1, blocking_issues=("clipping: x",)),))]
    report = build_gate_report(mode="offline", offline_runs=offline_runs, live_runs=())
    assert report["overall"] == "blocked"
    assert report["criteria"]["A3"]["verdict"] == FAIL
    assert report["criteria"]["A6"]["verdict"] == NOT_EVALUATED


def test_overall_incomplete_when_a1_has_no_live_evidence():
    offline_runs = [_run()]
    report = build_gate_report(mode="offline", offline_runs=offline_runs, live_runs=())
    assert report["criteria"]["A1"]["verdict"] == NOT_EVALUATED
    assert report["overall"] == "incomplete"


def test_overall_automated_pass_manual_pending_when_a1_a2_a3_a5_all_pass():
    """A4 (rung 5) is best-effort: no run reaching rung 5 is not a blocker for A1-A5."""

    decks = [f"deck-{i}" for i in range(A1_MIN_DISTINCT_DECKS)]
    live_runs = [
        _run(label=f"r{i}", source="live", deck_key=decks[i % len(decks)], unseen=i < A1_MIN_UNSEEN_DECKS,
             op_status="published")
        for i in range(A1_MIN_RUNS)
    ]
    report = build_gate_report(mode="live", offline_runs=(), live_runs=live_runs)
    assert report["criteria"]["A4"]["verdict"] == NOT_EVALUATED
    assert report["overall"] == "automated_pass_manual_pending"
    assert report["criteria"]["A1"]["verdict"] == PASS
    assert report["criteria"]["A6"]["verdict"] == NOT_EVALUATED
    assert report["criteria"]["A7"]["verdict"] == NOT_EVALUATED


def test_overall_blocked_when_only_a4_fails():
    """A4 is not required to be evaluated, but a measured A4 failure still blocks."""

    decks = [f"deck-{i}" for i in range(A1_MIN_DISTINCT_DECKS)]
    live_runs = [
        _run(label=f"r{i}", source="live", deck_key=decks[i % len(decks)], unseen=i < A1_MIN_UNSEEN_DECKS,
             op_status="published",
             slides=(SlideEvidence(index=0, rung=5, blocking_issues=("off_canvas: caption",)),))
        for i in range(A1_MIN_RUNS)
    ]
    report = build_gate_report(mode="live", offline_runs=(), live_runs=live_runs)
    assert report["criteria"]["A4"]["verdict"] == FAIL
    assert report["overall"] == "blocked"


def test_format_table_lists_all_seven_criteria():
    report = build_gate_report(mode="offline", offline_runs=[_run()], live_runs=())
    table = format_table(report)
    for criterion_id in ("A1", "A2", "A3", "A4", "A5", "A6", "A7"):
        assert criterion_id in table


# --- Offline report.json -> RunEvidence transform ----------------------------------


def _offline_report(*, rung5_blocking: tuple[str, ...] = ("overlap: figure",)) -> dict:
    return {
        "source": {"manifestId": "scm_test", "manifestDigest": "sha256:abc", "pageCount": 2},
        "timings": {"totalMs": 48_123.4},
        "providerCalls": 0,
        "readback": {
            "deck": {
                "passed": True, "checkedSlideCount": 2, "checkedBlockCount": 10,
                "missingBlockIds": [], "orderViolations": 0,
            },
        },
        "slides": [
            {"slideIndex": 0, "rung": 0, "blockingIssues": []},
            {"slideIndex": 1, "rung": 5, "blockingIssues": list(rung5_blocking)},
        ],
    }


def test_slides_from_report_reclassifies_rung_5_overlap_as_residual():
    slides = _slides_from_report(_offline_report(rung5_blocking=("overlap: figure",)))
    rung5 = next(s for s in slides if s.rung == 5)
    assert rung5.blocking_issues == ()
    assert rung5.residual_issues == ("overlap: figure",)


def test_slides_from_report_keeps_a_non_overlap_rung_5_issue_as_blocking():
    slides = _slides_from_report(_offline_report(rung5_blocking=("off_canvas: caption",)))
    rung5 = next(s for s in slides if s.rung == 5)
    assert rung5.blocking_issues == ("off_canvas: caption",)
    assert rung5.residual_issues == ()


def test_collect_offline_run_reads_block_counts_and_page_count():
    run = collect_offline_run(_offline_report(), label="deck-test", report_path=Path("r.json"))
    assert run.page_count == 2
    assert run.checked_block_count == 10
    assert run.missing_block_count == 0
    assert run.wall_ms == 48_123.4
    assert run.provider_calls == 0
    assert run.op_status is None  # offline mode never publishes


def test_collect_offline_run_handles_a_missing_readback_section():
    report = {"source": {"pageCount": 1}, "timings": {"totalMs": 100.0}, "providerCalls": 0, "slides": []}
    run = collect_offline_run(report, label="dry-run", report_path=Path("r.json"))
    assert run.readback_passed is None
    assert run.checked_block_count is None
    assert "no readback section" in run.notes[0]


# --- deck-43 known-baseline regression (2026-09-04 render_geometry.py defects) -----


def _deck43_known_baseline_report() -> dict:
    """Shape a report.json matching the known deck-43 baseline: 43/43 fitted,
    rungs 0:2 1:10 2:12 3:13 5:6, 965/965 blocks, 0 provider calls, and 5 of the
    6 rung-5 slides carrying a renderer-introduced ``overlap``-tagged DOM issue
    (the ``render_geometry.py`` MIN_FONT_PX / ``_GROWABLE_KINDS`` defects) that
    ``is_accepted_residual`` tags as an accepted residual even though a human
    confirmed 0 of them are the source page's own overlap.
    """

    rung_by_index = (
        [0] * 2 + [1] * 10 + [2] * 12 + [3] * 13 + [5] * 6
    )
    assert len(rung_by_index) == 43
    slides = []
    rung5_seen = 0
    for index, rung in enumerate(rung_by_index):
        blocking_issues: list[str] = []
        if rung == 5:
            rung5_seen += 1
            if rung5_seen <= 5:  # 5 of 6 rung-5 slides carry the renderer defect
                blocking_issues = ["overlap: figure/caption"]
        slides.append({"slideIndex": index, "rung": rung, "blockingIssues": blocking_issues})
    return {
        "source": {"manifestId": "scm_deck43", "manifestDigest": "sha256:32b098", "pageCount": 43},
        "timings": {"totalMs": 223_000.0},
        "providerCalls": 0,
        "readback": {
            "deck": {
                "passed": True, "checkedSlideCount": 43, "checkedBlockCount": 965,
                "missingBlockIds": [], "orderViolations": 0,
            },
        },
        "slides": slides,
    }


def test_deck43_known_baseline_a2_a3_pass_a4_fails_with_five_named_slides():
    report = _deck43_known_baseline_report()
    run = collect_offline_run(report, label="deck-43", report_path=Path("deck-43/report.json"))
    assert run.page_count == 43
    assert run.checked_block_count == 965 and run.missing_block_count == 0

    a2 = score_a2([run])
    assert a2.verdict == PASS
    assert "965/965" in a2.measured

    # Rungs 0-4 carry no blocking DOM defect in this baseline: A3 passes even
    # though the deck as a whole is not clean (rung 5's issues are A4's, not A3's).
    a3 = score_a3([run])
    assert a3.verdict == PASS

    # A4 must fail: 5 of 6 rung-5 slides are flagged, and none of the
    # overlap-category tagging on them may be trusted as a source-page overlap
    # without a human eyeball (see score_a4's docstring).
    a4 = score_a4([run])
    assert a4.verdict == FAIL
    flagged_note = next(note for note in a4.notes if note.startswith("needs eyeball"))
    assert flagged_note.count("deck-43#") == 5

    a5 = score_a5([run])
    assert a5.verdict == PASS  # 223s <= 300s, 0 provider calls

    report_doc = build_gate_report(mode="offline", offline_runs=[run], live_runs=())
    assert report_doc["overall"] == "blocked"
    assert report_doc["criteria"]["A4"]["verdict"] == FAIL
