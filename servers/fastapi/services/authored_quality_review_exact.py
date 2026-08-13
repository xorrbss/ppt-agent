# ruff: noqa: B020, B905

from services import authored_quality_review_service as _service


async def review_exact_candidates(ctx):
    ctx.accepted_local_indices: list[int] = []
    ctx.chosen_critiques = list(ctx.initial_critiques)
    ctx.rejection_reasons: _service.Counter[str] = _service.Counter()
    ctx.final_cache_hits = 0
    ctx.final_cache_misses = 0
    ctx.final_critique_ms = 0.0
    if ctx.candidate_local_indices:
        ctx.final_review_batch_count = 1
        ctx.candidate_pngs = [ctx.revised_pngs[index] for index in ctx.candidate_local_indices]
        ctx.candidate_contexts = [ctx.contents[index][:240] for index in ctx.candidate_local_indices]
        ctx.candidate_sources = [ctx.source_images[index] for index in ctx.candidate_local_indices]
        ctx.final_cache_hits, ctx.final_cache_misses = _service._observe_critique_cache(
            ctx.candidate_pngs,
            ctx.candidate_contexts,
            source_images=ctx.candidate_sources,
            reference_mode=ctx.plan_reference_mode,
        )
        ctx.final_critique_started_at = _service.time.perf_counter()
        ctx.candidate_critiques = await _service.critique_authored(
            ctx.candidate_pngs,
            contexts=ctx.candidate_contexts,
            source_images=ctx.candidate_sources,
            reference_mode=ctx.plan_reference_mode,
        )
        ctx.final_critique_ms = (_service.time.perf_counter() - ctx.final_critique_started_at) * 1000
        for ctx.offset, ctx.local_index in enumerate(ctx.candidate_local_indices):
            ctx.final_critique = (
                ctx.candidate_critiques[ctx.offset] if ctx.offset < len(ctx.candidate_critiques) else None
            )
            ctx.accepted, ctx.reason = _service._acceptance_decision(
                ctx.initial_critiques[ctx.local_index], ctx.final_critique
            )
            ctx.local_accepted, ctx.local_reason = ctx.candidate_local_decisions[ctx.local_index]
            if ctx.accepted and (not ctx.local_accepted):
                ctx.accepted, ctx.reason = (False, ctx.local_reason)
            ctx.repair_detail = next(
                (detail for detail in reversed(ctx.repair_details) if detail.get("local_index") == ctx.local_index), {}
            )
            ctx.path = str(ctx.repair_detail.get("path") or "none")
            ctx.ocr_required, ctx.ocr_accepted, ctx.ocr_reason = await _service._evaluate_candidate_ocr(
                runtime=ctx.ocr_runtime,
                entry=ctx.ocr_checks[ctx.local_index],
                reference_mode=ctx.plan_reference_mode,
                source_image=ctx.source_images[ctx.local_index],
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
            else:
                ctx.rejection_reasons[ctx.reason] += 1
        _service._log_review_event(
            reference_mode=ctx.plan_reference_mode,
            stage="final_review",
            slide_count=len(ctx.candidate_local_indices),
            attempted=len(ctx.candidate_local_indices),
            model=ctx.review_metadata["model"],
            prompt_version=ctx.review_metadata["prompt_version"],
            cache_version=ctx.review_metadata["cache_version"],
            critique_batch_count=1,
            critique_slide_request_count=ctx.final_cache_misses,
            cache_hits=ctx.final_cache_hits,
            cache_misses=ctx.final_cache_misses,
            render_ms=ctx.render_ms,
            critique_ms=ctx.final_critique_ms,
            revision_ms=ctx.revision_ms,
            indeterminate_count=sum(
                (
                    _service._critique_unusable_reason(critique) == "indeterminate"
                    for critique in ctx.candidate_critiques
                )
            ),
            **ctx.ocr_runtime.summary(),
        )
    ctx.fixed_local_indices = ctx.accepted_local_indices
    ctx.final_critiques = ctx.chosen_critiques
    _service._log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="acceptance",
        slide_count=len(ctx.targets),
        attempted=len(ctx.candidate_local_indices),
        accepted=len(ctx.accepted_local_indices),
        rejection_reasons=ctx.rejection_reasons,
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        render_ms=ctx.render_ms,
        revision_ms=ctx.revision_ms,
        indeterminate_count=sum(
            (_service._critique_unusable_reason(critique) == "indeterminate" for critique in ctx.final_critiques)
        ),
        **ctx.ocr_runtime.summary(),
    )
