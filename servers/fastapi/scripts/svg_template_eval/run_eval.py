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
import hashlib
import json
import os
import sys
import time
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metrics import (attempt_accounting, normalized_page_metrics, package_objects, retention,  # noqa: E402
                     structured_data_integrity, summarize, svg_structure, template_text_leaks)
from recovery import atomic_write_json, load_existing  # noqa: E402

API = "/api/v1/ppt"
DEFAULT_DSN = "postgresql://postgres@127.0.0.1:5433/chap"
_TERMINAL_BAD = {"error", "failed", "cancelled"}
RESULT_SCHEMA_VERSION = 2


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _manifest_identity(args: argparse.Namespace, cell: str | None) -> dict:
    """Operator-verified runtime identity, never inferred from this runner's checkout or live environment."""
    path = getattr(args, "identity_manifest", None)
    if not path:
        return {}
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    identity = manifest.get("cells", {}).get(cell or "default", manifest.get("default", {}))
    allowed = ("code_revision", "generation_config_sha256", "policy_revision", "policy_sha256", "input_sha256",
               "request_sha256", "template_pin_sha256")
    return {key: identity[key] for key in allowed if isinstance(identity.get(key), str) and identity[key]
            or key == "policy_revision" and isinstance(identity.get(key), int) and not isinstance(identity[key], bool)}


def _condition_identity(args: argparse.Namespace, payload: dict, cell: str | None,
                        synthetic_input: str | None = None) -> dict:
    identity = _manifest_identity(args, cell)
    result = {key: identity[key] for key in
              ("input_sha256", "request_sha256", "template_pin_sha256", "generation_config_sha256") if key in identity}
    # Matrix inputs are repository-owned fictional fixtures. Arbitrary request/customer text is never hashed here.
    if synthetic_input is not None:
        result["input_sha256"] = hashlib.sha256(synthetic_input.encode()).hexdigest()
        result["request_sha256"] = _digest(payload)
        result["template_pin_sha256"] = _digest(payload.get("svgTemplatePin") or payload.get("svg_template_pin") or {})
    return result


def _judge_identity(judge, judged: dict) -> str | None:
    """Bind actual image bytes, settings, implementation and served models without storing image/customer data."""
    if not judged.get("served_models") or judged.get("mixed_models"):
        return None
    images = {key: [hashlib.sha256(data).hexdigest() for _, data in getattr(judge, key, [])]
              for key in ("high", "low", "context")}
    return _digest({"settings": _safe_judge_settings(judge), "images": images, "served_models": judged["served_models"],
                    "implementation": hashlib.sha256(Path(__file__).with_name("judge.py").read_bytes()).hexdigest()})


def _safe_judge_settings(judge) -> dict:
    return {key: value for key, value in judge.settings().items()
            if key not in {"anchor_high", "anchor_low", "anchor_labels", "reference_pages"}}


def _resume_plan(existing: list[dict], cell_ids: list[str], count: int, retry_failed: bool = False
                 ) -> dict[str, tuple[int, int]]:
    """A quota of attempts; rerunning failures requires a separate explicit authorization flag."""
    todo = {}
    for cell in cell_ids:
        records = [item for item in existing if (item.get("cell") or "default") == cell]
        if any(item.get("completed") not in (True, False) for item in records):
            continue  # The POST/task outcome must first be reconciled; never duplicate uncertain provider work.
        consumed = sum(item.get("completed") is True for item in records) if retry_failed else len(records)
        remaining = max(0, count - consumed)
        if remaining:
            todo[cell] = (max((item.get("run", 0) for item in records), default=0) + 1, remaining)
    return todo


def _canonical_request(payload: dict) -> dict | None:
    """Compare complete public DTOs across wire aliases/defaults and persisted server-only fields."""
    from models.generate_presentation_request import GeneratePresentationRequest

    try:
        request = GeneratePresentationRequest.model_validate({key: value for key, value in payload.items()
                                                               if not key.startswith("_internal_")})
        # This deterministic template admission default is persisted after the initial DTO dump.
        # Other header defaults depend on the admitted policy and are never read from live env here.
        if request.svg_template_mode == "template-redesign" and request.common_header_mode is None:
            request.common_header_mode = "free"
        return request.model_dump(mode="json")
    except (ValueError, TypeError):
        return None


