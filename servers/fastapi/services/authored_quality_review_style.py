# ruff: noqa: B020, B905

from services import authored_quality_review_service as _service


async def review_style_candidates(ctx):
    ctx.accepted_local_indices = []
    ctx.chosen_critiques = list(ctx.initial_critiques)
    ctx.comparison_metadata = _service._style_comparison_observation_metadata()
    ctx.final_critique_ms = 0.0
    ctx.candidate_critiques: list[_service.SlideCritique | None] = []
    ctx.final_cache_hits = 0
    ctx.final_cache_misses = 0
    ctx.fallback_comparison_batch_count = 0
    ctx.repair_by_index = {
        detail.get("local_index"): detail
        for detail in ctx.repair_details
        if isinstance(detail, _service.Mapping) and isinstance(detail.get("local_index"), int)
    }
    if ctx.candidate_local_indices:
        ctx.baseline_candidate_pngs = [ctx.pngs[index] for index in ctx.candidate_local_indices]
        ctx.candidate_pngs = [ctx.revised_pngs[index] for index in ctx.candidate_local_indices]
        ctx.candidate_contexts = [ctx.contents[index][:240] for index in ctx.candidate_local_indices]
        ctx.final_cache_hits, ctx.final_cache_misses = _service._observe_style_comparison_cache(
            ctx.baseline_candidate_pngs, ctx.candidate_pngs, ctx.candidate_contexts
        )
        ctx.final_critique_started_at = _service.time.perf_counter()
        ctx.candidate_critiques = list(
            await _service.critique_authored_style_comparison(
                ctx.baseline_candidate_pngs, ctx.candidate_pngs, contexts=ctx.candidate_contexts
            )
        )
        ctx.final_critique_ms = (_service.time.perf_counter() - ctx.final_critique_started_at) * 1000
        for ctx.offset, ctx.local_index in enumerate(ctx.candidate_local_indices):
            ctx.final_critique = (
                ctx.candidate_critiques[ctx.offset] if ctx.offset < len(ctx.candidate_critiques) else None
            )
            ctx.accepted, ctx.reason = _service._style_acceptance_decision(
                ctx.initial_critiques[ctx.local_index], ctx.final_critique
            )
            ctx.local_accepted, ctx.local_reason = ctx.candidate_local_decisions[ctx.local_index]
            if ctx.accepted and (not ctx.local_accepted):
                ctx.accepted, ctx.reason = (False, ctx.local_reason)
            ctx.path = str(ctx.repair_by_index.get(ctx.local_index, {}).get("path") or "none")
            ctx.repair_detail = ctx.repair_by_index.get(ctx.local_index, {})
            ctx.ocr_required, ctx.ocr_accepted, ctx.ocr_reason = await _service._evaluate_candidate_ocr(
                runtime=ctx.ocr_runtime,
                entry=ctx.ocr_checks[ctx.local_index],
                reference_mode=ctx.plan_reference_mode,
                source_image=None,
                baseline_image=ctx.pngs[ctx.local_index],
                candidate_image=ctx.revised_pngs[ctx.local_index],
                baseline_metrics=ctx.browser_metrics[ctx.local_index],
                candidate_metrics=ctx.candidate_browser_metrics.get(ctx.local_index),
                signals=_service._ocr_route_signals(
                    ctx.candidate_local_reports.get(ctx.local_index), ctx.final_critique, ctx.repair_detail
                ),
                path=ctx.path,
            )
            if ctx.accepted and ctx.ocr_required and (not ctx.ocr_accepted):
                ctx.accepted, ctx.reason = (False, str(ctx.ocr_reason or "ocr_unavailable"))
            ctx.candidate_decisions.append(
                {
                    "slide_index": ctx.positions[ctx.local_index],
                    "accepted": ctx.accepted,
                    "reason": ctx.reason,
                    "attempted": True,
                    "candidate_proposed": True,
                    "candidate_rendered": True,
                    "validation_status": "passed" if ctx.accepted else "rejected",
                    "apply_status": "not_started",
                    "publish_status": "not_started",
                    "post_verify_status": "not_started",
                    "applied": False,
                    "rolled_back": False,
                    "rejection_reason": None if ctx.accepted else ctx.reason,
                    "path": ctx.path,
                }
            )
            if ctx.accepted:
                ctx.accepted_local_indices.append(ctx.local_index)
                ctx.chosen_critiques[ctx.local_index] = ctx.final_critique
    ctx.fallback_eligible_reasons = {
        "unchanged",
        "worsened",
        "regression",
        "local_regression",
        "local_baseline_unavailable",
        "local_candidate_unavailable",
        "local_high_remaining",
        "local_not_improved",
        "ocr_not_improved",
        "ocr_regression",
    }
    ctx.decisions_by_position = {decision["slide_index"]: decision for decision in ctx.candidate_decisions}
    ctx.fallback_local_indices = [
        local_index
        for local_index in ctx.candidate_local_indices
        if ctx.decisions_by_position[ctx.positions[local_index]]["reason"] in ctx.fallback_eligible_reasons
        and ctx.decisions_by_position[ctx.positions[local_index]]["path"] == "local_patch"
    ]
    if ctx.fallback_local_indices:
        ctx.clean_critique = _service.SlideCritique(needs_fix=False, issues=[])
        ctx.fallback_initial_critiques = [
            ctx.initial_critiques[index] if index in ctx.fallback_local_indices else ctx.clean_critique
            for index in range(len(ctx.targets))
        ]
        ctx.fallback_started_at = _service.time.perf_counter()
        ctx.fallback_result = await _service.revise_authored_deck(
            ctx.htmls,
            ctx.pngs,
            ctx.contents,
            ctx.roles,
            ctx.plan.brand,
            ctx.plan.design_system,
            max_cycles=1,
            style=ctx.style,
            slide_indices=ctx.positions,
            total_slides=len(ctx.slides),
            initial_critiques=ctx.fallback_initial_critiques,
            return_details=True,
            force_full_reauthor_indices=ctx.fallback_local_indices,
        )
        ctx.revision_ms += (_service.time.perf_counter() - ctx.fallback_started_at) * 1000
        if len(ctx.fallback_result) == 4:
            ctx.legacy_fallback_result = False
            ctx.fallback_htmls, ctx.fallback_pngs, ctx.fallback_candidate_indices, ctx.fallback_repair_details = (
                ctx.fallback_result
            )
        else:
            ctx.legacy_fallback_result = True
            ctx.fallback_htmls, ctx.fallback_pngs, ctx.fallback_candidate_indices = ctx.fallback_result
            ctx.fallback_repair_details = []
        ctx.fallback_candidate_indices = list(dict.fromkeys(ctx.fallback_candidate_indices))
        if any(
            (
                type(index) is not int or index not in ctx.fallback_local_indices
                for index in ctx.fallback_candidate_indices
            )
        ):
            raise RuntimeError("Authored quality review returned an invalid fallback candidate index")
        if ctx.legacy_fallback_result:
            ctx.fallback_candidate_set = set(ctx.fallback_candidate_indices)
            ctx.fallback_repair_details = []
            for ctx.index in ctx.fallback_local_indices:
                ctx.succeeded = ctx.index in ctx.fallback_candidate_set
                ctx.failure = None
                if not ctx.succeeded:
                    ctx.failure = {
                        "stage": "full_reauthor",
                        "code": "fallback_failed",
                        "error_type": "UnknownFallbackFailure",
                    }
                ctx.fallback_repair_details.append(
                    {
                        "local_index": ctx.index,
                        "slide_index": ctx.positions[ctx.index],
                        "path": "full_fallback",
                        "attempted": True,
                        "succeeded": ctx.succeeded,
                        "failure_detail": ctx.failure,
                        "patch": {"attempted": False, "succeeded": False, "failure_detail": None},
                        "fallback": {"attempted": True, "succeeded": ctx.succeeded, "failure_detail": ctx.failure},
                        "element_changes": [],
                    }
                )
        ctx.repair_details.extend(ctx.fallback_repair_details)
        ctx.fallback_detail_by_index = {
            detail.get("local_index"): detail
            for detail in ctx.fallback_repair_details
            if isinstance(detail, _service.Mapping) and isinstance(detail.get("local_index"), int)
        }
        ctx.fallback_browser_metrics = {
            index: _service.get_cached_browser_metrics(ctx.fallback_htmls[index])
            for index in ctx.fallback_candidate_indices
        }
        ctx.fallback_local_reports = {
            index: _service.inspect_authored_dom(
                ctx.fallback_htmls[index], ctx.fallback_browser_metrics[index], reference_mode=ctx.plan_reference_mode
            )
            for index in ctx.fallback_candidate_indices
        }
        ctx.fallback_local_decisions = {
            index: _service._local_acceptance_decision(ctx.local_reports[index], report)
            for index, report in ctx.fallback_local_reports.items()
        }
        ctx.fallback_critiques: list[_service.SlideCritique | None] = []
        if ctx.fallback_candidate_indices:
            ctx.fallback_comparison_batch_count = 1
            ctx.fallback_baseline_pngs = [ctx.pngs[index] for index in ctx.fallback_candidate_indices]
            ctx.fallback_candidate_pngs = [ctx.fallback_pngs[index] for index in ctx.fallback_candidate_indices]
            ctx.fallback_contexts = [ctx.contents[index][:240] for index in ctx.fallback_candidate_indices]
            ctx.cache_hits, ctx.cache_misses = _service._observe_style_comparison_cache(
                ctx.fallback_baseline_pngs, ctx.fallback_candidate_pngs, ctx.fallback_contexts
            )
            ctx.final_cache_hits += ctx.cache_hits
            ctx.final_cache_misses += ctx.cache_misses
            ctx.fallback_review_started_at = _service.time.perf_counter()
            ctx.fallback_critiques = list(
                await _service.critique_authored_style_comparison(
                    ctx.fallback_baseline_pngs, ctx.fallback_candidate_pngs, contexts=ctx.fallback_contexts
                )
            )
            ctx.final_critique_ms += (_service.time.perf_counter() - ctx.fallback_review_started_at) * 1000
            ctx.candidate_critiques.extend(ctx.fallback_critiques)
        ctx.fallback_critique_by_index = {
            local_index: ctx.fallback_critiques[offset] if offset < len(ctx.fallback_critiques) else None
            for offset, local_index in enumerate(ctx.fallback_candidate_indices)
        }
        for ctx.local_index in ctx.fallback_local_indices:
            ctx.decision = ctx.decisions_by_position[ctx.positions[ctx.local_index]]
            ctx.initial_rejection_reason = ctx.decision["reason"]
            ctx.final_critique = ctx.fallback_critique_by_index.get(ctx.local_index)
            if ctx.local_index not in ctx.fallback_candidate_indices:
                ctx.failure = ctx.fallback_detail_by_index.get(ctx.local_index, {}).get("failure_detail") or {}
                ctx.accepted, ctx.reason = (False, str(ctx.failure.get("code") or "fallback_failed"))
            else:
                ctx.accepted, ctx.reason = _service._style_acceptance_decision(
                    ctx.initial_critiques[ctx.local_index], ctx.final_critique
                )
                ctx.local_accepted, ctx.local_reason = ctx.fallback_local_decisions[ctx.local_index]
                if ctx.accepted and (not ctx.local_accepted):
                    ctx.accepted, ctx.reason = (False, ctx.local_reason)
                ctx.fallback_detail = ctx.fallback_detail_by_index.get(ctx.local_index, {})
                ctx.ocr_required, ctx.ocr_accepted, ctx.ocr_reason = await _service._evaluate_candidate_ocr(
                    runtime=ctx.ocr_runtime,
                    entry=ctx.ocr_checks[ctx.local_index],
                    reference_mode=ctx.plan_reference_mode,
                    source_image=None,
                    baseline_image=ctx.pngs[ctx.local_index],
                    candidate_image=ctx.fallback_pngs[ctx.local_index],
                    baseline_metrics=ctx.browser_metrics[ctx.local_index],
                    candidate_metrics=ctx.fallback_browser_metrics.get(ctx.local_index),
                    signals=_service._ocr_route_signals(
                        ctx.fallback_local_reports.get(ctx.local_index), ctx.final_critique, ctx.fallback_detail
                    ),
                    path="full_fallback",
                )
                if ctx.accepted and ctx.ocr_required and (not ctx.ocr_accepted):
                    ctx.accepted, ctx.reason = (False, str(ctx.ocr_reason or "ocr_unavailable"))
            ctx.decision.update(
                {
                    "accepted": ctx.accepted,
                    "reason": ctx.reason,
                    "attempted": True,
                    "candidate_proposed": True,
                    "candidate_rendered": ctx.local_index in ctx.fallback_candidate_indices,
                    "validation_status": "passed" if ctx.accepted else "rejected",
                    "apply_status": "not_started",
                    "publish_status": "not_started",
                    "post_verify_status": "not_started",
                    "applied": False,
                    "rolled_back": False,
                    "rejection_reason": None if ctx.accepted else ctx.reason,
                    "path": "full_fallback",
                    "initial_rejection_reason": ctx.initial_rejection_reason,
                }
            )
            if ctx.accepted:
                ctx.revised_htmls[ctx.local_index] = ctx.fallback_htmls[ctx.local_index]
                ctx.revised_pngs[ctx.local_index] = ctx.fallback_pngs[ctx.local_index]
                ctx.accepted_local_indices.append(ctx.local_index)
                ctx.chosen_critiques[ctx.local_index] = ctx.final_critique
    ctx.rejection_reasons = _service.Counter(
        (decision["reason"] for decision in ctx.candidate_decisions if not decision["accepted"])
    )
    ctx.fixed_local_indices = ctx.accepted_local_indices
    ctx.final_critiques = ctx.chosen_critiques
    ctx.final_review_batch_count = (1 if ctx.candidate_local_indices else 0) + ctx.fallback_comparison_batch_count
    _service._log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="final_review",
        slide_count=len(ctx.candidate_critiques),
        attempted=len(ctx.candidate_critiques),
        model=ctx.comparison_metadata["model"],
        prompt_version=ctx.comparison_metadata["prompt_version"],
        cache_version=ctx.comparison_metadata["cache_version"],
        critique_batch_count=(1 if ctx.candidate_local_indices else 0) + ctx.fallback_comparison_batch_count,
        critique_slide_request_count=ctx.final_cache_misses,
        cache_hits=ctx.final_cache_hits,
        cache_misses=ctx.final_cache_misses,
        render_ms=ctx.render_ms,
        critique_ms=ctx.final_critique_ms,
        revision_ms=ctx.revision_ms,
        indeterminate_count=sum(
            (_service._critique_unusable_reason(critique) == "indeterminate" for critique in ctx.candidate_critiques)
        ),
        **ctx.ocr_runtime.summary(),
    )
    _service._log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="acceptance",
        slide_count=len(ctx.targets),
        attempted=len(ctx.candidate_decisions),
        accepted=len(ctx.accepted_local_indices),
        rejection_reasons=ctx.rejection_reasons,
        model=ctx.comparison_metadata["model"],
        prompt_version=ctx.comparison_metadata["prompt_version"],
        cache_version=ctx.comparison_metadata["cache_version"],
        render_ms=ctx.render_ms,
        revision_ms=ctx.revision_ms,
        indeterminate_count=sum(
            (_service._critique_unusable_reason(critique) == "indeterminate" for critique in ctx.candidate_critiques)
        ),
        **ctx.ocr_runtime.summary(),
    )
