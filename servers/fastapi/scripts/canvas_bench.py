"""Canvas quality bench: measure a canvas × style, compare it, promote it.

A new canvas reaches users only through ``constants/canvas_stages.json``, and
only on measured evidence, never by hand edits
(docs/aspect-ratio-expansion-design-20260928.md §8,
docs/canvas-expansion-content-budget-spec-20260929.md §6).

    cd servers/fastapi
    uv run python scripts/canvas_bench.py run --base http://127.0.0.1:8091 \\
        --app-data <backend APP_DATA_DIRECTORY> --canvas a4-landscape --style default \\
        --manuscripts tests/fixtures/canvas_bench_manuscripts --slides 6 --out <dir>
    uv run python scripts/canvas_bench.py compare --baseline <dir|json> --candidate <dir|json> --out <dir>
    uv run python scripts/canvas_bench.py promote --canvas square-1x1 --style default \\
        --receipts <compare.json> [<compare.json> ...]

``run`` drives one deck at a time and attributes QualityReceipt ledgers by
write time, so point it at a backend no one else is generating on.  Rules
live in ``canvas_bench_rules.py``; API and evidence reading in
``canvas_bench_live.py``; PPTX checks and renders in ``canvas_bench_render.py``.
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

from scripts import canvas_bench_live as live  # noqa: E402
from scripts import canvas_bench_render as render  # noqa: E402
from scripts import canvas_bench_rules as rules  # noqa: E402

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


def run_one(client: live.BenchClient, manuscript: Path, args: argparse.Namespace,
            registry: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    body = _request_body(manuscript.read_text(encoding="utf-8").strip(), args, registry)
    deck_dir = out_dir / args.canvas / manuscript.stem
    started = time.time()
    status = client.generate(body)
    finished = time.time()
    data = status.get("data") or {}
    presentation_id = data.get("presentation_id")
    receipt: dict[str, Any] = {
        "schemaVersion": rules.RECEIPT_SCHEMA, "createdAt": _now(), "backendCommit": args.backend_commit,
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
    if receipt["export"]["ok"] and not args.no_render:
        pngs, skipped = render.render_pptx_slides(Path(receipt["export"]["artifact"]), deck_dir / "slides")
        receipt["renders"] = {"count": len(pngs), "dir": str(deck_dir / "slides"), "skipped": skipped}
    return receipt


def cmd_run(args: argparse.Namespace) -> int:
    registry = rules.load_registry()
    rules.canvas_emu(args.canvas, registry)
    manuscripts = sorted(Path(args.manuscripts).glob("*.txt"))[: args.limit or None]
    if not manuscripts:
        raise SystemExit(f"no *.txt manuscripts in {args.manuscripts}")
    out_dir = Path(args.out)
    client = live.BenchClient(args.base, timeout_seconds=args.timeout)
    try:
        for manuscript in manuscripts:
            print(f"[{args.canvas}] {manuscript.name} ...", flush=True)
            receipt = run_one(client, manuscript, args, registry, out_dir)
            path = out_dir / args.canvas / f"{manuscript.stem}.receipt.json"
            _write_json(path, receipt)
            print(f"  -> {path} generation={receipt['generation']['ok']} export={receipt['export']['ok']}", flush=True)
    finally:
        client.close()
    runs = sorted(p / "slides" for p in (out_dir / args.canvas).iterdir() if (p / "slides").is_dir())
    if runs and not args.no_render:
        skipped = render.contact_sheet(runs, out_dir / args.canvas / "contact-sheet")
        if skipped:
            print(f"contact sheet skipped: {skipped}")
    return 0


def _load_receipts(target: str) -> list[dict[str, Any]]:
    path = Path(target)
    files = sorted(path.glob("*.receipt.json")) if path.is_dir() else [path]
    receipts = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    if not receipts:
        raise SystemExit(f"no *.receipt.json under {target}")
    bad = [r for r in receipts if r.get("schemaVersion") != rules.RECEIPT_SCHEMA]
    if bad:
        raise SystemExit(f"{target}: not a {rules.RECEIPT_SCHEMA} receipt")
    return receipts


def cmd_compare(args: argparse.Namespace) -> int:
    result = rules.compare(_load_receipts(args.baseline), _load_receipts(args.candidate))
    result["createdAt"] = _now()
    markdown = rules.compare_markdown(result)
    out = Path(args.out)
    stem = f"compare-{result['canvas']}-vs-{'-'.join(result['baselineCanvas'])}"
    _write_json(out / f"{stem}.json", result)
    (out / f"{stem}.md").write_text(markdown, encoding="utf-8")
    print(markdown)
    return 0 if result["passed"] else 1


def cmd_promote(args: argparse.Namespace) -> int:
    registry = rules.load_registry()
    stages = json.loads(rules.CANVAS_STAGES_PATH.read_text(encoding="utf-8"))
    try:
        ratio = rules.gated_ratio(args.canvas, registry, stages)
        compares = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.receipts]
        if any(c.get("schemaVersion") != rules.COMPARE_SCHEMA for c in compares):
            raise ValueError(f"--receipts must be {rules.COMPARE_SCHEMA} outputs of `compare`")
        earned = rules.promotion_stage(compares, args.canvas, args.style)
        updated, outcome = rules.apply_promotion(stages, ratio, args.style, earned)
    except ValueError as error:
        print(f"promote refused: {error}", file=sys.stderr)
        return 2
    if outcome != "unchanged":
        rules.CANVAS_STAGES_PATH.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{args.canvas} ({ratio}) / {args.style}: earned {earned}; {outcome}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="generate, export and measure decks on one canvas")
    run.add_argument("--base", required=True, help="backend API origin, e.g. http://127.0.0.1:8091")
    run.add_argument("--canvas", required=True, help="canvas_registry.json id, e.g. a4-landscape")
    run.add_argument("--style", required=True)
    run.add_argument("--manuscripts", required=True, help="directory of *.txt prompts")
    run.add_argument("--slides", type=int, default=6)
    run.add_argument("--strategy", choices=("svg", "authored"), default=None, help="omit for the product default")
    run.add_argument("--out", required=True)
    run.add_argument("--app-data", default=None, help="the backend's APP_DATA_DIRECTORY, for QualityReceipts")
    run.add_argument("--language", default=DEFAULT_LANGUAGE)
    run.add_argument("--limit", type=int, default=0, help="use only the first N manuscripts")
    run.add_argument("--timeout", type=float, default=3600.0, help="seconds per deck and per export")
    run.add_argument("--backend-commit", default=None, help="commit the backend runs, recorded in receipts")
    run.add_argument("--no-render", action="store_true", help="skip PNG renders and the contact sheet")
    run.set_defaults(func=cmd_run)
    compare = sub.add_parser("compare", help="apply the promotion rule to two receipt sets")
    compare.add_argument("--baseline", required=True)
    compare.add_argument("--candidate", required=True)
    compare.add_argument("--out", required=True)
    compare.set_defaults(func=cmd_compare)
    promote = sub.add_parser("promote", help="write the earned stage into canvas_stages.json")
    promote.add_argument("--canvas", required=True)
    promote.add_argument("--style", required=True)
    promote.add_argument("--receipts", nargs="+", required=True, help="compare outputs, oldest first")
    promote.set_defaults(func=cmd_promote)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