def _db(dsn: str):
    try:
        import psycopg
    except ImportError as error:  # the backend's own environment has it
        raise SystemExit("psycopg is required: run this with the backend's virtualenv") from error
    return psycopg.connect(dsn, autocommit=True)


def _generate(client: httpx.Client, payload: dict, timeout: float, on_task=None) -> tuple[dict | None, str, float]:
    started = time.time()
    response = client.post(f"{API}/presentation/generate/async", json=payload)
    if response.status_code >= 400:
        if response.status_code >= 500 or response.status_code == 408:
            return None, "outcome_unknown", time.time() - started
        return None, f"rejected HTTP {response.status_code}", time.time() - started
    task = response.json()["id"]
    if on_task is not None:
        on_task(task)
    while True:
        status = client.get(f"{API}/presentation/status/{task}").json()
        status["evaluation_task_id"] = task
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
    first_try, causes = 0, collections.Counter()
    attempts_available = set()
    fallback_available, fallback_indexes = set(), set()
    slide_indexes = {index for index, content in slides if content}
    for index, checkpoint in checkpoints:
        checkpoint = checkpoint or {}
        terminal = checkpoint.get("terminal") or {}
        outcome = terminal.get("outcome") or ""
        if index in slide_indexes and (isinstance((checkpoint.get("quality") or {}).get("passed"), bool) or outcome):
            fallback_available.add(index)
            if "fallback" in outcome or "placeholder" in outcome:
                fallback_indexes.add(index)
        attempts = (checkpoint or {}).get("attempts") or []
        if index not in slide_indexes or not attempts:
            continue
        if isinstance((checkpoint.get("quality") or {}).get("passed"), bool) or outcome:
            attempts_available.add(index)
        attempt = attempts[0]
        if (len(attempts) == 1 and not terminal and (checkpoint.get("quality") or {}).get("passed") is True
                and not attempt.get("validation_error") and not (attempt.get("receipt") or {}).get("validation_error")
                and not (attempt.get("receipt") or {}).get("template_fallback")):
            first_try += 1
        elif len(attempts) > 1:
            code = (attempts[1].get("attempt_cause") or {}).get("code")
            if code:
                causes[code.split(":", 1)[-1]] += 1
    structures = [svg_structure(content) for _, content in slides if content]
    totals = {key: sum(item[key] for item in structures) for key in
              ("polygon", "path", "line", "polyline", "rect", "text", "icons", "pictures", "arrow_glyphs", "flow_shapes")}
    pages = len(slides)
    return {"pages": pages, "pages_reviewed_ok": pages - len(svg.get("reviewPages") or []),
            "fallback_pages": len(fallback_indexes) if len(fallback_available) == pages and pages else None,
            "fallback_evidence_pages": len(fallback_available),
            "first_try_ok": first_try if len(attempts_available) == pages and pages else None,
            "attempt_evidence_pages": len(attempts_available),
            "retried_pages": sum(len((checkpoint or {}).get("attempts") or []) > 1 for _, checkpoint in checkpoints),
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


_TEMPLATE_TEXTS: dict[str, str] = {}


def template_text(path: str | None) -> str | None:
    """The text of a template's own deck (a .pptx: every text frame and table cell) or a .txt file, cached.
    Read locally for the leak metric; it is never written to a result."""
    if not path:
        return None
    if path not in _TEMPLATE_TEXTS:
        if path.lower().endswith(".pptx"):
            from pptx import Presentation

            def texts(shapes):
                for shape in shapes:
                    if shape.has_text_frame:
                        yield shape.text_frame.text
                    if getattr(shape, "has_table", False) and shape.has_table:
                        yield from (cell.text for row in shape.table.rows for cell in row.cells)
                    if shape.shape_type == 6:  # group
                        yield from texts(shape.shapes)
            _TEMPLATE_TEXTS[path] = "\n".join(text for slide in Presentation(path).slides for text in texts(slide.shapes))
        else:
            _TEMPLATE_TEXTS[path] = Path(path).read_text(encoding="utf-8")
    return _TEMPLATE_TEXTS[path]


def _deck_record(client: httpx.Client | None, conn, judge, args: argparse.Namespace, data: dict, source: str | None,
                 cell: str | None, selection: dict | None, number: int, seconds: float, tag: str = "",
                 parts: dict[str, str] | None = None, template: str | None = None) -> dict:
    """The record of one completed generation (``data`` is the task's result data), measured from the database.
    The PPTX parts come from ``parts`` when given, else from the API when ``--pptx`` is set."""
    extra = {"cell": cell} if cell else {}
    record = measure_deck(conn, data["presentation_id"])
    texts = record.pop("_texts")
    if source is not None:
        kept = retention(source, texts)
        record["words_kept"], record["numbers_kept"] = (
            round(kept["words"], 3) if kept["words"] is not None else None,
            round(kept["numbers"], 3) if kept["numbers"] is not None else None)
    record["data_integrity"] = structured_data_integrity(None, None)
    record.update(normalized_page_metrics(record))
    if template is not None:
        leaks = template_text_leaks(template, texts, source or "")
        record["template_text_leak_ngrams"], record["template_text_leak_figures"] = leaks["ngrams"], leaks["figures"]
    if parts is None and args.pptx and client is not None:
        try:
            parts = _export_parts(client, data["presentation_id"], data.get("export_operation_id"), 240)
        except Exception as error:
            record["export_error_type"] = type(error).__name__
    record["pptx_evidence"] = "available" if parts else "unavailable"
    record.update(package_objects(parts) if parts else {})
    if judge is not None:
        from judge import judge_presentation, run_fields

        renders = Path(args.judge_renders) / cell if args.judge_renders and cell else (
            Path(args.judge_renders) if args.judge_renders else None)
        try:
            judged = judge_presentation(conn, data["presentation_id"], judge, renders)
            # Persist scores/usage but never judge prose, reasons, anchor names or customer-derived labels.
            record.update(run_fields(judged), judge={key: judged[key] for key in
                          ("cost_estimate_usd", "served_models", "mixed_models", "usage") if key in judged},
                          judge_identity=_judge_identity(judge, judged))
        except Exception as error:
            record["judge_error_type"] = type(error).__name__
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
                 on_run=None, template: str | None = None, condition: dict | None = None,
                 retry_records: list[dict] | None = None) -> list[dict]:
    """``count`` (default ``args.runs``) generations of one request numbered from ``first``, each measured; every
    record carries ``cell`` when given. ``on_run(runs_so_far)`` is called after each run (to save progress)."""
    runs: list[dict] = []
    for number in range(first, first + (args.runs if count is None else count)):
        tag = f"{cell} " if cell else ""
        extra = {"cell": cell} if cell else {}
        record = {"run": number, "attempt_id": uuid.uuid4().hex, "completed": None, "state": "dispatching",
                  "condition_identity": condition or _condition_identity(args, payload, cell),
                  "evaluation_identity": _manifest_identity(args, cell),
                  "expected_pages": payload.get("n_slides"), **extra}
        if retry_records:
            previous = retry_records.pop(0)
            record["retry_of"] = previous.get("attempt_id") or f"legacy:{cell or 'default'}:{previous.get('run')}"
            record["retry_authorization"] = "explicit --retry-failed"
        runs.append(record)
        if on_run is not None:
            on_run(runs)  # Durable record before a POST can spend or become uncertain.

        def task_started(task_id):
            record.update(task_id=task_id, state="running")
            if on_run is not None:
                on_run(runs)

        try:
            status, state, seconds = _generate(client, payload, args.timeout, on_task=task_started)
            record.update(seconds=round(seconds), state=str(state))
            if state == "completed":
                data = status["data"]
                record.update(completed=True, presentation_id=data["presentation_id"], measurement_status="pending")
                if on_run is not None:
                    on_run(runs)  # Generation completion survives export/judge/measurement failure.
                try:
                    record.update(_deck_record(client, conn, judge, args, data, source, cell, selection, number,
                                               seconds, tag, template=template), measurement_status="available")
                except Exception as error:
                    record.update(measurement_status="failed", measurement_error_type=type(error).__name__)
            elif state in _TERMINAL_BAD or str(state).startswith("rejected HTTP"):
                record["completed"] = False
            else:
                record["state"] = "outcome_unknown"
        except Exception as error:
            record.update(state="outcome_unknown", error_type=type(error).__name__)
        print(f"{tag}run {number}: {record['state']}; generation completed={record['completed']}", flush=True)
        if on_run is not None:
            on_run(runs)
        if record.get("completed") is None:
            break
    return runs


