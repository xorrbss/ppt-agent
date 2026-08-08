"""Bounded live-model operations benchmark for authored hybrid review.

This runner is intentionally separate from the deterministic default benchmark.
It invokes the configured provider, counts both structured generations and raw
full-slide author generations, and emits only content-free operational facts.

Run from ``servers/fastapi``::

    uv run python evaluation/hybrid_review/LIVE_BENCHMARK.py \
      --user-config ../../app_data/userConfig.json --provider codex
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import sys
import threading
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

SERVER_ROOT = Path(__file__).resolve().parents[2]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from scripts.benchmark_authored_hybrid_review import (
    _critique_summary,
    _load_live_configuration,
    _local_report_summary,
)
from services.authored_quality_acceptance import (
    critique_unusable_reason,
    local_acceptance_decision,
    style_acceptance_decision,
)
from services.authored_quality_routing import hybrid_style_initial_review
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    ensure_authored_qids,
    inspect_authored_dom,
)
from utils.authored_patch_engine import apply_authored_patch
from utils.authored_review_routing import plan_authored_review_routing
from utils.llm_calls import author_vision_qa
from utils.llm_calls.author_slide import Brand, build_design_system
from utils.llm_calls.author_slide_patch import propose_authored_slide_patch
from utils.slide_capture import (
    _browser_metrics_cache,
    _render_cache,
    get_cached_browser_metrics,
    render_html_to_png_with_metrics,
)


class ProviderRequestBudgetExceeded(RuntimeError):
    """Raised before a provider generation would exceed the campaign ceiling."""


class ProviderRequestBudget:
    """Thread-safe accounting shared by structured and raw author generations."""

    def __init__(self, maximum: int) -> None:
        if maximum <= 0:
            raise ValueError("maximum provider requests must be positive")
        self.maximum = maximum
        self._counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def consume(self, kind: str) -> None:
        with self._lock:
            if self.total >= self.maximum:
                raise ProviderRequestBudgetExceeded(
                    f"provider request budget exhausted ({self.maximum})"
                )
            self._counts[kind] += 1

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "total": self.total,
                "structured": self._counts["structured"],
                "full_author": self._counts["full_author"],
            }

    def delta(self, before: dict[str, int]) -> dict[str, int]:
        after = self.snapshot()
        return {key: after[key] - before.get(key, 0) for key in after}


class _CountingClient:
    def __init__(self, client: Any, budget: ProviderRequestBudget) -> None:
        self._client = client
        self._budget = budget

    def generate(self, *args: Any, **kwargs: Any) -> Any:
        self._budget.consume("full_author")
        return self._client.generate(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@asynccontextmanager
async def bounded_provider_requests(
    maximum: int,
) -> AsyncIterator[ProviderRequestBudget]:
    """Count every structured attempt and every raw full-author attempt.

    The structured hook is below schema/transient retry logic. The author hook is
    at ``client.generate`` and therefore also counts a multimodal text-only retry.
    Both hooks are restored even when a stage times out or exhausts the budget.
    """

    from utils import llm_utils
    from utils.llm_calls import author_slide

    budget = ProviderRequestBudget(maximum)
    original_structured = llm_utils._generate_structured_content
    original_get_client = author_slide.get_client

    async def counted_structured(*args: Any, **kwargs: Any) -> Any:
        budget.consume("structured")
        return await original_structured(*args, **kwargs)

    def counted_get_client(*args: Any, **kwargs: Any) -> _CountingClient:
        return _CountingClient(original_get_client(*args, **kwargs), budget)

    llm_utils._generate_structured_content = counted_structured
    author_slide.get_client = counted_get_client
    try:
        yield budget
    finally:
        llm_utils._generate_structured_content = original_structured
        author_slide.get_client = original_get_client


def observed_percentile(values: Iterable[float], percentile: float) -> float | None:
    """Return a nearest-rank observed percentile; never invent a sample value."""

    samples = sorted(float(value) for value in values)
    if not samples:
        return None
    if not 0 < percentile <= 1:
        raise ValueError("percentile must be within (0, 1]")
    return samples[max(0, math.ceil(percentile * len(samples)) - 1)]


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    samples = [round(float(value), 3) for value in values]
    return {
        "samples": samples,
        "count": len(samples),
        "p50_ms": round(observed_percentile(samples, 0.50) or 0.0, 3),
        "p95_ms": round(observed_percentile(samples, 0.95) or 0.0, 3),
        "method": "nearest_rank_observed",
    }


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _safe_error(error: BaseException) -> dict[str, str]:
    """Serialize no provider response, prompt, path, or credential material."""

    if isinstance(error, ProviderRequestBudgetExceeded):
        code = "provider_budget_exhausted"
    elif isinstance(error, TimeoutError):
        code = "stage_timeout"
    else:
        code = "stage_failed"
    return {"code": code, "error_type": type(error).__name__}


def _clear_runtime_caches() -> None:
    _render_cache.clear()
    _browser_metrics_cache.clear()
    author_vision_qa._vqa_cache.clear()
    author_vision_qa._vqa_inflight.clear()
    author_vision_qa._style_comparison_vqa_cache.clear()
    author_vision_qa._style_comparison_vqa_inflight.clear()


def _base_html(index: int, *, title: str | None = None, extra: str = "") -> str:
    heading = title or f"Revenue quality benchmark {index + 1}"
    cards = "".join(
        f'<section class="card"><b>Metric {item + 1}</b><span>{(index + 1) * 100 + item}</span></section>'
        for item in range(8)
    )
    return ensure_authored_qids(
        f"""<!doctype html><html><head><style>
