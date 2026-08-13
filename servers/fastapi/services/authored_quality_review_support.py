# ruff: noqa: B905

from services import authored_quality_review_service as _service


def _merge_dom_repair_triggers(
    critiques: list[_service.SlideCritique | None], local_reports: list[_service.DomQualityReport]
) -> list[_service.SlideCritique | None]:
    """Make required geometry defects start repair independently from VQA.

    VQA remains useful evidence, but it cannot veto deterministic clipping,
    off-canvas, overflow, or unintended-overlap telemetry.  Only medium/high
    geometry defects are promoted; unavailable telemetry stays fail-closed and
    does not fabricate a repair target.
    """
    geometry_categories = {"clipping", "off_canvas", "overflow", "overlap"}
    merged = list(critiques)
    for index, report in enumerate(local_reports):
        promoted = [
            issue
            for issue in report.issues
            if issue.category in geometry_categories and issue.severity in {"high", "medium"}
        ]
        if not promoted:
            continue
        critique = merged[index] if index < len(merged) else None
        existing = list(critique.issues) if critique is not None else []
        existing_keys = {(issue.type, issue.severity, issue.detail) for issue in existing}
        for issue in promoted:
            issue_type = issue.category if issue.category in {"overflow", "overlap"} else "other"
            detail = f"DOM_{issue.category.upper()}: {issue.detail}"[:200]
            key = (issue_type, issue.severity, detail)
            if key in existing_keys:
                continue
            existing.append(_service.CritiqueIssue(type=issue_type, severity=issue.severity, detail=detail))
            existing_keys.add(key)
        merged[index] = _service.SlideCritique(needs_fix=True, issues=existing[:8])
    return merged


async def _execute_sqlalchemy(session: _service.AsyncSession, statement: _service.Any) -> _service.Any:
    """Execute through SQLAlchemy even when a SQLModel session subclass is supplied.

    Production sessions are SQLAlchemy ``AsyncSession`` instances, while a few
    compatibility tests intentionally supply SQLModel's subclass.  SQLModel
    deprecates its inherited ``execute`` method and SQLAlchemy's ``scalars``
    dynamically dispatches to that deprecated override.  Calling the base method
    explicitly keeps one result contract (``Result``) in both environments without
    suppressing warnings or changing the database/session architecture.
    """
    return await _service.AsyncSession.execute(session, statement)


def _ocr_route_signals(
    local_report: _service.DomQualityReport | None,
    critique: _service.SlideCritique | None,
    repair_detail: _service.Mapping[str, _service.Any] | None = None,
) -> list[str]:
    """Return closed, content-free reasons for selectively running slide OCR."""
    signals: set[str] = set()
    if local_report is not None:
        for issue in local_report.issues:
            if issue.category in _service._OCR_DOM_CATEGORIES:
                signals.add(f"dom:{issue.category}")
    if critique is not None:
        for issue in critique.issues:
            issue_type = str(issue.type)
            if issue_type in {"overflow", "placeholder"} or _service._OCR_DETAIL_SIGNAL.search(issue.detail or ""):
                signals.add(f"vqa:{issue_type}")
    if repair_detail is not None:
        if str(repair_detail.get("path") or "") == "full_fallback":
            signals.add("repair:full_fallback")
        for change in repair_detail.get("element_changes") or []:
            if not isinstance(change, _service.Mapping):
                continue
            action = str(change.get("action") or "").casefold().replace("_", "")
            if "text" in action:
                signals.add("repair:text_change")
                break
    return sorted(signals)


async def _inspect_ocr_for_review(*args, **kwargs):
    """Resolve the service facade at call time so tests/integrators can patch it."""
    return await _service.inspect_authored_ocr(*args, **kwargs)


class _RequestOcrRuntime(_service.RequestOcrRuntime):
    def __init__(self, language: str) -> None:
        super().__init__(
            language,
            inspect_ocr=_service._inspect_ocr_for_review,
            logger=_service.LOGGER,
        )


async def _set_status(
    session: _service.AsyncSession,
    task: _service.AsyncPresentationGenerationTaskModel,
    *,
    status: str,
    message: str,
    data: dict | None = None,
    error: dict | None = None,
) -> None:
    task.status = status
    task.message = message
    task.data = data
    task.error = error
    task.updated_at = _service.get_current_utc_datetime()
    session.add(task)
    await session.commit()


def _resolve_positions(request: _service.AuthoredQualityReviewRequest, slide_count: int) -> list[int]:
    if request.scope == "all":
        return list(range(slide_count))
    if any((index >= slide_count for index in request.slide_indices)):
        raise ValueError("Slide index is outside the presentation")
    return list(request.slide_indices)


def _saved_brand(presentation: _service.PresentationModel) -> _service.Brand:
    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    language = str(theme.get("language") or presentation.language or "Korean")
    return _service.Brand(
        topic=presentation.title or presentation.content or "Presentation",
        language=language,
        primary=str(theme.get("primary") or "#2563EB"),
        fonts=str(theme.get("fonts") or ("Noto Sans KR" if language.lower().startswith("ko") else "Inter")),
        primary_is_explicit=True,
        fonts_are_explicit=True,
    )


