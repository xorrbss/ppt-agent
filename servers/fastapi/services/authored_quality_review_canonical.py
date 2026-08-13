from services import authored_quality_review_service as _service


def _canonical_metrics_with_flat_aliases(metrics: _service.Mapping[str, _service.Any]) -> dict[str, _service.Any]:
    """Keep canonical telemetry queryable by existing summary consumers.

    The nested shape is the source of truth, while the flat aliases let mixed
    legacy/canonical review runs add the same counter at the deck-summary seam.
    A producer-provided flat value always wins so a future producer cannot be
    silently double-counted.
    """
    normalized = dict(metrics)
    for group, aliases in _service._CANONICAL_METRIC_FLAT_ALIASES.items():
        values = metrics.get(group)
        if not isinstance(values, _service.Mapping):
            continue
        for nested_key, flat_key in aliases.items():
            if nested_key in values:
                normalized.setdefault(flat_key, values[nested_key])
    return normalized


def _merge_canonical_metric_group(
    previous: _service.Any, value: _service.Mapping[str, _service.Any], *, group: str
) -> dict[str, _service.Any]:
    """Aggregate canonical counters without weakening bounded/fail-closed flags."""
    merged = dict(previous) if isinstance(previous, _service.Mapping) else {}
    for key, item in value.items():
        prior = merged.get(key)
        if (
            isinstance(prior, (int, float))
            and (not isinstance(prior, bool))
            and isinstance(item, (int, float))
            and (not isinstance(item, bool))
        ):
            merged[str(key)] = prior + item
        elif group == "solver" and key == "search_complete" and isinstance(item, bool):
            merged[str(key)] = bool(prior) and item if prior is not None else item
        elif group == "rollout" and key == "kill_switch_enabled" and isinstance(item, bool):
            merged[str(key)] = bool(prior) or item
        else:
            merged[str(key)] = item
    return merged


def _indexed_canonical_issues(
    canonical: _service.Mapping[str, _service.Any], *, remaining_only: bool
) -> list[dict[str, _service.Any]]:
    """Flatten slide findings without discarding their navigation index."""
    indexed: list[dict[str, _service.Any]] = []
    raw_slides = canonical.get("slide_results")
    if isinstance(raw_slides, _service.Sequence) and (not isinstance(raw_slides, (str, bytes, bytearray))):
        for raw_slide in raw_slides:
            if not isinstance(raw_slide, _service.Mapping):
                continue
            slide_index = raw_slide.get("slide_index")
            if not isinstance(slide_index, int) or isinstance(slide_index, bool):
                continue
            if remaining_only and int(raw_slide.get("unresolved_confirmed_issue_count", 0)) <= 0:
                continue
            raw_issues = raw_slide.get("confirmed_issues")
            if not isinstance(raw_issues, _service.Sequence) or isinstance(raw_issues, (str, bytes, bytearray)):
                continue
            indexed.extend(
                (
                    {**dict(issue), "slide_index": slide_index}
                    for issue in raw_issues
                    if isinstance(issue, _service.Mapping)
                )
            )
    if indexed:
        return indexed
    fallback_key = "remaining_issues" if remaining_only else "issues"
    fallback = canonical.get(fallback_key)
    return [dict(issue) for issue in fallback or () if isinstance(issue, _service.Mapping)]


