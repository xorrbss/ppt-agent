"""`baseline` subcommand: scores a set of already-existing product-run output
directories that do NOT follow the `launch` convention of `RUN_DIR/<deck_id>/`
with a `manifest.json` (for example, ad hoc one-off driver runs kept from an
earlier session). Unlike `score`, callers name each deck's output directory
explicitly via a `--map` JSON file, so nothing here is hardcoded to any one
machine or session.

`--map` file shape: `{"<deck_id>": "<output_dir>", ...}`. Each output_dir is
expected to hold a `*.pptx` (the driver's own image_only.pptx/reordered.pptx
are excluded from the glob) and, optionally, a same-stem `.pdf`,
`run_report.json`, and `slide_evidence_report.json`.

Split out of the original run_suite.py (2026-09-25 eval suite), which had
this deck-id -> directory map hardcoded to one prior session's Temp scratch
path; that hardcoding is removed here in favor of `--map`. Scoring logic
itself is unchanged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from deck_scoring import score_one_deck
from suite_paths import DEFAULT_CONFIG, add_decks_root_arg, deck_by_id, load_config, now_iso, resolve_decks_root


def cmd_baseline(args: argparse.Namespace) -> None:
    cfg = load_config(Path(args.config))
    out_path = Path(args.out)
    decks_root = resolve_decks_root(args)
    deck_dirs: dict[str, Path] = {
        deck_id: Path(dir_str) for deck_id, dir_str in json.loads(Path(args.map).read_text(encoding="utf-8")).items()
    }
    report_dir = Path(args.report_dir) if args.report_dir else out_path.parent / "baseline_reports"
    summary = {"scored_at": now_iso(), "source": str(Path(args.map).resolve()), "decks": {}}

    for deck_id, out_dir in deck_dirs.items():
        deck_cfg = deck_by_id(cfg, deck_id)
        run_report = out_dir / "run_report.json"
        if not out_dir.exists():
            summary["decks"][deck_id] = {"deck_id": deck_id, "status": "missing", "reason": f"{out_dir} does not exist"}
            print(f"[baseline] {deck_id}: MISSING (dir gone: {out_dir})")
            continue
        pptx_candidates = [p for p in out_dir.glob("*.pptx") if p.name not in ("image_only.pptx", "reordered.pptx")]
        if not pptx_candidates:
            failure_detail = None
            if run_report.exists():
                rr = json.loads(run_report.read_text(encoding="utf-8"))
                failure_detail = rr.get("failure")
            summary["decks"][deck_id] = {"deck_id": deck_id, "status": "no_output", "failure": failure_detail}
            print(f"[baseline] {deck_id}: NO OUTPUT (failure={failure_detail})")
            continue
        pptx = pptx_candidates[0]
        pdf_candidate = pptx.with_suffix(".pdf")
        pdf = pdf_candidate if pdf_candidate.exists() else None
        evidence = out_dir / "slide_evidence_report.json"
        evidence = evidence if evidence.exists() else None
        try:
            result = score_one_deck(deck_cfg, decks_root, pptx, pdf, report_dir, deck_id,
                                     run_report if run_report.exists() else None, evidence)
            result["status"] = "scored"
            summary["decks"][deck_id] = result
            print(f"[baseline] {deck_id}: scored ok from {pptx}")
        except Exception as exc:  # noqa: BLE001
            summary["decks"][deck_id] = {"deck_id": deck_id, "status": "score_error", "error": str(exc)}
            print(f"[baseline] {deck_id}: SCORE ERROR: {exc}")

    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")


def add_baseline_subparser(sub) -> None:
    p_baseline = sub.add_parser("baseline")
    p_baseline.add_argument("--out", required=True, help="output path for the scored summary JSON")
    p_baseline.add_argument("--map", required=True,
                             help="JSON file mapping deck_id -> an existing output directory to score "
                                  "(does not need to follow the launch RUN_DIR/<deck_id>/ convention)")
    p_baseline.add_argument("--report-dir", default=None,
                             help="default: <out's parent>/baseline_reports")
    p_baseline.add_argument("--config", default=str(DEFAULT_CONFIG))
    add_decks_root_arg(p_baseline)
    p_baseline.set_defaults(func=cmd_baseline)