html,body{{margin:0;width:1280px;height:720px;overflow:hidden;background:#f8fafc;color:#0f172a;font-family:Arial,sans-serif}}
.slide{{box-sizing:border-box;width:1280px;height:720px;padding:58px 68px}}
h1{{margin:0 0 30px;font-size:44px;line-height:1.08}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:20px}}
.card{{box-sizing:border-box;height:190px;padding:24px;border:1px solid #cbd5e1;border-radius:16px;background:#fff}}
.card b{{display:block;font-size:19px;margin-bottom:42px}}.card span{{font-size:34px;font-weight:700;color:#2563eb}}
.audit-defect{{position:absolute;right:76px;bottom:34px;font-size:16px;font-weight:700;color:#dc2626}}
</style></head><body><main class="slide"><h1>{heading}</h1><div class="grid">{cards}</div>{extra}</main></body></html>"""
    )


def _patch_fixture_html() -> str:
    return ensure_authored_qids(
        """<!doctype html><html><head><style>
html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#f8fafc;color:#0f172a;font-family:Arial,sans-serif}
.slide{box-sizing:border-box;width:1280px;height:720px;padding:58px 70px}
h1{box-sizing:border-box;margin:0 0 34px;width:1080px;height:50px;overflow:hidden;font-size:46px;line-height:1.12}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:24px}
.card{box-sizing:border-box;height:220px;padding:28px;border-radius:18px;background:white;border:1px solid #cbd5e1}
.card b{display:block;font-size:19px;margin-bottom:58px}.card span{font-size:40px;font-weight:700;color:#2563eb}
</style></head><body><main class="slide"><h1>Quarterly Revenue Accelerates Across Enterprise and Public Sector</h1><div class="grid"><section class="card"><b>Revenue</b><span>$42.8M</span></section><section class="card"><b>Growth</b><span>+18%</span></section><section class="card"><b>Retention</b><span>96%</span></section></div></main></body></html>"""
    )


def _fallback_fixture_html() -> str:
    return ensure_authored_qids(
        """<!doctype html><html><head><style>
html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#fff;color:#0f172a;font-family:Arial,sans-serif}
.slide{box-sizing:border-box;width:1280px;height:720px;padding:60px}
h1{font-size:44px;margin:0}.placeholder{position:absolute;left:120px;top:230px;width:1040px;height:210px;display:flex;align-items:center;justify-content:center;border:8px dashed #dc2626;background:#fee2e2;color:#b91c1c;font-size:78px;font-weight:900;transform:rotate(-4deg)}
</style></head><body><main class="slide"><h1>Executive Operating Review</h1><div class="placeholder">TODO PLACEHOLDER</div></main></body></html>"""
    )


async def _render_and_inspect(html: str) -> tuple[bytes, Any, float, float]:
    render_started = time.perf_counter()
    png, metrics = await render_html_to_png_with_metrics(html)
    render_ms = _elapsed_ms(render_started)
    local_started = time.perf_counter()
    report = inspect_authored_dom(html, metrics, reference_mode=STYLE_REFERENCE)
    return png, report, render_ms, _elapsed_ms(local_started)


async def _run_clean_repeats(
    budget: ProviderRequestBudget, *, count: int, timeout: float
) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for index in range(count):
        started = time.perf_counter()
        html = _base_html(index)
        png, report, render_ms, local_ms = await _render_and_inspect(html)
        before = budget.snapshot()
        vqa_started = time.perf_counter()
        critique = (
            await asyncio.wait_for(
                author_vision_qa.critique_authored(
                    [png],
                    contexts=[
                        f"Clean business dashboard {index + 1} with eight metric cards; no placeholders or clipping."
                    ],
                ),
                timeout=timeout,
            )
        )[0]
        samples.append(
            {
                "temperature": "cold_process_cache"
                if index == 0
                else "warm_process_cache",
                "render_and_metrics_ms": round(render_ms, 3),
                "local_inspection_ms": round(local_ms, 3),
                "vqa_ms": round(_elapsed_ms(vqa_started), 3),
                "total_ms": round(_elapsed_ms(started), 3),
                "provider_requests": budget.delta(before),
                "local": _local_report_summary(report),
                "vqa": _critique_summary(critique),
            }
        )
    warm = samples[1:]
    return {
        "status": "completed",
        "samples": samples,
        "all_total": _distribution(item["total_ms"] for item in samples),
        "warm_total": _distribution(item["total_ms"] for item in warm),
        "browser_render_and_metrics": _distribution(
            item["render_and_metrics_ms"] for item in samples
        ),
        "vqa": _distribution(item["vqa_ms"] for item in samples),
        "target_clean_5_to_10_seconds": {
            "basis": "warm_total_p50_ms",
            "passed": bool(warm)
            and 5000
            <= _distribution(item["total_ms"] for item in warm)["p50_ms"]
            <= 10000,
        },
    }


async def _run_local_patch(
    budget: ProviderRequestBudget, *, timeout: float
) -> dict[str, Any]:
    context = (
        "Quarterly business dashboard titled Quarterly Revenue Accelerates Across "
        "Enterprise and Public Sector with revenue $42.8M, growth +18%, retention 96%."
    )
    started = time.perf_counter()
    html = _patch_fixture_html()
    baseline_png, baseline_report, render_ms, local_ms = await _render_and_inspect(html)
    before = budget.snapshot()
    vqa_started = time.perf_counter()
    initial = (
        await asyncio.wait_for(
            author_vision_qa.critique_authored([baseline_png], contexts=[context]),
            timeout=timeout,
        )
    )[0]
    initial_vqa_ms = _elapsed_ms(vqa_started)
    result: dict[str, Any] = {
        "status": "completed",
        "render_and_metrics_ms": round(render_ms, 3),
        "local_inspection_ms": round(local_ms, 3),
        "initial_vqa_ms": round(initial_vqa_ms, 3),
        "initial": _critique_summary(initial),
        "baseline_local": _local_report_summary(baseline_report),
        "accepted": False,
        "acceptance_reason": "initial_vqa_unusable_or_clean",
    }
    if initial is None or not initial.needs_fix or not baseline_report.needs_attention:
        result["provider_requests"] = budget.delta(before)
        result["total_ms"] = round(_elapsed_ms(started), 3)
        return result

    proposal_started = time.perf_counter()
    proposal = await asyncio.wait_for(
        propose_authored_slide_patch(
            baseline_png,
            [node.to_dict() for node in baseline_report.compact_dom],
            initial,
            reference_mode=STYLE_REFERENCE,
        ),
        timeout=timeout,
    )
    result["proposal_ms"] = round(_elapsed_ms(proposal_started), 3)
    result["proposal_ok"] = proposal.ok
    result["strategy"] = (
        proposal.plan.strategy if proposal.plan is not None else "full_reauthor"
    )
    result["action_count"] = (
        len(proposal.plan.actions) if proposal.plan is not None else 0
    )
    if not proposal.ok or proposal.plan is None:
        result["acceptance_reason"] = "patch_proposal_rejected"
        result["provider_requests"] = budget.delta(before)
        result["total_ms"] = round(_elapsed_ms(started), 3)
        return result

    apply_started = time.perf_counter()
    application = apply_authored_patch(html, proposal.plan)
    result["apply_ms"] = round(_elapsed_ms(apply_started), 3)
    result["apply_ok"] = application.ok
    result["changed_element_count"] = len(application.changes)
    if not application.ok:
        result["acceptance_reason"] = "patch_application_rejected"
        result["provider_requests"] = budget.delta(before)
        result["total_ms"] = round(_elapsed_ms(started), 3)
        return result

    candidate_png, candidate_report, candidate_render_ms, _ = await _render_and_inspect(
        application.html
    )
    comparison_started = time.perf_counter()
    comparison = (
        await asyncio.wait_for(
            author_vision_qa.critique_authored_style_comparison(
                [baseline_png], [candidate_png], contexts=[context]
            ),
            timeout=timeout,
        )
    )[0]
    result["candidate_render_ms"] = round(candidate_render_ms, 3)
    result["comparison_vqa_ms"] = round(_elapsed_ms(comparison_started), 3)
    result["candidate_local"] = _local_report_summary(candidate_report)
    result["comparison"] = _critique_summary(comparison)
    visual_accepted, reason = style_acceptance_decision(initial, comparison)
    local_accepted, local_reason = local_acceptance_decision(
        baseline_report, candidate_report
    )
    accepted = visual_accepted and local_accepted
    if visual_accepted and not local_accepted:
        reason = local_reason
    result.update(
        {
            "accepted": accepted,
            "visual_accepted": visual_accepted,
            "local_accepted": local_accepted,
            "local_acceptance_reason": local_reason,
            "acceptance_reason": reason,
            "provider_requests": budget.delta(before),
            "total_ms": round(_elapsed_ms(started), 3),
        }
    )
    return result


async def _run_full_fallback(
    budget: ProviderRequestBudget, *, timeout: float
) -> dict[str, Any]:
    context = (
        "Completed executive operating review: Q2 revenue $42.8M, margin 31%, "
        "retention 96%, and a three-step H2 execution roadmap. No placeholder text."
    )
    html = _fallback_fixture_html()
    started = time.perf_counter()
    baseline_png, baseline_report, render_ms, local_ms = await _render_and_inspect(html)
    before = budget.snapshot()
    initial_started = time.perf_counter()
    initial = (
        await asyncio.wait_for(
            author_vision_qa.critique_authored([baseline_png], contexts=[context]),
            timeout=timeout,
        )
    )[0]
    result: dict[str, Any] = {
        "status": "completed",
        "render_and_metrics_ms": round(render_ms, 3),
        "local_inspection_ms": round(local_ms, 3),
        "initial_vqa_ms": round(_elapsed_ms(initial_started), 3),
        "initial": _critique_summary(initial),
        "baseline_local": _local_report_summary(baseline_report),
        "accepted": False,
        "acceptance_reason": "initial_vqa_unusable_or_clean",
    }
    if initial is None or not initial.needs_fix:
        result["provider_requests"] = budget.delta(before)
        result["total_ms"] = round(_elapsed_ms(started), 3)
        return result

    brand = Brand(topic="Executive Operating Review", language="English")
    fallback_started = time.perf_counter()
    revised_htmls, revised_pngs, fixed, details = await asyncio.wait_for(
        author_vision_qa.revise_authored_deck(
            [html],
            [baseline_png],
            [context],
            ["CONTENT"],
            brand,
            build_design_system(brand),
            initial_critiques=[initial],
            return_details=True,
            force_full_reauthor_indices=[0],
        ),
        timeout=timeout,
    )
    result["fallback_stage_ms"] = round(_elapsed_ms(fallback_started), 3)
    result["changed"] = fixed == [0]
    result["repair_path"] = details[0].get("path") if details else None
    result["fallback_succeeded"] = bool(details and details[0].get("succeeded"))
    if fixed != [0]:
        result["acceptance_reason"] = "full_fallback_failed"
        result["provider_requests"] = budget.delta(before)
        result["total_ms"] = round(_elapsed_ms(started), 3)
        return result

    candidate_metrics = get_cached_browser_metrics(revised_htmls[0])
    candidate_report = inspect_authored_dom(
        revised_htmls[0], candidate_metrics, reference_mode=STYLE_REFERENCE
    )
    comparison_started = time.perf_counter()
    comparison = (
        await asyncio.wait_for(
            author_vision_qa.critique_authored_style_comparison(
                [baseline_png], [revised_pngs[0]], contexts=[context]
            ),
            timeout=timeout,
        )
    )[0]
    result["comparison_vqa_ms"] = round(_elapsed_ms(comparison_started), 3)
    result["candidate_local"] = _local_report_summary(candidate_report)
    result["comparison"] = _critique_summary(comparison)
    visual_accepted, reason = style_acceptance_decision(initial, comparison)
    local_accepted, local_reason = local_acceptance_decision(
        baseline_report, candidate_report
    )
    accepted = visual_accepted and local_accepted
    if visual_accepted and not local_accepted:
        reason = local_reason
    result.update(
        {
            "accepted": accepted,
            "visual_accepted": visual_accepted,
            "local_accepted": local_accepted,
            "local_acceptance_reason": local_reason,
            "acceptance_reason": reason,
            "provider_requests": budget.delta(before),
            "total_ms": round(_elapsed_ms(started), 3),
            "target_problem_20_to_60_seconds": 20000 <= _elapsed_ms(started) <= 60000,
        }
    )
    return result


async def _run_live_routing(
    budget: ProviderRequestBudget,
    *,
    slide_count: int,
    timeout: float,
    audit_defect: bool,
) -> dict[str, Any]:
    seed = f"live-boundary-{slide_count}"
    plan = plan_authored_review_routing(
        scope="all", slide_count=slide_count, sampling_seed=seed
    )
    audit_index = plan.clean_audit_indices[0] if audit_defect else None
    htmls = [
        _base_html(
            index,
            extra=(
                '<div class="audit-defect">TODO</div>' if audit_index == index else ""
            ),
        )
        for index in range(slide_count)
    ]
    render_started = time.perf_counter()
    rendered = await asyncio.gather(*(_render_and_inspect(html) for html in htmls))
    render_ms = _elapsed_ms(render_started)
    pngs = [item[0] for item in rendered]
    reports = [item[1] for item in rendered]
    contexts = [
        (
            f"Clean revenue dashboard slide {index + 1} of {slide_count} with eight metric cards; "
            + (
                "the final slide must contain no TODO placeholder."
                if index == audit_index
                else "no placeholders or clipping."
            )
        )
        for index in range(slide_count)
    ]
    before = budget.snapshot()
    review_started = time.perf_counter()

    def observe_cache(images: list[bytes], _: list[str]) -> tuple[int, int]:
        return 0, len(images)

    (
        initial,
        routing,
        cache_hits,
        cache_misses,
        montage_ms,
        detail_ms,
    ) = await asyncio.wait_for(
        hybrid_style_initial_review(
            pngs=pngs,
            contexts=contexts,
            positions=list(range(slide_count)),
            slide_count=slide_count,
            local_reports=reports,
            sampling_seed=seed,
            critique_batch=author_vision_qa.critique_authored,
            observe_cache=observe_cache,
            unusable_reason=critique_unusable_reason,
        ),
        timeout=timeout,
    )
    escalation = routing["escalation"]
    return {
        "status": "completed",
        "slide_count": slide_count,
        "audit_defect_injected": audit_defect,
        "audit_index": audit_index,
        "render_all_ms": round(render_ms, 3),
        "review_wall_ms": round(_elapsed_ms(review_started), 3),
        "montage_ms": round(montage_ms, 3),
        "detail_ms": round(detail_ms, 3),
        "provider_requests": budget.delta(before),
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "montage_batch_sizes": [
            len(batch["slide_indices"]) for batch in routing["plan"]["montage_batches"]
        ],
        "planned_audit_indices": routing["plan"]["clean_audit_indices"],
        "failed_audit_indices": escalation["failed_audit_indices"],
        "escalation_level": escalation["level"],
        "escalated_detail_count": len(escalation["detail_vqa_indices"]),
        "montage_promoted_batches": routing["montage_promoted_batches"],
        "montage_vqa_count": routing["montage_vqa_count"],
        "detail_vqa_count": routing["detail_vqa_count"],
        "needs_fix_count": sum(
            1 for critique in initial if critique is not None and critique.needs_fix
        ),
    }


async def _capture_stage(
    name: str,
    budget: ProviderRequestBudget,
    operation: Callable[[], Awaitable[dict[str, Any]]],
) -> tuple[str, dict[str, Any]]:
    before = budget.snapshot()
    started = time.perf_counter()
    try:
        result = await operation()
    except Exception as error:  # noqa: BLE001 - benchmark must record stage failures
        result = {
            "status": "failed",
            "error": _safe_error(error),
            "provider_requests": budget.delta(before),
            "total_ms": round(_elapsed_ms(started), 3),
        }
    return name, result


async def run_campaign(
    *,
    user_config: Path | None,
    provider_override: str | None,
    maximum_provider_requests: int,
    stage_timeout_seconds: float,
    clean_repeats: int,
) -> dict[str, Any]:
    if maximum_provider_requests < 1:
        raise ValueError("maximum_provider_requests must be positive")
    if not 1 <= stage_timeout_seconds <= 300:
        raise ValueError("stage_timeout_seconds must be between 1 and 300")
    if not 2 <= clean_repeats <= 10:
        raise ValueError("clean_repeats must be between 2 and 10")

    configuration = _load_live_configuration(user_config, provider_override)
    _clear_runtime_caches()
    campaign_started = time.perf_counter()
    stages: dict[str, Any] = {}
    async with bounded_provider_requests(maximum_provider_requests) as budget:
        # The default four-call campaign prioritizes the previously-unproven repair
        # paths. Later stages remain in the payload as explicit budget-exhausted
        # evidence instead of silently increasing cost to reach every scenario.
        operations: list[tuple[str, Callable[[], Awaitable[dict[str, Any]]]]] = [
            (
                "local_patch",
                lambda: _run_local_patch(budget, timeout=stage_timeout_seconds),
            ),
            (
                "full_fallback",
                lambda: _run_full_fallback(budget, timeout=stage_timeout_seconds),
            ),
            (
                "routing_8_audit_failure",
                lambda: _run_live_routing(
                    budget,
                    slide_count=8,
                    timeout=stage_timeout_seconds,
                    audit_defect=True,
                ),
            ),
            (
                "routing_10_boundary",
                lambda: _run_live_routing(
                    budget,
                    slide_count=10,
                    timeout=stage_timeout_seconds,
                    audit_defect=False,
                ),
            ),
            (
                "routing_11_boundary",
                lambda: _run_live_routing(
                    budget,
                    slide_count=11,
                    timeout=stage_timeout_seconds,
                    audit_defect=False,
                ),
            ),
            (
                "clean_repeats",
                lambda: _run_clean_repeats(
                    budget, count=clean_repeats, timeout=stage_timeout_seconds
                ),
            ),
        ]
        for name, operation in operations:
            if budget.snapshot()["total"] >= budget.maximum:
                stages[name] = {
                    "status": "skipped",
                    "reason": "provider_budget_exhausted",
                    "provider_requests": {
                        "total": 0,
                        "structured": 0,
                        "full_author": 0,
                    },
                }
                continue
            stage_name, result = await _capture_stage(name, budget, operation)
            stages[stage_name] = result
        final_budget = budget.snapshot()

    return {
        "schema_version": 1,
        "live_model_benchmark": True,
        "configuration": configuration,
        "cost_gate": {
            "maximum_provider_requests": maximum_provider_requests,
            "actual_provider_requests": final_budget["total"],
            "structured_provider_requests": final_budget["structured"],
            "full_author_provider_requests": final_budget["full_author"],
            "budget_exhausted": final_budget["total"] >= maximum_provider_requests,
            "stage_timeout_seconds": stage_timeout_seconds,
        },
        "stages": stages,
        "total_wall_ms": round(_elapsed_ms(campaign_started), 3),
        "privacy": {
            "credential_values_serialized": False,
            "prompt_bodies_serialized": False,
            "model_response_bodies_serialized": False,
            "generated_html_serialized": False,
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-config", type=Path)
    parser.add_argument("--provider")
    parser.add_argument("--max-provider-requests", type=int, default=4)
    parser.add_argument("--stage-timeout-seconds", type=float, default=180.0)
    parser.add_argument("--clean-repeats", type=int, default=3)
    return parser


def main() -> int:
    # Provider libraries and production repair code may emit full exception stacks.
    # The benchmark contract is a single sanitized JSON record, so suppress those
    # process-local logs while retaining error types/codes in the returned payload.
    logging.disable(logging.CRITICAL)
    args = _parser().parse_args()
    try:
        payload = asyncio.run(
            run_campaign(
                user_config=args.user_config,
                provider_override=args.provider,
                maximum_provider_requests=args.max_provider_requests,
                stage_timeout_seconds=args.stage_timeout_seconds,
                clean_repeats=args.clean_repeats,
            )
        )
    except Exception as error:  # noqa: BLE001 - emit sanitized benchmark failure
        payload = {
            "live_model_benchmark": True,
            "status": "configuration_failed",
            "error": _safe_error(error),
        }
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 1
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
