"""`compare` subcommand: prints a markdown delta table between two scored
runs (or a run and baseline_prev_session.json-shaped file), flagging any
`wrong_numbers.count > 0` and any metric regression.

Split out of the original run_suite.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; behavior is unchanged except the
timing fields (2026-09-25 follow-up: `analysis_duration_seconds` renamed to
`total_seconds` plus four per-stage fields -- see run_timing.py -- with
`analysis_duration_seconds` still read from an OLD summary so a stored run
predating the rename still compares) and the `concurrent_harness` run-level
flag noted next to every timing row.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

TIMING_KEYS = {"total_seconds", "analysis_wait_seconds", "generation_seconds",
               "export_pptx_seconds", "export_pdf_seconds"}

METRICS = [
    ("page_count_ok", "A4/page_count ok", "bool_good_true"),
    ("wrong_numbers.count", "wrong numbers", "lower_better"),
    ("wrong_number_candidates.count", "wrong number candidates (unreviewed)", "lower_better"),
    ("editable_text_ratio_overall", "editable ratio (overall)", "higher_better"),
    ("editable_text_ratio_body_mean", "editable ratio (body mean)", "higher_better"),
    ("editable_text_ratio_body_excl_mockup", "editable ratio (body, excl. mockup)", "higher_better"),
    ("char_recall_mean", "char recall (mean)", "higher_better"),
    ("char_precision_mean", "char precision (mean)", "higher_better"),
    ("table_cell_accuracy", "table cell accuracy", "higher_better"),
    ("full_page_copies", "full-page copies", "lower_better"),
    ("page_content_crops", "page-content crops", "lower_better"),
    ("min_font_pt", "min font pt", "higher_better"),
    ("total_seconds", "total duration (s)", "lower_better"),
    ("analysis_wait_seconds", "analysis-wait duration (s)", "lower_better"),
    ("generation_seconds", "generation duration (s)", "lower_better"),
    ("export_pptx_seconds", "export-pptx duration (s)", "lower_better"),
    ("export_pdf_seconds", "export-pdf duration (s)", "lower_better"),
]


def _get_path(d: dict, dotted: str):
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _get_metric(d: dict, key: str):
    value = _get_path(d, key)
    # A summary scored before the 2026-09-25 timing rename has
    # analysis_duration_seconds instead of total_seconds -- same formula
    # (sum of every driver stage), so it's a valid comparison point.
    if value is None and key == "total_seconds":
        return _get_path(d, "analysis_duration_seconds")
    return value


def _load_summary(path_str: str) -> dict:
    p = Path(path_str)
    if p.is_dir():
        p = p / "summary.json"
    return json.loads(p.read_text(encoding="utf-8"))


def _harness_note(base: dict, new: dict) -> str:
    """`concurrent_harness` is a run-level flag (see suite_launch.py's
    `--concurrent-harness`), not a per-deck one; shown once, next to every
    timing metric row, since it materially affects wall-clock duration."""
    bh, nh = base.get("concurrent_harness"), new.get("concurrent_harness")
    return f" [concurrent_harness: base={bh}, new={nh}]"


def _pixel_cause_table(base_decks: dict, new_decks: dict, deck_ids: list) -> list:
    """Compact `pixel_text_by_cause` table (2026-09-25 follow-up, see
    pixel_text_causes.py): one row per (deck, category) with base/new chars
    and region counts side by side. Skipped for a deck missing the field
    entirely (an older run, predating this metric)."""
    from pixel_text_causes import CATEGORIES

    lines = ["", "### Pixel text by cause", "",
              "| deck | category | base chars | new chars | base regions | new regions |",
              "|---|---|---|---|---|---|"]
    any_row = False
    for deck_id in deck_ids:
        b = (base_decks.get(deck_id, {}).get("pixel_text_by_cause") or {}).get("categories", {})
        n = (new_decks.get(deck_id, {}).get("pixel_text_by_cause") or {}).get("categories", {})
        if not b and not n:
            continue
        for cat in CATEGORIES:
            bc, nc = b.get(cat, {}), n.get(cat, {})
            if not bc and not nc:
                continue
            any_row = True
            lines.append(
                f"| {deck_id} | {cat} | {bc.get('chars')} | {nc.get('chars')} | "
                f"{bc.get('region_count')} | {nc.get('region_count')} |"
            )
    return lines if any_row else []


def cmd_compare(args: argparse.Namespace) -> None:
    base = _load_summary(args.base_run)
    new = _load_summary(args.run)
    base_decks = base.get("decks", {})
    new_decks = new.get("decks", {})
    deck_ids = sorted(set(base_decks) | set(new_decks))
    harness_note = _harness_note(base, new)

    lines = ["| deck | metric | base | new | delta | flag |", "|---|---|---|---|---|---|"]
    flags = []
    for deck_id in deck_ids:
        b = base_decks.get(deck_id, {})
        n = new_decks.get(deck_id, {})
        if b.get("status") != "scored" or n.get("status") != "scored":
            lines.append(f"| {deck_id} | (status) | {b.get('status')} | {n.get('status')} | - | - |")
            continue
        for key, label, direction in METRICS:
            if key in TIMING_KEYS:
                label = label + harness_note
            bv = _get_metric(b, key)
            nv = _get_metric(n, key)
            delta = "-"
            flag = ""
            if isinstance(bv, (int, float)) and isinstance(nv, (int, float)) and not isinstance(bv, bool):
                delta = round(nv - bv, 4)
                if direction == "lower_better" and nv > bv:
                    flag = "REGRESSION"
                elif direction == "higher_better" and nv < bv:
                    flag = "REGRESSION"
            elif isinstance(bv, bool) or isinstance(nv, bool):
                if direction == "bool_good_true" and bv and not nv:
                    flag = "REGRESSION"
            if key == "wrong_numbers.count" and isinstance(nv, (int, float)) and nv > 0:
                flag = "HARD FAIL (wrong numbers > 0)"
            if flag:
                flags.append(f"{deck_id}: {label} {bv} -> {nv} [{flag}]")
            lines.append(f"| {deck_id} | {label} | {bv} | {nv} | {delta} | {flag} |")

    print("\n".join(lines))
    pixel_cause_lines = _pixel_cause_table(base_decks, new_decks, deck_ids)
    if pixel_cause_lines:
        print("\n".join(pixel_cause_lines))
    if flags:
        print("\n### Flags\n")
        for f in flags:
            print(f"- {f}")
    else:
        print("\nNo regressions or hard fails detected.")


def add_compare_subparser(sub) -> None:
    p_compare = sub.add_parser("compare")
    p_compare.add_argument("--base-run", required=True, help="dir with summary.json, or a summary/baseline .json path")
    p_compare.add_argument("--run", required=True, help="dir with summary.json, or a summary/baseline .json path")
    p_compare.set_defaults(func=cmd_compare)
