"""Per-stage driver timing breakdown, read from a deck's own run_report.json
(`DriverRun`'s per-stage timings, see `raster_deck_run.py`).

**2026-09-25 fix:** the old `analysis_duration_seconds` field name implied
it measured only the raster-analysis stage; it was actually the sum of
EVERY driver stage (upload, snapshot, presentation create, analysis,
review, generate, evidence fetch, export x2). Renamed to `total_seconds`
(same formula, unchanged value) and broken out into the stages an auditor
actually cares about: `analysis_wait_seconds` (the `raster_analysis_wait`
stage alone), `generation_seconds` (`generate_async`), `export_pptx_seconds`
/ `export_pdf_seconds` (the sum of that format's request/follow/download
sub-stages). `suite_compare.py` maps old summaries' `analysis_duration_seconds`
onto `total_seconds` so a stored old run still compares.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_EXPORT_PPTX_STAGES = ("export_pptx_request", "export_pptx_follow", "export_pptx_download")
_EXPORT_PDF_STAGES = ("export_pdf_request", "export_pdf_follow", "export_pdf_download")

TIMING_FIELDS = (
    "total_seconds", "analysis_wait_seconds", "generation_seconds",
    "export_pptx_seconds", "export_pdf_seconds",
)


def _sum_or_none(timings: dict, stages: tuple) -> Optional[float]:
    present = [timings[s] for s in stages if s in timings]
    return round(sum(present), 1) if present else None


def stage_durations(run_report_path: Optional[Path]) -> dict:
    """All fields None when `run_report_path` is missing (older/failed
    runs, or a run that never reached that stage)."""
    if not run_report_path or not Path(run_report_path).exists():
        return dict.fromkeys(TIMING_FIELDS)
    rr = json.loads(Path(run_report_path).read_text(encoding="utf-8"))
    timings = {t["stage"]: t["seconds"] for t in rr.get("timings", [])}
    return {
        "total_seconds": round(sum(timings.values()), 1) if timings else None,
        "analysis_wait_seconds": timings.get("raster_analysis_wait"),
        "generation_seconds": timings.get("generate_async"),
        "export_pptx_seconds": _sum_or_none(timings, _EXPORT_PPTX_STAGES),
        "export_pdf_seconds": _sum_or_none(timings, _EXPORT_PDF_STAGES),
    }


def analysis_stage_totals(analysis: Optional[dict]) -> Optional[dict]:
    """Deck totals per analysis stage, from `analysis["stage_timings"]` when
    present -- an optional field (not produced by this codebase as of
    2026-09-25; being added separately) expected to hold per-page/per-stage
    seconds. Tolerates three shapes so this doesn't have to be revisited the
    moment the producer's exact format lands: `{stage: seconds}` (already a
    deck-level total), `{stage: [seconds, ...]}` (one entry per page --
    summed here), or `{"totals": {stage: seconds}, ...}` (nested; the
    "totals" key is used directly). Returns None when `analysis` is None or
    carries no `stage_timings` at all -- distinguishes "not available" from
    "available and empty"."""
    if not analysis:
        return None
    raw = analysis.get("stage_timings")
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("totals"), dict):
        raw = raw["totals"]
    totals = {}
    for stage, value in raw.items():
        if isinstance(value, (int, float)):
            totals[stage] = round(value, 1)
        elif isinstance(value, list) and all(isinstance(v, (int, float)) for v in value):
            totals[stage] = round(sum(value), 1)
    return totals or None
