# ruff: noqa: B020, B905

from services import authored_quality_review_service as _service


async def finish_clean_legacy_review(ctx):
    if not ctx.initial_issues:
        ctx.message = (
            "검수가 완료되었습니다. 수정이 필요한 슬라이드가 없습니다."
            if not ctx.initial_issues
            else f"검수가 완료되었습니다. {len(ctx.initial_issues)}개 슬라이드에서 문제를 확인했습니다."
        )
        ctx.total_ms = (_service.time.perf_counter() - ctx.review_started_at) * 1000
        ctx.local_high_remaining = sum(
            (any((issue.severity == "high" for issue in report.issues)) for report in ctx.local_reports)
        )
        _service._log_review_event(
            reference_mode=ctx.plan_reference_mode,
            stage="completed",
            slide_count=len(ctx.targets),
            model=ctx.review_metadata["model"],
            prompt_version=ctx.review_metadata["prompt_version"],
            cache_version=ctx.review_metadata["cache_version"],
            critique_batch_count=ctx.routing_metadata["vqa_batch_count"],
            critique_slide_request_count=ctx.initial_cache_misses,
            cache_hits=ctx.initial_cache_hits,
            cache_misses=ctx.initial_cache_misses,
            render_ms=ctx.render_ms,
            local_inspection_ms=ctx.local_inspection_ms,
            montage_ms=ctx.montage_ms,
            critique_ms=ctx.initial_critique_ms,
            total_ms=ctx.total_ms,
            local_high_remaining=ctx.local_high_remaining,
            indeterminate_count=ctx.initial_indeterminate_count,
            **ctx.ocr_summary,
        )
        await _service._set_status(
            ctx.session,
            ctx.task,
            status="completed",
            message=ctx.message,
            data={
                "presentation_id": str(ctx.presentation_id),
                "mode": ctx.request.mode,
                "scope": ctx.request.scope,
                "reviewed_count": len(ctx.targets),
                "issue_slide_count": len(ctx.initial_issues),
                "issues": ctx.initial_issues,
                "fixed_count": 0,
                "fixed_slide_indices": [],
                "remaining_issue_count": len(ctx.initial_issues),
                "remaining_slide_indices": [issue["slide_index"] for issue in ctx.initial_issues],
                "remaining_issues": ctx.initial_issues,
                "version_saved": False,
                "slide_results": _service._build_slide_results(
                    positions=ctx.positions,
                    initial_critiques=ctx.initial_critiques,
                    final_critiques=ctx.initial_critiques,
                    repair_details=[],
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
                        "ocr": ctx.ocr_summary["ocr_total_ms"],
                    },
                    "total_ms": round(ctx.total_ms, 3),
                    "cache_hits": ctx.initial_cache_hits,
                    "vqa_calls": ctx.initial_cache_misses,
                    "patch_count": 0,
                    "fallback_count": 0,
                    "patch_attempts": 0,
                    "patch_successes": 0,
                    "patch_failures": 0,
                    "patch_attempted": 0,
                    "patch_succeeded": 0,
                    "patch_failed": 0,
                    "fallback_attempts": 0,
                    "fallback_successes": 0,
                    "fallback_failures": 0,
                    "fallback_attempted": 0,
                    "fallback_succeeded": 0,
                    "fallback_failed": 0,
                    "candidate_proposed": 0,
                    "candidate_rendered": 0,
                    "validation_passed": 0,
                    "validation_rejected": 0,
                    "validation_inconclusive": 0,
                    "apply_committed": 0,
                    "publish_active": 0,
                    "postverify_passed": 0,
                    "applied": 0,
                    "rolled_back": 0,
                    "legacy_preserved": len(ctx.targets),
                    **ctx.ocr_summary,
                },
            },
        )
        return True
    return False
