"""Collect one deck's measurements from its harness app-data directory (stdlib only).

Reads <run>/app/fake-llm.db read-only, <run>/backend.log and the exported PPTX, and writes
<run>/summary.json, <run>/svg/sNN.svg (final slide SVGs), <run>/deck.pptx and
<run>/generation_timing_receipt.json. Never reads or prints the provider key.

CLI (normally called by run_deck.py):
  collect_run.py cost <app-dir>      print the logical-call cost so far in USD
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

TERMINAL_OPERATION = {"succeeded", "failed", "cancelled", "dead_lettered"}
GRAMMAR_MARKERS = ("compiled grammar is too large", "schema is too complex")
CALL_COLUMNS = ("stage", "slide_index", "retry_class", "state", "outcome", "estimated_cost_microunits",
                "actual_cost_microunits", "input_tokens", "output_tokens", "cached_input_tokens",
                "cache_write_input_tokens", "latency_ms")


def _db(app: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{app / 'fake-llm.db'}?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def _json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _call_cost(row) -> float:
    cost = row["actual_cost_microunits"]
    return (cost if cost is not None else row["estimated_cost_microunits"] or 0) / 1e6


def running_cost(app: Path) -> float:
    try:
        with _db(app) as db:
            return sum(_call_cost(row) for row in db.execute(
                "SELECT estimated_cost_microunits, actual_cost_microunits FROM public_provider_logical_calls"))
    except sqlite3.Error:
        return 0.0


def wait_export(app: Path, timeout: float, export_as: str = "pptx") -> float:
    """Seconds waited until the chained export operation is terminal (or the timeout)."""
    start = time.time()
    kind = f"export.{export_as}"
    while time.time() - start < timeout:
        try:
            with _db(app) as db:
                states = [row[0] for row in db.execute("SELECT state FROM workspace_operations WHERE kind = ?", (kind,))]
        except sqlite3.Error:
            states = []
        if states and all(state in TERMINAL_OPERATION for state in states):
            break
        if not states and time.time() - start > 90:
            break  # the chain never enqueued an export
        time.sleep(5)
    return round(time.time() - start, 1)


def _seconds(start, end):
    if not start or not end:
        return None

    def parse(value):
        return datetime.fromisoformat(str(value).replace("Z", "+00:00").replace(" ", "T"))
    return round((parse(end) - parse(start)).total_seconds(), 1)


def _find_pptx(app: Path) -> Path | None:
    best = None
    for path in (app / "public-artifacts").rglob("*"):
        if path.is_file() and path.stat().st_size > 1000:
            try:
                with zipfile.ZipFile(path) as archive:
                    if "ppt/presentation.xml" not in archive.namelist():
                        continue
            except (zipfile.BadZipFile, OSError):
                continue
            if best is None or path.stat().st_mtime > best.stat().st_mtime:
                best = path
    return best


def _pptx_native(pptx: Path) -> dict:
    with zipfile.ZipFile(pptx) as archive:
        names = archive.namelist()
        slides = [archive.read(n).decode("utf-8", "replace") for n in names
                  if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
    return {"slides": len(slides),
            "charts": sum(1 for n in names if re.fullmatch(r"ppt/charts/chart\d+\.xml", n)),
            "tables": sum(x.count("<a:tbl>") for x in slides),
            "text_boxes": sum(x.count('txBox="1"') for x in slides)}


def _calls(db) -> dict:
    rows = db.execute(f"SELECT {', '.join(CALL_COLUMNS)} FROM public_provider_logical_calls").fetchall()
    by_stage: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        entry = by_stage[row["stage"]]
        entry["calls"] += 1
        entry["cost_usd"] += _call_cost(row)
        entry["latency_s"] += (row["latency_ms"] or 0) / 1000
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens"):
            entry[key] += row[key] or 0
        if row["retry_class"] and row["retry_class"] != "initial":
            entry["retries"] += 1
        if row["state"] in {"failed", "unavailable"}:
            entry["failed"] += 1
    stages = {name: {k: (round(v, 4) if isinstance(v, float) else v) for k, v in counter.items()}
              for name, counter in sorted(by_stage.items())}

    def total(key):
        return sum(row[key] or 0 for row in rows)
    return {"provider_call_rows": len(rows),
            "retry_calls": sum(1 for row in rows if row["retry_class"] and row["retry_class"] != "initial"),
            "failed_calls": sum(1 for row in rows if row["state"] in {"failed", "unavailable"}),
            "cost_usd": round(sum(_call_cost(row) for row in rows), 4),
            "tokens": {k: total(k) for k in ("input_tokens", "output_tokens", "cached_input_tokens",
                                             "cache_write_input_tokens")},
            "stages": stages,
            "header_calls": sum(s["calls"] for n, s in stages.items() if "header" in n)}


def _pages(db) -> list[dict]:
    pages = []
    try:
        rows = db.execute("SELECT slide_index, role, svg_checkpoint FROM authored_slide_checkpoints "
                          "ORDER BY slide_index").fetchall()
    except sqlite3.Error:
        return pages
    for row in rows:
        payload = _json(row["svg_checkpoint"]) or {}
        attempts = [a for a in payload.get("attempts") or [] if isinstance(a, dict)]
        provider = [a for a in attempts if a.get("kind") == "provider"]
        first = provider[0] if provider else {}
        quality = first.get("quality") if isinstance(first.get("quality"), dict) else {}
        error = (first.get("receipt") or {}).get("validation_error")
        pages.append({
            "index": row["slide_index"], "role": row["role"], "provider_attempts": len(provider),
            "repairs": max(0, len(provider) - 1),
            "first_attempt_failed": bool(error or quality.get("failures") or quality.get("passed") is False),
            "first_validation_error": (error or "")[:200] or None,
            "attempt_causes": [(a.get("attempt_cause") or {}).get("code") for a in provider]})
    return pages


def _log_metrics(log: Path) -> dict:
    spans, grammar, errors = [], 0, 0
    if log.is_file():
        for line in log.read_text(errors="replace").splitlines():
            if "svg_outline_span" in line:
                parts = line.split("svg_outline_span", 1)[1].split()
                spans.append({k: v for k, _, v in (p.partition("=") for p in parts) if v})
            elif line.startswith("[provider-error]"):
                errors += 1
                if any(marker in line.lower() for marker in GRAMMAR_MARKERS):
                    grammar += 1
    return {"outline_spans": spans, "provider_errors": errors, "grammar_400": grammar}


def _timings(receipt: dict) -> dict:
    """Stage wall seconds (and summed-across-pages seconds) plus finer observations.

    ``stages`` holds the public stages, header stages included when the header ran;
    ``observations`` holds the setup.*, render.* and quality.* spans, header ones among them.
    """
    wall, summed = {}, {}
    for name, stage in (receipt.get("stages") or {}).items():
        if isinstance(stage, dict) and stage.get("state") == "observed":
            wall[name] = round((stage.get("wallMillis") or 0) / 1000, 2)
            summed[name] = round((stage.get("durationMillis") or 0) / 1000, 2)
    observed = {name: round((span.get("durationMillis") or 0) / 1000, 2)
                for name, span in (receipt.get("observations") or {}).items() if isinstance(span, dict)}
    header = {k: v for k, v in {**wall, **observed}.items() if "header" in k.lower()}
    return {"stage_wall_s": wall, "stage_sum_s": summed, "observation_s": observed, "header_stage_s": header,
            "queue_wait_s": round((receipt.get("queueWaitMillis") or 0) / 1000, 2),
            "wall_s_receipt": round((receipt.get("wallMillis") or 0) / 1000, 2)}


def collect(run: Path, meta: dict) -> dict:
    app = run / "app"
    out: dict = dict(meta)
    with _db(app) as db:
        out.update(_calls(db))
        ops = {row["kind"]: dict(row) for row in db.execute(
            "SELECT kind, state, error_code, result, created_at, started_at, completed_at FROM workspace_operations")}
        generation = ops.get("presentation.generate") or {}
        result = _json(generation.get("result")) or {}
        receipt = result.get("generation_timing_receipt") or {}
        (run / "generation_timing_receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=1))
        usage = (receipt.get("providerUsage") or {}).get("totals") or {}
        out.update(generate_state=generation.get("state"), generate_error=generation.get("error_code"),
                   wall_s_operation=_seconds(generation.get("started_at"), generation.get("completed_at")),
                   **_timings(receipt),
                   receipt_calls={"logical": usage.get("logicalCalls"), "physical": usage.get("physicalCalls"),
                                  "retry": usage.get("retryCalls")})
        export = ops.get(f"export.{'pdf' if 'export.pdf' in ops and 'export.pptx' not in ops else 'pptx'}") or {}
        out.update(export_state=export.get("state"), export_error=export.get("error_code"),
                   export_s=_seconds(export.get("started_at"), export.get("completed_at")))
        presentation = db.execute("SELECT id, theme FROM presentations ORDER BY rowid DESC").fetchone()
        theme = _json(presentation["theme"]) if presentation else {}
        svg = (theme or {}).get("svg") or {}
        quality_summary = svg.get("qualitySummary") or {}
        snapshot = svg.get("styleSnapshot") or {}
        header = snapshot.get("common_header") or {}
        out.update(
            gate={"status": quality_summary.get("status") or result.get("quality_status"),
                  "pages": quality_summary.get("pageCount"), "passed": quality_summary.get("passedPages"),
                  "flagged": quality_summary.get("flaggedPages"), "review_pages": quality_summary.get("reviewPages"),
                  "result_quality_pages": result.get("quality_pages")},
            quality_summary={k: v for k, v in quality_summary.items() if k != "compositionObservation"},
            vision_review=quality_summary.get("visionReview"),
            chart_advisories=quality_summary.get("chartAdvisories"),
            flagged_slides=svg.get("flaggedSlides"),
            header={"version": header.get("version"), "mode": snapshot.get("common_header_mode"),
                    "eyebrow": (header.get("eyebrow") or {}).get("enabled"),
                    "divider": (header.get("divider") or {}).get("enabled"),
                    "cover_exception": header.get("cover_exception"),
                    "selection": snapshot.get("common_header_selection")},
            authoring_profile=snapshot.get("authoring_profile"))
        slides = db.execute('SELECT "index", svg_content FROM slides ORDER BY "index"').fetchall()
        out["slides"] = len(slides)
        out["fallback_pages"] = [row["index"] for row in slides if "svg-fallback-page" in (row["svg_content"] or "")]
        svg_dir = run / "svg"
        svg_dir.mkdir(exist_ok=True)
        for row in slides:
            (svg_dir / f"s{int(row['index']):02d}.svg").write_text(row["svg_content"] or "", encoding="utf-8")
        pages = _pages(db)
    out["pages"] = pages
    out["first_attempt_failures"] = sum(p["first_attempt_failed"] for p in pages)
    out["repairs"] = sum(p["repairs"] for p in pages)
    out.update(_log_metrics(run / "backend.log"))
    pptx = _find_pptx(app)
    out["pptx"] = None
    if pptx is not None:
        shutil.copy(pptx, run / "deck.pptx")
        out["pptx"] = _pptx_native(pptx)
    (run / "summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return out


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "cost":
        print(running_cost(Path(sys.argv[2])))
    else:
        raise SystemExit(__doc__)
