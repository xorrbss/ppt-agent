"""Presentation-safe result shaping for authored quality reviews."""

import json
import re
from collections.abc import Mapping

from utils.llm_calls.critique_slide import SlideCritique


def critique_details(critique: SlideCritique | None) -> list[str]:
    if critique is None:
        return ["Visual review was unavailable"]
    return [issue.detail for issue in critique.issues]


def _display_patch_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _issue_tokens(detail: str) -> set[str]:
    normalized = re.sub(
        r"^(?:REGRESSION|INDETERMINATE):\s*", "", detail, flags=re.IGNORECASE
    )
    return {
        token
        for token in re.findall(r"[\w-]+", normalized.casefold())
        if len(token) > 2
    }


def _issue_match_score(issue, candidate) -> tuple[float, int, int]:
    before_tokens = _issue_tokens(issue.detail)
    candidate_tokens = _issue_tokens(candidate.detail)
    union = before_tokens | candidate_tokens
    similarity = len(before_tokens & candidate_tokens) / len(union) if union else 1.0
    return (
        similarity,
        int(candidate.type == issue.type),
        int(candidate.severity == issue.severity),
    )


def _match_issue_delta(
    before: SlideCritique | None, after: SlideCritique | None
) -> tuple[list, list]:
    """Match rephrased VQA findings by kind and content instead of exact prose."""
    before_issues = list(before.issues) if before is not None else []
    after_issues = list(after.issues) if after is not None else []
    unmatched_after = set(range(len(after_issues)))
    persisted_before: set[int] = set()

    for before_index, issue in enumerate(before_issues):
        same_type = [
            index for index in unmatched_after if after_issues[index].type == issue.type
        ]
        candidates = same_type or list(unmatched_after)
        if not candidates:
            continue
        scored_candidates = [
            (_issue_match_score(issue, after_issues[index]), index)
            for index in candidates
        ]
        (similarity, same_kind, _same_severity), match = max(scored_candidates)
        if (same_kind and len(same_type) == 1) or similarity >= 0.34:
            persisted_before.add(before_index)
            unmatched_after.remove(match)

    resolved = [
        issue
        for index, issue in enumerate(before_issues)
        if index not in persisted_before
    ]
    return resolved, after_issues


def _issue_matches_change(issue, change: Mapping) -> bool:
    qid = str(change.get("element_id") or change.get("qid") or "").casefold()
    detail = issue.detail.casefold()
    if qid and qid != "slide-root" and qid in detail:
        return True
    action = str(change.get("action") or "").casefold()
    compatible_types = {
        "settext": {"placeholder", "overflow"},
        "setstyle": {
            "overflow",
            "overlap",
            "low-contrast",
            "misalignment",
            "empty-space",
        },
        "move": {"overflow", "overlap", "misalignment", "empty-space"},
        "resize": {"overflow", "overlap", "misalignment", "empty-space"},
        "crop": {"overflow", "empty-space", "other"},
        "full_reauthor": {
            "overflow",
            "overlap",
            "low-contrast",
            "placeholder",
            "misalignment",
            "empty-space",
            "other",
        },
    }
    return issue.type in compatible_types.get(action, set())


def _attribute_issues(
    issues: list, changes: list[Mapping]
) -> tuple[list[list[str]], list[str]]:
    attributed = [[] for _ in changes]
    unattributed: list[str] = []
    for issue in issues:
        explicit = [
            index
            for index, change in enumerate(changes)
            if str(change.get("element_id") or change.get("qid") or "").casefold()
            not in {"", "slide-root"}
            and str(change.get("element_id") or change.get("qid") or "").casefold()
            in issue.detail.casefold()
        ]
        compatible = [
            index
            for index, change in enumerate(changes)
            if _issue_matches_change(issue, change)
        ]
        targets = explicit or (compatible if len(compatible) == 1 else [])
        if targets:
            for index in targets:
                attributed[index].append(issue.detail)
        else:
            unattributed.append(issue.detail)
    return attributed, unattributed


