#!/usr/bin/env python3
"""Repeatable evaluation of SVG template generation: run a request N times, measure each deck.

One run is one noisy sample (the same input gave 3 to 7 fallback pages), so a change is judged on
several runs of a fixed input, never on one. Each run records stability (quality gate, fallback pages,
pages right on the first draft, why the others needed a repair), what the pages are made of (flow shapes,
native tables, charts, icons) and, given the source text, how much of it survived.

    run_eval.py run --payload request.json --runs 3 --label baseline --out results/
    run_eval.py compare results/baseline.json results/with-policy.json
    run_eval.py matrix --templates tpl-a.json tpl-b.json --runs 3 --label baseline --out results/   # inputs x templates
    run_eval.py decide results/baseline.json results/with-policy.json                             # paired verdict
    run_eval.py matrix ... --resume          # after a crash: keep the completed runs of the saved result, do the rest
    run_eval.py recover --templates ... --label baseline --dsn ... --app-dir app/ --after-dsn <clone source>

``request.json`` is a ``POST /presentation/generate/async`` body (it carries the template pin from
``register_template.py``). A variant that is a server setting (an environment switch) is measured by restarting
the backend with it and running again under another ``--label``. The database is read directly for the
per-page attempts; point ``--dsn`` (or ``EVAL_DATABASE_DSN``) at the backend's database.

``--judge-references refs/`` also scores every deck against reference pages of the template with the vision
judge in ``judge.py`` (``judge_<axis>``, ``judge_overall`` and ``judge_version`` fields, 1 to 5; it also needs
``--judge-anchor-high`` and ``--judge-anchor-low``).
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
from recovery import (atomic_write_json, load_existing, map_job_to_cell, newest_per_cell,  # noqa: E402
                      plan_resume)

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


def _make_judge(args: argparse.Namespace):
    if not args.judge_references:
        return None
    from judge import judge_from_options

    options = {key[len("judge_"):]: value for key, value in vars(args).items() if key.startswith("judge_")}
    return judge_from_options(args.judge_references, options)


def _make_judge_for(args: argparse.Namespace, selection: dict | None, cache: dict):
    """The judge of one cell's selection (built once per distinct references + anchors), or None."""
    if selection is None:
        return None
    key = (selection["references"], tuple(selection["anchor_high"]), tuple(selection["anchor_low"]))
    if key not in cache:
        from judge import judge_from_options

        options = {k[len("judge_"):]: v for k, v in vars(args).items() if k.startswith("judge_")}
        options.update(anchor_high=",".join(selection["anchor_high"]), anchor_low=",".join(selection["anchor_low"]))
        cache[key] = judge_from_options(selection["references"], options)
    return cache[key]


def _deck_record(client: httpx.Client | None, conn, judge, args: argparse.Namespace, data: dict, source: str | None,
                 cell: str | None, selection: dict | None, number: int, seconds: float, tag: str = "",
                 parts: dict[str, str] | None = None) -> dict:
    """The record of one completed generation (``data`` is the task's result data), measured from the database.
    The PPTX parts come from ``parts`` when given, else from the API when ``--pptx`` is set."""
    extra = {"cell": cell} if cell else {}
    record = measure_deck(conn, data["presentation_id"])
    texts = record.pop("_texts")
    if source is not None:
        kept = retention(source, texts)
        record["words_kept"], record["numbers_kept"] = (round(kept["words"], 3), round(kept["numbers"], 3))
    if parts is None and args.pptx and client is not None:
        parts = _export_parts(client, data["presentation_id"], data.get("export_operation_id"), 240)
    record.update(package_objects(parts) if parts else {})
    if judge is not None:
        from judge import judge_presentation, print_deck, run_fields

        renders = Path(args.judge_renders) / cell if args.judge_renders and cell else (
            Path(args.judge_renders) if args.judge_renders else None)
        judged = judge_presentation(conn, data["presentation_id"], judge, renders)
        print_deck(f"{tag}run {number} judge", judged)
        record.update(run_fields(judged), judge=judged)
        if selection is not None:
            record["judge_anchors"] = {"references": selection["references"], "anchor_high": judge.anchor_names["high"],
                                       "anchor_low": judge.anchor_names["low"], "sources": selection["sources"]}
    record.update(run=number, completed=True, seconds=round(seconds),
                  gate_passed=(data.get("quality_pages") or {}).get("passed"),
                  gate_flagged=(data.get("quality_pages") or {}).get("flagged"),
                  presentation_id=data["presentation_id"], **extra)
    print(f"{tag}run {number}: {record['seconds']}s gate {record['gate_passed']}/{record['gate_flagged']} "
          f"fallback {record['fallback_pages']} first-try {record['first_try_ok']}/{record['pages']} "
          f"flow {record['pages_with_flow_shapes']} pages tables {record.get('tables', '-')} "
          f"repairs {record['repair_causes']}", flush=True)
    return record


