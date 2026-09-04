"""Pure scoring for the Composer verbatim-restyle promotion gate (A1-A7).

Everything here works on plain dataclasses built from ``report.json`` (offline
mode) or ``composer-receipt/v1`` plus ``workspace_operations`` rows (live
mode), so it is unit-testable without Chromium, a database or a live
backend. See
``docs/generation-quality-root-cause-and-composer-architecture-20260904.md``
section 7a for the gate this implements.

Criteria A1, A6 and A7 require evidence this module cannot itself produce
(A1 needs >= 20 live runs; A6/A7 need a human or a separate live-stack run):
they resolve to ``not_evaluated`` unless matching evidence is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

PASS, FAIL, NOT_EVALUATED = "pass", "fail", "not_evaluated"

A1_MIN_RUNS = 20
A1_MIN_SUCCESS_RATE = 0.95
A1_MIN_DISTINCT_DECKS = 5
A1_MIN_UNSEEN_DECKS = 3
A5_MAX_PAGES = 50
A5_MAX_SECONDS = 300.0


@dataclass(frozen=True)
class SlideEvidence:
    """One fitted slide, with residual-overlap accounting already applied.

    ``blocking_issues`` is the final (non-residual) list -- empty for a
    rung-5 slide whose only issues are source-overlap residue accepted by
    ``services.composer.receipt.is_accepted_residual``. ``residual_issues``
    carries that accepted residue separately, for A4.
    """

    index: int
    rung: int | None
    blocking_issues: tuple[str, ...] = ()
    residual_issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunEvidence:
    """One Composer run (one offline deck, or one live operation)."""

    label: str
    source: str  # "offline" | "live"
    evidence_path: str
    deck_key: str | None = None
    unseen: bool = False
    page_count: int | None = None
    wall_ms: float | None = None
    provider_calls: int | None = None
    op_status: str | None = None  # "published" | "failed" | "timeout" | None
    readback_passed: bool | None = None
    checked_block_count: int | None = None
    missing_block_count: int | None = None
    slides: tuple[SlideEvidence, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CriterionResult:
    id: str
    description: str
    threshold: str
    measured: str
    verdict: str
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "threshold": self.threshold,
            "measured": self.measured,
            "verdict": self.verdict,
            "evidence": list(self.evidence),
            "notes": list(self.notes),
        }


def _not_evaluated(criterion_id: str, description: str, threshold: str, reason: str) -> CriterionResult:
    return CriterionResult(criterion_id, description, threshold, measured="n/a", verdict=NOT_EVALUATED, notes=[reason])


# --- A1: operations reaching published (live only) ----------------------------


def score_a1(live_runs: Sequence[RunEvidence]) -> CriterionResult:
    description = "Operations reaching published"
    threshold = (
        f">= {A1_MIN_SUCCESS_RATE:.0%} over {A1_MIN_RUNS} consecutive live runs "
        f"(>= {A1_MIN_DISTINCT_DECKS} distinct decks, >= {A1_MIN_UNSEEN_DECKS} previously unseen)"
    )
    if not live_runs:
        return _not_evaluated("A1", description, threshold, "no live runs supplied; run --mode live to evaluate")
    total = len(live_runs)
    published = sum(1 for run in live_runs if run.op_status == "published")
    rate = published / total
    distinct = {run.deck_key for run in live_runs if run.deck_key}
    unseen = {run.deck_key for run in live_runs if run.deck_key and run.unseen}
    sample_ok = total >= A1_MIN_RUNS and len(distinct) >= A1_MIN_DISTINCT_DECKS and len(unseen) >= A1_MIN_UNSEEN_DECKS
    passed = sample_ok and rate >= A1_MIN_SUCCESS_RATE
    measured = f"{published}/{total} published ({rate:.1%}), {len(distinct)} distinct decks, {len(unseen)} unseen"
    notes = [] if sample_ok else [
        "sample below the required minimum runs/decks/unseen; verdict fails on sample size, not only success rate"
    ]
    return CriterionResult(
        "A1", description, threshold, measured, PASS if passed else FAIL,
        evidence=[run.evidence_path for run in live_runs], notes=notes,
    )


# --- A2: source blocks present in readback -------------------------------------


def score_a2(runs: Sequence[RunEvidence]) -> CriterionResult:
    description = "Source blocks present in readback"
    threshold = "100%, every run"
    evaluated = [run for run in runs if run.readback_passed is not None]
    if not evaluated:
        return _not_evaluated("A2", description, threshold, "no run produced readback evidence")
    per_run = []
    all_complete = True
    for run in evaluated:
        total = run.checked_block_count
        missing = run.missing_block_count or 0
        if total is None:
            per_run.append(f"{run.label}: readback error, no block count")
            all_complete = False
            continue
        present = total - missing
        per_run.append(f"{run.label}: {present}/{total}")
        if total <= 0 or missing > 0 or not run.readback_passed:
            all_complete = False
    return CriterionResult(
        "A2", description, threshold, measured=", ".join(per_run), verdict=PASS if all_complete else FAIL,
        evidence=[run.evidence_path for run in evaluated],
    )


# --- A3: blocking DOM defects at rungs 0-4 -------------------------------------


def score_a3(runs: Sequence[RunEvidence]) -> CriterionResult:
    description = "Blocking DOM defects at rungs 0-4"
    threshold = "0"
    considered = [(run, slide) for run in runs for slide in run.slides if slide.rung is not None and slide.rung <= 4]
    if not considered:
        return _not_evaluated("A3", description, threshold, "no run produced fitted slides at rungs 0-4")
    blocking = [f"{run.label}#{slide.index}" for run, slide in considered if slide.blocking_issues]
    measured = f"{len(blocking)} of {len(considered)} rung-0..4 slides carry a blocking DOM defect"
    notes = [f"blocking: {', '.join(blocking)}"] if blocking else []
    return CriterionResult(
        "A3", description, threshold, measured, verdict=PASS if not blocking else FAIL,
        evidence=sorted({run.evidence_path for run, _ in considered}), notes=notes,
    )


# --- A4: rung-5 slides ---------------------------------------------------------


def score_a4(runs: Sequence[RunEvidence]) -> CriterionResult:
    """Rung-5 slides: never auto-pass on the DOM issue category alone.

    ``is_accepted_residual`` (and the receipt's ``blockingIssues`` /
    ``residualIssues`` split derived from it) only tells us every remaining
    DOM issue at rung 5 is *tagged* category ``overlap`` -- that tag does not
    by itself prove the overlap is the source page's own, as opposed to a
    renderer defect that happens to manifest as an overlap (see the
    2026-09-04 ``render_geometry.py`` MIN_FONT_PX / ``_GROWABLE_KINDS`` fix,
    which turned out to cause exactly that on 5 of 6 rung-5 slides in
    deck-43, 0 of them genuine source overlap). A1-A3/A5 are safe to score
    automatically; A4 is not -- any rung-5 slide carrying an issue (blocking
    or accepted-residual) fails here and is named for a human to apply the
    "does the source page itself overlap here" test from decision 7a. Only a
    rung-5 slide with zero DOM issues at all needs no eyeball.
    """

    description = "Rung 5 slides"
    threshold = "residual overlap only where the source page itself overlaps; each such slide named in the receipt"
    rung5 = [(run, slide) for run in runs for slide in run.slides if slide.rung == 5]
    if not rung5:
        return _not_evaluated("A4", description, threshold, "no run reached rung 5")
    flagged = [
        f"{run.label}#{slide.index}" for run, slide in rung5 if slide.blocking_issues or slide.residual_issues
    ]
    named = [f"{run.label}#{slide.index}" for run, slide in rung5]
    measured = f"{len(rung5)} rung-5 slides, {len(flagged)} carry a DOM issue needing a human overlap eyeball"
    notes = [
        "no auto-pass on DOM issue category: presence of any blocking or accepted-residual issue at rung 5 "
        "fails here until a human confirms the source page itself overlaps there (decision 7a)",
        f"named rung-5 slides: {', '.join(named)}",
    ]
    if flagged:
        notes.append(f"needs eyeball: {', '.join(flagged)}")
    return CriterionResult(
        "A4", description, threshold, measured, verdict=PASS if not flagged else FAIL,
        evidence=sorted({run.evidence_path for run, _ in rung5}), notes=notes,
    )


# --- A5: wall time and provider calls ------------------------------------------


def score_a5(runs: Sequence[RunEvidence]) -> CriterionResult:
    description = "Wall time and provider calls"
    threshold = f"<= {A5_MAX_SECONDS / 60:.0f} min for <= {A5_MAX_PAGES} pages, 0 provider calls"
    applicable = [run for run in runs if (run.page_count or 0) <= A5_MAX_PAGES]
    if not applicable:
        return _not_evaluated("A5", description, threshold, "no run with <= 50 pages was evaluated")
    lines = []
    passed = True
    for run in applicable:
        seconds = None if run.wall_ms is None else run.wall_ms / 1000.0
        calls = run.provider_calls
        ok = seconds is not None and seconds <= A5_MAX_SECONDS and calls == 0
        passed = passed and ok
        seconds_text = "n/a" if seconds is None else f"{seconds:.1f}s"
        calls_text = "unmeasured" if calls is None else str(calls)
        lines.append(f"{run.label}: {seconds_text} / {calls_text} provider calls")
        if calls is None:
            passed = False
    return CriterionResult(
        "A5", description, threshold, measured="; ".join(lines), verdict=PASS if passed else FAIL,
        evidence=[run.evidence_path for run in applicable],
    )


# --- A6 / A7: out of this harness's reach --------------------------------------


def score_a6() -> CriterionResult:
    return _not_evaluated(
        "A6", "Hybrid PPTX export",
        "round-trips, opens editable in PowerPoint, native shapes not a raster page",
        "requires opening an exported PPTX in PowerPoint; not exercised by this harness. "
        "See docs/generation-quality-root-cause-and-composer-architecture-20260904.md section 7a.",
    )


def score_a7() -> CriterionResult:
    return _not_evaluated(
        "A7", "Rollback",
        "COMPOSER_RESTYLE_ENABLED=false restores the authored path, no data migration",
        "requires flipping the flag against a running stack and confirming the authored path resumes; "
        "not exercised by this harness. See "
        "docs/generation-quality-root-cause-and-composer-architecture-20260904.md section 7a.",
    )


# --- Aggregate report ------------------------------------------------------------


def build_gate_report(
    *, mode: str, offline_runs: Sequence[RunEvidence], live_runs: Sequence[RunEvidence], notes: Sequence[str] = ()
) -> dict[str, Any]:
    all_runs = list(offline_runs) + list(live_runs)
    criteria = {
        "A1": score_a1(live_runs),
        "A2": score_a2(all_runs),
        "A3": score_a3(all_runs),
        "A4": score_a4(all_runs),
        "A5": score_a5(all_runs),
        "A6": score_a6(),
        "A7": score_a7(),
    }
    # A4 only applies when a run actually reached rung 5 (not every deck does); it is
    # scored and can still fail the gate via ``any_fail`` below, but its own
    # ``not_evaluated`` (no rung-5 slides seen) does not block "complete" the way an
    # unmeasured A1/A2/A3/A5 does.
    required_ids = ("A1", "A2", "A3", "A5")
    required = [criteria[criterion_id] for criterion_id in required_ids]
    any_fail = any(result.verdict == FAIL for result in criteria.values())
    automated_complete = all(result.verdict != NOT_EVALUATED for result in required)
    automated_pass = all(result.verdict == PASS for result in required)
    if any_fail:
        overall = "blocked"
    elif automated_complete and automated_pass:
        overall = "automated_pass_manual_pending"
    else:
        overall = "incomplete"
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": mode,
        "overall": overall,
        "notes": list(notes),
        "criteria": {criterion_id: result.as_dict() for criterion_id, result in criteria.items()},
        "runs": {
            "offline": [run.label for run in offline_runs],
            "live": [run.label for run in live_runs],
        },
    }


def format_table(report: dict[str, Any]) -> str:
    """Short human-readable table for stdout."""

    lines = [f"Composer verbatim-restyle promotion gate -- mode={report['mode']} overall={report['overall']}", ""]
    header = f"{'ID':<3} {'verdict':<14} {'measured':<60} threshold"
    lines.append(header)
    lines.append("-" * len(header))
    for criterion_id in ("A1", "A2", "A3", "A4", "A5", "A6", "A7"):
        result = report["criteria"][criterion_id]
        measured = result["measured"]
        if len(measured) > 58:
            measured = measured[:55] + "..."
        lines.append(f"{criterion_id:<3} {result['verdict']:<14} {measured:<60} {result['threshold']}")
    return "\n".join(lines)


__all__ = [
    "A1_MIN_DISTINCT_DECKS",
    "A1_MIN_RUNS",
    "A1_MIN_SUCCESS_RATE",
    "A1_MIN_UNSEEN_DECKS",
    "A5_MAX_PAGES",
    "A5_MAX_SECONDS",
    "FAIL",
    "NOT_EVALUATED",
    "PASS",
    "CriterionResult",
    "RunEvidence",
    "SlideEvidence",
    "build_gate_report",
    "format_table",
    "score_a1",
    "score_a2",
    "score_a3",
    "score_a4",
    "score_a5",
    "score_a6",
    "score_a7",
]