def build_slide_results(
    *,
    positions: list[int],
    initial_critiques: list[SlideCritique | None],
    final_critiques: list[SlideCritique | None],
    repair_details: list[dict],
    candidate_decisions: list[dict] | None = None,
) -> list[dict]:
    repairs: dict[int, list[dict]] = {}
    for detail in repair_details:
        if isinstance(detail, dict) and isinstance(detail.get("local_index"), int):
            repairs.setdefault(detail["local_index"], []).append(detail)
    decisions = {
        decision.get("slide_index"): decision
        for decision in (candidate_decisions or [])
        if isinstance(decision, dict)
    }
    results: list[dict] = []
    for local_index, position in enumerate(positions):
        resolved_issues, remaining_issues = _match_issue_delta(
            initial_critiques[local_index], final_critiques[local_index]
        )
        resolved = [issue.detail for issue in resolved_issues]
        remaining = [issue.detail for issue in remaining_issues]
        slide_repairs = repairs.get(local_index, [])
        repair = slide_repairs[-1] if slide_repairs else None
        decision = decisions.get(position)
        path = str((decision or {}).get("path") or (repair or {}).get("path") or "none")
        attempted = bool(
            decision is not None
            or any(detail.get("attempted", True) for detail in slide_repairs)
        )
        # Validation success is deliberately not an apply receipt.  Older callers
        # omitted ``applied`` and therefore accidentally exposed an accepted or
        # merely rendered candidate as live.  Missing publication evidence now
        # fails closed.
        applied = bool((decision or {}).get("applied", False))
        rejection_reason = None
        if attempted and not applied and not bool((decision or {}).get("accepted")):
            failure_detail = (repair or {}).get("failure_detail") or {}
            rejection_reason = str(
                (decision or {}).get("rejection_reason")
                or (decision or {}).get("reason")
                or failure_detail.get("code")
                or (repair or {}).get("fallback_reason")
                or "no_candidate"
            )
        raw_changes: list[tuple[dict, dict]] = []
        for detail in slide_repairs:
            for change in detail.get("element_changes") or []:
                if isinstance(change, Mapping):
                    raw_changes.append((detail, dict(change)))
        change_mappings = [change for _detail, change in raw_changes]
        resolved_by_change, slide_level_resolved = _attribute_issues(
            resolved_issues, change_mappings
        )
        remaining_by_change, slide_level_remaining = _attribute_issues(
            remaining_issues, change_mappings
        )
        element_changes: list[dict] = []
        for change_index, (detail, change) in enumerate(raw_changes):
            detail_applied = bool(applied and detail is repair)
            element_changes.append(
                {
                    "element_id": str(
                        change.get("element_id") or change.get("qid") or "slide-root"
                    ),
                    "before": _display_patch_value(change.get("before", "")),
                    "after": _display_patch_value(change.get("after", "")),
                    "path": str(detail.get("path") or path),
                    "attempted": True,
                    "applied": detail_applied,
                    "rejection_reason": None if detail_applied else rejection_reason,
                    "resolved_issues": resolved_by_change[change_index],
                    "remaining_issues": remaining_by_change[change_index],
                }
            )
        results.append(
            {
                "slide_index": position,
                "path": path,
                "attempted": attempted,
                "accepted": bool((decision or {}).get("accepted", False)),
                "applied": applied,
                "apply_status": str(
                    (decision or {}).get("apply_status") or "not_started"
                ),
                "publish_status": str(
                    (decision or {}).get("publish_status") or "not_started"
                ),
                "post_verify_status": str(
                    (decision or {}).get("post_verify_status") or "not_started"
                ),
                "rolled_back": bool((decision or {}).get("rolled_back", False)),
                "rejection_reason": rejection_reason,
                "resolved_issues": resolved,
                "remaining_issues": remaining,
                "slide_level_resolved_issues": slide_level_resolved,
                "slide_level_remaining_issues": slide_level_remaining,
                "element_changes": element_changes,
            }
        )
    return results
