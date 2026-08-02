from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "evaluation"
    / "hybrid_review"
    / "LIVE_BENCHMARK.py"
)
SPEC = importlib.util.spec_from_file_location("authored_live_benchmark", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(benchmark)


def test_observed_percentile_uses_actual_nearest_rank_sample() -> None:
    assert benchmark.observed_percentile([50, 10, 30, 20, 40], 0.50) == 30
    assert benchmark.observed_percentile([50, 10, 30, 20, 40], 0.95) == 50
    assert benchmark.observed_percentile([], 0.95) is None


def test_budget_counts_structured_and_raw_author_requests(monkeypatch) -> None:
    from utils import llm_utils
    from utils.llm_calls import author_slide

    async def fake_structured(*args, **kwargs):
        return "structured"

    class FakeClient:
        def generate(self, *args, **kwargs):
            return "author"

    monkeypatch.setattr(llm_utils, "_generate_structured_content", fake_structured)
    monkeypatch.setattr(
        author_slide, "get_client", lambda *args, **kwargs: FakeClient()
    )

    async def exercise() -> None:
        async with benchmark.bounded_provider_requests(2) as budget:
            assert await llm_utils._generate_structured_content() == "structured"
            assert author_slide.get_client().generate() == "author"
            assert budget.snapshot() == {
                "total": 2,
                "structured": 1,
                "full_author": 1,
            }
            with pytest.raises(benchmark.ProviderRequestBudgetExceeded):
                await llm_utils._generate_structured_content()

    asyncio.run(exercise())
    assert llm_utils._generate_structured_content is fake_structured


def test_safe_error_never_serializes_exception_message() -> None:
    payload = benchmark._safe_error(RuntimeError("secret-token prompt and response"))
    assert payload == {"code": "stage_failed", "error_type": "RuntimeError"}


def test_cli_defaults_to_four_provider_requests() -> None:
    args = benchmark._parser().parse_args([])
    assert args.max_provider_requests == 4


def test_budget_exhaustion_is_detectable_before_a_followup_stage() -> None:
    budget = benchmark.ProviderRequestBudget(1)
    budget.consume("structured")
    assert budget.snapshot()["total"] >= budget.maximum


def test_audit_fixture_targets_the_real_planned_clean_audit() -> None:
    plan = benchmark.plan_authored_review_routing(
        scope="all", slide_count=8, sampling_seed="live-boundary-8"
    )
    assert len(plan.clean_audit_indices) == 2
    assert plan.clean_audit_indices[0] in plan.detail_vqa_indices
