from services.slidespec_pipeline_performance import (
    IncrementalInspectionCache,
    adjacent_influence_digests,
    inspection_cache_digest,
)
from scripts.slidespec_pipeline_benchmark import (
    _phase_summary,
    _stage_usage,
    _summary_only,
)


def _inspection_digest(source: str, adjacent: str) -> str:
    return inspection_cache_digest(
        spec_digest="spec",
        content_digest="content",
        source_digest=source,
        font_digest="font",
        browser_digest="chrome-v1",
        adjacent_digest=adjacent,
    )


def test_incremental_review_reuses_unchanged_and_rechecks_dependency_neighbors() -> None:
    before = adjacent_influence_digests(("a", "b", "c", "d", "e"))
    after = adjacent_influence_digests(("a", "b", "changed", "d", "e"))
    cache: IncrementalInspectionCache[str] = IncrementalInspectionCache()
    for index, adjacent in enumerate(before):
        cache.put(_inspection_digest(f"source-{index}", adjacent), f"review-{index}")

    reused = {
        index
        for index, adjacent in enumerate(after)
        if cache.get(_inspection_digest(f"source-{index}", adjacent)) is not None
    }

    assert reused == {0, 4}
    assert set(range(5)) - reused == {1, 2, 3}


def test_stage_usage_records_available_tokens_and_cost_without_fake_zeroes() -> None:
    usage = _stage_usage(
        {
            "stage_usage": {
                "llm": {"cache_hits": 3},
                "chrome": {"cache_hits": 9, "cache_misses": 1},
                "acceptance": {"cache_hits": 2, "cache_misses": 1},
                "postverify": {"cache_hits": 0, "cache_misses": 1},
            },
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "llm_total_tokens": 150,
            "llm_cost_usd": 0.0125,
        }
    )

    assert usage["llm"] == {
        "cache_hits": 3.0,
        "input_tokens": 120.0,
        "output_tokens": 30.0,
        "total_tokens": 150.0,
        "cost_usd": 0.0125,
    }
    assert usage["chrome"] == {"cache_hits": 9.0, "cache_misses": 1.0}
    unavailable = _stage_usage({})
    assert "total_tokens" not in unavailable["llm"]
    assert "cost_usd" not in unavailable["llm"]


def test_phase_summary_reports_branch_unknown_candidate_and_review_funnels() -> None:
    base = {
        "deck_wall_ms": 10.0,
        "stage_timings_ms": {"chrome": 5.0},
        "solver_elapsed_ms": 1.0,
        "solver_branches": 4,
        "solver_unknown": 1,
        "solver_unsat_core_size": 0,
        "candidate_proposed": 2,
        "candidate_rendered": 2,
        "candidate_accepted": 1,
        "candidate_rejected": 1,
        "candidate_inconclusive": 0,
        "applied": 1,
        "postverify_passed": 1,
        "confirmed_issue_count": 1,
        "incomplete_measurement_count": 1,
        "unresolved_confirmed_issue_count": 1,
        "clean_slide_count": 18,
        "stage_usage": {
            "llm": {"cache_hits": 1.0, "total_tokens": 150.0, "cost_usd": 0.01},
            "chrome": {"cache_hits": 5.0, "cache_misses": 2.0},
            "acceptance": {"cache_hits": 0.0, "cache_misses": 2.0},
            "postverify": {"cache_hits": 0.0, "cache_misses": 1.0},
        },
    }

    summary = _phase_summary([base, base])

    assert summary["funnel"]["branch"] == {"branches": 8, "unknown": 2}
    assert summary["funnel"]["candidate"]["candidate_accepted"] == 2
    assert summary["funnel"]["review"] == {
        "confirmed_issue_count": 2,
        "incomplete_measurement_count": 2,
        "unresolved_confirmed_issue_count": 2,
        "clean_slide_count": 36,
    }
    assert summary["stage_usage"]["llm"]["total_tokens"] == 300.0
    assert summary["stage_usage"]["llm"]["cost_usd"] == 0.02
    assert summary["stage_usage"]["llm"]["coverage_runs"]["cost_usd"] == 2


def test_summary_only_keeps_slo_and_usage_but_drops_candidate_diagnostics() -> None:
    result = {
        "schema": "slidespec-pipeline-benchmark/2",
        "cold": {
            "deck_wall_ms": {"p50": 100.0, "p95": 120.0, "p99": 125.0},
            "funnel": {"branch": {"unknown": 1}},
            "stage_usage": {"llm": {"cost_usd": 0.01}},
            "runs": [{"diagnostics": {"triggered_slides": ["large"]}}],
        },
        "warm": {"deck_wall_ms": {"p50": 10.0}, "runs": []},
    }

    compact = _summary_only(result)

    assert compact["cold"]["deck_wall_ms"]["p95"] == 120.0
    assert compact["cold"]["stage_usage"]["llm"]["cost_usd"] == 0.01
    assert "runs" not in compact["cold"]
    assert "runs" not in compact["warm"]
    assert "runs" in result["cold"]
