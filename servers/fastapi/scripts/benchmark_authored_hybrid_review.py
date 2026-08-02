"""Benchmark the authored hybrid-review fast path.

The default and ``--ocr-self-test`` modes make no model calls. They measure the
real local Chrome/DOM/OCR paths, structured patch apply, candidate re-render, and
routing boundaries. ``--live-self-test`` is a separate, explicit, credential-gated
mode with a hard provider-request ceiling and sanitized output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from services.authored_quality_review_service import _render_review_montage
from utils.authored_dom_quality import (
    STYLE_REFERENCE,
    build_compact_dom_map,
    ensure_authored_qids,
    has_dom_quality_regression,
    inspect_authored_dom,
)
from utils.authored_ocr_quality import (
    AuthoredOcrCache,
    compare_authored_ocr,
    compare_expected_candidate,
    inspect_authored_ocr,
    inspect_tesseract,
)
from utils.authored_patch_engine import apply_authored_patch
from utils.authored_review_routing import plan_authored_review_routing
from utils.slide_capture import (
    _browser_metrics_cache,
    _render_cache,
    render_html_to_png_with_metrics,
)

_SAFE_CONFIGURATION_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")


class _ProviderRequestBudgetExceeded(RuntimeError):
    pass


class _ProviderRequestBudget:
    """Count actual provider generations and stop before the configured ceiling.

    Counting at ``_generate_structured_content`` includes schema/empty-response retries,
    unlike counting only high-level VQA helpers. This makes the cost gate conservative.
    """

    def __init__(self, maximum: int) -> None:
        if maximum <= 0:
            raise ValueError("max_provider_requests must be positive")
        self.maximum = maximum
        self.requests = 0

    def snapshot(self) -> int:
        return self.requests

    def delta(self, before: int) -> int:
        return self.requests - before


@asynccontextmanager
async def _bounded_provider_requests(
    maximum: int,
) -> AsyncIterator[_ProviderRequestBudget]:
    """Temporarily instrument the shared structured-generation trust boundary."""

    from utils import llm_utils

    budget = _ProviderRequestBudget(maximum)
    original = llm_utils._generate_structured_content

    async def counted(*args: Any, **kwargs: Any) -> Any:
        if budget.requests >= budget.maximum:
            raise _ProviderRequestBudgetExceeded(
                f"provider request budget exhausted ({budget.maximum})"
            )
        budget.requests += 1
        return await original(*args, **kwargs)

    llm_utils._generate_structured_content = counted
    try:
        yield budget
    finally:
        llm_utils._generate_structured_content = original


def _safe_configuration_token(value: Any, *, fallback: str = "unknown") -> str:
    token = str(value or "")
    return token if _SAFE_CONFIGURATION_TOKEN.fullmatch(token) else fallback


def _load_live_configuration(
    user_config: Path | None, provider_override: str | None
) -> dict[str, Any]:
    """Hydrate the normal app configuration without serializing credentials."""

    if user_config is not None:
        if not user_config.is_file():
            raise FileNotFoundError(f"USER_CONFIG_PATH does not exist: {user_config}")
        os.environ["USER_CONFIG_PATH"] = str(user_config.resolve())

    from utils.user_config import update_env_with_user_config

    update_env_with_user_config()
    if provider_override:
        os.environ["LLM"] = provider_override

    from utils.llm_config import get_llm_config
    from utils.llm_provider import get_llm_provider, get_model

    get_llm_config()  # Validate availability without returning or logging the object.
    return {
        "provider": _safe_configuration_token(get_llm_provider().value),
        "model": _safe_configuration_token(get_model()),
        "credential_validated": True,
    }


def _critique_summary(critique: Any) -> dict[str, Any]:
    """Return content-free facts suitable for a committed benchmark report."""

    if critique is None:
        return {"usable": False, "needs_fix": None, "issue_count": None, "issues": []}
    issues = list(getattr(critique, "issues", ()) or ())
    return {
        "usable": getattr(critique, "needs_fix", None) == bool(issues),
        "needs_fix": bool(getattr(critique, "needs_fix", False)),
        "issue_count": len(issues),
        "issues": [
            {
                "type": _safe_configuration_token(getattr(issue, "type", None)),
                "severity": _safe_configuration_token(getattr(issue, "severity", None)),
            }
            for issue in issues
        ],
    }


def _local_report_summary(report: Any) -> dict[str, Any]:
    return {
        "telemetry_status": report.telemetry_status,
        "inspected_elements": report.inspected_elements,
        "needs_attention": report.needs_attention,
        "issue_count": len(report.issues),
        "issues": [
            {
                "type": _safe_configuration_token(issue.category),
                "severity": _safe_configuration_token(issue.severity),
                "qids": [
                    _safe_configuration_token(qid, fallback="unknown")
                    for qid in issue.qids
                ],
            }
            for issue in report.issues
        ],
    }


def _html(index: int = 0, *, defective: bool = False) -> str:
    title_style = ' style="height:32px;overflow:hidden"' if defective else ""
    return f"""<!doctype html><html><head><style>