def _write_result(args: argparse.Namespace, judge, runs: list[dict], requested: int, judges: dict | None = None,
                  quiet: bool = False, **extra) -> dict:
    done = [item for item in runs if item.get("completed")]
    accounting = attempt_accounting(runs, requested)
    summary = summarize(done)
    for key in ("first_try_ok_rate", "fallback_rate"):
        rate = accounting[key]
        if rate["value"] is not None:
            available = [normalized_page_metrics(item)[key] for item in done if key in normalized_page_metrics(item)]
            summary[key] = {"mean": round(rate["value"], 6), "min": min(available), "max": max(available),
                            "n": rate["available_runs"], "numerator": rate["numerator"], "pages": rate["pages"],
                            "aggregation": "pooled actual pages"}
    result = {"schema_version": RESULT_SCHEMA_VERSION, "label": args.label, "runs": runs, "summary": summary,
              "accounting": accounting,
              "runner_identity": {"scope": "evaluation harness only; backend loading unverified",
                  "source_sha256": {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                    for name in ("run_eval.py", "metrics.py", "decide.py", "request_builder.py", "recovery.py", "judge.py")}},
              "completed_runs": len(done), "requested_runs": requested, **extra}
    if judge is not None:
        costs = [(item.get("judge") or {}).get("cost_estimate_usd") for item in done]
        result.update(judge_settings=_safe_judge_settings(judge),
                      judge_cost_estimate_usd=None if None in costs else round(sum(costs), 4))
    if judges:
        result["judge_settings_by_template"] = {name: _safe_judge_settings(item) for name, item in judges.items()}
    atomic_write_json(Path(args.out) / f"{args.label}.json", result)
    if not quiet:
        print_summary(result)
    return result