def _merge_slidespec_review_data(
    *,
    presentation_id: _service.uuid.UUID,
    request: _service.AuthoredQualityReviewRequest,
    legacy: _service.Mapping[str, _service.Any] | None,
    canonical: _service.Mapping[str, _service.Any],
) -> dict[str, _service.Any]:
    """Merge disjoint per-slide owners without changing the legacy contract."""
    result = dict(legacy or {})
    result.setdefault("presentation_id", str(presentation_id))
    result.setdefault("mode", request.mode)
    result.setdefault("scope", request.scope)
    additive = (
        "reviewed_count",
        "issue_slide_count",
        "detailed_issue_count",
        "confirmed_issue_count",
        "incomplete_measurement_count",
        "unresolved_confirmed_issue_count",
        "clean_slide_count",
        "fixed_count",
        "resolved_issue_count",
        "candidate_count",
        "accepted_candidate_count",
        "rejected_candidate_count",
        "remaining_slide_count",
        "remaining_issue_count",
    )
    for key in additive:
        result[key] = int(result.get(key, 0)) + int(canonical.get(key, 0))
    result["issues"] = list(result.get("issues", [])) + _service._indexed_canonical_issues(
        canonical, remaining_only=False
    )
    result["remaining_issues"] = list(result.get("remaining_issues", [])) + _service._indexed_canonical_issues(
        canonical, remaining_only=True
    )
    result["slide_results"] = list(result.get("slide_results", [])) + list(canonical.get("slide_results", []))
    result["candidate_decisions"] = list(result.get("candidate_decisions", [])) + list(
        canonical.get("candidate_decisions", [])
    )
    rejection_reasons = dict(result.get("rejection_reasons") or {})
    for code, count in (canonical.get("rejection_reasons") or {}).items():
        rejection_reasons[str(code)] = rejection_reasons.get(str(code), 0) + int(count)
    result["rejection_reasons"] = rejection_reasons
    for key in ("fixed_slide_indices", "remaining_slide_indices"):
        canonical_key = "remaining_issue_indices" if key == "remaining_slide_indices" else key
        result[key] = sorted(set(result.get(key, [])) | set(canonical.get(canonical_key, [])))
    result["version_saved"] = bool(result.get("version_saved") or canonical.get("version_saved"))
    routing = dict(result.get("routing") or {})
    routing.update(canonical.get("routing") or {})
    result["routing"] = routing
    metrics = dict(result.get("metrics") or {})
    canonical_metrics = _service._canonical_metrics_with_flat_aliases(canonical.get("metrics") or {})
    for key, value in canonical_metrics.items():
        previous = metrics.get(key)
        if key == "stage_timings_ms" and isinstance(value, _service.Mapping):
            merged_timings = dict(previous) if isinstance(previous, _service.Mapping) else {}
            for stage, duration in value.items():
                prior_duration = merged_timings.get(stage, 0)
                if isinstance(duration, (int, float)) and (not isinstance(duration, bool)):
                    merged_timings[str(stage)] = float(prior_duration) + float(duration)
            metrics[key] = merged_timings
            continue
        if key in _service._CANONICAL_METRIC_FLAT_ALIASES and isinstance(value, _service.Mapping):
            metrics[key] = _service._merge_canonical_metric_group(previous, value, group=key)
            continue
        if (
            isinstance(previous, (int, float))
            and (not isinstance(previous, bool))
            and isinstance(value, (int, float))
            and (not isinstance(value, bool))
        ):
            metrics[key] = previous + value
        else:
            metrics[key] = value
    result["metrics"] = metrics
    result["slide_results"] = sorted(result["slide_results"], key=lambda item: int(item.get("slide_index", 0)))
    return result


def _unclaimed_slidespec_review_data(
    positions: _service.Iterable[int], failures: _service.Mapping[int, str]
) -> dict[str, _service.Any]:
    """Describe slides that could not enter the typed repair pipeline.

    Quality review must never turn an import/rollout failure into an automatic
    full-page rewrite.  These entries therefore remain revision-neutral and
    carry a concrete reason that the UI can surface next to the existing,
    explicitly user-invoked single-page remake action.
    """
    ordered = tuple(sorted(set((int(position) for position in positions))))
    slide_results: list[dict[str, _service.Any]] = []
    remaining_issues: list[dict[str, _service.Any]] = []
    for position in ordered:
        reason = str(failures.get(position) or "repair.typed_pipeline_unavailable")
        issue = {
            "slide_index": position,
            "type": "quality_measurement_incomplete",
            "severity": "high",
            "detail": reason,
            "reason_code": reason,
            "applied": False,
        }
        remaining_issues.append(issue)
        slide_results.append(
            {
                "slide_index": position,
                "path": "none",
                "attempted": False,
                "applied": False,
                "reason_codes": (reason, "quality_review.automatic_page_regeneration_disabled"),
                "confirmed_issues": [],
                "remaining_issues": [reason],
                "confirmed_issue_count": 0,
                "incomplete_measurement_count": 1,
                "unresolved_confirmed_issue_count": 0,
                "clean_slide_count": 0,
            }
        )
    return {
        "reviewed_count": len(ordered),
        "issue_slide_count": 0,
        "detailed_issue_count": 0,
        "confirmed_issue_count": 0,
        "incomplete_measurement_count": len(ordered),
        "unresolved_confirmed_issue_count": 0,
        "clean_slide_count": 0,
        "issues": [],
        "fixed_count": 0,
        "resolved_issue_count": 0,
        "fixed_slide_indices": [],
        "remaining_slide_count": len(ordered),
        "remaining_issue_count": len(ordered),
        "remaining_slide_indices": list(ordered),
        "remaining_issues": remaining_issues,
        "candidate_count": 0,
        "accepted_candidate_count": 0,
        "rejected_candidate_count": 0,
        "candidate_decisions": [],
        "rejection_reasons": dict(
            _service.Counter(
                (str(failures.get(position) or "repair.typed_pipeline_unavailable") for position in ordered)
            )
        ),
        "version_saved": False,
        "slide_results": slide_results,
        "routing": {"slidespec": True, "claimed_positions": [], "automatic_page_regeneration": False},
        "metrics": {"individual_fallback_count": len(ordered), "automatic_page_regeneration_calls": 0},
    }


