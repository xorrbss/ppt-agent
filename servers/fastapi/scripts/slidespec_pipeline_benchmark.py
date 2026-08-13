"""Replay the real SlideSpec review pipeline against an operational presentation.

The benchmark is intentionally dry-run only: repair is forced into shadow mode and
the executor's benchmark hook suppresses shadow-artifact persistence.  Cold runs
clear every process-local review cache before replay; warm runs retain them.  The
reported total percentile is deck wall-clock time, not a sum of per-slide timings.
"""

from __future__ import annotations

import os
import time
from typing import Any, Mapping
import uuid


DEFAULT_PRESENTATION_ID = "15f91f1d-66d7-491d-abbb-c02481358b96"


from scripts.slidespec_benchmark_metrics import (
    _clear_review_caches,
    _funnel_metric,
    _stage_usage,
    _summary,
)



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
    from scripts.slidespec_benchmark_cli import benchmark_main

    benchmark_main(
        run_benchmark=run_benchmark,
        summary_only=_summary_only,
        default_presentation_id=DEFAULT_PRESENTATION_ID,
    )


if __name__ == "__main__":
    main()
# ruff: noqa: E402, F401