def _client(args: argparse.Namespace) -> httpx.Client:
    return httpx.Client(base_url=args.base, timeout=120, headers=dict(h.split("=", 1) for h in args.header))


def run(args: argparse.Namespace) -> None:
    if (Path(args.out) / f"{args.label}.json").exists():
        raise SystemExit("Result already exists; use a new label to preserve attempt history")
    payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    source = Path(args.source).read_text(encoding="utf-8") if args.source else None
    conn = _db(args.dsn)
    judge = _make_judge(args)
    with _client(args) as client:
        runs = _sample_runs(client, conn, judge, args, payload, source, None,
                            on_run=lambda so_far: _write_result(args, judge, so_far, args.runs, quiet=True,
                                                                cells=["default"]),
                            template=template_text(args.template_text))
    _write_result(args, judge, runs, args.runs, cells=["default"])


def matrix(args: argparse.Namespace) -> None:
    """One variant label over input x template cells, ``--runs`` runs per cell (cell id on every run record)."""
    from request_builder import build_cells, input_source_text, judge_selection, list_inputs, load_template_spec

    names = [name.strip() for name in args.inputs.split(",")] if args.inputs else list_inputs()
    specs = [load_template_spec(path) for path in args.templates]
    cells = build_cells(names, specs)
    for cell in cells:
        cell["judge"] = judge_selection(cell["spec"], args.judge_references, args.judge_anchor_high,
                                        args.judge_anchor_low)
    if args.dry_run:
        for cell in cells:
            print(f"{cell['cell']}: requested pages={cell['payload'].get('n_slides')}; "
                  f"judge={'selected' if cell['judge'] else 'none'}")
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
            kept = list(existing.get("runs", []))
            for cell in cells:
                current = _condition_identity(args, cell["payload"], cell["cell"], input_source_text(cell["input"]))
                for item in kept:
                    previous = item.get("condition_identity") or {}
                    if item.get("cell") == cell["cell"] and any(previous[key] != current[key]
                                                                 for key in previous.keys() & current.keys()):
                        raise SystemExit("Resume conditions differ; use a new label to preserve history")
            todo = _resume_plan(kept, ids, args.runs, getattr(args, "retry_failed", False))
            print(f"resume: {len(kept)} attempt records kept, cells still to run: {sorted(todo) or 'none'}", flush=True)
    elif (Path(args.out) / f"{args.label}.json").exists():
        raise SystemExit("Result already exists; use --resume or a new label to preserve attempt history")
    runs: list[dict] = list(kept)

    def save(extra_runs: list[dict], quiet: bool = True) -> None:
        _write_result(args, first, runs + extra_runs, requested, judges=judges, cells=ids, quiet=quiet)

    with _client(args) as client:
        for cell in cells:
            if cell["cell"] not in todo:
                continue
            start, count = todo[cell["cell"]]
            source = input_source_text(cell['input'])
            failures = [item for item in kept if item.get("cell") == cell["cell"] and item.get("completed") is False]
            runs += _sample_runs(client, conn, _make_judge_for(args, cell["judge"], cache), args, cell["payload"],
                                 source, cell["cell"], cell["judge"], first=start, count=count, on_run=save,
                                 template=template_text(cell["spec"].get("template_text")),
                                 condition=_condition_identity(args, cell["payload"], cell["cell"], source),
                                 retry_records=failures if getattr(args, "retry_failed", False) else None)
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
    """Reconcile stored attempts without erasing failures, excluding missing exports, or dispatching a retry."""
    from request_builder import build_cells, input_source_text, judge_selection, list_inputs, load_template_spec

    names = [name.strip() for name in args.inputs.split(",")] if args.inputs else list_inputs()
    specs = [load_template_spec(path) for path in args.templates]
    cells = build_cells(names, specs)
    for cell in cells:
        cell["judge"] = judge_selection(cell["spec"], args.judge_references, args.judge_anchor_high,
                                        args.judge_anchor_low)
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
            "select j.presentation_id::text, j.request_payload, t.data, j.created_at, t.created_at, t.updated_at,"
            " j.task_id, j.state, t.status from presentation_generation_jobs j"
            " left join async_presentation_generation_tasks t on t.id = j.task_id order by j.created_at")
        rows = cursor.fetchall()
    existing = load_existing(Path(args.out) / f"{args.label}.json", args.label)
    cache: dict = {}
    judges: dict = {}
    runs: list[dict] = list((existing or {}).get("runs", []))
    ids = [cell["cell"] for cell in cells]
    matched = 0

    def save():
        _write_result(args, next(iter(judges.values()), None), runs, args.runs * len(cells), judges=judges,
                      cells=ids, quiet=True, recovery_history="all matching durable jobs; pre-job failures may be unavailable")

    for pid, payload, data, created, task_created, task_updated, task_id, job_state, task_state in rows:
        if after is not None and created <= after:
            continue
        canonical = _canonical_request(payload)
        matches = [cell for cell in cells if canonical is not None and _canonical_request(cell["payload"]) == canonical]
        if len(matches) != 1:
            continue
        cell = matches[0]
        previous = next((item for item in runs if item.get("task_id") == str(task_id)
                         or (pid and item.get("presentation_id") == pid)), None)
        if previous and (previous.get("completed") is False or previous.get("completed") is True
                         and previous.get("measurement_status") != "pending"):
            continue
        record = previous if previous is not None else {
            "run": max((item.get("run", 0) for item in runs if item.get("cell") == cell["cell"]), default=0) + 1,
            "attempt_id": f"task:{task_id}", "cell": cell["cell"], "task_id": str(task_id), "recovered": True,
            # Historical runtime identities cannot be inferred from today's harness invocation.
            "evaluation_identity": {}, "condition_identity": {}, "expected_pages": payload.get("n_slides")}
        if previous is None:
            runs.append(record)
        matched += 1
        seconds = (task_updated - task_created).total_seconds() if task_created and task_updated else 0
        completed = task_state == "completed" and isinstance(data, dict) and data.get("presentation_id") == pid
        record.update(completed=True if completed else (False if task_state in _TERMINAL_BAD
                                                        or job_state in _TERMINAL_BAD else None),
                      state=task_state or job_state, seconds=seconds)
        if completed:
            record.update(presentation_id=pid, measurement_status="pending")
        save()
        if not completed:
            continue
        try:
            judge = _make_judge_for(args, cell["judge"], cache)
            if judge is not None:
                judges.setdefault(cell["template"], judge)
            parts = _pptx_from_store(conn, args.app_dir, pid, data.get("export_operation_id")) if args.app_dir else None
            record.update(_deck_record(None, conn, judge, args, data, input_source_text(cell["input"]), cell["cell"],
                                       cell["judge"], record["run"], seconds, parts=parts,
                                       template=template_text(cell["spec"].get("template_text"))),
                          measurement_status="available")
        except Exception as error:
            record.update(measurement_status="failed", measurement_error_type=type(error).__name__)
        save()
    save()
    print(f"recover: retained {len(runs)} attempt records, reconciled {matched}; no generation dispatched", flush=True)


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
    accounting = result.get("accounting") or attempt_accounting(result.get("runs", []), result.get("requested_runs"))
    print(f"   attempts {accounting['attempted_runs']}; failed {accounting['failed_runs']}; "
          f"unknown {accounting['unknown_outcome_runs']}; retries {accounting['retry_attempts']}; "
          f"completion rate {accounting['completion_rate']}")
    for key in ("first_try_ok_rate", "fallback_rate"):
        item = accounting[key]
        print(f"   {key}: {item['value']} ({item['numerator']}/{item['pages']} measured pages; "
              f"{item['missing_runs']} completed decks missing evidence)")
    for key, value in result["summary"].items():
        if key in {"run", "seconds", "pages"} or key.startswith("_"):
            continue
        print(f"   {key:<24} mean {value['mean']:<8} min {value['min']:<6} max {value['max']}")


