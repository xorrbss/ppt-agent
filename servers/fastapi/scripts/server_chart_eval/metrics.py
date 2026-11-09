"""Chart metrics for one generated svg deck, and per-arm summary tables (stdlib only).

Reads a deck directory produced by run_matrix.py (<dir>/app/fake-llm.db, <dir>/backend.log and the
exported PPTX under <dir>/app/public-artifacts/) read-only and writes <dir>/metrics.json. The generic
collection (provider cost, stage timings, slides, PNG-free) is the existing live-measurement kit's
collect_run.collect; this module adds the chart view on top of it:

  charts.declared_final      native chart objects of the published slides (slide content nativeObjects)
  charts.server / author     of those, drawn by the server renderer (declaration record with a ``render``
                             marker) vs drawn by the author model and bound as a native chart
  charts.dropped             declarations dropped before publication, by reason (final bound record and
                             every author attempt's receipt)
  pptx.native_charts         c:chart graphic frames per slide / chart parts with their type
  per page                   outcome (clean | flagged-published | svg_fallback_page), failure kinds of the
                             first attempt, of every attempt and of the published page, repair attempts
  v5                         gated (non-trusted) charts and the paint evidence that proved them

CLI:  metrics.py <deck-dir>            recompute and print <deck-dir>/metrics.json
      metrics.py --summarize <dir>     per-arm table over every <dir>/*/metrics.json
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "svg_live_measurement"))

import collect_run  # noqa: E402

CHART_TYPE_TAGS = ("barChart", "bar3DChart", "lineChart", "line3DChart", "pieChart", "pie3DChart",
                   "doughnutChart", "areaChart", "area3DChart", "scatterChart", "radarChart", "bubbleChart",
                   "stockChart", "ofPieChart")
FALLBACK_MARKER = "svg-fallback-page"


def _loads(value):
    return collect_run._json(value)


def _kinds(failures) -> list[str]:
    """Failure kind labels; a chart failure carries its reason (``native_requested_chart_missing:why``)."""
    out = []
    for item in failures or []:
        if not isinstance(item, dict) or not item.get("kind"):
            continue
        reason = item.get("reason")
        out.append(f"{item['kind']}:{reason}" if reason and item["kind"] == "native_requested_chart_missing"
                   else item["kind"])
    return out


def pptx_charts(pptx: Path | None) -> dict:
    """Native chart graphic frames per slide, resolved through the slide relationships."""
    if pptx is None:
        return {"available": False}
    per_slide: dict[int, list[str]] = {}
    parts = Counter()
    with zipfile.ZipFile(pptx) as archive:
        names = set(archive.namelist())
        for name in names:
            match = re.fullmatch(r"ppt/slides/slide(\d+)\.xml", name)
            if not match:
                continue
            number = int(match.group(1))
            xml = archive.read(name).decode("utf-8", "replace")
            ids = re.findall(r"<c:chart\b[^>]*\br:id=\"([^\"]+)\"", xml)
            types = []
            rels_name = f"ppt/slides/_rels/slide{number}.xml.rels"
            rels = archive.read(rels_name).decode("utf-8", "replace") if rels_name in names else ""
            targets = {}
            for rel in re.findall(r"<Relationship\b[^>]*>", rels):
                rid = re.search(r"\bId=\"([^\"]+)\"", rel)
                target = re.search(r"\bTarget=\"([^\"]+)\"", rel)
                if rid and target:
                    targets[rid.group(1)] = target.group(1)
            for rid in ids:
                target = targets.get(rid, "")
                part = "ppt/" + target.lstrip("./").removeprefix("../") if target else ""
                chart_xml = archive.read(part).decode("utf-8", "replace") if part in names else ""
                found = [tag for tag in CHART_TYPE_TAGS if f"<c:{tag}>" in chart_xml or f"<c:{tag} " in chart_xml]
                kind = found[0] if found else "unknown"
                types.append(kind)
                parts[kind] += 1
            if types:
                per_slide[number] = types
        chart_parts = sum(1 for name in names if re.fullmatch(r"ppt/charts/chart\d+\.xml", name))
    return {"available": True, "chart_parts": chart_parts, "graphic_frames": sum(len(v) for v in per_slide.values()),
            "by_type": dict(parts), "per_slide_1based": {str(k): v for k, v in sorted(per_slide.items())}}


def _declarations_of(payload: dict) -> tuple[list[dict], list[dict]]:
    record = payload.get("nativeDeclarations") or {}
    return list(record.get("objects") or []), list(record.get("dropped") or [])


def _attempt_dropped(attempt: dict) -> list[dict]:
    receipt = attempt.get("receipt") if isinstance(attempt.get("receipt"), dict) else {}
    declared = receipt.get("native_declarations") if isinstance(receipt.get("native_declarations"), dict) else {}
    return [d for d in declared.get("dropped") or [] if isinstance(d, dict)]


def chart_view(db: sqlite3.Connection, pptx: dict) -> dict:
    flagged = {}
    presentation = db.execute("SELECT theme FROM presentations ORDER BY rowid DESC").fetchone()
    theme = _loads(presentation["theme"]) if presentation else {}
    svg_theme = (theme or {}).get("svg") or {}
    for entry in svg_theme.get("flaggedSlides") or []:
        if isinstance(entry, dict) and isinstance(entry.get("index"), int):
            flagged[entry["index"]] = entry
    snapshot = svg_theme.get("styleSnapshot") or {}
    checkpoints = {}
    for row in db.execute("SELECT slide_index, svg_checkpoint FROM authored_slide_checkpoints"):
        checkpoints[row["slide_index"]] = _loads(row["svg_checkpoint"]) or {}

    pages, totals = [], Counter()
    dropped_reasons, first_kinds, attempt_kinds, final_kinds = Counter(), Counter(), Counter(), Counter()
    v5_gated = v5_proven = 0
    pptx_per_slide = (pptx or {}).get("per_slide_1based") or {}
    for row in db.execute('SELECT "index", content, properties, svg_content FROM slides ORDER BY "index"'):
        index = row["index"]
        content = _loads(row["content"]) or {}
        properties = _loads(row["properties"]) or {}
        svg = row["svg_content"] or ""
        payload = checkpoints.get(index, {})
        declared_objects, final_dropped = _declarations_of(payload)
        server_ids = {o["id"] for o in declared_objects if o.get("render")}
        charts = [o for o in ((content.get("nativeObjects") or {}).get("objects") or []) if o.get("kind") == "chart"]
        chart_rows = [{"id": c.get("id"), "type": (c.get("payload") or {}).get("chartType"),
                       "grouping": (c.get("payload") or {}).get("grouping"),
                       "series": len((c.get("payload") or {}).get("series") or []),
                       "points": len((c.get("payload") or {}).get("categories") or []),
                       "origin": "server" if c.get("id") in server_ids else "author"} for c in charts]
        attempts = [a for a in payload.get("attempts") or [] if isinstance(a, dict) and a.get("kind") == "provider"]
        per_attempt = [_kinds((a.get("quality") or {}).get("failures")) for a in attempts]
        attempt_dropped = [d for a in attempts for d in _attempt_dropped(a)]
        for d in final_dropped:
            dropped_reasons[d.get("reason", "?")] += 1
        # An attempt's drop is a drop at that attempt even when a later attempt fixed it.
        attempt_drop_reasons = Counter(d.get("reason", "?") for d in attempt_dropped)
        initial = _kinds((payload.get("initial_quality") or {}).get("failures"))
        published_failures = _kinds((payload.get("quality") or {}).get("failures"))
        fallback = FALLBACK_MARKER in svg
        entry = flagged.get(index)
        outcome = "svg_fallback_page" if fallback else (entry.get("remakeOutcome") if entry else "clean")
        first_kinds.update(initial)
        for kinds in per_attempt:
            attempt_kinds.update(kinds)
        final_kinds.update(published_failures if not fallback else [])
        evidence = properties.get("svgNativeChartPaint") if isinstance(properties, dict) else None
        paint = (evidence or {}).get("paint") or {}
        gated = [c["id"] for c in chart_rows if c["origin"] == "author"] if snapshot and \
            "v5" in str(snapshot.get("native_requirements_revision", "")).lower() else []
        proven = [i for i in (evidence or {}).get("chartIds") or [] if paint.get("passed") is True and i in gated]
        v5_gated += len(gated)
        v5_proven += len(proven)
        pptx_here = pptx_per_slide.get(str(index + 1), [])
        totals["declared_final"] += len(chart_rows)
        totals["server"] += sum(1 for c in chart_rows if c["origin"] == "server")
        totals["author"] += sum(1 for c in chart_rows if c["origin"] == "author")
        totals["pptx_native"] += len(pptx_here)
        totals["exported_as_drawing"] += max(0, len(chart_rows) - len(pptx_here)) if pptx.get("available") else 0
        pages.append({
            "index": index, "role": content.get("role") or payload.get("role"), "outcome": outcome,
            "fallback": fallback, "flag_reasons": (entry or {}).get("reasons"),
            "provider_attempts": len(attempts), "charts": chart_rows,
            "dropped_final": final_dropped, "dropped_in_attempts": dict(attempt_drop_reasons),
            "first_attempt_failure_kinds": initial, "attempt_failure_kinds": per_attempt,
            "published_failure_kinds": published_failures if not fallback else [],
            "pptx_native_charts": pptx_here,
            "v5": {"gated": gated, "proven": proven, "paint_passed": paint.get("passed"),
                   "paint_reason": paint.get("reason")} if gated or evidence else None})
        totals["dropped_in_attempts"] += sum(attempt_drop_reasons.values())
        for reason, count in attempt_drop_reasons.items():
            dropped_reasons[f"(attempt) {reason}"] += count
    return {
        "snapshot": {"server_chart_render": snapshot.get("server_chart_render"),
                     "native_requirements_revision": snapshot.get("native_requirements_revision")},
        "totals": {**totals, "dropped_final": sum(1 for p in pages for _ in p["dropped_final"]),
                   "fallback_pages": sum(p["fallback"] for p in pages),
                   "flagged_published_pages": sum(1 for p in pages if p["outcome"] not in ("clean", "svg_fallback_page")),
                   "pages_with_charts": sum(1 for p in pages if p["charts"]),
                   "v5_gated": v5_gated, "v5_paint_proven": v5_proven},
        "dropped_by_reason": dict(dropped_reasons), "first_attempt_failure_kinds": dict(first_kinds),
        "all_attempt_failure_kinds": dict(attempt_kinds), "published_failure_kinds": dict(final_kinds),
        "pages": pages}


def operations(db: sqlite3.Connection) -> dict:
    rows = db.execute("SELECT kind, state, error_code, started_at, completed_at FROM workspace_operations "
                      "ORDER BY created_at").fetchall()
    out = defaultdict(list)
    for row in rows:
        out[row["kind"]].append({"state": row["state"], "error": row["error_code"],
                                 "s": collect_run._seconds(row["started_at"], row["completed_at"])})
    export = out.get("export.pptx", [])
    verify = out.get("svg.chart.verify", [])
    return {"export_runs": export, "export_s_total": round(sum(e["s"] or 0 for e in export), 1),
            "export_succeeded": sum(e["state"] == "succeeded" for e in export),
            "chart_verify_runs": verify, "chart_verify_s_total": round(sum(v["s"] or 0 for v in verify), 1)}


def collect_deck(deck: Path, meta: dict) -> dict:
    """Compute and write <deck>/metrics.json. Never raises: a broken deck records its error."""
    deck = Path(deck)
    result: dict = dict(meta)
    try:
        generic = collect_run.collect(deck, dict(meta))  # also writes summary.json, svg/, deck.pptx
    except Exception as error:  # noqa: BLE001 - a failed deck must still leave a metrics record
        generic = {}
        result["collect_error"] = f"{type(error).__name__}: {error}"
    for key in ("cost_usd", "tokens", "stages", "provider_call_rows", "retry_calls", "failed_calls", "receipt_calls",
                "wall_s_operation", "queue_wait_s", "stage_wall_s", "gate", "flagged_slides", "chart_advisories",
                "grammar_400", "provider_errors", "generate_state", "generate_error", "slides", "first_attempt_failures",
                "repairs", "authoring_profile"):
        if key in generic:
            result[key] = generic[key]
    result["wall_s_generate"] = generic.get("wall_s_operation")
    pptx_path = deck / "deck.pptx"
    pptx = {"available": False}
    if pptx_path.is_file():
        try:
            pptx = pptx_charts(pptx_path)
        except (zipfile.BadZipFile, OSError) as error:
            pptx = {"available": False, "error": str(error)}
    result["pptx"] = pptx
    db_path = deck / "app" / "fake-llm.db"
    if db_path.is_file():
        try:
            with collect_run._db(deck / "app") as db:
                result["ops"] = operations(db)
                result["charts"] = chart_view(db, pptx)
        except sqlite3.Error as error:
            result["metrics_error"] = f"sqlite: {error}"
    result["export_s"] = (result.get("ops") or {}).get("export_s_total")
    result["chart_totals"] = (result.get("charts") or {}).get("totals")
    (deck / "metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str))
    return result


def _fmt(value, digits=1):
    if value is None:
        return "-"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def summarize(root: Path) -> str:
    root = Path(root)
    decks = []
    for path in sorted(root.glob("*/metrics.json")):
        try:
            decks.append(json.loads(path.read_text()))
        except ValueError:
            continue
    by_arm: dict[str, list[dict]] = defaultdict(list)
    for deck in decks:
        by_arm[deck.get("arm", "?")].append(deck)
    lines = []
    header = ["cell", "state", "slides", "cost$", "gen_s", "exp_s", "decl", "srv", "auth", "dropped", "pptx_ch",
              "as_draw", "fallback", "flagged", "1st-fail", "repairs"]
    lines.append("per deck")
    lines.append(" | ".join(header))
    for deck in decks:
        t = deck.get("chart_totals") or {}
        lines.append(" | ".join(_fmt(x) for x in (
            deck.get("cell"), deck.get("client_state"), deck.get("slides"), deck.get("cost_usd"),
            deck.get("wall_s_generate"), deck.get("export_s"), t.get("declared_final"), t.get("server"),
            t.get("author"), t.get("dropped_in_attempts"), (deck.get("pptx") or {}).get("graphic_frames"),
            t.get("exported_as_drawing"), t.get("fallback_pages"), t.get("flagged_published_pages"),
            deck.get("first_attempt_failures"), deck.get("repairs"))))
    lines.append("")
    lines.append("per arm (sums over completed decks unless noted; med = median)")
    lines.append(" | ".join(["arm", "decks", "ok", "cost$ med", "cost$ sum", "gen_s med", "exp_s med", "declared", "server",
                              "author", "dropped(attempts)", "pptx native", "drawn instead", "fallback pg",
                              "flagged pg", "v5 gated", "v5 proven"]))
    for arm, group in sorted(by_arm.items()):
        ok = [d for d in group if d.get("client_state") == "completed"]
        def total(key, ok=ok):
            return sum((d.get("chart_totals") or {}).get(key, 0) for d in ok)
        costs = [d["cost_usd"] for d in ok if d.get("cost_usd") is not None]
        gens = [d["wall_s_generate"] for d in ok if d.get("wall_s_generate") is not None]
        exps = [d["export_s"] for d in ok if d.get("export_s") is not None]
        lines.append(" | ".join(_fmt(x) for x in (
            arm, len(group), len(ok), median(costs) if costs else None, sum(costs) if costs else None,
            median(gens) if gens else None, median(exps) if exps else None, total("declared_final"), total("server"),
            total("author"), total("dropped_in_attempts"), sum((d.get("pptx") or {}).get("graphic_frames") or 0 for d in ok),
            total("exported_as_drawing"), total("fallback_pages"), total("flagged_published_pages"),
            total("v5_gated"), total("v5_paint_proven"))))
    lines.append("")
    lines.append("failure kinds per arm (first attempt | all attempts | published) and drop reasons")
    for arm, group in sorted(by_arm.items()):
        agg = [Counter(), Counter(), Counter(), Counter()]
        for d in group:
            c = d.get("charts") or {}
            for counter, key in zip(agg, ("first_attempt_failure_kinds", "all_attempt_failure_kinds",
                                          "published_failure_kinds", "dropped_by_reason"), strict=True):
                counter.update(c.get(key) or {})
        lines.append(f"[{arm}]")
        for label, counter in zip(("first", "all attempts", "published", "dropped"), agg, strict=True):
            lines.append(f"  {label}: " + (", ".join(f"{k} x{v}" for k, v in counter.most_common()) or "none"))
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--summarize":
        print(summarize(Path(sys.argv[2])))
    elif len(sys.argv) == 2:
        previous = {}
        existing = Path(sys.argv[1]) / "metrics.json"
        if existing.is_file():
            previous = {k: v for k, v in json.loads(existing.read_text()).items()
                        if k in ("cell", "prompt", "arm", "run", "mode", "client_state", "wall_s_client",
                                 "followup_wait_s", "wall_s_cell", "arm_env")}
        print(json.dumps(collect_deck(Path(sys.argv[1]), previous), ensure_ascii=False, indent=1, default=str))
    else:
        raise SystemExit(__doc__)
