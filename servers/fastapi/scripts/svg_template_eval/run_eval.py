#!/usr/bin/env python3
"""Repeatable evaluation of SVG template generation: run a request N times, measure each deck.

One run is one noisy sample (the same input gave 3 to 7 fallback pages), so a change is judged on
several runs of a fixed input, never on one. Each run records stability (quality gate, fallback pages,
pages right on the first draft, why the others needed a repair), what the pages are made of (flow shapes,
native tables, charts, icons) and, given the source text, how much of it survived.

    run_eval.py run --payload request.json --runs 3 --label baseline --out results/
    run_eval.py compare results/baseline.json results/with-policy.json

``request.json`` is a ``POST /presentation/generate/async`` body (it carries the template pin from
``register_template.py``). A variant that is a server setting (an environment switch) is measured by restarting
the backend with it and running again under another ``--label``. The database is read directly for the
per-page attempts; point ``--dsn`` (or ``EVAL_DATABASE_DSN``) at the backend's database.

``--judge-references refs/`` also scores every deck against reference pages of the template with the vision
judge in ``judge.py`` (``judge_<axis>`` and ``judge_overall`` fields, 1 to 5).
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import package_objects, retention, summarize, svg_structure  # noqa: E402

API = "/api/v1/ppt"
DEFAULT_DSN = "postgresql://postgres@127.0.0.1:5433/chap"
_TERMINAL_BAD = {"error", "failed", "cancelled"}


def _db(dsn: str):
    try:
        import psycopg
    except ImportError as error:  # the backend's own environment has it
        raise SystemExit("psycopg is required: run this with the backend's virtualenv") from error
    return psycopg.connect(dsn, autocommit=True)


def _generate(client: httpx.Client, payload: dict, timeout: float) -> tuple[dict | None, str, float]:
    started = time.time()
    response = client.post(f"{API}/presentation/generate/async", json=payload)
    if response.status_code >= 400:
        hint = (" (probably the template's stored files are gone: register it again, ideally into a fresh database"
                " and app-data directory; see README)" if response.status_code == 500 else "")
        return None, f"rejected HTTP {response.status_code}{hint}", 0.0
    task = response.json()["id"]
    while True:
        status = client.get(f"{API}/presentation/status/{task}").json()
        state = status.get("status")
        if state not in {"pending", "running", "processing", "queued"}:
            return status, state, time.time() - started
        if time.time() - started > timeout:
            return status, "timeout", time.time() - started
        time.sleep(10)


def _find_operation(node, operation_id: str, found: list[dict]) -> None:
    """Collect the timeline item of ``operation_id`` (a dict with its status) into ``found``."""
    if isinstance(node, dict):
        if node.get("operation_id") == operation_id and "status" in node:
            found.append(node)
            return
        for value in node.values():
            _find_operation(value, operation_id, found)
    elif isinstance(node, list):
        for value in node:
            _find_operation(value, operation_id, found)


def _export_parts(client: httpx.Client, presentation_id: str, operation_id: str | None, wait: float) -> dict[str, str]:
    """The slide XML parts of the chained PPTX export, or {} when it is not available."""
    if not operation_id:
        return {}
    deadline = time.time() + wait
    while time.time() < deadline:
        timeline = client.get(f"{API}/workspace/presentations/{presentation_id}/timeline").json()
        found: list[dict] = []
        _find_operation(timeline, operation_id, found)
        if found and found[0]["status"] == "succeeded":
            artifact = (found[0].get("result") or {}).get("artifact_id")
            data = client.get(f"{API}/workspace/artifacts/{artifact}/download").content
            with zipfile.ZipFile(BytesIO(data)) as archive:
                return {name: archive.read(name).decode("utf-8", "ignore") for name in archive.namelist()
                        if name.startswith("ppt/slides/slide")}
        if found and found[0]["status"] in _TERMINAL_BAD:
            return {}
        time.sleep(8)
    return {}


def measure_deck(conn, presentation_id: str) -> dict:
    """Stability and structure of one published deck, read from the database."""
    with conn.cursor() as cursor:
        cursor.execute("select theme from presentations where id = %s", (presentation_id,))
        theme = (cursor.fetchone() or [None])[0] or {}
        cursor.execute("select index, svg_content from slides where presentation = %s order by index",
                       (presentation_id,))
        slides = cursor.fetchall()
        cursor.execute("select id from presentation_generation_jobs where presentation_id = %s "
                       "order by created_at desc limit 1", (presentation_id,))
        job = cursor.fetchone()
        checkpoints = []
        if job:
            cursor.execute("select slide_index, svg_checkpoint from authored_slide_checkpoints where job_id = %s "
                           "order by slide_index", (job[0],))
            checkpoints = cursor.fetchall()
    svg = (theme or {}).get("svg") or {}
    vision = (svg.get("advisory") or {}).get("vision") or {}
    excluded = vision.get("unreviewedPages") if vision.get("reason") == "mandatory_evidence_invalid" else []
    first_try, causes = 0, collections.Counter()
    for _, checkpoint in checkpoints:
        attempts = (checkpoint or {}).get("attempts") or []
        if len(attempts) <= 1:
            first_try += 1
        else:
            code = (attempts[1].get("attempt_cause") or {}).get("code")
            if code:
                causes[code.split(":", 1)[-1]] += 1
    structures = [svg_structure(content) for _, content in slides if content]
    totals = {key: sum(item[key] for item in structures) for key in
              ("polygon", "path", "line", "polyline", "rect", "text", "icons", "pictures", "arrow_glyphs", "flow_shapes")}
    pages = len(slides)
    return {"pages": pages, "pages_reviewed_ok": pages - len(svg.get("reviewPages") or []),
            "fallback_pages": len(excluded or []), "first_try_ok": first_try, "retried_pages": len(checkpoints) - first_try,
            "pages_with_flow_shapes": sum(1 for item in structures if item["polygon"] or item["line"] or item["path"]),
            **totals, "repair_causes": dict(causes),
            "_texts": " ".join(text for item in structures for text in item["texts"])}


def run(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    source = Path(args.source).read_text(encoding="utf-8") if args.source else None
    conn = _db(args.dsn)
    judge = None
    if args.judge_references:
        from judge import Judge

        chosen = [name.strip() for name in args.judge_ref_pages.split(",")] if args.judge_ref_pages else None
        judge = Judge(Path(args.judge_references), chosen, model=args.judge_model, effort=args.judge_effort or None,
                      temperature=args.judge_temperature, fallbacks=not args.judge_no_fallbacks,
                      workers=args.judge_workers)
    runs: list[dict] = []
    with httpx.Client(base_url=args.base, timeout=120, headers=dict(h.split("=", 1) for h in args.header)) as client:
        for number in range(1, args.runs + 1):
            status, state, seconds = _generate(client, payload, args.timeout)
            if state != "completed":
                detail = (status or {}).get("error") or state
                print(f"run {number}: {state} {detail}", flush=True)
                runs.append({"run": number, "completed": False, "state": str(state)})
                continue
            data = status["data"]
            record = measure_deck(conn, data["presentation_id"])
            texts = record.pop("_texts")
            if source is not None:
                kept = retention(source, texts)
                record["words_kept"], record["numbers_kept"] = (round(kept["words"], 3), round(kept["numbers"], 3))
            if args.pptx:
                parts = _export_parts(client, data["presentation_id"], data.get("export_operation_id"), 240)
                record.update(package_objects(parts) if parts else {})
            if judge is not None:
                from judge import judge_presentation, print_deck, run_fields

                judged = judge_presentation(conn, data["presentation_id"], judge,
                                            Path(args.judge_renders) if args.judge_renders else None)
                print_deck(f"run {number} judge", judged)
                record.update(run_fields(judged), judge=judged)
            record.update(run=number, completed=True, seconds=round(seconds),
                          gate_passed=(data.get("quality_pages") or {}).get("passed"),
                          gate_flagged=(data.get("quality_pages") or {}).get("flagged"),
                          presentation_id=data["presentation_id"])
            runs.append(record)
            print(f"run {number}: {record['seconds']}s gate {record['gate_passed']}/{record['gate_flagged']} "
                  f"fallback {record['fallback_pages']} first-try {record['first_try_ok']}/{record['pages']} "
                  f"flow {record['pages_with_flow_shapes']} pages tables {record.get('tables', '-')} "
                  f"repairs {record['repair_causes']}", flush=True)
    done = [item for item in runs if item.get("completed")]
    result = {"label": args.label, "runs": runs, "summary": summarize(done), "payload": args.payload,
              "completed_runs": len(done), "requested_runs": args.runs}
    if judge is not None:
        costs = [item["judge"]["cost_estimate_usd"] for item in done if "judge" in item]
        result.update(judge_settings=judge.settings(),
                      judge_cost_estimate_usd=None if None in costs else round(sum(costs), 4))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(result)


def print_summary(result: dict) -> None:
    print(f"\n== {result['label']}: {result['completed_runs']}/{result['requested_runs']} runs completed")
    for key, value in result["summary"].items():
        if key in {"run", "seconds", "pages"} or key.startswith("_"):
            continue
        print(f"   {key:<24} mean {value['mean']:<8} min {value['min']:<6} max {value['max']}")


def compare(args: argparse.Namespace) -> None:
    left, right = (json.loads(Path(path).read_text(encoding="utf-8")) for path in (args.a, args.b))
    keys = sorted(set(left["summary"]) | set(right["summary"]))
    print(f"{'metric':<26}{left['label'][:18]:>20}{right['label'][:18]:>20}   (mean over completed runs)")
    for key in keys:
        if key in {"run", "pages"}:
            continue
        a, b = left["summary"].get(key), right["summary"].get(key)
        print(f"{key:<26}{(a['mean'] if a else '-'):>20}{(b['mean'] if b else '-'):>20}")
    print(f"{'runs completed':<26}{left['completed_runs']:>20}{right['completed_runs']:>20}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="generate N decks from one request and measure each")
    run_parser.add_argument("--payload", required=True, help="generate/async request body (JSON)")
    run_parser.add_argument("--runs", type=int, default=3)
    run_parser.add_argument("--label", required=True)
    run_parser.add_argument("--out", default="eval-results")
    run_parser.add_argument("--source", help="source text, to measure how much of it the deck kept")
    run_parser.add_argument("--pptx", action="store_true", help="also fetch the PPTX and count native tables and charts")
    run_parser.add_argument("--base", default="http://127.0.0.1:8000")
    run_parser.add_argument("--dsn", default=os.environ.get("EVAL_DATABASE_DSN", DEFAULT_DSN))
    run_parser.add_argument("--header", action="append", default=[], help="extra request header, K=V (repeatable)")
    run_parser.add_argument("--timeout", type=float, default=1500, help="seconds to wait for one deck")
    run_parser.add_argument("--judge-references", help="reference page PNGs: also score each deck with judge.py")
    run_parser.add_argument("--judge-renders", help="keep the judged page PNGs here")
    from judge import add_judge_options

    add_judge_options(run_parser, prefix="judge-")
    run_parser.set_defaults(handler=run)
    compare_parser = sub.add_parser("compare", help="print two result files side by side")
    compare_parser.add_argument("a")
    compare_parser.add_argument("b")
    compare_parser.set_defaults(handler=compare)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
