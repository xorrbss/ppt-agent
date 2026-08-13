"""Replay the real SlideSpec review pipeline against an operational presentation.

The benchmark is intentionally dry-run only: repair is forced into shadow mode and
the executor's benchmark hook suppresses shadow-artifact persistence.  Cold runs
clear every process-local review cache before replay; warm runs retain them.  The
reported total percentile is deck wall-clock time, not a sum of per-slide timings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import statistics
import time
from typing import Any, Mapping
import uuid


DEFAULT_PRESENTATION_ID = "15f91f1d-66d7-491d-abbb-c02481358b96"


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


def _slide_fingerprint(slides: list[Any]) -> str:
    from services.slidespec_pipeline_performance import digest_parts

    return digest_parts(
        "slidespec-benchmark-original/1",
        [
            {
                "id": str(slide.id),
                "index": slide.index,
                "html": slide.html_content,
                "content": slide.content,
                "properties": slide.properties,
            }
            for slide in slides
        ],
    )


async def _load_presentation(
    presentation_id: uuid.UUID,
) -> tuple[Any, list[Any]]:
    from sqlalchemy import select

    from models.sql.presentation import PresentationModel
    from models.sql.slide import SlideModel
    from services.database import async_session_maker

    async with async_session_maker() as session:
        presentation = await session.get(PresentationModel, presentation_id)
        if presentation is None:
            raise RuntimeError(f"presentation not found: {presentation_id}")
        rows = await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index, SlideModel.id)
        )
        return presentation, list(rows.scalars().all())


async def _one_replay(
    presentation_id: uuid.UUID,
    owner_id: object | None,
    positions: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    from sqlalchemy import select

    from models.sql.slide import SlideModel
    from services.database import async_session_maker
    from services.slidespec_authored_executor import execute_slidespec_authored_review

    async with async_session_maker() as session:
        rows = await session.execute(
            select(SlideModel)
            .where(SlideModel.presentation == presentation_id)
            .order_by(SlideModel.index, SlideModel.id)
        )
        slides = list(rows.scalars().all())
        started = time.perf_counter()
        execution = await execute_slidespec_authored_review(
            presentation_id=presentation_id,
            slides=slides,
            positions=positions or tuple(range(len(slides))),
            opt_in=True,
            owner_id=owner_id,
            session=session,
            dry_run=True,
        )
        outer_wall_ms = (time.perf_counter() - started) * 1000
    metrics = execution.data.get("metrics", {})
    solver = metrics.get("solver", {})
    stage = dict(metrics.get("stage_timings_ms", {}))
    stage["slide_total_sum"] = float(metrics.get("total_ms", 0.0))
    triggered_slides = []
    for raw in execution.data.get("slide_results", ()):
        if not isinstance(raw, Mapping) or not raw.get("trigger"):
            continue
        outcomes = [
            {
                "plan_id": str(outcome.get("plan_id", "")),
                "source": str(outcome.get("source", "")),
                "render_status": str(outcome.get("render_status", "")),
                "validation_status": str(outcome.get("validation_status", "")),
                "reason_codes": [
                    str(code) for code in outcome.get("reason_codes", ()) if code
                ],
                "target_delta_reasons": [
                    str(code)
                    for code in outcome.get("reason_codes", ())
                    if str(code).startswith("acceptance.target_issue")
                ],
                "diagnostics": dict(outcome.get("diagnostics", {}))
                if isinstance(outcome.get("diagnostics"), Mapping)
                else {},
            }
            for outcome in raw.get("outcomes", ())
            if isinstance(outcome, Mapping)
        ]
        triggered_slides.append(
            {
                "slide_index": int(raw.get("slide_index", -1)),
                "candidate_count": int(raw.get("candidate_count", 0)),
                "accepted": bool(raw.get("accepted")),
                "rejection_reason": raw.get("rejection_reason"),
                "reason_codes": [str(code) for code in raw.get("reason_codes", ())],
                "candidate_rejections": list(raw.get("candidate_rejections", ())),
                "projection_rejections": list(raw.get("projection_rejections", ())),
                "confirmed_issues": [
                    {
                        name: issue.get(name)
                        for name in (
                            "code",
                            "severity",
                            "semantic_id",
                            "qid",
                            "related_semantic_ids",
                            "physical_issue_id",
                        )
                    }
                    for issue in raw.get("confirmed_issues", ())
                    if isinstance(issue, Mapping)
                ],
                "incomplete_measurements": [
                    {
                        name: measurement.get(name)
                        for name in (
                            "scope",
                            "sensor",
                            "sensors",
                            "metric",
                            "stage",
                            "stages",
                            "qid",
                            "qids",
                            "semantic_id",
                            "semantic_ids",
                            "constraint_id",
                            "constraint_ids",
                            "reason_code",
                            "reason_codes",
                            "dependency_refs",
                            "evidence_atom_count",
                        )
                        if measurement.get(name) is not None
                    }
                    for measurement in raw.get("incomplete_measurements", ())
                    if isinstance(measurement, Mapping)
                ],
                "outcomes": outcomes,
            }
        )
    return {
        "deck_wall_ms": float(metrics.get("deck_wall_ms", outer_wall_ms)),
        "outer_wall_ms": round(outer_wall_ms, 3),
        "stage_timings_ms": stage,
        "llm_calls": int(metrics.get("llm_calls", 0)),
        "llm_candidates": int(metrics.get("llm_candidates", 0)),
        "proposal_cache_hits": int(metrics.get("proposal_cache_hits", 0)),
        "proposal_cache_misses": int(metrics.get("proposal_cache_misses", 0)),
        "proposal_preflight_skips": int(
            metrics.get("proposal_preflight_skips", 0)
        ),
        "inspection_cache_hits": int(metrics.get("inspection_cache_hits", 0)),
        "inspection_cache_misses": int(metrics.get("inspection_cache_misses", 0)),
        "pareto_pruned": int(metrics.get("pareto_pruned", 0)),
        "solver_elapsed_ms": float(solver.get("elapsed_ms", 0.0)),
        "solver_branches": int(solver.get("branches", 0)),
        "solver_unknown": int(solver.get("unknown", 0)),
        "solver_unsat_core_size": int(solver.get("unsat_core_size", 0)),
        "candidate_proposed": int(metrics.get("candidate_proposed", 0)),
        "candidate_rendered": int(metrics.get("candidate_rendered", 0)),
        "candidate_accepted": int(metrics.get("candidate_accepted", 0)),
        "candidate_rejected": _funnel_metric(
            metrics, "candidate_rejected", "validation_rejected"
        ),
        "candidate_inconclusive": _funnel_metric(
            metrics, "candidate_inconclusive", "validation_inconclusive"
        ),
        "applied": int(metrics.get("applied", 0)),
        "postverify_passed": int(metrics.get("postverify_passed", 0)),
        "stage_usage": _stage_usage(metrics),
        "claimed_count": len(execution.claimed_positions),
        "issue_slide_count": int(execution.data.get("issue_slide_count", 0)),
        "confirmed_issue_count": int(
            execution.data.get(
                "confirmed_issue_count",
                execution.data.get("issue_slide_count", 0),
            )
        ),
        "incomplete_measurement_count": int(
            execution.data.get("incomplete_measurement_count", 0)
        ),
        "unresolved_confirmed_issue_count": int(
            execution.data.get(
                "unresolved_confirmed_issue_count",
                execution.data.get("remaining_issue_count", 0),
            )
        ),
        "clean_slide_count": int(
            execution.data.get(
                "clean_slide_count",
                max(
                    0,
                    len(execution.claimed_positions)
                    - int(execution.data.get("issue_slide_count", 0))
                    - int(execution.data.get("incomplete_measurement_count", 0)),
                ),
            )
        ),
        "candidate_count": int(execution.data.get("candidate_count", 0)),
        "fixed_count": int(execution.data.get("fixed_count", 0)),
        "diagnostics": {
            "rejection_reasons": dict(
                execution.data.get("rejection_reasons", {})
            ),
            "remaining_issue_count": int(
                execution.data.get("remaining_issue_count", 0)
            ),
            "remaining_issue_indices": list(
                execution.data.get("remaining_issue_indices", ())
            ),
            "triggered_slides": triggered_slides,
        },
    }


def _phase_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    stage_names = sorted(
        {
            str(stage)
            for run in runs
            for stage in run["stage_timings_ms"]
        }
    )
    operational_names = (
        "solver_elapsed_ms",
        "solver_branches",
        "solver_unknown",
        "solver_unsat_core_size",
        "candidate_proposed",
        "candidate_rendered",
        "candidate_accepted",
        "candidate_rejected",
        "candidate_inconclusive",
        "applied",
        "postverify_passed",
    )
    stage_usage_totals: dict[str, dict[str, float]] = {}
    stage_usage_coverage: dict[str, dict[str, int]] = {}
    for run in runs:
        usage = run.get("stage_usage", {})
        if not isinstance(usage, Mapping):
            continue
        for stage, raw_values in usage.items():
            if not isinstance(raw_values, Mapping):
                continue
            totals = stage_usage_totals.setdefault(str(stage), {})
            coverage = stage_usage_coverage.setdefault(str(stage), {})
            for name, value in raw_values.items():
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                key = str(name)
                totals[key] = round(totals.get(key, 0.0) + float(value), 6)
                coverage[key] = coverage.get(key, 0) + 1

    return {
        "run_count": len(runs),
        "deck_wall_ms": _summary([run["deck_wall_ms"] for run in runs]),
        "stages_ms": {
            stage: _summary(
                [float(run["stage_timings_ms"].get(stage, 0.0)) for run in runs]
            )
            for stage in stage_names
        },
        "operational": {
            name: {
                "total": round(sum(float(run.get(name, 0)) for run in runs), 3),
                **_summary([float(run.get(name, 0)) for run in runs]),
            }
            for name in operational_names
        },
        "funnel": {
            "branch": {
                "branches": sum(int(run.get("solver_branches", 0)) for run in runs),
                "unknown": sum(int(run.get("solver_unknown", 0)) for run in runs),
            },
            "candidate": {
                name: sum(int(run.get(name, 0)) for run in runs)
                for name in (
                    "candidate_proposed",
                    "candidate_rendered",
                    "candidate_accepted",
                    "candidate_rejected",
                    "candidate_inconclusive",
                )
            },
            "review": {
                name: sum(int(run.get(name, 0)) for run in runs)
                for name in (
                    "confirmed_issue_count",
                    "incomplete_measurement_count",
                    "unresolved_confirmed_issue_count",
                    "clean_slide_count",
                )
            },
        },
        "stage_usage": {
            stage: {
                **values,
                "coverage_runs": stage_usage_coverage.get(stage, {}),
            }
            for stage, values in stage_usage_totals.items()
        },
        "totals": {
            key: sum(int(run.get(key, 0)) for run in runs)
            for key in (
                "llm_calls",
                "llm_candidates",
                "proposal_cache_hits",
                "proposal_cache_misses",
                "proposal_preflight_skips",
                "inspection_cache_hits",
                "inspection_cache_misses",
                "pareto_pruned",
                "render_requests",
                "render_cache_hits",
                "metrics_cache_hits",
                "chrome_process_launches",
                "session_reuse_hits",
                "session_restarts",
                "page_sessions_opened",
                "page_sessions_closed",
            )
        },
        "runs": runs,
    }


def _summary_only(result: Mapping[str, Any]) -> dict[str, Any]:
    """Drop per-candidate diagnostics while preserving every aggregate/SLO field."""

    compact = dict(result)
    for phase in ("cold", "warm"):
        raw = compact.get(phase)
        if isinstance(raw, Mapping):
            compact[phase] = {
                str(key): value for key, value in raw.items() if key != "runs"
            }
    return compact


async def run_benchmark(
    *,
    presentation_id: uuid.UUID,
    cold_runs: int,
    warm_runs: int,
    expected_slides: int,
    positions: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    # Match server startup so the replay uses the configured provider/model.
    from utils.user_config import refresh_process_runtime_settings

    refresh_process_runtime_settings()
    presentation, original_slides = await _load_presentation(presentation_id)
    if len(original_slides) != expected_slides:
        raise RuntimeError(
            f"expected {expected_slides} slides, found {len(original_slides)}"
        )
    if positions is not None and (
        len(positions) != len(set(positions))
        or any(position < 0 or position >= len(original_slides) for position in positions)
    ):
        raise RuntimeError("diagnostic positions must be unique and within the deck")
    original_fingerprint = _slide_fingerprint(original_slides)

    previous = {
        name: os.environ.get(name)
        for name in (
            "SLIDESPEC_REPAIR_APPLY",
            "SLIDESPEC_REPAIR_ALL_SLIDES",
            "SLIDESPEC_REPAIR_CANARY",
            "SLIDESPEC_KILL_SWITCH",
        )
    }
    os.environ["SLIDESPEC_REPAIR_APPLY"] = "0"
    os.environ["SLIDESPEC_REPAIR_ALL_SLIDES"] = "1"
    os.environ["SLIDESPEC_REPAIR_CANARY"] = "100"
    os.environ["SLIDESPEC_KILL_SWITCH"] = "0"
    cold: list[dict[str, Any]] = []
    warm: list[dict[str, Any]] = []
    try:
        from utils.slide_capture import get_render_runtime_counters

        counter_names = (
            "requests",
            "render_cache_hits",
            "metrics_cache_hits",
            "chrome_process_launches",
            "session_reuse_hits",
            "session_restarts",
            "page_sessions_opened",
            "page_sessions_closed",
        )

        async def measured_replay() -> dict[str, Any]:
            before = get_render_runtime_counters()
            run = await _one_replay(
                presentation_id, presentation.owner_id, positions=positions
            )
            after = get_render_runtime_counters()
            for name in counter_names:
                output_name = "render_requests" if name == "requests" else name
                run[output_name] = int(after.get(name, 0)) - int(before.get(name, 0))
            return run

        for _ in range(cold_runs):
            _clear_review_caches()
            cold.append(await measured_replay())

        # The final cold run is the explicit warm-up.  Do not reset here: warm
        # sample 1 must observe the exact cache state produced by a full replay.
        for _ in range(warm_runs):
            warm.append(await measured_replay())
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    _, final_slides = await _load_presentation(presentation_id)
    final_fingerprint = _slide_fingerprint(final_slides)
    if final_fingerprint != original_fingerprint:
        raise RuntimeError("dry-run benchmark changed the source presentation")

    return {
        "schema": "slidespec-pipeline-benchmark/2",
        "presentation_id": str(presentation_id),
        "slide_count": len(original_slides),
        "positions": list(positions) if positions is not None else "all",
        "dry_run": True,
        "source_unchanged": True,
        "cold": _phase_summary(cold),
        "warm": _phase_summary(warm),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--presentation-id", default=DEFAULT_PRESENTATION_ID)
    parser.add_argument("--cold-runs", type=int, default=10)
    parser.add_argument("--warm-runs", type=int, default=10)
    parser.add_argument("--expected-slides", type=int, default=90)
    parser.add_argument(
        "--positions",
        help="Comma-separated zero-based slide positions for a bounded diagnostic replay",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Omit per-candidate run diagnostics from stdout/output JSON",
    )
    args = parser.parse_args()
    if args.cold_runs < 0 or args.warm_runs < 0 or args.cold_runs + args.warm_runs < 1:
        parser.error("run counts must be non-negative with at least one replay")

    positions = (
        tuple(int(value.strip()) for value in args.positions.split(",") if value.strip())
        if args.positions
        else None
    )
    result = asyncio.run(
        run_benchmark(
            presentation_id=uuid.UUID(args.presentation_id),
            cold_runs=args.cold_runs,
            warm_runs=args.warm_runs,
            expected_slides=args.expected_slides,
            positions=positions,
        )
    )
    payload = _summary_only(result) if args.summary_only else result
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
