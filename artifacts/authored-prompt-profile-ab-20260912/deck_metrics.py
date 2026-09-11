"""Automatic metrics for one A/B deck (PROTOCOL.md section 5.2).

Inputs are files the series already produces: the terminal status
(``03-final-status.json``), the rendered ``slides/sNN.{html,metrics.json}``
from ``render_live_deck.py``, and the attempt-timing receipt under
``app_data/authored-quality-receipts``. Nothing is generated or written to the
database.

    python deck_metrics.py <deck-dir> [--receipts <app_data/authored-quality-receipts>]

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
    if not job_id:
        return {}
    candidates = sorted(receipts_dir.glob(f"job-{job_id}*-attempt-timing.json"))
    if not candidates:
        return {}
    document = json.loads(candidates[-1].read_text(encoding="utf-8"))
    entries = document.get("receipts") or {}
    values = list(entries.values()) if isinstance(entries, dict) else list(entries)
    if not values:
        return {}
    last = values[-1]
    usage = (last.get("providerUsage") or {}).get("totals") or {}
    stages = last.get("stages") or {}
    return {
        "attempt": last.get("attemptNumber"),
        "outcome": last.get("outcome"),
        "wall_ms": last.get("wallMillis"),
        "stages_ms": {k: (v.get("ms") if isinstance(v, dict) else v) for k, v in stages.items()},
        "logical_calls": usage.get("logicalCalls"),
        "physical_calls": usage.get("physicalCalls"),
        "retry_calls": usage.get("retryCalls"),
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        "latency_ms": usage.get("latencyMs"),
        "receipt_file": candidates[-1].name,
    }


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

    # The attempt-timing receipt is named by the generation job id, which the
    # terminal status carries as its own top-level "id"; the acceptance body
    # (02-accepted.json) carries the same id when the series saved it.
    accepted_path = deck / "02-accepted.json"
    accepted = json.loads(accepted_path.read_text(encoding="utf-8-sig")) if accepted_path.exists() else {}
    job_id = next((value for value in (
        *(_walk(status, "job_id") + _walk(status, "jobId")), status.get("id"),
        accepted.get("id"), accepted.get("job_id"), accepted.get("task_id"),
    ) if value), None)
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
