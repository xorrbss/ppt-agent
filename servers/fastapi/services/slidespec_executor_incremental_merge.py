"""Merge cached and refreshed SlideSpec review results deterministically."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

def _merge_incremental_slide_results(
    passes: Sequence[Mapping[str, Any]],
    *,
    complete: bool,
) -> dict[str, Any]:
    """Merge one slide's bounded CAS repair chain without losing provenance."""

    if not passes:
        raise ValueError("incremental repair requires at least one inspection pass")
    merged = dict(passes[-1])
    merged["trigger"] = next(
        (item.get("trigger") for item in passes if item.get("trigger")), None
    )
    merged["applied"] = any(bool(item.get("applied")) for item in passes)
    merged["rolled_back"] = any(bool(item.get("rolled_back")) for item in passes)
    merged["outcomes"] = [
        outcome
        for item in passes
        for outcome in item.get("outcomes", [])
        if isinstance(outcome, Mapping)
    ]
    merged["candidate_count"] = sum(
        int(item.get("candidate_count", 0)) for item in passes
    )
    merged["projection_rejections"] = [
        rejection
        for item in passes
        for rejection in item.get("projection_rejections", [])
    ]
    merged["no_candidate"] = list(passes[-1].get("no_candidate", []))
    merged["reason_codes"] = list(
        dict.fromkeys(
            str(code)
            for item in passes
            for code in item.get("reason_codes", [])
        )
    )
    merged["confirmed_issue_count"] = max(
        int(item.get("confirmed_issue_count", 0)) for item in passes
    )
    merged["incomplete_measurement_count"] = max(
        int(item.get("incomplete_measurement_count", 0)) for item in passes
    )
    merged["unresolved_confirmed_issue_count"] = (
        0
        if complete
        else int(passes[-1].get("unresolved_confirmed_issue_count", 0))
    )
    merged["clean_slide_count"] = int(
        passes[-1].get("clean_slide_count", 0)
    )
    confirmed_issues: dict[str, Mapping[str, Any]] = {}
    for item in passes:
        for issue in item.get("confirmed_issues", []):
            if not isinstance(issue, Mapping):
                continue
            identity = str(
                issue.get("physical_issue_id")
                or ":".join(
                    (
                        str(issue.get("code", "")),
                        str(issue.get("semantic_id", "")),
                        str(issue.get("qid", "")),
                    )
                )
            )
            confirmed_issues.setdefault(identity, issue)
    merged["confirmed_issues"] = [dict(item) for item in confirmed_issues.values()]
    if confirmed_issues:
        merged["confirmed_issue_count"] = len(confirmed_issues)
    merged["incomplete_measurements"] = list(
        passes[-1].get("incomplete_measurements", [])
    )
    merged["total_ms"] = round(
        sum(float(item.get("total_ms", 0.0)) for item in passes), 3
    )

    telemetry: dict[str, Any] = {}
    for item in passes:
        current = item.get("telemetry", {})
        if not isinstance(current, Mapping):
            continue
        for key, value in current.items():
            if key == "stage_timings_ms" and isinstance(value, Mapping):
                stages = telemetry.setdefault(key, {})
                for stage, duration in value.items():
                    stages[str(stage)] = stages.get(str(stage), 0.0) + float(duration)
            elif key == "repair_costs" and isinstance(value, list):
                telemetry.setdefault(key, []).extend(value)
            elif key == "no_candidate_reasons" and isinstance(value, list):
                telemetry[key] = sorted(
                    set(telemetry.get(key, ())) | {str(item) for item in value}
                )
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                telemetry[key] = telemetry.get(key, 0) + value
            else:
                telemetry[key] = value
    merged["telemetry"] = telemetry

    funnel: dict[str, int] = {}
    for item in passes:
        current = item.get("funnel", {})
        if not isinstance(current, Mapping):
            continue
        for key, value in current.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                funnel[str(key)] = funnel.get(str(key), 0) + int(value)
    merged["funnel"] = funnel

    metrics = {
        "solver": {
            "elapsed_ms": 0.0,
            "branches": 0,
            "unknown": 0,
            "unsat_core_size": 0,
            "search_complete": True,
        },
        "candidates": {
            "proposed": 0,
            "rendered": 0,
            "accepted": 0,
            "rejected": 0,
            "projection_rejected": 0,
        },
        "transaction": {"rollback_count": 0},
        "rollout": {"kill_switch_enabled": False},
    }
    for item in passes:
        current = item.get("metrics", {})
        if not isinstance(current, Mapping):
            continue
        solver = current.get("solver", {})
        if isinstance(solver, Mapping):
            for key in ("elapsed_ms", "branches", "unknown", "unsat_core_size"):
                metrics["solver"][key] += float(solver.get(key, 0) or 0)
            metrics["solver"]["search_complete"] = bool(
                metrics["solver"]["search_complete"]
            ) and bool(solver.get("search_complete", False))
        candidates = current.get("candidates", {})
        if isinstance(candidates, Mapping):
            for key in metrics["candidates"]:
                metrics["candidates"][key] += int(candidates.get(key, 0) or 0)
        transaction = current.get("transaction", {})
        if isinstance(transaction, Mapping):
            metrics["transaction"]["rollback_count"] += int(
                transaction.get("rollback_count", 0) or 0
            )
        rollout = current.get("rollout", {})
        if isinstance(rollout, Mapping):
            metrics["rollout"]["kill_switch_enabled"] = bool(
                metrics["rollout"]["kill_switch_enabled"]
            ) or bool(rollout.get("kill_switch_enabled"))
    merged["metrics"] = metrics
    merged["incremental_passes"] = len(passes)
    merged["incremental_complete"] = complete
    return merged

