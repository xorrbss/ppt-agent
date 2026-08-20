# ruff: noqa: B020, B905

from services import authored_quality_review_service as _service
from services.authored_deck_chapter_plan import load_chapter_plan
from services.authored_deck_illustration_plan import load_illustration_plan
from services.authored_header_contract_service import (
    load_header_contract,
    page_number_mode_for,
)


async def prepare_legacy_review(ctx):
    ctx.review_started_at = _service.time.perf_counter()
    ctx.presentation = await ctx.session.get(_service.PresentationModel, ctx.presentation_id)
    if ctx.presentation is None:
        raise ValueError("Presentation not found")
    ctx.outline = ctx.presentation.get_presentation_outline()
    if ctx.outline is None or not ctx.outline.slides:
        raise ValueError("The saved AI-authored outline is missing")
    ctx.slide_rows = await _service._execute_sqlalchemy(
        ctx.session,
        _service.select(_service.SlideModel)
        .where(_service.SlideModel.presentation == ctx.presentation_id)
        .order_by(_service.SlideModel.index),
    )
    ctx.slides = list(ctx.slide_rows.scalars().all())
    if not ctx.slides or not ctx.presentation.is_authored() or (not all((slide.is_authored() for slide in ctx.slides))):
        raise ValueError("High-quality review supports AI-authored presentations only")
    if len(ctx.outline.slides) != len(ctx.slides):
        raise ValueError("Saved outline and slide counts do not match")
    ctx.positions = _service._resolve_positions(ctx.request, len(ctx.slides))
    ctx.targets = [ctx.slides[position] for position in ctx.positions]
    if any((not slide.html_content for slide in ctx.targets)):
        raise ValueError("One or more slides are missing their editable authored HTML")
    await _service._set_status(
        ctx.session, ctx.task, status="processing", message="슬라이드 렌더링을 준비하고 있습니다."
    )
    ctx.theme = ctx.presentation.theme if isinstance(ctx.presentation.theme, dict) else {}
    ctx.saved_style_id = str(ctx.theme.get("style") or "default")
    ctx.provenance_row = await ctx.session.get(_service.AuthoredExactProvenance, ctx.presentation_id)
    ctx.exact_source_images: tuple[bytes, ...] | None = None
    if ctx.provenance_row is not None:
        ctx.private_style_id = (
            ctx.provenance_row.provenance.get("style_id") if isinstance(ctx.provenance_row.provenance, dict) else None
        )
        if not isinstance(ctx.private_style_id, str) or ctx.private_style_id != ctx.saved_style_id:
            raise _service.ExactProvenanceError("Saved exact reconstruction style identity changed")
        ctx.style = _service.resolve_authored_style(ctx.private_style_id)
        ctx.snapshot_id = ctx.provenance_row.provenance.get("source_snapshot_id")
        if ctx.snapshot_id is not None:
            if not isinstance(ctx.snapshot_id, str):
                raise _service.ExactProvenanceError("Saved exact reconstruction source snapshot is invalid")
            try:
                ctx.style = await _service.materialize_exact_source_style(
                    ctx.style, ctx.snapshot_id, expected_slide_count=len(ctx.slides)
                )
            except _service.ExactSourceSnapshotError as error:
                raise _service.ExactProvenanceError(
                    "Saved exact reconstruction source snapshot is unavailable"
                ) from error
        ctx.exact_source_images = _service.verify_exact_provenance(
            ctx.provenance_row.provenance, ctx.style, expected_slide_count=len(ctx.slides)
        )
        ctx.saved_reference_mode = _service.EXACT_RECONSTRUCTION
    else:
        ctx.legacy_reference_mode = ctx.theme.get("reference_mode")
        if (
            isinstance(ctx.legacy_reference_mode, str)
            and _service.normalize_reference_mode(ctx.legacy_reference_mode) == _service.EXACT_RECONSTRUCTION
        ):
            raise _service.ExactProvenanceError("Saved exact reconstruction provenance is missing")
        ctx.style = _service.resolve_authored_style(ctx.saved_style_id)
        if (
            _service.normalize_reference_mode(getattr(ctx.style, "reference_mode", _service.STYLE_REFERENCE))
            == _service.EXACT_RECONSTRUCTION
        ):
            raise _service.ExactProvenanceError("Saved exact reconstruction provenance is missing")
        ctx.saved_reference_mode = _service.STYLE_REFERENCE
    # Reviewing re-authors slides against this plan, so it has to carry the
    # deck's own frozen chrome rather than the built-in defaults.
    header_contract = load_header_contract(ctx.presentation.theme)
    ctx.plan = _service.prepare_authored_deck(
        ctx.outline,
        _service._saved_brand(ctx.presentation),
        ctx.style,
        page_number_mode=page_number_mode_for(header_contract),
        header_contract=header_contract,
        # Reviewing a page re-authors it, so it needs the same chapter prefix
        # and the same forced role the first pass gave it.
        chapter_plan=load_chapter_plan(ctx.presentation.theme),
        # The persisted plan is the original deck decision.  Recomputing it
        # from a review subset would give an edited page a different right to
        # use a generated picture than its untouched siblings.
        illustration_plan=load_illustration_plan(ctx.presentation.theme),
    )
    ctx.plan_reference_mode = _service.normalize_reference_mode(
        getattr(ctx.plan, "reference_mode", ctx.saved_reference_mode)
    )
    if ctx.plan_reference_mode != ctx.saved_reference_mode:
        raise ValueError("Saved authored reference mode does not match its source style")
    ctx.contents = [ctx.outline.slides[position].content for position in ctx.positions]
    ctx.roles = [
        _service._saved_role(target, ctx.plan.roles[position]) for position, target in zip(ctx.positions, ctx.targets)
    ]
    ctx.persisted_htmls = [str(target.html_content) for target in ctx.targets]
    ctx.htmls = [_service.ensure_authored_qids(html) for html in ctx.persisted_htmls]
    ctx.exact_reconstruction = ctx.plan_reference_mode == _service.EXACT_RECONSTRUCTION
    ctx.illustration_reauthor_kwargs = {}
    illustration_slots = getattr(ctx.plan, "illustration_slots", ())
    if not ctx.exact_reconstruction:
        # Slots must be aligned to ``ctx.htmls`` / ``ctx.contents`` rather than
        # the persisted outline: a subset review is addressed by local indices.
        if illustration_slots:
            ctx.illustration_reauthor_kwargs["illustration_slots"] = [
                illustration_slots[position] for position in ctx.positions
            ]
        if getattr(ctx.style, "illustration_prompt", None):
            ctx.illustration_reauthor_kwargs["image_service"] = (
                _service.ImageGenerationService(_service.get_images_directory())
            )
    ctx.final_review_batch_count = 0
    if ctx.exact_reconstruction:
        ctx.planned_references = getattr(ctx.plan, "reference_images", None)
        if ctx.planned_references is None or len(ctx.planned_references) != len(ctx.slides):
            raise ValueError("Exact reconstruction review requires a complete source mapping")
        if any((len(references) != 1 for references in ctx.planned_references)):
            raise ValueError("Exact reconstruction review requires exactly one source per slide")
        ctx.target_references = [ctx.planned_references[position] for position in ctx.positions]
    ctx.source_images = (
        [ctx.exact_source_images[position] for position in ctx.positions]
        if ctx.exact_reconstruction and ctx.exact_source_images is not None
        else None
    )
    ctx.review_metadata = _service._review_observation_metadata()
    ctx.ocr_runtime = _service._RequestOcrRuntime(_service._ocr_language(ctx.plan.brand.language))
    await _service._set_status(
        ctx.session, ctx.task, status="processing", message=f"{len(ctx.targets)}개 슬라이드를 렌더링하고 있습니다."
    )
    ctx.render_started_at = _service.time.perf_counter()
    ctx.rendered = list(await _service.asyncio.gather(*(_service._render_review_html(html) for html in ctx.htmls)))
    ctx.pngs = [png for png, _metrics in ctx.rendered]
    ctx.browser_metrics = [metrics for _png, metrics in ctx.rendered]
    ctx.render_ms = (_service.time.perf_counter() - ctx.render_started_at) * 1000
    ctx.local_started_at = _service.time.perf_counter()
    ctx.local_reports = [
        _service.inspect_authored_dom(html, metrics, reference_mode=ctx.plan_reference_mode)
        for html, metrics in zip(ctx.htmls, ctx.browser_metrics)
    ]
    ctx.local_inspection_ms = (_service.time.perf_counter() - ctx.local_started_at) * 1000
    await _service._set_status(
        ctx.session, ctx.task, status="processing", message="잘림, 겹침, 정렬, 대비를 병렬로 검사하고 있습니다."
    )
    ctx.critique_kwargs = {}
    if ctx.exact_reconstruction:
        ctx.critique_kwargs = {"source_images": ctx.source_images, "reference_mode": ctx.plan_reference_mode}
    ctx.critique_contexts = [content[:240] for content in ctx.contents]
    ctx.routing_metadata: dict = {}
    ctx.montage_ms = 0.0
    ctx.use_hybrid_deck_routing = (
        ctx.request.scope in {"all", "subset"} and (not ctx.exact_reconstruction) and (len(ctx.targets) >= 6)
    )
    if ctx.use_hybrid_deck_routing:
        (
            ctx.initial_critiques,
            ctx.routing_metadata,
            ctx.initial_cache_hits,
            ctx.initial_cache_misses,
            ctx.montage_ms,
            ctx.initial_critique_ms,
        ) = await _service._hybrid_style_initial_review(
            pngs=ctx.pngs,
            contexts=ctx.critique_contexts,
            positions=ctx.positions,
            slide_count=len(ctx.slides),
            local_reports=ctx.local_reports,
            sampling_seed=str(ctx.presentation_id),
            scope=ctx.request.scope,
        )
    else:
        ctx.routing_plan = _service.plan_authored_review_routing(
            scope=ctx.request.scope,
            slide_count=len(ctx.slides),
            local_issue_indices=[
                ctx.positions[index] for index, report in enumerate(ctx.local_reports) if report.needs_attention
            ],
            current_slide_index=ctx.positions[0] if ctx.request.scope == "current" else None,
            target_indices=ctx.positions,
            sampling_seed=str(ctx.presentation_id),
        )
        ctx.routing_metadata = {
            "plan": ctx.routing_plan.as_dict(),
            "escalation": None,
            "montage_promoted_batches": [],
            "montage_vqa_count": 0,
            "detail_vqa_count": len(ctx.targets),
            "montage_vqa_batch_count": 0,
            "detail_vqa_batch_count": 1 if ctx.targets else 0,
            "vqa_batch_count": 1 if ctx.targets else 0,
        }
        ctx.initial_cache_hits, ctx.initial_cache_misses = _service._observe_critique_cache(
            ctx.pngs, ctx.critique_contexts, source_images=ctx.source_images, reference_mode=ctx.plan_reference_mode
        )
        ctx.initial_critique_started_at = _service.time.perf_counter()
        ctx.initial_critiques = list(
            await _service.critique_authored(ctx.pngs, contexts=ctx.critique_contexts, **ctx.critique_kwargs)
        )
        if len(ctx.initial_critiques) != len(ctx.targets):
            ctx.initial_critiques = (ctx.initial_critiques + [None] * len(ctx.targets))[: len(ctx.targets)]
        ctx.initial_critique_ms = (_service.time.perf_counter() - ctx.initial_critique_started_at) * 1000
    ctx.initial_critiques = _service._merge_dom_repair_triggers(ctx.initial_critiques, ctx.local_reports)
    ctx.initial_indeterminate_count = sum(
        (_service._critique_unusable_reason(critique) == "indeterminate" for critique in ctx.initial_critiques)
    )
    ctx.ocr_checks: list[dict[str, _service.Any]] = []
    for ctx.local_index, (ctx.position, ctx.local_report, ctx.initial_critique) in enumerate(
        zip(ctx.positions, ctx.local_reports, ctx.initial_critiques)
    ):
        ctx.ocr_checks.append(
            await _service._capture_initial_ocr(
                runtime=ctx.ocr_runtime,
                slide_index=ctx.position,
                reference_mode=ctx.plan_reference_mode,
                source_image=ctx.source_images[ctx.local_index] if ctx.source_images is not None else None,
                baseline_image=ctx.pngs[ctx.local_index],
                baseline_metrics=ctx.browser_metrics[ctx.local_index],
                signals=_service._ocr_route_signals(ctx.local_report, ctx.initial_critique),
            )
        )
    ctx.ocr_summary = ctx.ocr_runtime.summary()
    _service._log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="initial_review",
        slide_count=len(ctx.pngs),
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        critique_batch_count=ctx.routing_metadata["vqa_batch_count"],
        critique_slide_request_count=ctx.initial_cache_misses,
        cache_hits=ctx.initial_cache_hits,
        cache_misses=ctx.initial_cache_misses,
        render_ms=ctx.render_ms,
        critique_ms=ctx.initial_critique_ms,
        indeterminate_count=ctx.initial_indeterminate_count,
        **ctx.ocr_summary,
    )
    if ctx.exact_reconstruction and any(
        (_service._critique_score(critique) is None for critique in ctx.initial_critiques)
    ):
        raise RuntimeError("Exact reconstruction comparison failed; no slide was treated as clean")
    ctx.initial_issues = _service._serialize_critiques(ctx.positions, ctx.initial_critiques)
