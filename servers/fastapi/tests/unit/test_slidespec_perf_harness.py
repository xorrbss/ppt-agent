"""Fast deterministic local baseline for a representative 20-slide corpus.

This intentionally measures only QID migration plus local DOM inspection.  Browser
rendering, LLM, OCR, and VQA retain separate provider-stage measurements in the
service integration suite.
"""

import importlib.util
from pathlib import Path
from time import perf_counter

from utils.authored_dom_quality import (
    build_compact_dom_map,
    ensure_authored_qids,
    inspect_authored_dom,
)


BENCHMARK_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "slidespec_solver_benchmark.py"
)
BENCHMARK_SPEC = importlib.util.spec_from_file_location(
    "slidespec_solver_benchmark", BENCHMARK_PATH
)
assert BENCHMARK_SPEC is not None and BENCHMARK_SPEC.loader is not None
solver_benchmark = importlib.util.module_from_spec(BENCHMARK_SPEC)
BENCHMARK_SPEC.loader.exec_module(solver_benchmark)


def _slide_html(index: int) -> str:
    return (
        f'<section class="slide" data-slide="{index}">'
        f"<h1>Slide {index} title</h1>"
        "<p>Representative body copy with Korean 텍스트 and 42% metric.</p>"
        "<div><span>Supporting label</span></div>"
        "</section>"
    )


def _complete_metrics(html: str) -> dict:
    nodes = build_compact_dom_map(html)
    return {
        "width": 1280,
        "height": 720,
        "elements": [
            {
                "qid": node.qid,
                "tag": node.tag,
                "bbox": {"x": 10, "y": 10, "width": 300, "height": 40},
                "rendered_text": node.text,
            }
            for node in nodes
        ],
    }


def test_synthetic_twenty_slide_local_dom_timing_baseline():
    started = perf_counter()
    reports = []
    for index in range(20):
        html = ensure_authored_qids(_slide_html(index))
        reports.append(inspect_authored_dom(html, _complete_metrics(html)))
    elapsed_seconds = perf_counter() - started

    assert len(reports) == 20
    assert all(report.telemetry_status == "available" for report in reports)
    # This is a local CPU characterization guard, not an LLM/OCR/VQA SLO.
    assert elapsed_seconds < 5.0


def test_solver_benchmark_percentile_is_observed_nearest_rank() -> None:
    assert solver_benchmark.observed_percentile([50, 10, 30, 20, 40], 0.50) == 30
    assert solver_benchmark.observed_percentile([50, 10, 30, 20, 40], 0.95) == 50


def test_seeded_mixed_non_overlap_solver_regression_thresholds() -> None:
    summary = solver_benchmark.run_benchmark(cases=16, seed=20260804)

    assert summary["seed"] == 20260804
    assert summary["cases"] == 16
    assert summary["feasible_ratio"] == 1.0
    assert summary["unknown_ratio"] == 0.0
    assert summary["bounded_optimality_ratio"] == 0.5
    # Largest-slack ordering makes every case in this fixed corpus choose a
    # compatible separation on the first deterministic branch.  Requiring a
    # multi-branch case here would preserve the former avoidable tail latency.
    assert summary["multi_branch_ratio"] == 0.0
    # Loose wall-clock thresholds catch exponential regressions while allowing
    # shared Windows CI noise.  Branch thresholds are deterministic and carry
    # the stronger algorithmic regression signal.
    assert summary["duration_ms"]["p50"] < 50
    assert summary["duration_ms"]["p95"] < 400
    assert summary["duration_ms"]["p99"] < 700
    assert summary["branches"]["p50"] <= 1
    assert summary["branches"]["p95"] == 1
    assert summary["branches"]["p99"] == 1
