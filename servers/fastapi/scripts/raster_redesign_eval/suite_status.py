"""`status` subcommand: prints scheduler + per-deck progress for a `launch`
output directory.

Split out of the original run_suite.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def cmd_status(args: argparse.Namespace) -> None:
    run_dir = Path(args.out)
    state_path = run_dir / "scheduler_state.json"
    sched_decks: dict[str, dict] = {}
    if state_path.exists():
        sched = json.loads(state_path.read_text(encoding="utf-8"))
        sched_decks = sched.get("decks", {})
        print(f"scheduler: updated_at={sched.get('updated_at')} done={sched.get('done')}")
    else:
        print("scheduler_state.json not found (launch not run yet, or a different --out)")

    deck_dirs = sorted(p for p in run_dir.iterdir() if p.is_dir()) if run_dir.exists() else []
    print(f"{'deck':<10} {'sched_status':<12} {'last_stage':<24} {'ok':<6} failure")
    for d in deck_dirs:
        deck_id = d.name
        sched_status = sched_decks.get(deck_id, {}).get("status", "-")
        rr_path = d / "run_report.json"
        last_stage, ok, failure = "-", "-", ""
        if rr_path.exists():
            rr = json.loads(rr_path.read_text(encoding="utf-8"))
            timings = rr.get("timings", [])
            if timings:
                last_stage = timings[-1]["stage"]
                ok = str(timings[-1]["ok"])
            if rr.get("failure"):
                failure = rr["failure"].get("detail", "")[:80]
        print(f"{deck_id:<10} {sched_status:<12} {last_stage:<24} {ok:<6} {failure}")


def add_status_subparser(sub) -> None:
    p_status = sub.add_parser("status")
    p_status.add_argument("--out", required=True)
    p_status.set_defaults(func=cmd_status)
