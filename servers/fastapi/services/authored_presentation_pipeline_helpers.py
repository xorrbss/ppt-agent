"""Small quality helpers behind the authored presentation pipeline facade."""

from __future__ import annotations


def _evaluate_slide_quality(
    *,
    index: int,
    html: str,
    browser_metrics: Mapping[str, Any] | None,
    capacity_plan: AuthoredCapacityPlan | None,
    composition_profile: CompositionProfile | None,
    reference_mode: str,
) -> tuple[Any | None, dict[str, Any] | None]:
    """Evaluate one exact render before its durable checkpoint is advanced."""

    if (
        composition_profile is None
        or not composition_profile.strict_dom_gate
        or reference_mode == EXACT_RECONSTRUCTION
    ):
        return None, None
    issues: list[dict[str, Any]] = []
    if capacity_plan is not None:
        issues.extend(
            {
                "category": "capacity",
                "severity": "error",
                "message": violation,
            }
            for violation in capacity_violations(
                html,
                browser_metrics,
                capacity_plan.at(index),
            )
        )
    report = inspect_authored_dom(
        html,
        browser_metrics,
        reference_mode=reference_mode,
        strict_structural_off_canvas=(composition_profile.profile_id != "ordinary"),
    )
    defects = blocking_safety_issues(report)
    if report.telemetry_status != "available":
        detail = (
            ", ".join(
                f"{issue.category}:{issue.severity}" for issue in defects[:6]
            )
            or f"telemetry:{report.telemetry_status}"
        )
        raise AuthoredStageError(
            slide_index=index,
            stage="render",
            code="authored_composition_safety_gate_failed",
            retryable=True,
            message=f"Slide {index + 1} failed the composition safety gate ({detail})",
        )
    issues.extend(
        issue.to_dict() if hasattr(issue, "to_dict") else {"message": str(issue)}
        for issue in defects
    )
    return report, ({"slide_index": index, "issues": issues} if issues else None)

def _local_fit_quality_flag(
    index: int, failure: Mapping[str, Any]
) -> dict[str, Any]:
    """Preserve a post-acceptance visual failure as bounded receipt evidence."""

    reasons = ["local_fit:whole_page_quality"]
    raw_issues = failure.get("issues")
    if isinstance(raw_issues, Sequence) and not isinstance(raw_issues, (str, bytes)):
        for issue in raw_issues:
            if not isinstance(issue, Mapping):
                continue
            parts = [
                str(issue.get(key) or "").strip()
                for key in ("category", "severity", "message")
            ]
            detail = ":".join(part for part in parts if part)[:160]
            if detail and detail not in reasons:
                reasons.append(detail)
            if len(reasons) == 8:
                break
    return {
        "index": int(index),
        "reason": "composition_safety:local_fit",
        "reasons": reasons,
        "remakeAttempted": False,
        "remakeOutcome": "not_attempted",
    }

def _local_fit_unresolved_flag(
    index: int, unresolved_reasons: Sequence[object]
) -> dict[str, Any]:
    reasons = [
        f"local_fit:{str(reason).strip()}"
        for reason in unresolved_reasons
        if str(reason).strip()
    ]
    return {
        "index": int(index),
        "reason": "composition_safety:local_fit",
        "reasons": reasons[:8] or ["local_fit:unsupported_geometry"],
        "remakeAttempted": False,
        "remakeOutcome": "not_attempted",
    }


_LOCAL_NAMES = frozenset(globals())

def bind_runtime(runtime) -> None:
    for name, value in vars(runtime).items():
        if name not in _LOCAL_NAMES and not name.startswith("__"):
            globals()[name] = value


def build_pipeline_result(
    completed_results, *, plan, started, evidence_ledger, evidence_attempt_id,
    author_flagged_slides, authoring_cooldown_wait_seconds,
):
    htmls = [result[1] for result in completed_results]
    pngs = [result[2] for result in completed_results]
    quality_gate_reports = [
        result[7] for result in completed_results if result[7] is not None
    ]
    quality_gate_failures = [
        result[8] for result in completed_results if result[8] is not None
    ]
    quality_gate_failed_indices = [
        int(failure["slide_index"]) for failure in quality_gate_failures
    ]
    scene_envelope = seal_deck_scene_envelope(
        result[9] for result in completed_results
    )
    return AuthoredGenerationPipelineResult(
        deck=AuthoredDeckResult(htmls=htmls, design_system=plan.design_system),
        pngs=pngs,
        elapsed=time.monotonic() - started,
        author_work=sum(result[3] for result in completed_results),
        illustration_work=sum(result[4] for result in completed_results),
        render_work=sum(result[5] for result in completed_results),
        quality_work=sum(result[6] for result in completed_results),
        quality_gate_reports=tuple(quality_gate_reports),
        quality_gate_failed_indices=tuple(quality_gate_failed_indices),
        quality_gate_failures=tuple(quality_gate_failures),
        evidence_ledger=evidence_ledger,
        evidence_attempt_id=evidence_attempt_id,
        scene_envelope=scene_envelope,
        author_provider_attempt_counts=tuple(
            int(result[10]) for result in completed_results
        ),
        author_flagged_slides=tuple(
            author_flagged_slides[index] for index in sorted(author_flagged_slides)
        ),
        cooldown_wait_seconds=authoring_cooldown_wait_seconds[0],
        # Slides are authored in parallel child tasks, so the quality loop's
        # per-slide choice has no ambient way back to the measurement stage.
        # It travels with the slide's own result instead. E4: the same result
        # slot carries this slide's fired A2 normalisation rules (possibly
        # empty), merged in under "normalizations" so a slide that needed
        # normalisation is still recorded even when the quality loop did not
        # run for it.
        slide_quality_receipts={
            int(result[0]): {**dict(result[11]), "normalizations": list(result[12])}
            for result in completed_results
            if (isinstance(result[11], dict) and result[11]) or result[12]
        },
        local_fit_receipts=tuple(
            dict(result[13]) if isinstance(result[13], Mapping) else {}
            for result in completed_results
        ),
    )
