"""Canvas quality bench: measure a canvas × style, compare it, promote it.

A new canvas reaches users only through ``constants/canvas_stages.json``, and
only on measured evidence, never by hand edits
(docs/aspect-ratio-expansion-design-20260928.md §8,
docs/canvas-expansion-content-budget-spec-20260929.md §6).

    cd servers/fastapi
    # one canvas, one deck per manuscript
    uv run python scripts/canvas_bench.py run --base http://127.0.0.1:8091 \\
        --app-data <backend APP_DATA_DIRECTORY> --canvas a4-landscape --style default \\
        --manuscripts tests/fixtures/canvas_bench_manuscripts --slides 6 --out <dir> \\
        --backend-commit <sha> --ink-fix-commit <sha>
    # pooled matrix: serial, interleaved, resumable (rerun the same command to resume)
    uv run python scripts/canvas_bench.py matrix --base http://127.0.0.1:8091 --app-data <dir> \\
        --style default --manuscripts tests/fixtures/canvas_bench_manuscripts --limit 3 --slides 6 \\
        --canvas-decks wide-16x9=18 --canvas-decks square-1x1=12 --backend-commit <sha> \\
        --ink-fix-commit <sha> --out <dir>
    # decision rule on the pooled receipts of any number of run/matrix directories
    uv run python scripts/canvas_bench.py compare --baseline <dir> [<dir> ...] --baseline-canvas wide-16x9 \\
        --candidate <dir> [<dir> ...] --candidate-canvas square-1x1 --out <dir>
    uv run python scripts/canvas_bench.py promote --canvas square-1x1 --style default --receipts <compare.json>
    uv run python scripts/canvas_bench.py invalidate <receipt.json> --note "machine restarted"

``run``/``matrix`` drive one deck at a time and attribute QualityReceipt
ledgers by write time, so point them at a backend no one else is generating
on.  The decision rule lives in ``canvas_bench_assess.py`` (statistics in
``canvas_bench_stats.py``, metric epochs in ``canvas_bench_epoch.py``, matrix
planning in ``canvas_bench_matrix.py``, registry and promotion in
``canvas_bench_rules.py``); API and evidence reading in ``canvas_bench_live.py``;
PPTX checks and renders in ``canvas_bench_render.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from scripts import canvas_bench_epoch as epochs  # noqa: E402
from scripts import canvas_bench_live as live  # noqa: E402
from scripts import canvas_bench_matrix as matrix  # noqa: E402
from scripts import canvas_bench_render as render  # noqa: E402
from scripts import canvas_bench_rules as rules  # noqa: E402
from scripts import canvas_bench_watch as watch  # noqa: E402

DEFAULT_LANGUAGE = "Korean (한국어)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _request_body(content: str, args: argparse.Namespace, registry: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "content": content,
        "n_slides": args.slides,
        "language": args.language,
        "template": "authored",
        "authored_style": args.style,
        "export_as": "pptx",
        **rules.request_fields(args.canvas, registry),
    }
    if args.strategy:
        body["strategy"] = args.strategy
    return body


def _export_evidence(client: live.BenchClient, presentation_id: str, operation_id: str | None,
                     deck_dir: Path, expected: tuple[int, int]) -> dict[str, Any]:
    evidence: dict[str, Any] = {"ok": False, "operationId": operation_id, "error": None, "artifact": None,
                                "slideSizeEmu": None, "slideParts": None,
                                "expectedEmu": {"cx": expected[0], "cy": expected[1]}, "sizeMatches": None}
    if not operation_id:
        evidence["error"] = "export_not_chained"
        return evidence
    card = client.wait_operation(presentation_id, operation_id)
    if card is None:
        evidence["error"] = "export_timeout"
        return evidence
    if card.get("status") != "succeeded":
        evidence["error"] = f"{card.get('status')}: {card.get('error') or card.get('error_message')}"
        return evidence
    artifact_id = (card.get("result") or {}).get("artifact_id") or (card.get("result") or {}).get("artifactId")
    if not artifact_id:
        evidence["error"] = "export_published_no_artifact"
        return evidence
    pptx = client.download_artifact(str(artifact_id), deck_dir / "deck.pptx")
    size = render.pptx_slide_size(pptx)
    evidence.update(ok=True, artifact=str(pptx), slideParts=render.pptx_slide_parts(pptx),
                    slideSizeEmu=None if size is None else {"cx": size[0], "cy": size[1]},
                    sizeMatches=size == expected)
    return evidence


def _run_one(client: live.BenchClient, manuscript: Path, args: argparse.Namespace,
            registry: dict[str, Any], out_dir: Path, stem: str | None = None,
            repeat: int | None = None) -> dict[str, Any]:
    body = _request_body(manuscript.read_text(encoding="utf-8").strip(), args, registry)
    deck_dir = out_dir / args.canvas / (stem or manuscript.stem)
    started = time.time()
    status = client.generate(body)
    finished = time.time()
    data = status.get("data") or {}
    presentation_id = data.get("presentation_id")
    receipt: dict[str, Any] = {
        "schemaVersion": rules.RECEIPT_SCHEMA, "createdAt": _now(), "backendCommit": args.backend_commit,
        "metricEpoch": args.run_epoch, "batch": args.batch, "repeat": repeat,
        "manuscript": manuscript.name, "canvas": args.canvas, "style": args.style,
        "strategy": args.strategy, "requestedSlides": args.slides,
        "request": {k: v for k, v in body.items() if k != "content"},
        "presentationId": presentation_id, "taskId": status.get("id"), "operationId": status.get("operation_id"),
        "clientWallSeconds": round(finished - started, 2),
        "generation": {"ok": status.get("status") == "completed" and bool(presentation_id),
                       "status": status.get("status"),
                       "error": status.get("error") or status.get("failure") or status.get("detail")},
    }
    expected = rules.canvas_emu(args.canvas, registry)
    if not presentation_id:
        receipt.update(slideCount=0, timing={"totalSeconds": None, "perSlideSeconds": None}, retries={"total": None},
                       localFitUnresolved=None, gate={"blockers": [], "flags": []},
                       export={"ok": False, "error": "generation_failed", "sizeMatches": None,
                               "expectedEmu": {"cx": expected[0], "cy": expected[1]}})
        return receipt
    presentation = client.presentation(presentation_id)
    export_operation_id = data.get("export_operation_id")
    export = _export_evidence(client, presentation_id, export_operation_id, deck_dir, expected)
    timeline = client.timeline(presentation_id)
    generation_card = live.operation_card(timeline, operation_type="presentation.generate")
    export_card = live.operation_card(timeline, operation_id=export_operation_id) if export_operation_id else None
    timing = live.timing_evidence(generation_card)
    quality = live.quality_receipts_between(Path(args.app_data), started, finished) if args.app_data else []
    slides = live.slide_count(presentation)
    formats = live.source_formats(presentation)
    total = timing["totalSeconds"]
    theme = presentation.get("theme") if isinstance(presentation.get("theme"), dict) else {}
    overlap_basis, unmeasured = epochs.deck_overlap_basis((generation_card or {}).get("result"), presentation)
    receipt["overlapBasis"] = overlap_basis
    receipt["metricEpoch"] = args.metric_epoch or epochs.observed_epoch(overlap_basis) or args.run_epoch
    if not overlap_basis and args.run_epoch == "unmeasured" and not args.metric_epoch:
        unmeasured = unmeasured or "missing_overlap_basis"
    if unmeasured:
        receipt["primaryMetric"] = {"status": "unmeasured", "reason": unmeasured}
    receipt.update(
        slideCount=slides,
        sourceFormats=formats,
        resolvedCanvas=theme.get("documentCanvas") or (theme.get("authored_vnext") or {}).get("canvas"),
        timing={"source": "generation_timing_receipt.wallMillis", "totalSeconds": total,
                "perSlideSeconds": None if total is None or not slides else round(total / slides, 2),
                "receipt": timing["receipt"]},
        retries=timing["retries"] or {"total": None},
        localFitUnresolved=live.local_fit_unresolved(timing, formats),
        gate=live.gate_evidence(quality, timing, generation_card, export_card),
        export=export,
    )
    if unmeasured:
        receipt["gate"]["flags"]["flaggedPages"] = None
    if receipt["export"]["ok"] and not args.no_render:
        pngs, skipped = render.render_pptx_slides(Path(receipt["export"]["artifact"]), deck_dir / "slides")
        receipt["renders"] = {"count": len(pngs), "dir": str(deck_dir / "slides"), "skipped": skipped}
    return receipt


def run_one(client: live.BenchClient, manuscript: Path, args: argparse.Namespace,
            registry: dict[str, Any], out_dir: Path, stem: str | None = None,
            repeat: int | None = None) -> dict[str, Any]:
    receipt = _run_one(client, manuscript, args, registry, out_dir, stem, repeat)
    client.watch.end()
    overrun = watch.backend_overrun((receipt.get("timing") or {}).get("receipt") or {},
                                   receipt["clientWallSeconds"], client.finished_seconds)
    receipt["clock"] = client.watch.evidence(getattr(args, "power_guard", "none"), overrun)
    if client.watch.detected:
        clock = receipt["clock"]
        receipt["invalid"] = {"reason": "infra_lost", "note":
                              f"clock: slept {clock['sleptSeconds']} s; poll gap {clock['maxPollGapSeconds']} s"}
    elif receipt["generation"]["ok"]:
        client.finished_seconds.append(receipt["clientWallSeconds"])
    return receipt


def _prepare(args: argparse.Namespace) -> dict[str, Any]:
    """Validate the canvas ids and resolve the metric epoch and batch before any deck runs."""

    registry = rules.load_registry()
    for canvas in getattr(args, "canvas_ids", None) or [args.canvas]:
        rules.canvas_emu(canvas, registry)
    try:
        args.run_epoch = (epochs.resolve_run_epoch(args.metric_epoch, args.backend_commit, args.ink_fix_commit)
                          if args.metric_epoch or args.backend_commit or args.ink_fix_commit else "unmeasured")
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.batch = args.batch or datetime.now(timezone.utc).strftime("%Y%m%d")
    print(f"metric epoch: {args.run_epoch}; batch: {args.batch}", flush=True)
    return registry


def _manuscripts(args: argparse.Namespace) -> list[Path]:
    manuscripts = sorted(Path(args.manuscripts).glob("*.txt"))[: args.limit or None]
    if not manuscripts:
        raise SystemExit(f"no *.txt manuscripts in {args.manuscripts}")
    return manuscripts


def _contact_sheet(out_dir: Path, canvas: str) -> None:
    runs = sorted(p / "slides" for p in (out_dir / canvas).iterdir() if (p / "slides").is_dir())
    if runs:
        skipped = render.contact_sheet(runs, out_dir / canvas / "contact-sheet")
        if skipped:
            print(f"contact sheet skipped: {skipped}")


def cmd_run(args: argparse.Namespace) -> int:
    registry = _prepare(args)
    manuscripts = _manuscripts(args)
    out_dir = Path(args.out)
    client = live.BenchClient(args.base, timeout_seconds=args.timeout)
    try:
        with watch.power_guard(f"canvas bench: {out_dir}") as args.power_guard:
            print(f"power guard: {args.power_guard}", flush=True)
            for manuscript in manuscripts:
                print(f"[{args.canvas}] {manuscript.name} ...", flush=True)
                receipt = run_one(client, manuscript, args, registry, out_dir)
                path = out_dir / args.canvas / f"{manuscript.stem}.receipt.json"
                _write_json(path, receipt)
                print(f"  -> {path} generation={receipt['generation']['ok']} "
                      f"export={receipt['export']['ok']}", flush=True)
    finally:
        client.close()
    if not args.no_render:
        _contact_sheet(out_dir, args.canvas)
    return 0


def cmd_matrix(args: argparse.Namespace) -> int:
    try:
        canvas_decks = matrix.parse_canvas_decks(args.canvas_decks)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    args.canvas_ids, args.canvas = list(canvas_decks), None
    registry = _prepare(args)
    plan = matrix.plan_matrix(canvas_decks, _manuscripts(args), args.seed)
    out_dir = Path(args.out)
    if args.dry_run:
        todo = {(d.canvas, d.stem) for d in matrix.pending(plan, out_dir)}
        for deck in plan:
            print(f"{'todo' if (deck.canvas, deck.stem) in todo else 'done'}  {deck.canvas}  {deck.stem}")
        return 0
    client = live.BenchClient(args.base, timeout_seconds=args.timeout)
    base_receipt = {"schemaVersion": rules.RECEIPT_SCHEMA, "backendCommit": args.backend_commit,
                    "metricEpoch": args.run_epoch, "batch": args.batch, "style": args.style,
                    "strategy": args.strategy, "requestedSlides": args.slides}

    def run_deck(deck: matrix.Deck) -> dict[str, Any]:
        deck_args = argparse.Namespace(**{**vars(args), "canvas": deck.canvas})
        return run_one(client, deck.manuscript, deck_args, registry, out_dir, stem=deck.stem, repeat=deck.repeat)

    try:
        with watch.power_guard(f"canvas bench: {out_dir}") as args.power_guard:
            print(f"power guard: {args.power_guard}", flush=True)
            return matrix.run_matrix(plan, out_dir, run_deck, base_receipt, _write_json,
                                     health_probe=client.health_probe)
    finally:
        client.close()


def load_receipts(targets: list[str], canvas: str | None = None) -> list[dict[str, Any]]:
    """Receipts under directories (recursively) or single files, optionally one canvas only."""

    files: list[Path] = []
    for target in targets:
        path = Path(target)
        files += sorted(path.rglob("*.receipt.json")) if path.is_dir() else [path]
    receipts = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    bad = [r for r in receipts if r.get("schemaVersion") not in rules.ACCEPTED_RECEIPT_SCHEMAS]
    if bad:
        raise SystemExit(f"{targets}: not a {rules.RECEIPT_SCHEMA} (or v1) receipt")
    if canvas:
        receipts = [r for r in receipts if r.get("canvas") == canvas]
    if not receipts:
        raise SystemExit(f"no *.receipt.json under {targets}" + (f" for canvas {canvas}" if canvas else ""))
    return receipts


def cmd_compare(args: argparse.Namespace) -> int:
    baseline = load_receipts(args.baseline, args.baseline_canvas)
    candidate = load_receipts(args.candidate, args.candidate_canvas)
    try:
        result = rules.compare(baseline, candidate, epoch=args.epoch, slides=args.slides)
    except ValueError as error:
        print(f"compare refused: {error}", file=sys.stderr)
        return 2
    result["createdAt"] = _now()
    markdown = rules.compare_markdown(result)
    out = Path(args.out)
    stem = f"compare-{result['canvas']}-vs-{'-'.join(result['baselineCanvas'])}"
    _write_json(out / f"{stem}.json", result)
    (out / f"{stem}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    if result["earnedStage"] != "disabled":
        return 0
    return 1 if any(t["verdict"] == "fail" for t in result["tiers"].values()) else 3


def cmd_promote(args: argparse.Namespace) -> int:
    registry = rules.load_registry()
    stages = json.loads(rules.CANVAS_STAGES_PATH.read_text(encoding="utf-8"))
    try:
        ratio = rules.gated_ratio(args.canvas, registry, stages)
        compares = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.receipts]
        if any(c.get("schemaVersion") != rules.COMPARE_SCHEMA for c in compares):
            raise ValueError(f"--receipts must be {rules.COMPARE_SCHEMA} outputs of `compare`")
        earned = rules.promotion_stage(compares, args.canvas, args.style)
        current = stages.get(ratio, {}).get(args.style, "disabled")
        if rules.STAGE_ORDER.index(earned) < rules.STAGE_ORDER.index(current):
            print(f"{args.canvas} ({ratio}) / {args.style}: earned {earned}; stays {current} "
                  "(a bench result never lowers a stage; demotion is a manual decision)")
            return 0
        updated, outcome = rules.apply_promotion(stages, ratio, args.style, earned)
    except ValueError as error:
        print(f"promote refused: {error}", file=sys.stderr)
        return 2
    if outcome != "unchanged":
        rules.CANVAS_STAGES_PATH.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{args.canvas} ({ratio}) / {args.style}: earned {earned}; {outcome}")
    return 0


def cmd_invalidate(args: argparse.Namespace) -> int:
    path = Path(args.receipt)
    receipt = json.loads(path.read_text(encoding="utf-8"))
    receipt["invalid"] = {"reason": "infra_lost", "note": args.note}
    _write_json(path, receipt)
    print(f"{path}: marked invalid (infra_lost); it no longer pools")
    return 0


def _add_run_options(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--base", required=True, help="backend API origin, e.g. http://127.0.0.1:8091")
    sub.add_argument("--style", required=True)
    sub.add_argument("--manuscripts", required=True, help="directory of *.txt prompts")
    sub.add_argument("--slides", type=int, default=6)
    sub.add_argument("--strategy", choices=("svg", "authored"), default=None, help="omit for the product default")
    sub.add_argument("--out", required=True)
    sub.add_argument("--app-data", default=None, help="the backend's APP_DATA_DIRECTORY, for QualityReceipts")
    sub.add_argument("--language", default=DEFAULT_LANGUAGE)
    sub.add_argument("--limit", type=int, default=0, help="use only the first N manuscripts")
    sub.add_argument("--timeout", type=float, default=3600.0, help="seconds per deck and per export")
    sub.add_argument("--backend-commit", default=None, help="commit the backend runs, recorded in receipts")
    sub.add_argument("--ink-fix-commit", default=None,
                     help="commit that introduced svg text_overlap ink boxes; decides the metric epoch by ancestry")
    sub.add_argument("--metric-epoch", default=None, help="explicit metric epoch label (overrides ancestry)")
    sub.add_argument("--batch", default=None, help="batch label recorded in receipts (default: today, UTC)")
    sub.add_argument("--no-render", action="store_true", help="skip PNG renders and the contact sheet")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="generate, export and measure decks on one canvas")
    _add_run_options(run)
    run.add_argument("--canvas", required=True, help="canvas_registry.json id, e.g. a4-landscape")
    run.set_defaults(func=cmd_run)
    matrix_cmd = sub.add_parser("matrix", help="run a pooled canvas matrix serially and resumably")
    _add_run_options(matrix_cmd)
    matrix_cmd.add_argument("--canvas-decks", action="append", default=[], metavar="CANVAS=N",
                            help="decks for a canvas, repeatable; spread round-robin over the manuscripts")
    matrix_cmd.add_argument("--seed", type=int, default=20260929, help="interleave order seed")
    matrix_cmd.add_argument("--dry-run", action="store_true", help="print the plan (done/todo) and exit")
    matrix_cmd.set_defaults(func=cmd_matrix)
    compare = sub.add_parser("compare", help="apply the pooled decision rule to receipt sets")
    compare.add_argument("--baseline", nargs="+", required=True, help="receipt directories or files")
    compare.add_argument("--candidate", nargs="+", required=True)
    compare.add_argument("--baseline-canvas", default=None, help="keep only this canvas from --baseline")
    compare.add_argument("--candidate-canvas", default=None)
    compare.add_argument("--epoch", default=None, help="metric epoch to pool (default: newest candidate deck's)")
    compare.add_argument("--slides", type=int, default=None, help="requested slide count to pool")
    compare.add_argument("--out", required=True)
    compare.set_defaults(func=cmd_compare)
    promote = sub.add_parser("promote", help="write the earned stage into canvas_stages.json")
    promote.add_argument("--canvas", required=True)
    promote.add_argument("--style", required=True)
    promote.add_argument("--receipts", nargs="+", required=True, help="compare output (newest last)")
    promote.set_defaults(func=cmd_promote)
    invalidate = sub.add_parser("invalidate", help="mark a deck lost to the environment (infra_lost)")
    invalidate.add_argument("receipt")
    invalidate.add_argument("--note", required=True)
    invalidate.set_defaults(func=cmd_invalidate)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
