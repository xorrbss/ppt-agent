"""`score` subcommand: scores every deck's output in a `launch` run
directory against its configured ground truth / fixtures, writing
`RUN_DIR/summary.json` + `RUN_DIR/reports/<deck>_raw_report.json`.

Split out of the original run_suite.py (2026-09-25 eval suite) to keep each
module under the repo's file-size limit; behavior is unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from deck_scoring import find_run_outputs, score_one_deck
from suite_paths import DEFAULT_CONFIG, add_decks_root_arg, deck_by_id, load_config, now_iso, resolve_decks_root


def cmd_score(args: argparse.Namespace) -> None:
    cfg = load_config(Path(args.config))
    run_dir = Path(args.out)
    decks_root = resolve_decks_root(args)
    deck_ids = args.decks.split(",") if args.decks else [d["id"] for d in cfg["decks"]]

    summary = {"run_dir": str(run_dir), "scored_at": now_iso(), "decks": {}}
    for deck_id in deck_ids:
        deck_cfg = deck_by_id(cfg, deck_id)
        deck_out = run_dir / deck_id
        pptx, pdf, run_report, evidence = find_run_outputs(deck_out)
        if pptx is None:
            failure_detail = None
            if run_report and run_report.exists():
                rr = json.loads(run_report.read_text(encoding="utf-8"))
                failure_detail = rr.get("failure")
            summary["decks"][deck_id] = {
                "deck_id": deck_id, "status": "no_output",
                "failure": failure_detail,
            }
            print(f"[score] {deck_id}: NO OUTPUT (failure={failure_detail})")
            continue
        try:
            result = score_one_deck(deck_cfg, decks_root, pptx, pdf, run_dir / "reports", deck_id,
                                     run_report, evidence)
            result["status"] = "scored"
            summary["decks"][deck_id] = result
            print(f"[score] {deck_id}: scored ok, page_count={result['page_count']}, "
                  f"wrong_numbers={result['wrong_numbers']['count']}")
        except Exception as exc:  # noqa: BLE001
            summary["decks"][deck_id] = {"deck_id": deck_id, "status": "score_error", "error": str(exc)}
            print(f"[score] {deck_id}: SCORE ERROR: {exc}")

    out_path = run_dir / "summary.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")


def add_score_subparser(sub) -> None:
    p_score = sub.add_parser("score")
    p_score.add_argument("--out", required=True)
    p_score.add_argument("--decks", default=None)
    p_score.add_argument("--config", default=str(DEFAULT_CONFIG))
    add_decks_root_arg(p_score)
    p_score.set_defaults(func=cmd_score)
