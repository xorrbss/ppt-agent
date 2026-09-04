"""Composer verbatim-restyle promotion gate (A1-A7).

One-command acceptance sweep for the absolute gate in
``docs/generation-quality-root-cause-and-composer-architecture-20260904.md``
section 7a. Scores A1/A2/A3/A5 (plus a best-effort A4) and always emits A6/A7
as ``not_evaluated`` -- they require a human, or a separate live-stack run,
per that section.

Two modes:

``--mode offline`` drives the existing ``scripts/composer_offline_eval.py``
in-process (unchanged, no database writes, no provider calls) over the
stored targets (``deck-35``, ``deck-43``) plus any ``--pptx`` given, then
scores the resulting ``report.json`` files. This is the mode that can be run
today::

    .venv\\Scripts\\python.exe scripts/composer_acceptance_gate.py --mode offline

``--mode live`` submits real ``source_content_restyle`` operations against a
running backend and polls them to terminal state. It is implemented in
``composer_acceptance_gate_live.py`` but requires the explicit
``--i-understand-this-submits-live-operations`` flag, and this session does
not run it -- another session owns the live stack for A6/A7. See that
module's docstring for the one-line command.

Exit codes: 0 the automated criteria (A1-A5) all passed or were correctly
marked not_evaluated with no failure, 1 at least one criterion failed,
2 a Composer stage or input error stopped the sweep before it could score
anything (the same convention as ``composer_offline_eval.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from scripts import composer_offline_eval as offline_eval  # noqa: E402
from scripts.composer_acceptance_gate_score import (  # noqa: E402
    RunEvidence,
    SlideEvidence,
    build_gate_report,
    format_table,
)

DEFAULT_TARGETS = ("deck-35", "deck-43")
DEFAULT_OUT_ROOT = FASTAPI_ROOT.parents[1] / "artifacts" / "composer-acceptance-gate"

EXIT_OK, EXIT_FAILED, EXIT_BLOCKED = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=("offline", "live"), required=True, help="which sweep to run")
    parser.add_argument("--out", type=Path, help="output directory (default: artifacts/composer-acceptance-gate/<ts>)")
    parser.add_argument(
        "--env-json", type=Path,
        help="captured environment (database targets in offline mode; DB polling in live mode)",
    )

    offline = parser.add_argument_group("offline mode")
    offline.add_argument(
        "--target", action="append", dest="targets",
        help=f"named deck from composer_offline_eval_targets.json (repeatable; default {', '.join(DEFAULT_TARGETS)})",
    )
    offline.add_argument(
        "--pptx", action="append", type=Path, default=[], help="extract this PPTX directly (repeatable)"
    )
    offline.add_argument("--concurrency", type=int, default=2, help="parallel Chromium fits per deck (default 2)")
    offline.add_argument("--limit", type=int, help="fit only the first N pages of each deck")
    offline.add_argument("--render-timeout", type=int, default=60, help="seconds per Chromium capture")

    live = parser.add_argument_group("live mode (implemented, not executed by this harness)")
    live.add_argument("--manifest", type=Path, help="JSON deck manifest for live submissions")
    live.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1/ppt", help="backend base URL")
    live.add_argument("--runs", type=int, default=20, help="number of live operations to submit (default 20)")
    live.add_argument("--poll-interval", type=float, default=20.0, help="seconds between DB polls (default 20)")
    live.add_argument("--op-timeout-minutes", type=float, default=10.0, help="per-operation terminal-state timeout")
    live.add_argument(
        "--i-understand-this-submits-live-operations", action="store_true", dest="live_confirmed",
        help="required to actually run --mode live; omitting it stops before any HTTP call",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.mode == "live" and not args.manifest:
        parser.error("--mode live requires --manifest")
    return args


# --- Offline collection ---------------------------------------------------------


def _is_accepted_residual(rung: int | None, blocking_issues: Sequence[str]) -> bool:
    from services.composer.receipt import is_accepted_residual

    return rung is not None and is_accepted_residual(rung, blocking_issues)


def _slides_from_report(report: dict[str, Any]) -> tuple[SlideEvidence, ...]:
    """Rebuild final/residual blocking issues the way ``build_composer_receipt`` does.

    ``composer_offline_eval.py``'s per-slide rows carry the raw fit-ladder
    ``blockingIssues`` (a rung-5 slide with source-overlap residue still lists
    it there); the live receipt already separates that into ``blockingIssues``
    (final) and ``residualIssues``. Applying the same rule here keeps A3/A4
    scoring identical between the two sources.
    """

    slides = []
    for row in report.get("slides", ()):
        rung = row.get("rung")
        raw_issues = tuple(row.get("blockingIssues") or ())
        if raw_issues and _is_accepted_residual(rung, raw_issues):
            blocking, residual = (), raw_issues
        else:
            blocking, residual = raw_issues, ()
        slides.append(
            SlideEvidence(index=row["slideIndex"], rung=rung, blocking_issues=blocking, residual_issues=residual)
        )
    return tuple(slides)


def collect_offline_run(report: dict[str, Any], *, label: str, report_path: Path) -> RunEvidence:
    source = report.get("source") or {}
    deck = (report.get("readback") or {}).get("deck") or {}
    notes = []
    if not report.get("readback"):
        notes.append("run produced no readback section (dry run or a stage error)")
    return RunEvidence(
        label=label,
        source="offline",
        evidence_path=str(report_path),
        deck_key=str(source.get("manifestDigest") or source.get("manifestId") or label),
        page_count=source.get("pageCount"),
        wall_ms=(report.get("timings") or {}).get("totalMs"),
        provider_calls=report.get("providerCalls"),
        op_status=None,  # offline mode never publishes; A1 is not scored from it
        readback_passed=deck.get("passed") if report.get("readback") else None,
        checked_block_count=deck.get("checkedBlockCount"),
        missing_block_count=len(deck.get("missingBlockIds") or ()),
        slides=_slides_from_report(report),
        notes=tuple(notes),
    )


def _offline_targets(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    """``(label, extra composer_offline_eval argv)`` per requested deck."""

    targets: list[tuple[str, list[str]]] = []
    names = args.targets or list(DEFAULT_TARGETS)
    for name in names:
        targets.append((name, ["--target", name]))
    for pptx_path in args.pptx:
        label = pptx_path.stem
        targets.append((label, ["--pptx", str(pptx_path), "--label", label]))
    return targets


async def run_offline(args: argparse.Namespace, out_dir: Path) -> tuple[list[RunEvidence], list[str]]:
    runs: list[RunEvidence] = []
    problems: list[str] = []
    for label, extra_argv in _offline_targets(args):
        deck_out = out_dir / label
        argv = [
            *extra_argv, "--out", str(deck_out),
            "--concurrency", str(args.concurrency), "--render-timeout", str(args.render_timeout),
        ]
        if args.limit:
            argv += ["--limit", str(args.limit)]
        if args.env_json:
            argv += ["--env-json", str(args.env_json)]
        namespace = offline_eval.parse_args(argv)
        exit_code = await offline_eval.run(namespace)
        report_path = deck_out / "report.json"
        if not report_path.is_file():
            problems.append(f"{label}: composer_offline_eval produced no report.json (exit {exit_code})")
            continue
        with report_path.open(encoding="utf-8") as handle:
            report = json.load(handle)
        runs.append(collect_offline_run(report, label=label, report_path=report_path))
        if exit_code == offline_eval.EXIT_MISSING_STAGE:
            problems.append(f"{label}: a Composer stage module is missing; see stdout above")
    return runs, problems


# --- Live mode (submission implemented, execution gated) -----------------------


async def run_live(args: argparse.Namespace, out_dir: Path) -> tuple[list[RunEvidence], list[str]]:
    if not args.live_confirmed:
        return [], [
            "live mode requires --i-understand-this-submits-live-operations; refusing to submit anything. "
            "See composer_acceptance_gate_live.py for what it will do and how to run it."
        ]
    from scripts.composer_acceptance_gate_live import LiveConfig, run_live_sweep

    config = LiveConfig(
        manifest_path=args.manifest, base_url=args.base_url, env_json=args.env_json, runs=args.runs,
        poll_interval_seconds=args.poll_interval, op_timeout_minutes=args.op_timeout_minutes, out_dir=out_dir,
    )
    return await run_live_sweep(config)


# --- Entry point ------------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    out_dir = args.out or DEFAULT_OUT_ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    offline_runs: list[RunEvidence] = []
    live_runs: list[RunEvidence] = []
    problems: list[str] = []
    if args.mode == "offline":
        offline_runs, problems = await run_offline(args, out_dir)
    else:
        live_runs, problems = await run_live(args, out_dir)
    if not offline_runs and not live_runs:
        for problem in problems:
            print(problem)
        return EXIT_BLOCKED
    report = build_gate_report(mode=args.mode, offline_runs=offline_runs, live_runs=live_runs, notes=problems)
    report_path = out_dir / "gate-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(format_table(report))
    print(f"\nwrote {report_path}")
    for problem in problems:
        print(f"note: {problem}")
    return EXIT_OK if report["overall"] != "blocked" else EXIT_FAILED


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(run(parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
