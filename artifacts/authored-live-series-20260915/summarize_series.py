#!/usr/bin/env python3
"""Stage table for the authored verbatim placeholder-authoring live series.

Design: docs/authored-verbatim-placeholder-authoring-design-20260911.md section 4
(experiments (a) shadow parity and (c) fresh identical runs). Procedure and
knob values: this directory's RUNBOOK.md, especially section 6a, which
documents a real gap found while building this: `repairLoop.modelCalls/admitted`
and `visionReview.slidesReviewed` are not durably persisted anywhere offline in
this stack's local-development receipt profile -- only `repairLoop.*` has a
worker-log line to fall back to (stage-level, not per-run); `visionReview.*`
has none and is always reported "unavailable". This script does not paper over
that gap with an invented number.

Metric extraction lives in series_metrics.py (file-size split, see its
docstring); this file is the CLI and markdown-rendering facade over it.

Two modes, selected by which repeatable input flag is used:

  --run-dir DIR [--run-dir DIR ...]        dry-run: an ad-hoc single stage made
                                            of exactly these run directories.
                                            No DB, no worker log, no live stack.
                                            Every field that can only come from
                                            a live stack is reported as
                                            "unavailable (dry-run)".
  --stage-dir DIR [--stage-dir DIR ...]    live: each DIR is one stage,
                                            produced by series.ps1 (its
                                            run-1..run-N subdirectories, its own
                                            series-summary.json). Pair with
                                            --worker-log DIR-for-that-stage (by
                                            position) to also get admission-
                                            shadow parity and repair-loop
                                            call/admit totals for that stage.

Every field this script cannot source is written as the literal string
"unavailable" plus a reason, never a guessed number, never a hardcoded
placeholder -- see RUNBOOK.md section 6a for why two fields are always
"unavailable" in the current stack.

Usage:
  python summarize_series.py --run-dir a --run-dir b --run-dir c --out table.md
  python summarize_series.py --stage-dir s1 --stage-dir s2 --worker-log w1 --worker-log w2 --out table.md
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from series_metrics import UNAVAILABLE, build_stage, discover_run_dirs


def fmt(value: Any) -> str:
    return UNAVAILABLE if value is None else str(value)


def render_stage_table(stages: list[dict]) -> str:
    header = (
        "| stage | runs | publish rate | flagged/run (review_required / repair_not_admitted) | "
        "captures (count/reuse, avg per slide) | shadow parity mismatches | repair-loop calls/admitted "
        "(stage total) | vision review slides reviewed | provider requests/output tokens per deck | "
        "wall time (s) |\n"
        "| --- | ---: | ---: | --- | --- | --- | --- | --- | --- | ---: |"
    )
    rows = [header]
    for s in stages:
        flagged_cell = f"{fmt(s['flaggedAvgPerRun'])} ({s['reviewRequiredTotal']} / {s['repairNotAdmittedTotal']})"
        capture_cell = (
            f"{fmt(s['captureCountTotal'])}/{fmt(s['captureReuseCountTotal'])}, "
            f"avg {fmt(s['captureCountPerSlideAvg'])} ({s['captureDataRuns']} runs)"
        )
        if s["shadowLog"].get("available"):
            shadow_cell = f"{s['shadowLog']['mismatchedRows']}/{s['shadowLog']['totalRows']} (worker log)"
        elif s["shadowMismatchTotal"] is not None:
            shadow_cell = (
                f"{s['shadowMismatchTotal']}/{s['shadowRowTotal']} "
                f"(receipt sum, {s['shadowDataRuns']} runs, no worker log)"
            )
        else:
            shadow_cell = UNAVAILABLE
        loop = s["repairLoop"]
        if loop.get("available"):
            loop_cell = f"{loop['modelCallsTotal']}/{loop['admittedTotal']} of {loop['slidesTotal']} slides"
        else:
            loop_cell = f"{UNAVAILABLE} ({loop.get('reason')})"
        provider_cell = (
            f"{fmt(s['providerRequestTotal'])}/{fmt(s['providerOutputTokensTotal'])} "
            f"({s['providerDataRuns']} runs)"
            if s["providerRequestTotal"] is not None
            else UNAVAILABLE
        )
        wall_cell = f"{fmt(s['wallSecondsAvg'])} ({s['wallDataRuns']} runs)"
        rows.append(
            "| {name} | {n} | {pub} | {flag} | {cap} | {shadow} | {loop} | {vision} | {prov} | {wall} |".format(
                name=s["name"], n=s["runCount"], pub=fmt(s["publishRate"]), flag=flagged_cell,
                cap=capture_cell, shadow=shadow_cell, loop=loop_cell,
                vision=s["visionReviewSlidesReviewed"], prov=provider_cell, wall=wall_cell,
            )
        )
    return "\n".join(rows) + "\n"


def render_cross_stage(stages: list[dict]) -> str:
    if len(stages) < 2:
        return ""

    def delta(a, b):
        return UNAVAILABLE if a is None or b is None else round(a - b, 3)

    def avg(total, count):
        return (total / count) if count else None

    lines = [
        "\n## Cross-stage comparison (each row vs. the previous stage)\n",
        "| stage | Δ publish rate | Δ flagged/run | Δ review_required/run | Δ repair_not_admitted/run |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    prev = stages[0]
    for s in stages[1:]:
        lines.append(
            "| {name} | {pub} | {flag} | {rev} | {rep} |".format(
                name=f"{prev['name']} -> {s['name']}",
                pub=delta(s["publishRate"], prev["publishRate"]),
                flag=delta(s["flaggedAvgPerRun"], prev["flaggedAvgPerRun"]),
                rev=delta(
                    avg(s["reviewRequiredTotal"], s["runCount"]),
                    avg(prev["reviewRequiredTotal"], prev["runCount"]),
                ),
                rep=delta(
                    avg(s["repairNotAdmittedTotal"], s["runCount"]),
                    avg(prev["repairNotAdmittedTotal"], prev["runCount"]),
                ),
            )
        )
        prev = s
    return "\n".join(lines) + "\n"


def render_caveats(stages: list[dict]) -> str:
    lines = ["\n## Data completeness notes\n"]
    for s in stages:
        run_caveats = sorted({c for r in s["runs"] for c in r.get("caveats", [])})
        if not run_caveats:
            continue
        lines.append(f"- **{s['name']}**:")
        lines.extend(f"  - {c}" for c in run_caveats)
    lines.append(
        "\n`visionReview slides reviewed` is always `unavailable` in every stage: it is not durably "
        "persisted anywhere offline in this stack's local-development receipt profile. See RUNBOOK.md "
        "section 6a."
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", action="append", default=[], type=Path,
                         help="dry-run: an individual run.ps1 output directory")
    parser.add_argument("--stage-dir", action="append", default=[], type=Path,
                         help="live: a series.ps1 stage directory")
    parser.add_argument("--worker-log", action="append", default=[], type=Path,
                         help="live: worker log paired by position with --stage-dir")
    parser.add_argument("--stage-name", default="dry-run", help="label for the ad-hoc --run-dir stage")
    parser.add_argument("--dry-run", action="store_true",
                         help="documentation only; behavior follows which of --run-dir/--stage-dir is used")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    if not args.run_dir and not args.stage_dir:
        parser.error("pass at least one --run-dir (dry-run) or --stage-dir (live)")

    stages: list[dict] = []
    if args.run_dir:
        stages.append(build_stage(args.stage_name, args.run_dir, worker_log=None, is_dry_run=True))
    for i, stage_dir in enumerate(args.stage_dir):
        worker_log = args.worker_log[i] if i < len(args.worker_log) else None
        run_dirs = discover_run_dirs(stage_dir)
        stages.append(build_stage(stage_dir.name, run_dirs, worker_log=worker_log, is_dry_run=False))

    doc = [
        "# Authored verbatim placeholder-authoring live series: stage table\n",
        f"Generated by summarize_series.py. Mode: {'dry-run (--run-dir)' if args.run_dir else 'live (--stage-dir)'}.\n",
        render_stage_table(stages),
        render_cross_stage(stages),
        render_caveats(stages),
    ]
    text = "\n".join(doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
