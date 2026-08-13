"""Bounded, UI-safe quality-review workspace payload projection."""

import math
from collections.abc import Mapping, Sequence
from typing import Any

_MAX_QUALITY_REVIEW_REASON_CODE_LENGTH = 128
_MAX_QUALITY_REVIEW_REASON_CODES = 32
_MAX_QUALITY_REVIEW_ISSUES = 500
_MAX_QUALITY_REVIEW_MESSAGE_LENGTH = 500
_MAX_QUALITY_REVIEW_METRIC_VALUE = 1_000_000
_MAX_QUALITY_REVIEW_TIMING_MS = 86_400_000
_MAX_QUALITY_REVIEW_STAGE_TIMINGS = 32
_QUALITY_REVIEW_COUNT_KEYS = frozenset(
    {
        "reviewed_count",
        "issue_slide_count",
        "detailed_issue_count",
        "confirmed_issue_count",
        "fixed_count",
        "resolved_issue_count",
        "remaining_slide_count",
        "remaining_issue_count",
        "pattern_cluster_count",
        "pattern_diagnostic_family_count",
        "pattern_representative_count",
        "pattern_recipe_count",
        "pattern_reuse_attempts",
        "pattern_reuse_hits",
        "pattern_reuse_applied",
        "composite_plan_count",
        "composite_operations_merged",
        "render_candidates_rejected_early",
        "full_render_count",
        "llm_pattern_call_count",
        "llm_eligible_count",
        "llm_response_count",
        "llm_schema_pass_count",
        "llm_valid_plan_count",
        "llm_provider_failure_count",
        "llm_schema_reject_count",
        "llm_preflight_pass_count",
        "llm_rendered_count",
        "proposal_preflight_unprojectable_count",
        "negative_cache_hit_count",
        "llm_circuit_open_count",
        "individual_fallback_count",
    }
)


def _quality_review_number(value: Any) -> int | float | None:
    """Keep bounded finite counters/timings out of an untrusted task payload."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(value, _MAX_QUALITY_REVIEW_TIMING_MS)


def _quality_review_count(value: Any) -> int | None:
    """Counters are integral; timings use the broader finite-number helper."""

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, _MAX_QUALITY_REVIEW_METRIC_VALUE)


def _sanitize_quality_review_stage_timings(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, float] = {}
    for raw_stage, raw_duration in value.items():
        stage = str(raw_stage).strip()
        duration = _quality_review_number(raw_duration)
        if not stage or duration is None:
            continue
        safe[stage[:128]] = float(duration)
        if len(safe) >= _MAX_QUALITY_REVIEW_STAGE_TIMINGS:
            break
    return safe


def _sanitize_quality_review_reason_codes(value: Any) -> list[str]:
    """Keep only bounded, low-cardinality LLM no-candidate reason codes."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    safe: list[str] = []
    for raw_reason in value:
        if not isinstance(raw_reason, str):
            continue
        reason = raw_reason.strip()
        if not reason or reason in safe:
            continue
        safe.append(reason[:_MAX_QUALITY_REVIEW_REASON_CODE_LENGTH])
        if len(safe) >= _MAX_QUALITY_REVIEW_REASON_CODES:
            break
    return safe