async def _run_authored_quality_review_unlocked(
    presentation_id: _service.uuid.UUID,
    request: _service.AuthoredQualityReviewRequest,
    task: _service.AsyncPresentationGenerationTaskModel,
    session: _service.AsyncSession,
) -> None:
    """Import eligible authored slides, then route canonical owners to SlideSpec."""
    presentation = await session.get(_service.PresentationModel, presentation_id)
    if presentation is None:
        raise ValueError("Presentation not found")
    outline = presentation.get_presentation_outline()
    if outline is None or not outline.slides:
        raise ValueError("The saved AI-authored outline is missing")
    slide_rows = await _service._execute_sqlalchemy(
        session,
        _service.select(_service.SlideModel)
        .where(_service.SlideModel.presentation == presentation_id)
        .order_by(_service.SlideModel.index),
    )
    slides = list(slide_rows.scalars().all())
    if not slides or not presentation.is_authored() or (not all((slide.is_authored() for slide in slides))):
        raise ValueError("High-quality review supports AI-authored presentations only")
    if len(outline.slides) != len(slides):
        raise ValueError("Saved outline and slide counts do not match")
    positions = _service._resolve_positions(request, len(slides))
    if any((not slides[position].html_content for position in positions)):
        raise ValueError("One or more slides are missing their editable authored HTML")
    from services.slidespec_artifact_store import ACTIVE_POINTER_KEY
    from services.slidespec_authored_importer import ensure_authored_slidespec_imports

    imported = await ensure_authored_slidespec_imports(
        presentation_id=presentation_id,
        slides=slides,
        positions=positions,
        opt_in=True,
        owner_id=getattr(presentation, "owner_id", None),
        session=session,
    )
    slides = list(imported.slides)
    claimed_hint = {
        position
        for position in positions
        if isinstance(slides[position].properties, _service.Mapping)
        and ACTIVE_POINTER_KEY in slides[position].properties
    }
    unclaimed_positions = [position for position in positions if position not in claimed_hint]
    unclaimed_data: _service.Mapping[str, _service.Any] | None = (
        _service._unclaimed_slidespec_review_data(unclaimed_positions, imported.failures)
        if unclaimed_positions
        else None
    )
    if not claimed_hint:
        data = _service._merge_slidespec_review_data(
            presentation_id=presentation_id, request=request, legacy=unclaimed_data, canonical={}
        )
        await _service._set_status(
            session,
            task,
            status="completed",
            message="검수는 완료되었지만 자동 수정으로 확정된 변경은 없습니다.",
            data=data,
        )
        return
    from services.slidespec_authored_executor import execute_slidespec_authored_review

    canonical = await execute_slidespec_authored_review(
        presentation_id=presentation_id,
        slides=slides,
        positions=sorted(claimed_hint),
        opt_in=True,
        owner_id=getattr(presentation, "owner_id", None),
        session=session,
    )
    claimed = set(canonical.claimed_positions)
    if claimed != claimed_hint:
        await session.rollback()
        raise RuntimeError("SlideSpec ownership changed during authored review")
    routing = canonical.data.setdefault("routing", {})
    routing["imported_positions"] = list(imported.imported_positions)
    routing["import_failures"] = {str(position): code for position, code in imported.failures.items()}
    data = _service._merge_slidespec_review_data(
        presentation_id=presentation_id, request=request, legacy=unclaimed_data, canonical=canonical.data
    )
    await _service._set_status(
        session,
        task,
        status="completed",
        message=f"검수가 완료되었습니다. {data['fixed_count']}개 슬라이드를 수정했습니다."
        if data["fixed_count"]
        else "검수는 완료되었지만 자동 수정으로 확정된 변경은 없습니다.",
        data=data,
    )