def compare(args: argparse.Namespace) -> None:
    from decide import decide, format_table

    left, right = (json.loads(Path(path).read_text(encoding="utf-8")) for path in (args.a, args.b))
    print(format_table(decide(left, right), left.get("label", "A"), right.get("label", "B")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run", help="generate N decks from one request and measure each")
    run_parser.add_argument("--payload", required=True, help="generate/async request body (JSON)")
    run_parser.add_argument("--runs", type=int, default=3)
    run_parser.add_argument("--label", required=True)
    run_parser.add_argument("--out", default="eval-results")
    run_parser.add_argument("--identity-manifest", help="sanitized operator-verified runtime/source identity JSON")
    run_parser.add_argument("--source", help="source text, to measure how much of it the deck kept")
    run_parser.add_argument("--template-text", help="the template's own deck (.pptx) or text (.txt), local only:"
                                                    " counts template wording that reached the deck")
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
    matrix_parser.add_argument("--identity-manifest", help="sanitized operator-verified runtime/source identity JSON")
    matrix_parser.add_argument("--resume", action="store_true",
                               help="preserve all attempts and fill only unattempted slots; uncertain outcomes block a cell")
    matrix_parser.add_argument("--retry-failed", action="store_true",
                               help="with --resume, explicitly authorize failed-slot retries with retained lineage")
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
    recover_parser.add_argument("--allow-no-pptx", action="store_true",
                                help="compatibility flag; incomplete exports are now always retained as missing evidence")
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
    if getattr(args, "retry_failed", False) and not getattr(args, "resume", False):
        parser.error("--retry-failed requires --resume")
    if hasattr(args, "runs") and args.runs < 1:
        parser.error("--runs must be positive")
    args.handler(args)


if __name__ == "__main__":
    main()