def _normalize_quality_review_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Project approved review telemetry to stable top-level result fields.

    The executor can evolve its nested ``metrics`` layout without making the
    workspace event contract depend on it.  Only aggregate counters and bounded
    timings are promoted; candidate diagnostics, source content and model output
    remain private to the review task.
    """

    normalized = dict(payload)
    raw_metrics = payload.get("metrics")
    metrics = raw_metrics if isinstance(raw_metrics, Mapping) else {}
    for key in _QUALITY_REVIEW_COUNT_KEYS:
        value = payload.get(key, metrics.get(key))
        count = _quality_review_count(value)
        if count is not None:
            normalized[key] = count
        else:
            normalized.pop(key, None)
    total_ms = _quality_review_number(payload.get("total_ms", metrics.get("total_ms")))
    if total_ms is not None:
        normalized["total_ms"] = total_ms
    else:
        normalized.pop("total_ms", None)
    wall_time_ms = _quality_review_number(payload.get("wall_time_ms", metrics.get("deck_wall_ms")))
    if wall_time_ms is not None:
        normalized["wall_time_ms"] = wall_time_ms
    else:
        normalized.pop("wall_time_ms", None)
    # ``confirmed_issue_count`` is the established executor name.  Keep it as
    # a compatibility alias while making the UI-facing unit explicit.
    detailed = _quality_review_count(
        payload.get(
            "detailed_issue_count",
            payload.get("confirmed_issue_count", metrics.get("confirmed_issue_count")),
        )
    )
    if detailed is not None:
        normalized["detailed_issue_count"] = detailed
        normalized["confirmed_issue_count"] = detailed
    raw_timings = payload.get("stage_timings_ms", metrics.get("stage_timings_ms"))
    timings = _sanitize_quality_review_stage_timings(raw_timings)
    if timings:
        normalized["stage_timings_ms"] = timings
        aggregate_stage_time_ms = _quality_review_number(
            payload.get(
                "aggregate_stage_time_ms",
                metrics.get("aggregate_stage_time_ms", sum(timings.values())),
            )
        )
        if aggregate_stage_time_ms is not None:
            normalized["aggregate_stage_time_ms"] = aggregate_stage_time_ms
        else:
            normalized.pop("aggregate_stage_time_ms", None)
    else:
        normalized.pop("stage_timings_ms", None)
        normalized.pop("aggregate_stage_time_ms", None)
    reason_codes = _sanitize_quality_review_reason_codes(
        payload.get("no_candidate_reasons", metrics.get("no_candidate_reasons"))
    )
    if reason_codes:
        normalized["no_candidate_reasons"] = reason_codes
    else:
        normalized.pop("no_candidate_reasons", None)
    return normalized


def _quality_review_issue_message(value: Mapping[str, Any]) -> str | None:
    for key in ("message", "detail", "title", "code", "reason"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:_MAX_QUALITY_REVIEW_MESSAGE_LENGTH]
    return None


def _sanitize_quality_review_issues(value: Any) -> list[dict[str, Any]]:
    """Flatten findings while retaining only user-visible review history."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    safe: list[dict[str, Any]] = []
    for raw_issue in value:
        if not isinstance(raw_issue, Mapping):
            continue
        raw_slide_index = raw_issue.get("slide_index")
        slide_index = (
            raw_slide_index
            if isinstance(raw_slide_index, int) and not isinstance(raw_slide_index, bool) and raw_slide_index >= 0
            else None
        )
        nested = raw_issue.get("issues")
        candidates = (
            nested if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)) else (raw_issue,)
        )
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            message = _quality_review_issue_message(candidate)
            issue_type = next(
                (
                    candidate.get(key).strip()[:100]
                    for key in ("type", "category", "code")
                    if isinstance(candidate.get(key), str) and candidate.get(key).strip()
                ),
                None,
            )
            severity = candidate.get("severity")
            severity = severity.strip()[:50] if isinstance(severity, str) and severity.strip() else None
            fix_action = next(
                (
                    candidate.get(key).strip()[:_MAX_QUALITY_REVIEW_MESSAGE_LENGTH]
                    for key in ("fix_action", "action", "resolution")
                    if isinstance(candidate.get(key), str) and candidate.get(key).strip()
                ),
                None,
            )
            applied = candidate.get("applied")
            if message is None and issue_type is None and severity is None:
                continue
            item: dict[str, Any] = {}
            if slide_index is not None:
                item["slide_index"] = slide_index
            if issue_type is not None:
                item["type"] = issue_type
            if severity is not None:
                item["severity"] = severity
            if message is not None:
                # ``message`` remains for older result-card clients; the
                # explicit description key is the page-history contract.
                item["message"] = message
                item["description"] = message
            if fix_action is not None:
                item["fix_action"] = fix_action
            if isinstance(applied, bool):
                item["applied"] = applied
            safe.append(item)
            if len(safe) >= _MAX_QUALITY_REVIEW_ISSUES:
                return safe
    return safe


def _sanitize_quality_review_slide_results(value: Any) -> list[dict[str, Any]]:
    """Keep page-level fix outcomes without DOM/HTML or before/after content."""

    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    safe: list[dict[str, Any]] = []
    for raw_result in value:
        if not isinstance(raw_result, Mapping):
            continue
        raw_slide_index = raw_result.get("slide_index")
        if not isinstance(raw_slide_index, int) or isinstance(raw_slide_index, bool) or raw_slide_index < 0:
            continue
        item: dict[str, Any] = {"slide_index": raw_slide_index}
        for key in ("attempted", "applied"):
            if isinstance(raw_result.get(key), bool):
                item[key] = raw_result[key]
        raw_action = raw_result.get("fix_action", raw_result.get("path"))
        if isinstance(raw_action, str) and raw_action.strip():
            item["fix_action"] = raw_action.strip()[:_MAX_QUALITY_REVIEW_MESSAGE_LENGTH]
        rejection_reason = raw_result.get("rejection_reason")
        if isinstance(rejection_reason, str) and rejection_reason.strip():
            item["rejection_reason"] = rejection_reason.strip()[:_MAX_QUALITY_REVIEW_MESSAGE_LENGTH]
        raw_rejections = raw_result.get("candidate_rejections")
        if isinstance(raw_rejections, Sequence) and not isinstance(raw_rejections, (str, bytes, bytearray)):
            candidate_rejections: list[dict[str, Any]] = []
            for raw_rejection in raw_rejections[:16]:
                if not isinstance(raw_rejection, Mapping):
                    continue
                rejection: dict[str, Any] = {}
                for key in (
                    "plan_id",
                    "source",
                    "render_status",
                    "validation_status",
                ):
                    candidate = raw_rejection.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        rejection[key] = candidate.strip()[:128]
                reason_codes = raw_rejection.get("reason_codes")
                if isinstance(reason_codes, Sequence) and not isinstance(reason_codes, (str, bytes, bytearray)):
                    rejection["reason_codes"] = [
                        code.strip()[:128] for code in reason_codes if isinstance(code, str) and code.strip()
                    ][:16]
                if rejection:
                    candidate_rejections.append(rejection)
            if candidate_rejections:
                item["candidate_rejections"] = candidate_rejections
        for key in (
            "resolved_issues",
            "remaining_issues",
            "slide_level_resolved_issues",
            "slide_level_remaining_issues",
        ):
            raw_items = raw_result.get(key)
            if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes, bytearray)):
                continue
            item[key] = [
                issue.strip()[:_MAX_QUALITY_REVIEW_MESSAGE_LENGTH]
                for issue in raw_items
                if isinstance(issue, str) and issue.strip()
            ][:_MAX_QUALITY_REVIEW_ISSUES]
        safe.append(item)
        if len(safe) >= 500:
            break
    return safe
