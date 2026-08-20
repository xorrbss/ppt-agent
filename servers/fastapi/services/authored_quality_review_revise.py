from services import authored_quality_review_service as _service


async def revise_legacy_review(ctx):
    await _service._set_status(
        ctx.session,
        ctx.task,
        status="processing",
        message=f"문제가 발견된 {len(ctx.initial_issues)}개 슬라이드를 자동 수정하고 있습니다.",
    )
    ctx.revise_kwargs = {"initial_critiques": ctx.initial_critiques, "return_details": True}
    # Test doubles substitute a lightweight plan object, and the deck-plan
    # dataclass only carries prefixes for a chaptered deck, so both absences
    # mean the same thing: this review re-authors without a breadcrumb.
    plan_prefixes = getattr(ctx.plan, "chapter_prefixes", ())
    if plan_prefixes:
        ctx.revise_kwargs["chapter_prefixes"] = [
            plan_prefixes[position] for position in ctx.positions
        ]
    ctx.candidate_decisions: list[dict] = []
    if ctx.exact_reconstruction:
        ctx.revise_kwargs["reference_images_by_slide"] = ctx.target_references
    ctx.revise_kwargs.update(getattr(ctx, "illustration_reauthor_kwargs", {}))
    ctx.revision_started_at = _service.time.perf_counter()
    ctx.revision_result = await _service.revise_authored_deck(
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
        **ctx.revise_kwargs,
    )
    if len(ctx.revision_result) == 4:
        ctx.revised_htmls, ctx.revised_pngs, ctx.candidate_local_indices, ctx.repair_details = ctx.revision_result
    else:
        ctx.revised_htmls, ctx.revised_pngs, ctx.candidate_local_indices = ctx.revision_result
        ctx.repair_details = []
    ctx.candidate_local_indices = list(dict.fromkeys(ctx.candidate_local_indices))
    if any((type(index) is not int or index < 0 or index >= len(ctx.targets) for index in ctx.candidate_local_indices)):
        raise RuntimeError("Authored quality review returned an invalid candidate index")
    ctx.revision_ms = (_service.time.perf_counter() - ctx.revision_started_at) * 1000
    ctx.candidate_browser_metrics = {
        index: _service.get_cached_browser_metrics(ctx.revised_htmls[index]) for index in ctx.candidate_local_indices
    }
    ctx.candidate_local_reports = {
        index: _service.inspect_authored_dom(
            ctx.revised_htmls[index], ctx.candidate_browser_metrics[index], reference_mode=ctx.plan_reference_mode
        )
        for index in ctx.candidate_local_indices
    }
    ctx.candidate_local_decisions = {
        index: _service._local_acceptance_decision(ctx.local_reports[index], report)
        for index, report in ctx.candidate_local_reports.items()
    }
    ctx.fallback_local_reports: dict[int, _service.DomQualityReport] = {}
    _service._log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="correction",
        slide_count=len(ctx.targets),
        attempted=len(ctx.initial_issues),
        accepted=len(ctx.candidate_local_indices),
        rejection_reasons={"no_candidate": len(ctx.initial_issues) - len(ctx.candidate_local_indices)}
        if len(ctx.candidate_local_indices) < len(ctx.initial_issues)
        else None,
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        render_ms=ctx.render_ms,
        revision_ms=ctx.revision_ms,
        **ctx.ocr_runtime.summary(),
    )
    await _service._set_status(
        ctx.session, ctx.task, status="processing", message="수정 결과를 다시 검증하고 있습니다."
    )
