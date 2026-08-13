# ruff: noqa: B020, B905

from services import authored_quality_review_service as _service


async def finalize_legacy_review(ctx):
    ctx.remaining_issues = _service._serialize_critiques(ctx.positions, ctx.final_critiques)
    ctx.fixed_positions = [ctx.positions[index] for index in ctx.fixed_local_indices]
    ctx.persist_ms = 0.0
    if ctx.fixed_local_indices:
        ctx.persist_started_at = _service.time.perf_counter()
        ctx.pending_updates: list[tuple[int, str, dict, dict]] = []
        for ctx.local_index in ctx.fixed_local_indices:
            ctx.position = ctx.positions[ctx.local_index]
            ctx.slide = ctx.targets[ctx.local_index]
            ctx.image_reference = _service._write_reviewed_png(
                ctx.presentation_id, ctx.position, ctx.revised_pngs[ctx.local_index]
            )
            ctx.pending_updates.append(
                (
                    ctx.local_index,
                    ctx.revised_htmls[ctx.local_index],
                    {
                        **(ctx.slide.content if isinstance(ctx.slide.content, dict) else {}),
                        "__authored__": True,
                        "image": ctx.image_reference,
                        "role": ctx.roles[ctx.local_index],
                    },
                    {
                        **(ctx.slide.properties if isinstance(ctx.slide.properties, dict) else {}),
                        "image": ctx.image_reference,
                    },
                )
            )
        await _service.version_service.snapshot_slides(
            ctx.session,
            ctx.presentation_id,
            [slide.model_dump(mode="json") for slide in ctx.slides],
            label="고품질 검수 전",
            force=True,
        )
        for ctx.local_index, ctx.revised_html, ctx.revised_content, ctx.revised_properties in ctx.pending_updates:
            ctx.slide = ctx.targets[ctx.local_index]
            ctx.result = await _service._execute_sqlalchemy(
                ctx.session,
                _service.update(_service.SlideModel)
                .where(
                    _service.SlideModel.id == ctx.slide.id,
                    _service.SlideModel.html_content == ctx.persisted_htmls[ctx.local_index],
                    _service.SlideModel.content == ctx.slide.content,
                    _service.SlideModel.properties == ctx.slide.properties,
                )
                .values(html_content=ctx.revised_html, content=ctx.revised_content, properties=ctx.revised_properties)
                .execution_options(synchronize_session=False),
            )
            if ctx.result.rowcount != 1:
                await ctx.session.rollback()
                raise RuntimeError("Presentation changed during quality review; no fixes were applied")
        await ctx.session.flush()
        ctx.expected_by_id = {
            ctx.targets[local_index].id: (revised_html, revised_content, revised_properties)
            for local_index, revised_html, revised_content, revised_properties in ctx.pending_updates
        }
        ctx.readback = await _service._execute_sqlalchemy(
            ctx.session,
            _service.select(
                _service.SlideModel.id,
                _service.SlideModel.html_content,
                _service.SlideModel.content,
                _service.SlideModel.properties,
            ).where(_service.SlideModel.id.in_(tuple(ctx.expected_by_id))),
        )
        ctx.readback_by_id = {row.id: (row.html_content, row.content, row.properties) for row in ctx.readback.all()}
        if ctx.readback_by_id != ctx.expected_by_id:
            await ctx.session.rollback()
            raise RuntimeError("Published authored slide read-back did not match")
        ctx.fixed_position_set = set(ctx.fixed_positions)
        for ctx.decision in ctx.candidate_decisions:
            if ctx.decision.get("slide_index") not in ctx.fixed_position_set:
                continue
            ctx.html = ctx.revised_htmls[ctx.positions.index(ctx.decision["slide_index"])]
            ctx.decision.update(
                {
                    "apply_status": "committed",
                    "publish_status": "active",
                    "post_verify_status": "passed",
                    "html_hash": "sha256:" + _service.hashlib.sha256(ctx.html.encode("utf-8")).hexdigest(),
                    "applied": True,
                }
            )
        ctx.persist_ms = (_service.time.perf_counter() - ctx.persist_started_at) * 1000
    ctx.task_rejection_reasons: _service.Counter[str] = _service.Counter(
        (decision["reason"] for decision in ctx.candidate_decisions if not decision["accepted"])
    )
    ctx.no_candidate_count = max(0, len(ctx.initial_issues) - len(ctx.candidate_local_indices))
    if ctx.no_candidate_count:
        ctx.task_rejection_reasons["no_candidate"] += ctx.no_candidate_count
    ctx.fallback_count = sum(
        (
            detail.get("path") == "full_fallback" and detail.get("succeeded", True)
            for detail in ctx.repair_details
            if isinstance(detail, _service.Mapping)
        )
    )
    ctx.patch_count = sum(
        (
            detail.get("path") == "local_patch" and detail.get("succeeded", True)
            for detail in ctx.repair_details
            if isinstance(detail, _service.Mapping)
        )
    )
    ctx.patch_attempts = sum(
        (
            bool((detail.get("patch") or {}).get("attempted"))
            for detail in ctx.repair_details
            if isinstance(detail, _service.Mapping)
        )
    )
    ctx.patch_successes = sum(
        (
            bool((detail.get("patch") or {}).get("succeeded"))
            for detail in ctx.repair_details
            if isinstance(detail, _service.Mapping)
        )
    )
    ctx.fallback_attempts = sum(
        (
            bool((detail.get("fallback") or {}).get("attempted"))
            for detail in ctx.repair_details
            if isinstance(detail, _service.Mapping)
        )
    )
    ctx.fallback_successes = sum(
        (
            bool((detail.get("fallback") or {}).get("succeeded"))
            for detail in ctx.repair_details
            if isinstance(detail, _service.Mapping)
        )
    )
    ctx.ocr_summary = ctx.ocr_runtime.summary()
    ctx.funnel_metrics = {
        "candidate_proposed": sum((bool(decision.get("candidate_proposed")) for decision in ctx.candidate_decisions)),
        "candidate_rendered": sum((bool(decision.get("candidate_rendered")) for decision in ctx.candidate_decisions)),
        "validation_passed": sum(
            (decision.get("validation_status") == "passed" for decision in ctx.candidate_decisions)
        ),
        "validation_rejected": sum(
            (decision.get("validation_status") == "rejected" for decision in ctx.candidate_decisions)
        ),
        "validation_inconclusive": sum(
            (decision.get("validation_status") == "inconclusive" for decision in ctx.candidate_decisions)
        ),
        "apply_committed": sum((decision.get("apply_status") == "committed" for decision in ctx.candidate_decisions)),
        "publish_active": sum((decision.get("publish_status") == "active" for decision in ctx.candidate_decisions)),
        "postverify_passed": sum(
            (decision.get("post_verify_status") == "passed" for decision in ctx.candidate_decisions)
        ),
        "applied": sum((bool(decision.get("applied")) for decision in ctx.candidate_decisions)),
        "rolled_back": sum((bool(decision.get("rolled_back")) for decision in ctx.candidate_decisions)),
        "legacy_preserved": len(ctx.targets)
        - sum((bool(decision.get("applied")) for decision in ctx.candidate_decisions)),
    }
    ctx.total_ms = (_service.time.perf_counter() - ctx.review_started_at) * 1000
    ctx.final_local_reports = list(ctx.local_reports)
    for ctx.local_index in ctx.fixed_local_indices:
        ctx.accepted_report = ctx.fallback_local_reports.get(ctx.local_index) or ctx.candidate_local_reports.get(
            ctx.local_index
        )
        if ctx.accepted_report is not None:
            ctx.final_local_reports[ctx.local_index] = ctx.accepted_report
    ctx.local_high_remaining = sum(
        (any((issue.severity == "high" for issue in report.issues)) for report in ctx.final_local_reports)
    )
    _service._log_review_event(
        reference_mode=ctx.plan_reference_mode,
        stage="completed",
        slide_count=len(ctx.targets),
        attempted=len(ctx.candidate_decisions),
        accepted=len(ctx.fixed_local_indices),
        rejection_reasons=ctx.task_rejection_reasons,
        model=ctx.review_metadata["model"],
        prompt_version=ctx.review_metadata["prompt_version"],
        cache_version=ctx.review_metadata["cache_version"],
        critique_batch_count=ctx.routing_metadata["vqa_batch_count"] + ctx.final_review_batch_count,
        critique_slide_request_count=ctx.initial_cache_misses + ctx.final_cache_misses,
        cache_hits=ctx.initial_cache_hits + ctx.final_cache_hits,
        cache_misses=ctx.initial_cache_misses + ctx.final_cache_misses,
        render_ms=ctx.render_ms,
        local_inspection_ms=ctx.local_inspection_ms,
        montage_ms=ctx.montage_ms,
        critique_ms=ctx.initial_critique_ms + ctx.final_critique_ms,
        revision_ms=ctx.revision_ms,
        persist_ms=ctx.persist_ms,
        total_ms=ctx.total_ms,
        patch_attempted=ctx.patch_attempts,
        patch_accepted=ctx.patch_successes,
        patch_rejected=max(0, ctx.patch_attempts - ctx.patch_successes),
        fallback_attempted=ctx.fallback_attempts,
        fallback_succeeded=ctx.fallback_successes,
        fallback_failed=max(0, ctx.fallback_attempts - ctx.fallback_successes),
        local_high_remaining=ctx.local_high_remaining,
        indeterminate_count=sum(
            (_service._critique_unusable_reason(critique) == "indeterminate" for critique in ctx.final_critiques)
        ),
        **ctx.ocr_summary,
    )
    await _service._set_status(
        ctx.session,
        ctx.task,
        status="completed",
        message=f"검수가 완료되었습니다. {len(ctx.fixed_positions)}개 슬라이드를 수정했습니다."
        if ctx.fixed_positions
        else "검수는 완료되었지만 자동 수정할 수 있는 변경은 생성되지 않았습니다.",
        data={
            "presentation_id": str(ctx.presentation_id),
            "mode": ctx.request.mode,
            "scope": ctx.request.scope,
            "reviewed_count": len(ctx.targets),
            "issue_slide_count": len(ctx.initial_issues),
            "issues": ctx.initial_issues,
            "fixed_count": len(ctx.fixed_positions),
            "fixed_slide_indices": ctx.fixed_positions,
            "candidate_count": len(ctx.candidate_local_indices),
            "accepted_candidate_count": len(ctx.fixed_positions),
            "rejected_candidate_count": len(ctx.candidate_local_indices) - len(ctx.fixed_positions),
            "rejection_reasons": dict(sorted(ctx.task_rejection_reasons.items())),
            "candidate_decisions": ctx.candidate_decisions,
            "remaining_issue_count": len(ctx.remaining_issues),
            "remaining_slide_indices": [issue["slide_index"] for issue in ctx.remaining_issues],
            "remaining_issues": ctx.remaining_issues,
            "version_saved": bool(ctx.fixed_positions),
            "slide_results": _service._build_slide_results(
                positions=ctx.positions,
                initial_critiques=ctx.initial_critiques,
                final_critiques=ctx.final_critiques,
                repair_details=ctx.repair_details,
                candidate_decisions=ctx.candidate_decisions,
            ),
            "local_checks": [
                {
                    "slide_index": position,
                    "telemetry_status": report.telemetry_status,
                    "inspected_elements": report.inspected_elements,
                    "issues": [issue.to_dict() for issue in report.issues],
                }
                for position, report in zip(ctx.positions, ctx.local_reports)
            ],
            "ocr_checks": ctx.ocr_checks,
            "routing": ctx.routing_metadata,
            "metrics": {
                "stage_timings_ms": {
                    "render": round(ctx.render_ms, 3),
                    "local_inspection": round(ctx.local_inspection_ms, 3),
                    "montage_vqa": round(ctx.montage_ms, 3),
                    "detail_vqa": round(ctx.initial_critique_ms, 3),
                    "repair": round(ctx.revision_ms, 3),
                    "final_vqa": round(ctx.final_critique_ms, 3),
                    "ocr": ctx.ocr_summary["ocr_total_ms"],
                    "persist": round(ctx.persist_ms, 3),
                },
                "total_ms": round(ctx.total_ms, 3),
                "cache_hits": ctx.initial_cache_hits + ctx.final_cache_hits,
                "vqa_calls": ctx.initial_cache_misses + ctx.final_cache_misses,
                "patch_count": ctx.patch_count,
                "fallback_count": ctx.fallback_count,
                "patch_attempts": ctx.patch_attempts,
                "patch_successes": ctx.patch_successes,
                "patch_failures": max(0, ctx.patch_attempts - ctx.patch_successes),
                "patch_attempted": ctx.patch_attempts,
                "patch_succeeded": ctx.patch_successes,
                "patch_failed": max(0, ctx.patch_attempts - ctx.patch_successes),
                "fallback_attempts": ctx.fallback_attempts,
                "fallback_successes": ctx.fallback_successes,
                "fallback_failures": max(0, ctx.fallback_attempts - ctx.fallback_successes),
                "fallback_attempted": ctx.fallback_attempts,
                "fallback_succeeded": ctx.fallback_successes,
                "fallback_failed": max(0, ctx.fallback_attempts - ctx.fallback_successes),
                **ctx.funnel_metrics,
                **ctx.ocr_summary,
            },
        },
    )