def _sample_runs(client: httpx.Client, conn, judge, args: argparse.Namespace, payload: dict, source: str | None,
                 cell: str | None, selection: dict | None = None, first: int = 1, count: int | None = None,
                 on_run=None) -> list[dict]:
    """``count`` (default ``args.runs``) generations of one request numbered from ``first``, each measured; every
    record carries ``cell`` when given. ``on_run(runs_so_far)`` is called after each run (to save progress)."""
    runs: list[dict] = []
    for number in range(first, first + (args.runs if count is None else count)):
        tag = f"{cell} " if cell else ""
        status, state, seconds = _generate(client, payload, args.timeout)
        extra = {"cell": cell} if cell else {}
        if state != "completed":
            detail = (status or {}).get("error") or state
            print(f"{tag}run {number}: {state} {detail}", flush=True)
            runs.append({"run": number, "completed": False, "state": str(state), **extra})
        else:
            runs.append(_deck_record(client, conn, judge, args, status["data"], source, cell, selection, number,
                                     seconds, tag))
        if on_run is not None:
            on_run(runs)
    return runs


def _write_result(args: argparse.Namespace, judge, runs: list[dict], requested: int, judges: dict | None = None,
                  quiet: bool = False, **extra) -> dict:
    done = [item for item in runs if item.get("completed")]
    result = {"label": args.label, "runs": runs, "summary": summarize(done),
              "completed_runs": len(done), "requested_runs": requested, **extra}
    if judge is not None:
        costs = [item["judge"]["cost_estimate_usd"] for item in done if "judge" in item]
        result.update(judge_settings=judge.settings(),
                      judge_cost_estimate_usd=None if None in costs else round(sum(costs), 4))
    if judges:
        result["judge_settings_by_template"] = {name: item.settings() for name, item in judges.items()}
    atomic_write_json(Path(args.out) / f"{args.label}.json", result)
    if not quiet:
        print_summary(result)
    return result


def _client(args: argparse.Namespace) -> httpx.Client:
    return httpx.Client(base_url=args.base, timeout=120, headers=dict(h.split("=", 1) for h in args.header))


