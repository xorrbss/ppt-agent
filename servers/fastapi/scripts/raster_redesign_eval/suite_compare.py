"""`compare` subcommand: prints a markdown delta table between two scored
runs (or a run and baseline_prev_session.json-shaped file), flagging any
`wrong_numbers.count > 0` and any metric regression.

Split out of the original run_suite.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

METRICS = [
    ("page_count_ok", "A4/page_count ok", "bool_good_true"),
    ("wrong_numbers.count", "wrong numbers", "lower_better"),
    ("editable_text_ratio_overall", "editable ratio (overall)", "higher_better"),
    ("editable_text_ratio_body_mean", "editable ratio (body mean)", "higher_better"),
    ("editable_text_ratio_body_excl_mockup", "editable ratio (body, excl. mockup)", "higher_better"),
    ("char_recall_mean", "char recall (mean)", "higher_better"),
    ("char_precision_mean", "char precision (mean)", "higher_better"),
    ("table_cell_accuracy", "table cell accuracy", "higher_better"),
    ("full_page_copies", "full-page copies", "lower_better"),
    ("page_content_crops", "page-content crops", "lower_better"),
    ("min_font_pt", "min font pt", "higher_better"),
    ("analysis_duration_seconds", "duration (s)", "lower_better"),
]


def _get_path(d: dict, dotted: str):
    cur: Any = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _load_summary(path_str: str) -> dict:
    p = Path(path_str)
    if p.is_dir():
        p = p / "summary.json"
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_compare(args: argparse.Namespace) -> None:
    base = _load_summary(args.base_run)
    new = _load_summary(args.run)
    base_decks = base.get("decks", {})
    new_decks = new.get("decks", {})
    deck_ids = sorted(set(base_decks) | set(new_decks))

    lines = ["| deck | metric | base | new | delta | flag |", "|---|---|---|---|---|---|"]
    flags = []
    for deck_id in deck_ids:
        b = base_decks.get(deck_id, {})
        n = new_decks.get(deck_id, {})
        if b.get("status") != "scored" or n.get("status") != "scored":
            lines.append(f"| {deck_id} | (status) | {b.get('status')} | {n.get('status')} | - | - |")
            continue
        for key, label, direction in METRICS:
            bv = _get_path(b, key)
            nv = _get_path(n, key)
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