html,body{{margin:0;width:1280px;height:720px;overflow:hidden;background:#f8fafc;color:#0f172a;font-family:Arial,sans-serif}}
.slide{{box-sizing:border-box;width:1280px;height:720px;padding:64px}}
h1{{margin:0 0 24px;font-size:44px;line-height:1.1}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:18px}}
.card{{box-sizing:border-box;height:116px;padding:18px;border:1px solid #cbd5e1;border-radius:14px;background:#fff}}
.card b{{display:block;font-size:21px;margin-bottom:8px}}.card span{{font-size:15px;color:#475569}}
</style></head><body><main class="slide"><h1{title_style}>Hybrid review benchmark {index}</h1><div class="grid">
{"".join(f'<div class="card"><b>Metric {item + 1}</b><span>Value {index * 100 + item:,}</span></div>' for item in range(16))}
</div></main></body></html>"""


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def _routing_result(
    slide_count: int, *, local_issue_indices: list[int] | None = None
) -> dict:
    """Report calls from the actual routing plan, without making a VQA request."""

    route = plan_authored_review_routing(
        scope="all",
        slide_count=slide_count,
        local_issue_indices=local_issue_indices or (),
        sampling_seed="benchmark",
    )
    montage_requests = len(route.montage_batches)
    detail_requests = len(route.detail_vqa_indices)
    return {
        "montage_batch_sizes": [
            len(batch.slide_indices) for batch in route.montage_batches
        ],
        "montage_vqa_requests_planned": montage_requests,
        "detail_vqa_requests_planned": detail_requests,
        "planned_initial_vqa_requests": montage_requests + detail_requests,
        "old_full_detail_vqa_requests": slide_count,
    }


_OCR_EXPECTED_TEXT = "\uac00\uaca9 2026 +12%"


def _ocr_fixture_html(*, width: int | None = None) -> str:
    """Return a slide whose CSS clips Korean, digits, and a symbol when requested."""

    clipping = (
        ""
        if width is None
        else (f"width:{width}px;height:54px;overflow:hidden;white-space:nowrap;")
    )
    return f"""<!doctype html><html><head><style>
html,body{{margin:0;width:1280px;height:720px;overflow:hidden;background:#ffffff;color:#0f172a;font-family:Arial,'Malgun Gothic',sans-serif}}
.slide{{box-sizing:border-box;width:1280px;height:720px;padding:220px 92px}}
.ocr-target{{box-sizing:border-box;display:block;padding:8px 12px;border:2px solid #2563eb;font-size:40px;line-height:1.2;{clipping}}}
</style></head><body><main class="slide"><div class="ocr-target">{_OCR_EXPECTED_TEXT}</div></main></body></html>"""


async def run_ocr_suspect_only_benchmark() -> dict:
    """Measure the zero-model local route and its suspect-only OCR extension.

    The rendered slides are deliberately kept outside route timing.  Both routes
    receive the same captured images, so their ``total_ms`` values are comparable.
    """

    fixtures = [
        {"name": "clean", "html": _ocr_fixture_html(), "expected_issue": False},
        {
            "name": "missing_korean_digits_symbol",
            "html": _ocr_fixture_html(width=74),
            "expected_issue": True,
        },
        {
            "name": "candidate_improved",
            "html": _ocr_fixture_html(),
            "expected_issue": False,
        },
        {
            "name": "candidate_regression",
            "html": _ocr_fixture_html(width=1),
            "expected_issue": True,
        },
    ]
    prepared = [ensure_authored_qids(item["html"]) for item in fixtures]

    render_started = time.perf_counter()
    rendered = list(
        await asyncio.gather(
            *(render_html_to_png_with_metrics(html) for html in prepared)
        )
    )
    fixture_render_ms = _elapsed_ms(render_started)

    local_started = time.perf_counter()
    reports = [
        inspect_authored_dom(html, metrics, reference_mode=STYLE_REFERENCE)
        for html, (_png, metrics) in zip(prepared, rendered)
    ]
    no_ocr_total_ms = _elapsed_ms(local_started)
    suspect_indices = [
        index for index, report in enumerate(reports) if report.needs_attention
    ]
    expected_issue_indices = [
        index for index, fixture in enumerate(fixtures) if fixture["expected_issue"]
    ]

    runtime = inspect_tesseract(
        explicit_command=None,
        tessdata_dir=None,
        language="kor+eng",
        timeout_seconds=5,
    )
    runtime_payload = {
        "engine": "tesseract",
        "status": runtime.status,
        "version": runtime.version,
        "requested_languages": list(runtime.requested_languages),
        "available_languages": list(runtime.available_languages),
        "tessdata_dir": runtime.tessdata_dir,
        "inspection_ms": round(runtime.inspection_seconds * 1000, 3),
        "unavailable_reason": runtime.failure_reason,
    }

    ocr_started = time.perf_counter()
    cache = AuthoredOcrCache()
    results: dict[int, object] = {}

    async def inspect_index(index: int):
        if index not in results:
            results[index] = await inspect_authored_ocr(
                rendered[index][0],
                expected_text=_OCR_EXPECTED_TEXT,
                language="kor+eng",
                cache=cache,
            )
        return results[index]

    # This is the production-shaped path: only DOM-suspect slides get image OCR.
    for index in suspect_indices:
        await inspect_index(index)
    suspect_route_ms = _elapsed_ms(ocr_started)
    route_results = {str(index): results[index].to_dict() for index in suspect_indices}
    route_comparisons = {
        str(index): compare_authored_ocr(
            _OCR_EXPECTED_TEXT, results[index], mode="expected"
        ).to_dict()
        for index in suspect_indices
    }
    flagged_indices = [
        index
        for index in suspect_indices
        if not route_comparisons[str(index)]["accepted"]
    ]
    route_ocr_calls = cache.calls
    route_requests = cache.requests
    route_cache_hits = cache.cache_hits

    # Candidate checks share the same request-local cache.  They are benchmark
    # verification work, not part of the suspect-only route timing above.
    baseline = await inspect_index(1)
    improved = await inspect_index(2)
    regression = await inspect_index(3)
    candidate_checks = {
        "baseline_expected": compare_authored_ocr(
            _OCR_EXPECTED_TEXT, baseline, mode="expected"
        ).to_dict(),
        "improved_expected": compare_authored_ocr(
            _OCR_EXPECTED_TEXT, improved, mode="expected"
        ).to_dict(),
        "regression_expected": compare_authored_ocr(
            _OCR_EXPECTED_TEXT, regression, mode="expected"
        ).to_dict(),
        "candidate_improvement": compare_expected_candidate(
            _OCR_EXPECTED_TEXT, baseline, improved
        ).to_dict(),
        "candidate_regression": compare_expected_candidate(
            _OCR_EXPECTED_TEXT, improved, regression
        ).to_dict(),
    }

    cache_probe = None
    if suspect_indices:
        probe = await inspect_authored_ocr(
            rendered[suspect_indices[0]][0],
            expected_text=_OCR_EXPECTED_TEXT,
            language="kor+eng",
            cache=cache,
        )
        cache_probe = probe.to_dict()

    true_positive = sorted(set(flagged_indices) & set(expected_issue_indices))
    false_positive = sorted(set(flagged_indices) - set(expected_issue_indices))
    false_negative = sorted(set(expected_issue_indices) - set(true_positive))
    payload = {
        "model_calls": 0,
        "fixture_render_ms": round(fixture_render_ms, 3),
        "runtime": runtime_payload,
        "no_ocr_route": {
            "slides": len(fixtures),
            "image_ocr_calls": 0,
            "cache_hits": 0,
            "ocr_ms": 0.0,
            "total_ms": round(no_ocr_total_ms, 3),
            "local_suspect_indices": suspect_indices,
            "local_suspect_count": len(suspect_indices),
        },
        "suspect_only_ocr_route": {
            "slides": len(fixtures),
            "suspected_slide_indices": suspect_indices,
            "image_ocr_calls": route_ocr_calls,
            "inspection_requests": route_requests,
            "cache_hits": route_cache_hits,
            "ocr_ms": round(suspect_route_ms, 3),
            "total_ms": round(no_ocr_total_ms + suspect_route_ms, 3),
            "unavailable_reason": runtime.failure_reason,
            "route_ocr_results": route_results,
            "route_expected_comparisons": route_comparisons,
            "cache_probe": cache_probe,
        },
        "candidate_verification": {
            "additional_image_ocr_calls": cache.calls - route_ocr_calls,
            "total_image_ocr_calls": cache.calls,
            "total_inspection_requests": cache.requests,
            "total_cache_hits": cache.cache_hits,
        },
        "detection": {
            "expected_issue_indices": expected_issue_indices,
            "ocr_flagged_indices": flagged_indices,
            "true_positive_indices": true_positive,
            "false_positive_indices": false_positive,
            "false_negative_indices": false_negative,
            "true_positives": len(true_positive),
            "false_positives": len(false_positive),
            "false_negatives": len(false_negative),
        },
        "candidate_checks": candidate_checks,
    }
    return payload


async def run_live_self_test(
    *,
    user_config: Path | None,
    provider_override: str | None,
    max_provider_requests: int,
    stage_timeout_seconds: float,
) -> dict[str, Any]:
    """Run a bounded production-component live gate on one clean and one bad slide.

    The gate deliberately does not invoke the expensive full-HTML fallback. A failed
    or rejected local patch is reported as ``full_fallback_required`` so the benchmark
    proves the normal fast path without silently turning a four-request ceiling into an
    unbounded authoring run.
    """

    if max_provider_requests < 4:
        raise ValueError(
            "live self-test requires max_provider_requests >= 4 "
            "(two VQA, one patch, one comparison)"
        )
    if not 1 <= stage_timeout_seconds <= 300:
        raise ValueError("stage_timeout_seconds must be between 1 and 300")

    configuration = _load_live_configuration(user_config, provider_override)

    from services.authored_quality_acceptance import (
        local_acceptance_decision,
        style_acceptance_decision,
    )
    from utils.llm_calls import author_vision_qa
    from utils.llm_calls.author_slide_patch import propose_authored_slide_patch

    # Exact call counts require a cold request-local observation. These are successful
    # response caches only; clearing them does not alter any authored presentation data.
    author_vision_qa._vqa_cache.clear()
    author_vision_qa._vqa_inflight.clear()
    author_vision_qa._style_comparison_vqa_cache.clear()
    author_vision_qa._style_comparison_vqa_inflight.clear()
    _render_cache.clear()
    _browser_metrics_cache.clear()

    clean_html = ensure_authored_qids(_html(index=1))
    defective_html = ensure_authored_qids(_html(index=2, defective=True))
    context = (
        "Business metrics dashboard titled Hybrid review benchmark with sixteen "
        "metric cards and numeric values."
    )
    live_started = time.perf_counter()

    async with _bounded_provider_requests(max_provider_requests) as budget:
        clean_started = time.perf_counter()
        clean_png, clean_metrics = await render_html_to_png_with_metrics(clean_html)
        clean_render_ms = _elapsed_ms(clean_started)
        clean_local_started = time.perf_counter()
        clean_report = inspect_authored_dom(
            clean_html, clean_metrics, reference_mode=STYLE_REFERENCE
        )
        clean_local_ms = _elapsed_ms(clean_local_started)
        before_requests = budget.snapshot()
        clean_vqa_started = time.perf_counter()
        clean_critique = (
            await asyncio.wait_for(
                author_vision_qa.critique_authored([clean_png], contexts=[context]),
                timeout=stage_timeout_seconds,
            )
        )[0]
        clean_vqa_ms = _elapsed_ms(clean_vqa_started)
        clean_requests = budget.delta(before_requests)
        clean_total_ms = _elapsed_ms(clean_started)

        defective_started = time.perf_counter()
        defective_png, defective_metrics = await render_html_to_png_with_metrics(
            defective_html
        )
        defective_render_ms = _elapsed_ms(defective_started)
        defective_local_started = time.perf_counter()
        defective_report = inspect_authored_dom(
            defective_html, defective_metrics, reference_mode=STYLE_REFERENCE
        )
        defective_local_ms = _elapsed_ms(defective_local_started)
        before_requests = budget.snapshot()
        defective_vqa_started = time.perf_counter()
        defective_critique = (
            await asyncio.wait_for(
                author_vision_qa.critique_authored([defective_png], contexts=[context]),
                timeout=stage_timeout_seconds,
            )
        )[0]
        defective_vqa_ms = _elapsed_ms(defective_vqa_started)
        defective_vqa_requests = budget.delta(before_requests)

        patch_payload: dict[str, Any] = {
            "attempted": False,
            "proposal_ok": False,
            "strategy": None,
            "action_count": 0,
            "action_types": [],
            "provider_requests": 0,
            "proposal_ms": 0.0,
            "apply_ms": 0.0,
        }
        candidate_payload: dict[str, Any] = {
            "render_ms": 0.0,
            "comparison_vqa_ms": 0.0,
            "comparison_provider_requests": 0,
            "accepted": False,
            "acceptance_reason": "patch_not_attempted",
            "full_fallback_required": bool(
                defective_report.needs_attention
                or getattr(defective_critique, "needs_fix", False)
            ),
        }

        critique_summary = _critique_summary(defective_critique)
        if defective_report.needs_attention and critique_summary["needs_fix"]:
            patch_payload["attempted"] = True
            before_requests = budget.snapshot()
            patch_started = time.perf_counter()
            proposal = await asyncio.wait_for(
                propose_authored_slide_patch(
                    defective_png,
                    [node.to_dict() for node in defective_report.compact_dom],
                    defective_critique,
                    reference_mode=STYLE_REFERENCE,
                ),
                timeout=stage_timeout_seconds,
            )
            patch_payload["proposal_ms"] = round(_elapsed_ms(patch_started), 3)
            patch_payload["provider_requests"] = budget.delta(before_requests)
            patch_payload["proposal_ok"] = proposal.ok
            patch_payload["strategy"] = (
                proposal.plan.strategy if proposal.plan is not None else "full_reauthor"
            )
            if proposal.plan is not None:
                patch_payload["action_count"] = len(proposal.plan.actions)
                patch_payload["action_types"] = [
                    action.action for action in proposal.plan.actions
                ]

            if proposal.ok and proposal.plan is not None:
                apply_started = time.perf_counter()
                application = apply_authored_patch(defective_html, proposal.plan)
                patch_payload["apply_ms"] = round(_elapsed_ms(apply_started), 3)
                patch_payload["apply_ok"] = application.ok
                patch_payload["changed_elements"] = len(application.changes)
                if application.ok:
                    candidate_started = time.perf_counter()
                    (
                        candidate_png,
                        candidate_metrics,
                    ) = await render_html_to_png_with_metrics(application.html)
                    candidate_payload["render_ms"] = round(
                        _elapsed_ms(candidate_started), 3
                    )
                    candidate_report = inspect_authored_dom(
                        application.html,
                        candidate_metrics,
                        reference_mode=STYLE_REFERENCE,
                    )
                    candidate_payload["local"] = _local_report_summary(candidate_report)
                    local_regression = has_dom_quality_regression(
                        defective_report, candidate_report
                    )
                    before_requests = budget.snapshot()
                    comparison_started = time.perf_counter()
                    comparison_critique = (
                        await asyncio.wait_for(
                            author_vision_qa.critique_authored_style_comparison(
                                [defective_png], [candidate_png], contexts=[context]
                            ),
                            timeout=stage_timeout_seconds,
                        )
                    )[0]
                    candidate_payload["comparison_vqa_ms"] = round(
                        _elapsed_ms(comparison_started), 3
                    )
                    candidate_payload["comparison_provider_requests"] = budget.delta(
                        before_requests
                    )
                    candidate_payload["comparison"] = _critique_summary(
                        comparison_critique
                    )
                    visual_accepted, reason = style_acceptance_decision(
                        defective_critique, comparison_critique
                    )
                    accepted = visual_accepted
                    local_accepted, local_reason = local_acceptance_decision(
                        defective_report, candidate_report
                    )
                    if accepted and not local_accepted:
                        accepted, reason = False, local_reason
                    candidate_payload["visual_comparison_accepted"] = visual_accepted
                    candidate_payload["local_acceptance_reason"] = local_reason
                    candidate_payload["accepted"] = accepted
                    candidate_payload["acceptance_reason"] = reason
                    candidate_payload["local_regression"] = local_regression
                    candidate_payload["full_fallback_required"] = not accepted
        elif defective_report.needs_attention:
            candidate_payload["acceptance_reason"] = "initial_vqa_unusable_or_clean"

        defective_total_ms = _elapsed_ms(defective_started)

    return {
        "live_model_self_test": True,
        "configuration": configuration,
        "cost_gate": {
            "maximum_provider_requests": max_provider_requests,
            "actual_provider_requests": budget.requests,
            "full_reauthor_allowed": False,
            "stage_timeout_seconds": stage_timeout_seconds,
        },
        "clean_slide": {
            "render_and_metrics_ms": round(clean_render_ms, 3),
            "local_inspection_ms": round(clean_local_ms, 3),
            "local": _local_report_summary(clean_report),
            "full_resolution_vqa_ms": round(clean_vqa_ms, 3),
            "provider_requests": clean_requests,
            "vqa": _critique_summary(clean_critique),
            "total_ms": round(clean_total_ms, 3),
        },
        "problem_slide": {
            "render_and_metrics_ms": round(defective_render_ms, 3),
            "local_inspection_ms": round(defective_local_ms, 3),
            "local": _local_report_summary(defective_report),
            "full_resolution_vqa_ms": round(defective_vqa_ms, 3),
            "initial_vqa_provider_requests": defective_vqa_requests,
            "initial_vqa": critique_summary,
            "patch": patch_payload,
            "candidate": candidate_payload,
            "total_ms": round(defective_total_ms, 3),
        },
        "total_wall_ms": round(_elapsed_ms(live_started), 3),
        "privacy": {
            "credential_values_serialized": False,
            "prompt_bodies_serialized": False,
            "model_response_bodies_serialized": False,
        },
    }


async def run() -> dict:
    _render_cache.clear()
    _browser_metrics_cache.clear()

    migration_started = time.perf_counter()
    html = ensure_authored_qids(_html(defective=True))
    migration_ms = _elapsed_ms(migration_started)

    render_started = time.perf_counter()
    _png, metrics = await render_html_to_png_with_metrics(html)
    cold_render_ms = _elapsed_ms(render_started)

    inspect_started = time.perf_counter()
    report = inspect_authored_dom(
        html,
        metrics,
        reference_mode=STYLE_REFERENCE,
    )
    inspection_ms = _elapsed_ms(inspect_started)

    title_qid = next(
        node.qid for node in build_compact_dom_map(html) if node.tag == "h1"
    )
    plan = {
        "strategy": "patch",
        "actions": [
            {
                "action": "resize",
                "qid": title_qid,
                "height": "56px",
            }
        ],
    }
    samples: list[float] = []
    applied = None
    for _ in range(1000):
        started = time.perf_counter()
        applied = apply_authored_patch(html, plan)
        samples.append(_elapsed_ms(started))
    assert applied is not None and applied.ok

    candidate_started = time.perf_counter()
    _candidate_png, candidate_metrics = await render_html_to_png_with_metrics(
        applied.html
    )
    candidate_render_ms = _elapsed_ms(candidate_started)
    candidate_report = inspect_authored_dom(
        applied.html,
        candidate_metrics,
        reference_mode=STYLE_REFERENCE,
    )

    deck_htmls = [ensure_authored_qids(_html(index)) for index in range(8)]
    deck_started = time.perf_counter()
    deck_rendered = list(
        await asyncio.gather(
            *(render_html_to_png_with_metrics(item) for item in deck_htmls)
        )
    )
    deck_pngs = [png for png, _metrics in deck_rendered]
    deck_render_ms = _elapsed_ms(deck_started)
    deck_inspect_started = time.perf_counter()
    deck_reports = [
        inspect_authored_dom(
            item,
            metrics,
            reference_mode=STYLE_REFERENCE,
        )
        for item, (_png, metrics) in zip(deck_htmls, deck_rendered)
    ]
    deck_inspection_ms = _elapsed_ms(deck_inspect_started)
    route = plan_authored_review_routing(
        scope="all",
        slide_count=8,
        local_issue_indices=[
            index for index, item in enumerate(deck_reports) if item.needs_attention
        ],
        sampling_seed="benchmark",
    )
    montage_started = time.perf_counter()
    montage = _render_review_montage(
        dict(enumerate(deck_pngs)), route.montage_batches[0].slide_indices
    )
    montage_ms = _elapsed_ms(montage_started)

    ordered = sorted(samples)
    result = {
        "model_calls": 0,
        "one_slide": {
            "qid_migration_ms": round(migration_ms, 3),
            "cold_render_and_metrics_ms": round(cold_render_ms, 3),
            "local_inspection_ms": round(inspection_ms, 3),
            "inspected_elements": report.inspected_elements,
            "local_issue_count": len(report.issues),
            "patch_apply_median_ms": round(statistics.median(samples), 4),
            "patch_apply_p95_ms": round(ordered[int(len(ordered) * 0.95) - 1], 4),
            "candidate_render_and_metrics_ms": round(candidate_render_ms, 3),
            "candidate_local_issue_count": len(candidate_report.issues),
        },
        "eight_slide_deck": {
            "render_and_metrics_ms": round(deck_render_ms, 3),
            "local_inspection_ms": round(deck_inspection_ms, 3),
            "montage_build_ms": round(montage_ms, 3),
            "montage_bytes": len(montage),
            "local_suspect_count": sum(item.needs_attention for item in deck_reports),
            **_routing_result(
                8,
                local_issue_indices=[
                    index
                    for index, item in enumerate(deck_reports)
                    if item.needs_attention
                ],
            ),
        },
        "routing_boundaries": {
            "10_slide_deck": _routing_result(10),
            "11_slide_deck": _routing_result(11),
        },
    }
    result["ocr_suspect_only"] = await run_ocr_suspect_only_benchmark()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--ocr-self-test",
        action="store_true",
        help="run only the deterministic no-model OCR route benchmark",
    )
    parser.add_argument(
        "--live-self-test",
        action="store_true",
        help=(
            "run the credential-gated clean/problem live model gate; requires "
            "--acknowledge-model-cost"
        ),
    )
    parser.add_argument(
        "--acknowledge-model-cost",
        action="store_true",
        help="explicitly acknowledge that --live-self-test can incur provider cost",
    )
    parser.add_argument(
        "--user-config",
        type=Path,
        help="existing userConfig.json; credential values are never reported",
    )
    parser.add_argument(
        "--provider",
        help="optional provider override after normal user-config hydration",
    )
    parser.add_argument(
        "--max-provider-requests",
        type=int,
        default=4,
        help="hard ceiling including parse, schema, and transient retries (default: 4)",
    )
    parser.add_argument(
        "--stage-timeout-seconds",
        type=float,
        default=90.0,
        help="timeout for each live VQA/patch stage (default: 90)",
    )
    args = parser.parse_args()
    if args.ocr_self_test and args.live_self_test:
        parser.error("--ocr-self-test and --live-self-test are mutually exclusive")
    if args.live_self_test and not args.acknowledge_model_cost:
        parser.error("--live-self-test requires --acknowledge-model-cost")
    if args.live_self_test:
        result = asyncio.run(
            run_live_self_test(
                user_config=args.user_config,
                provider_override=args.provider,
                max_provider_requests=args.max_provider_requests,
                stage_timeout_seconds=args.stage_timeout_seconds,
            )
        )
    elif args.ocr_self_test:
        result = {
            "model_calls": 0,
            "ocr_suspect_only": asyncio.run(run_ocr_suspect_only_benchmark()),
        }
    else:
        result = asyncio.run(run())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
