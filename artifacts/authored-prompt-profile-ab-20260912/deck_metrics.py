"""Automatic metrics for one A/B deck (PROTOCOL.md section 5.2).

Inputs are files the series already produces: the terminal status
(``03-final-status.json``), the rendered ``slides/sNN.{html,metrics.json}``
from ``render_live_deck.py``, and the attempt-timing receipt under
``app_data/authored-quality-receipts``. Nothing is generated or written to the
database.

    python deck_metrics.py <deck-dir> [--receipts <app_data/authored-quality-receipts>] [--job <id>]

Body-slide medians exclude the TOC and chapter-cover indexes the 2026-09-05
series page excluded (1, 2, 5, 9, 13, 16, 19).
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

from services.authored_composition_policy import blocking_safety_issues  # noqa: E402
from services.authored_flag_classification import (  # noqa: E402
    classify_flagged_slides,
    review_pages_for,
)
from utils.authored_dom_quality import inspect_authored_dom  # noqa: E402

EXCLUDED_INDEXES = {1, 2, 5, 9, 13, 16, 19}
_DROP = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def visible_chars(html: str) -> int:
    """Visible text length with <style>/<script> bodies removed (2026-09-07 correction)."""
    text = _TAG.sub(" ", _DROP.sub(" ", html))
    return len(re.sub(r"\s+", "", text))


def _px(value: Any) -> float | None:
    try:
        return float(str(value).rstrip("px"))
    except (TypeError, ValueError):
        return None


def slide_metrics(html: str, metrics: dict[str, Any]) -> dict[str, Any]:
    elements = [e for e in metrics.get("elements") or [] if isinstance(e, dict)]
    text_owners = [e for e in elements if str(e.get("owned_text") or "").strip()]
    sizes = [px for e in text_owners if (px := _px((e.get("computed_style") or {}).get("fontSize"))) is not None]
    blocking = {}
    for issue in blocking_safety_issues(inspect_authored_dom(html, metrics)):
        name = str(getattr(issue, "category", "?"))
        blocking[name] = blocking.get(name, 0) + 1
    return {
        "visible_chars": visible_chars(html),
        "text_leaves": len(text_owners),
        "font_min_px": min(sizes) if sizes else None,
        "font_median_px": statistics.median(sizes) if sizes else None,
        "blocking": blocking,
    }


def receipt_for(receipts_dir: Path, job_id: str | None) -> dict[str, Any]:
    """Per-call provider usage for one generation job.

    The durable per-call source for the ordinary async path is the
    ``provider_usage_receipts`` table (``job_id`` = ``presentation_generation_jobs.id``,
    one row per logical call with stage, retry class, outcome, latency and
    tokens). The ``job-*-attempt-timing.json`` files under
    ``authored-quality-receipts`` belong to the workspace lane and do not cover
    this path (0 of 171 matched a generation job on 2026-09-12); one is read
    only as an optional extra when it happens to exist.
    """
    if not job_id:
        return {}
    summary: dict[str, Any] = {}
    by_stage: dict[str, dict[str, Any]] = {}
    for stage, retry_class, outcome, latency, tokens_in, tokens_out, cached, requests in _usage_rows(job_id):
        bucket = by_stage.setdefault(stage or "?", {
            "calls": 0, "requests": 0, "retries": 0, "failed": 0,
            "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0, "latencies_ms": [],
        })
        bucket["calls"] += 1
        bucket["requests"] += int(requests or 0)
        bucket["retries"] += 1 if (retry_class or "initial") != "initial" else 0
        bucket["failed"] += 1 if outcome != "succeeded" else 0
        bucket["input_tokens"] += int(tokens_in or 0)
        bucket["output_tokens"] += int(tokens_out or 0)
        bucket["cached_tokens"] += int(cached or 0)
        if isinstance(latency, (int, float)):
            bucket["latencies_ms"].append(float(latency))
    for bucket in by_stage.values():
        latencies = sorted(bucket.pop("latencies_ms"))
        bucket["latency_p50_ms"] = latencies[len(latencies) // 2] if latencies else None
        bucket["latency_p90_ms"] = (
            latencies[min(len(latencies) - 1, int(len(latencies) * 0.9))] if latencies else None
        )
    if by_stage:
        summary["provider_usage"] = by_stage
        summary["author_calls"] = by_stage.get("author", {}).get("calls", 0)
    candidates = sorted(receipts_dir.glob(f"job-{job_id}*-attempt-timing.json")) if receipts_dir.exists() else []
    if candidates:
        document = json.loads(candidates[-1].read_text(encoding="utf-8"))
        entries = document.get("receipts") or {}
        values = list(entries.values()) if isinstance(entries, dict) else list(entries)
        if values:
            last = values[-1]
            stages = last.get("stages") or {}
            summary["attempt_timing"] = {
                "wall_ms": last.get("wallMillis"),
                "stages_ms": {k: (v.get("ms") if isinstance(v, dict) else v) for k, v in stages.items()},
                "receipt_file": candidates[-1].name,
            }
    return summary


def _usage_rows(job_id: str) -> list[tuple[Any, ...]]:
    """Rows of ``provider_usage_receipts`` for a job id given with or without hyphens."""
    import os

    import psycopg

    url_file = os.environ.get("LOCAL_FIT_DB_URL_FILE")
    if not url_file:
        return []
    raw = job_id.replace("-", "")
    uuid = f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}" if len(raw) == 32 else job_id
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", Path(url_file).read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        return c.execute(
            "select stage, retry_class, outcome, latency_ms, input_tokens, output_tokens,"
            " cached_tokens, request_count from provider_usage_receipts"
            " where job_id = %s order by created_at",
            (uuid,),
        ).fetchall()


def job_id_for(task_id: str | None, presentation_id: str | None) -> str | None:
    """The generation job id (UUID) for a deck, read-only through the jobs table.

    The terminal status carries the task id, not the job id; ``LOCAL_FIT_DB_URL_FILE``
    must point at the database URL file.
    """
    import os

    import psycopg

    url_file = os.environ.get("LOCAL_FIT_DB_URL_FILE")
    if not url_file or not (task_id or presentation_id):
        return None
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", Path(url_file).read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        row = None
        if task_id:
            row = c.execute("select id from presentation_generation_jobs where task_id = %s"
                            " order by created_at desc limit 1", (task_id,)).fetchone()
        if row is None and presentation_id:
            row = c.execute("select id from presentation_generation_jobs where presentation_id = %s"
                            " order by created_at desc limit 1", (presentation_id,)).fetchone()
    return str(row[0]) if row else None



def _walk(obj: Any, key: str, acc: list[Any] | None = None) -> list[Any]:
    acc = [] if acc is None else acc
    if isinstance(obj, dict):
        for name, value in obj.items():
            if name == key:
                acc.append(value)
            _walk(value, key, acc)
    elif isinstance(obj, list):
        for value in obj:
            _walk(value, key, acc)
    return acc


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    deck = Path(argv[0])
    receipts_dir = (
        Path(argv[argv.index("--receipts") + 1]) if "--receipts" in argv
        else Path("../../app_data/authored-quality-receipts")
    )
    status = json.loads((deck / "03-final-status.json").read_text(encoding="utf-8-sig"))
    gate = (_walk(status, "quality_gate_receipt") or [{}])[0]
    flagged = gate.get("flaggedSlides") or []
    residual = gate.get("residualIssues") or []
    classified = classify_flagged_slides(flagged, residual_failures=residual)
    per_slide: dict[int, dict[str, Any]] = {}
    for html_path in sorted((deck / "slides").glob("s*.html")):
        index = int(html_path.stem[1:])
        metrics_path = html_path.with_suffix(".metrics.json")
        metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
        per_slide[index] = slide_metrics(html_path.read_text(encoding="utf-8"), metrics)
    body = [m for i, m in per_slide.items() if i not in EXCLUDED_INDEXES]

    def median_of(key: str) -> float | None:
        values = [m[key] for m in body if isinstance(m.get(key), (int, float))]
        return statistics.median(values) if values else None

    accepted_path = deck / "02-accepted.json"
    accepted = json.loads(accepted_path.read_text(encoding="utf-8-sig")) if accepted_path.exists() else {}
    task_id = next((value for value in (status.get("id"), accepted.get("id"), accepted.get("task_id"))
                    if isinstance(value, str) and value.startswith("task-")), None)
    presentation_id = (_walk(status, "presentation_id") or _walk(status, "presentationId") or [None])[0]
    job_id = (argv[argv.index("--job") + 1] if "--job" in argv
              else job_id_for(task_id, str(presentation_id) if presentation_id else None))
    summary = {
        "deck": deck.name,
        "slides": len(per_slide),
        "review_pages": len(review_pages_for(classified)),
        "raw_flags": len(flagged),
        "residual_issues": len(residual),
        "quality_pass": gate.get("qualityPass"),
        "blocking_on_published": sum(sum(m["blocking"].values()) for m in per_slide.values()),
        "body_median": {
            "visible_chars": median_of("visible_chars"),
            "text_leaves": median_of("text_leaves"),
            "font_median_px": median_of("font_median_px"),
        },
        "font_min_px": min((m["font_min_px"] for m in per_slide.values() if m["font_min_px"]), default=None),
        "receipt": receipt_for(receipts_dir, str(job_id) if job_id else None),
        "per_slide": per_slide,
    }
    (deck / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "per_slide"}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