def run(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    source = Path(args.source).read_text(encoding="utf-8") if args.source else None
    conn = _db(args.dsn)
    judge = _make_judge(args)
    with _client(args) as client:
        runs = _sample_runs(client, conn, judge, args, payload, source, None,
                            on_run=lambda so_far: _write_result(args, judge, so_far, args.runs, quiet=True,
                                                                payload=args.payload))
    _write_result(args, judge, runs, args.runs, payload=args.payload)


def matrix(args: argparse.Namespace) -> None:
    """One variant label over input x template cells, ``--runs`` runs per cell (cell id on every run record)."""
    from request_builder import INPUTS, build_cells, judge_selection, list_inputs, load_template_spec

    names = [name.strip() for name in args.inputs.split(",")] if args.inputs else list_inputs()
    specs = [load_template_spec(path) for path in args.templates]
    cells = build_cells(names, specs)
    for cell in cells:
        cell["judge"] = judge_selection(cell["spec"], args.judge_references, args.judge_anchor_high,
                                        args.judge_anchor_low)
    if args.dry_run:
        for cell in cells:
            print(cell["cell"], json.dumps({k: v for k, v in cell["payload"].items() if k != "content"},
                                           ensure_ascii=False))
            print("   judge", json.dumps(cell["judge"], ensure_ascii=False) if cell["judge"] else "none")
        return
    conn = _db(args.dsn)
    cache: dict = {}
    judges: dict = {}
    for cell in cells:
        judge = _make_judge_for(args, cell["judge"], cache)
        if judge is not None:
            judges.setdefault(cell["template"], judge)
    first = next(iter(judges.values()), None)
    ids = [cell["cell"] for cell in cells]
    requested = args.runs * len(cells)
    kept: list[dict] = []
    todo = {cell: (1, args.runs) for cell in ids}
    if args.resume:
        existing = load_existing(Path(args.out) / f"{args.label}.json", args.label)
        if existing:
            kept, todo = plan_resume(existing.get("runs", []), ids, args.runs)
            print(f"resume: {len(kept)} completed runs kept, cells still to run: {sorted(todo) or 'none'}", flush=True)
    runs: list[dict] = list(kept)

    def save(extra_runs: list[dict], quiet: bool = True) -> None:
        _write_result(args, first, runs + extra_runs, requested, judges=judges, cells=ids, quiet=quiet)

    with _client(args) as client:
        for cell in cells:
            if cell["cell"] not in todo:
                continue
            start, count = todo[cell["cell"]]
            source = (INPUTS / f"{cell['input']}.txt").read_text(encoding="utf-8")
            runs += _sample_runs(client, conn, _make_judge_for(args, cell["judge"], cache), args, cell["payload"],
                                 source, cell["cell"], cell["judge"], first=start, count=count, on_run=save)
    save([], quiet=False)


def _pptx_from_store(conn, app_dir: Path, presentation_id: str, operation_id: str | None) -> dict[str, str]:
    """The slide XML parts of the deck's PPTX export read straight from the local artifact store under
    ``app_dir`` ({} when there is none). The stored bytes are checked against the artifact's recorded sha256."""
    import hashlib

    with conn.cursor() as cursor:
        cursor.execute(
            "select a.storage_key, a.sha256 from workspace_artifacts a join workspace_operations o on o.id = a.operation_id"
            " where a.presentation_id = %s and a.kind = 'export.pptx' and a.status = 'ready' and o.state = 'succeeded'"
            " and (%s::text is null or o.id::text = %s) order by a.created_at desc limit 1",
            (presentation_id, operation_id, operation_id))
        row = cursor.fetchone()
    if not row:
        return {}
    path = Path(app_dir) / "public-artifacts" / row[0]
    if not path.is_file():
        return {}
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row[1]:
        raise SystemExit(f"artifact {path} does not match its recorded sha256")
    with zipfile.ZipFile(BytesIO(data)) as archive:
        return {name: archive.read(name).decode("utf-8", "ignore") for name in archive.namelist()
                if name.startswith("ppt/slides/slide")}


def recover(args: argparse.Namespace) -> None:
    """Rebuild the result file of a matrix whose process died, from the completed decks in the database."""
    from request_builder import INPUTS, build_cells, judge_selection, list_inputs, load_template_spec

    names = [name.strip() for name in args.inputs.split(",")] if args.inputs else list_inputs()
    specs = [load_template_spec(path) for path in args.templates]
    cells = build_cells(names, specs)
    for cell in cells:
        cell["judge"] = judge_selection(cell["spec"], args.judge_references, args.judge_anchor_high,
                                        args.judge_anchor_low)
    inputs = {name: (INPUTS / f"{name}.txt").read_text(encoding="utf-8") for name in names}
    conn = _db(args.dsn)
    after = args.after
    if args.after_dsn:
        with _db(args.after_dsn) as other, other.cursor() as cursor:
            cursor.execute("select max(created_at) from presentations")
            after = cursor.fetchone()[0]
    elif after:
        import datetime

        after = datetime.datetime.fromisoformat(after)
    with conn.cursor() as cursor:
        cursor.execute(
            "select j.presentation_id::text, j.request_payload, t.data, p.created_at, t.created_at, t.updated_at"
            " from presentation_generation_jobs j join async_presentation_generation_tasks t on t.id = j.task_id"
            " join presentations p on p.id = j.presentation_id"
            " where j.state = 'published' and t.status = 'completed' order by p.created_at")
        rows = cursor.fetchall()
    candidates = []
    for pid, payload, data, created, task_created, task_updated in rows:
        cell = map_job_to_cell(payload, cells, inputs)
        if cell and isinstance(data, dict) and data.get("presentation_id") == pid:
            candidates.append({"cell": cell, "created_at": created, "data": data,
                               "seconds": (task_updated - task_created).total_seconds()})
    chosen = newest_per_cell(candidates, after)
    print(f"recover: {len(rows)} completed decks, cutoff {after}, matched cells {sorted(chosen)}", flush=True)
    cache: dict = {}
    judges: dict = {}
    runs: list[dict] = []
    ids = [cell["cell"] for cell in cells]
    for cell in cells:
        judge = _make_judge_for(args, cell["judge"], cache)
        if judge is not None:
            judges.setdefault(cell["template"], judge)
        item = chosen.get(cell["cell"])
        if not item:
            print(f"{cell['cell']}: nothing to recover", flush=True)
            continue
        parts = _pptx_from_store(conn, args.app_dir, item["data"]["presentation_id"],
                                 item["data"].get("export_operation_id")) if args.app_dir else None
        runs.append(_deck_record(None, conn, judge, args, item["data"], inputs[cell["input"]], cell["cell"],
                                 cell["judge"], 1, item["seconds"], f"{cell['cell']} ", parts))
        _write_result(args, next(iter(judges.values()), None), runs, args.runs * len(cells), judges=judges,
                      cells=ids, quiet=True)
    _write_result(args, next(iter(judges.values()), None), runs, args.runs * len(cells), judges=judges, cells=ids)


def decide_command(args: argparse.Namespace) -> None:
    from decide import decide, format_table, judge_versions, unpaired_cells

    left, right = (json.loads(Path(path).read_text(encoding="utf-8")) for path in (args.a, args.b))
    only_a, only_b = unpaired_cells(left, right)
    if only_a or only_b:
        print(f"warning: cells left out (present in one result only): A {only_a} B {only_b}")
    versions = [sorted(judge_versions(result)) for result in (left, right)]
    if len(set(versions[0]) | set(versions[1])) > 1:
        print(f"warning: judge versions differ (A {versions[0]} B {versions[1]}); judge scores are not comparable")
    report = decide(left, right)
    print(format_table(report, left["label"], right["label"]))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


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
    from decide import judge_versions

    versions = [sorted(judge_versions(result)) for result in (left, right)]
    if len(set(versions[0]) | set(versions[1])) > 1:
        print(f"warning: judge versions differ ({versions[0]} vs {versions[1]}); judge_* rows are not comparable")


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
    matrix_parser = sub.add_parser("matrix", help="one variant over input x template cells, R runs per cell")
    matrix_parser.add_argument("--templates", nargs="+", required=True,
                               help="template manifests (JSON: id, pin, canvas_emu, optional frame_map, optional judge "
                                    "{references, anchor_high, anchor_low} that overrides the --judge-* flags)")
    matrix_parser.add_argument("--inputs", help="comma-separated input names (default: every inputs/requests/*.json)")
    matrix_parser.add_argument("--runs", type=int, default=3, help="runs per cell")
    matrix_parser.add_argument("--label", required=True)
    matrix_parser.add_argument("--out", default="eval-results")
    matrix_parser.add_argument("--resume", action="store_true",
                               help="load the existing result file of this label and run only the cells still short")
    matrix_parser.add_argument("--dry-run", action="store_true", help="print the cells' requests and stop")
    matrix_parser.add_argument("--pptx", action="store_true")
    matrix_parser.add_argument("--base", default="http://127.0.0.1:8000")
    matrix_parser.add_argument("--dsn", default=os.environ.get("EVAL_DATABASE_DSN", DEFAULT_DSN))
    matrix_parser.add_argument("--header", action="append", default=[])
    matrix_parser.add_argument("--timeout", type=float, default=1500)
    matrix_parser.add_argument("--judge-references")
    matrix_parser.add_argument("--judge-renders")
    add_judge_options(matrix_parser, prefix="judge-")
    matrix_parser.set_defaults(handler=matrix)
    recover_parser = sub.add_parser("recover", help="rebuild the result file of a matrix from the completed decks "
                                                    "already in the database (newest per cell)")
    recover_parser.add_argument("--templates", nargs="+", required=True)
    recover_parser.add_argument("--inputs")
    recover_parser.add_argument("--runs", type=int, default=1, help="runs per cell the matrix asked for")
    recover_parser.add_argument("--label", required=True)
    recover_parser.add_argument("--out", default="eval-results")
    recover_parser.add_argument("--app-dir", help="the backend's app-data directory: PPTX objects are read from its "
                                                  "local artifact store (omit to skip them)")
    recover_parser.add_argument("--after-dsn", help="database this one was cloned from: only decks newer than its "
                                                    "newest presentation count")
    recover_parser.add_argument("--after", help="ISO timestamp: only decks created after it count")
    recover_parser.add_argument("--dsn", default=os.environ.get("EVAL_DATABASE_DSN", DEFAULT_DSN))
    recover_parser.add_argument("--judge-references")
    recover_parser.add_argument("--judge-renders")
    recover_parser.set_defaults(pptx=False, header=[])
    add_judge_options(recover_parser, prefix="judge-")
    recover_parser.set_defaults(handler=recover)
    decide_parser = sub.add_parser("decide", help="paired verdict per metric of result B against result A")
    decide_parser.add_argument("a")
    decide_parser.add_argument("b")
    decide_parser.add_argument("--json-out")
    decide_parser.set_defaults(handler=decide_command)
    compare_parser = sub.add_parser("compare", help="print two result files side by side")
    compare_parser.add_argument("a")
    compare_parser.add_argument("b")
    compare_parser.set_defaults(handler=compare)
    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