def _saved_role(slide: _service.SlideModel, fallback: str) -> str:
    if isinstance(slide.content, dict) and slide.content.get("role"):
        return str(slide.content["role"])
    layout = slide.layout or ""
    if layout.startswith("authored:") and layout.partition(":")[2]:
        return layout.partition(":")[2]
    return fallback


def _serialize_critiques(positions: _service.Iterable[int], critiques: list) -> list[dict]:
    results: list[dict] = []
    for position, critique in zip(positions, critiques):
        if critique is None or not critique.needs_fix:
            continue
        results.append(
            {"slide_index": position, "issues": [issue.model_dump(mode="json") for issue in critique.issues]}
        )
    return results


async def _render_review_html(html: str) -> tuple[bytes, _service.Mapping | None]:
    """Render with co-located telemetry while preserving legacy test doubles."""
    if _service.render_html_to_png is not _service._ORIGINAL_RENDER_HTML_TO_PNG:
        png = await _service.render_html_to_png(html)
        return (png, _service.get_cached_browser_metrics(html))
    return await _service.render_html_to_png_with_metrics(html)


async def _hybrid_style_initial_review(
    *,
    pngs: list[bytes],
    contexts: list[str],
    positions: list[int],
    slide_count: int,
    local_reports: list[_service.DomQualityReport],
    sampling_seed: str,
    scope: _service.ReviewScope = "all",
) -> tuple[list[_service.SlideCritique | None], dict, int, int, float, float]:
    """Delegate routing while preserving service-level monkeypatch points."""
    return await _service.hybrid_style_initial_review(
        pngs=pngs,
        contexts=contexts,
        positions=positions,
        slide_count=slide_count,
        local_reports=local_reports,
        sampling_seed=sampling_seed,
        scope=scope,
        critique_batch=_service.critique_authored,
        observe_cache=_service._observe_critique_cache,
        unusable_reason=_service._critique_unusable_reason,
    )


def _log_review_event(
    *,
    reference_mode: str,
    stage: str,
    slide_count: int,
    attempted: int = 0,
    accepted: int = 0,
    rejection_reasons: _service.Mapping[str, int] | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    cache_version: str | None = None,
    critique_batch_count: int = 0,
    critique_slide_request_count: int = 0,
    cache_hits: int = 0,
    cache_misses: int = 0,
    render_ms: float = 0.0,
    critique_ms: float = 0.0,
    revision_ms: float = 0.0,
    indeterminate_count: int = 0,
    ocr_executed: bool = False,
    ocr_requests: int = 0,
    ocr_calls: int = 0,
    ocr_cache_hits: int = 0,
    ocr_total_ms: float = 0.0,
    local_inspection_ms: float = 0.0,
    montage_ms: float = 0.0,
    persist_ms: float = 0.0,
    total_ms: float = 0.0,
    patch_attempted: int = 0,
    patch_accepted: int = 0,
    patch_rejected: int = 0,
    fallback_attempted: int = 0,
    fallback_succeeded: int = 0,
    fallback_failed: int = 0,
    local_high_remaining: int = 0,
) -> None:
    """Emit telemetry through this module's logger for compatibility."""
    event = _service.build_review_event(
        reference_mode=reference_mode,
        stage=stage,
        slide_count=slide_count,
        attempted=attempted,
        accepted=accepted,
        rejection_reasons=rejection_reasons,
        model=model,
        prompt_version=prompt_version,
        cache_version=cache_version,
        critique_batch_count=critique_batch_count,
        critique_slide_request_count=critique_slide_request_count,
        cache_hits=cache_hits,
        cache_misses=cache_misses,
        render_ms=render_ms,
        critique_ms=critique_ms,
        revision_ms=revision_ms,
        indeterminate_count=indeterminate_count,
        ocr_executed=ocr_executed,
        ocr_requests=ocr_requests,
        ocr_calls=ocr_calls,
        ocr_cache_hits=ocr_cache_hits,
        ocr_total_ms=ocr_total_ms,
        local_inspection_ms=local_inspection_ms,
        montage_ms=montage_ms,
        persist_ms=persist_ms,
        total_ms=total_ms,
        patch_attempted=patch_attempted,
        patch_accepted=patch_accepted,
        patch_rejected=patch_rejected,
        fallback_attempted=fallback_attempted,
        fallback_succeeded=fallback_succeeded,
        fallback_failed=fallback_failed,
        local_high_remaining=local_high_remaining,
    )
    _service.LOGGER.info(
        "authored_quality_review %s", _service.json.dumps(event, sort_keys=True, separators=(",", ":"))
    )


def _write_reviewed_png(presentation_id: _service.uuid.UUID, slide_position: int, png: bytes) -> str:
    return _service.publish_reviewed_png(presentation_id, slide_position, png)
