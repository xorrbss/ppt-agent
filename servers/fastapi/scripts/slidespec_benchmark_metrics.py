"""Metric normalization and cache-reset helpers for SlideSpec benchmarks."""

from __future__ import annotations

import statistics
from typing import Any, Mapping

def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _summary(values: list[float]) -> dict[str, float]:
    return {
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "p99": round(_percentile(values, 0.99), 3),
        "min": round(min(values), 3) if values else 0.0,
        "max": round(max(values), 3) if values else 0.0,
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
    }


def _funnel_metric(
    metrics: Mapping[str, Any], public_name: str, executor_name: str
) -> int:
    """Read the executor funnel while preserving benchmark output field names."""

    return int(metrics.get(executor_name, metrics.get(public_name, 0)))


def _optional_number(*values: object) -> float | None:
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _stage_usage(metrics: Mapping[str, Any]) -> dict[str, dict[str, float]]:
    """Normalize cache/token/cost counters without inventing unavailable usage."""

    raw = metrics.get("stage_usage", {})
    raw = raw if isinstance(raw, Mapping) else {}
    normalized: dict[str, dict[str, float]] = {}
    for stage in ("llm", "chrome", "acceptance", "postverify"):
        stage_raw = raw.get(stage, {})
        stage_raw = stage_raw if isinstance(stage_raw, Mapping) else {}
        values = {
            str(key): float(value)
            for key, value in stage_raw.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        if stage == "llm":
            aliases = {
                "input_tokens": (
                    metrics.get("llm_input_tokens"),
                    metrics.get("prompt_tokens"),
                ),
                "output_tokens": (
                    metrics.get("llm_output_tokens"),
                    metrics.get("completion_tokens"),
                ),
                "total_tokens": (metrics.get("llm_total_tokens"),),
                "cost_usd": (metrics.get("llm_cost_usd"), metrics.get("cost_usd")),
            }
            for name, candidates in aliases.items():
                candidate = _optional_number(values.get(name), *candidates)
                if candidate is not None:
                    values[name] = candidate
        normalized[stage] = values
    return normalized


def _clear_review_caches() -> None:
    from services.slidespec_authored_executor import _BASELINE_INSPECTION_CACHE
    from services.slidespec_pipeline_performance import (
        DEFAULT_EVIDENCE_ATOM_CACHE,
        DEFAULT_NEGATIVE_PROPOSAL_CACHE,
    )
    from utils import slide_capture
    from utils.authored_ocr_quality import DEFAULT_CACHE
    from utils.llm_calls import author_vision_qa

    _BASELINE_INSPECTION_CACHE.clear()
    DEFAULT_EVIDENCE_ATOM_CACHE.clear()
    DEFAULT_NEGATIVE_PROPOSAL_CACHE.clear()
    slide_capture.reset_render_runtime(clear_caches=True, retire_session=True)
    DEFAULT_CACHE.entries.clear()
    author_vision_qa._style_comparison_vqa_cache.clear()
    author_vision_qa._style_comparison_vqa_failures.clear()
    author_vision_qa._vqa_cache.clear()
