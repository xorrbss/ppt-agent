"""Descriptive cell-paired heuristic, with explicit attempt and comparison integrity.

Scores remain conditional on completed decks. Every attempted deck is accounted for
separately; missing evidence never becomes a zero or an implicitly matching identity.
"""
from __future__ import annotations

import json
import math
import statistics

try:
    from scripts.svg_template_eval.metrics import attempt_accounting, normalized_page_metrics
except ModuleNotFoundError:  # Direct run_eval.py execution places this directory on sys.path.
    from metrics import attempt_accounting, normalized_page_metrics

DEFAULT_CELL = "default"
JUDGE_AXES = ("structure", "density", "hierarchy", "tone", "visuals")
CONDITION_KEYS = ("input_sha256", "template_pin_sha256", "request_sha256", "generation_config_sha256")
# Heuristic thresholds only. Raw page counts remain descriptive across differently sized decks.
RULES: dict[str, tuple[float, int]] = {
    **{f"judge_{axis}": (0.2, 1) for axis in JUDGE_AXES},
    "judge_overall": (0.2, 1),
    "fallback_rate": (0.05, -1),
    "first_try_ok_rate": (0.05, 1),
}
_SKIP = {"run", "attempt", "attempt_number", "retry_of", "seconds", "pages", "cell", "judge_version"}


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def judge_versions(result: dict) -> set[int]:
    """Historical version reader; missing versions describe legacy version 1 only.

    This legacy convention is not sufficient comparison identity.
    """
    return {run.get("judge_version", 1) for run in result.get("runs", [])
            if isinstance(run.get("judge_version", 1), int) and not isinstance(run.get("judge_version", 1), bool)
            if run.get("completed") is True and any(key.startswith("judge_") and key != "judge_version"
                                                    and _numeric(value) for key, value in run.items())}


def _cell(run: dict) -> str:
    return run.get("cell") or DEFAULT_CELL


def _cells(result: dict) -> set[str]:
    return {str(cell) for cell in result.get("cells", [])} | {_cell(run) for run in result.get("runs", [])}


def _metric_values(run: dict) -> dict:
    # Ignore stored rate fields: the denominator must be recoverable from actual page counts.
    values = {key: value for key, value in run.items()
              if key not in _SKIP and key not in {"fallback_rate", "first_try_ok_rate"} and _numeric(value)}
    values.update(normalized_page_metrics(run))
    return values


def cell_means(result: dict) -> dict[str, dict[str, float]]:
    """Completed-deck metric means; page rates pool their actual page denominators."""
    values: dict[str, dict[str, list[float]]] = {}
    page_totals: dict[str, dict[str, list[float]]] = {}
    for run in result.get("runs", []):
        if run.get("completed") is not True:
            continue
        cell = _cell(run)
        for key, value in _metric_values(run).items():
            if key in {"fallback_rate", "first_try_ok_rate"}:
                count_key = "fallback_pages" if key == "fallback_rate" else "first_try_ok"
                totals = page_totals.setdefault(key, {}).setdefault(cell, [0, 0])
                totals[0] += run[count_key]
                totals[1] += run["pages"]
            else:
                values.setdefault(key, {}).setdefault(cell, []).append(float(value))
    means = {key: {cell: statistics.fmean(items) for cell, items in cells.items()} for key, cells in values.items()}
    means.update({key: {cell: totals[0] / totals[1] for cell, totals in cells.items()}
                  for key, cells in page_totals.items()})
    return means


def verdict(overall: float, diffs: list[float], threshold: float, direction: int) -> str:
    """Threshold and majority-sign heuristic; carries no statistical confidence claim."""
    if abs(overall) <= threshold or math.isclose(abs(overall), threshold, rel_tol=1e-12, abs_tol=1e-12):
        return "no difference"
    sign = 1 if overall > 0 else -1
    agree = sum(1 for diff in diffs if diff * sign > 0)
    if agree * 2 <= len(diffs):
        return "inconsistent"
    return "better" if sign == direction else "worse"


def _identity_issues(runs_a: list[dict], runs_b: list[dict], *, judge: bool = False) -> list[str]:
    if not runs_a or not runs_b:
        return ["missing cell"]
    identities = []
    for runs in (runs_a, runs_b):
        arm = set()
        for run in runs:
            identity = run.get("judge_identity" if judge else "condition_identity")
            if judge:
                if not isinstance(identity, str) or not identity.strip():
                    return ["missing judge identity"]
                version = run.get("judge_version")
                if not isinstance(version, int) or isinstance(version, bool):
                    return ["missing judge version"]
                arm.add((identity, version))
            else:
                if (not isinstance(identity, dict)
                        or any(not isinstance(identity.get(key), str) or not identity[key].strip()
                               for key in CONDITION_KEYS)):
                    return ["missing condition identity"]
                arm.add(json.dumps({key: identity[key] for key in CONDITION_KEYS}, sort_keys=True))
        identities.append(arm)
    if len(identities[0]) != 1 or len(identities[1]) != 1:
        return ["mixed judge identity" if judge else "mixed condition identity"]
    if identities[0] != identities[1]:
        return ["incompatible judge identity" if judge else "incompatible condition identity"]
    return []


def _runtime_issues(runs_a: list[dict], runs_b: list[dict]) -> list[str]:
    """Actual runtime identities are supplied by verified evidence, never harness HEAD."""
    for runs in (runs_a, runs_b):
        if not runs:
            continue  # The condition check reports a missing cell separately.
        identities = set()
        for run in runs:
            identity = run.get("evaluation_identity")
            if not isinstance(identity, dict):
                return ["missing runtime identity"]
            if any(not isinstance(identity.get(key), str) or not identity[key].strip()
                   for key in ("code_revision", "generation_config_sha256", "policy_sha256")):
                return ["missing runtime identity"]
            revision = identity.get("policy_revision")
            if (isinstance(revision, bool) or not isinstance(revision, (str, int))
                    or isinstance(revision, str) and not revision.strip()):
                return ["missing runtime policy revision"]
            condition = run.get("condition_identity")
            if (isinstance(condition, dict)
                    and identity["generation_config_sha256"] != condition.get("generation_config_sha256")):
                return ["runtime configuration identity disagrees with condition"]
            identities.add(json.dumps({key: identity[key] for key in
                                       ("code_revision", "generation_config_sha256", "policy_revision", "policy_sha256")},
                                      sort_keys=True))
        if len(identities) != 1:
            return ["mixed runtime identity within arm"]
    return []


def _superseded_failures(runs: list[dict]) -> set[int]:
    """Indexes of failed attempts an authorized ``--retry-failed`` attempt completed.

    The failure stays in the attempt accounting; only the completion decision
    treats the slot as recovered, so an authorized retry can yield a comparable
    result instead of always reading as a regression.
    """
    recovered = {run.get("retry_of") for run in runs if run.get("completed") is True and run.get("retry_of")}
    superseded = set()
    for index, run in enumerate(runs):
        if run.get("completed") is not False:
            continue
        identities = {run.get("attempt_id"), f"legacy:{run.get('cell') or 'default'}:{run.get('run')}"}
        if identities & recovered:
            superseded.add(index)
    return superseded


def _effective_completion(runs: list[dict]) -> tuple[float | None, int, int]:
    """(completion rate, failed, unknown) after recovered failures are set aside."""
    superseded = _superseded_failures(runs)
    effective = [run for index, run in enumerate(runs) if index not in superseded]
    completed = sum(run.get("completed") is True for run in effective)
    failed = sum(run.get("completed") is False for run in effective)
    unknown = sum(run.get("completed") is not True and run.get("completed") is not False for run in effective)
    return (completed / len(effective) if effective else None), failed, unknown


def _completion_status(a: dict, b: dict, accounting_a: dict, accounting_b: dict) -> str:
    (rate_a, _, _), (rate_b, _, _) = (_effective_completion(a.get("runs", [])),
                                      _effective_completion(b.get("runs", [])))
    if rate_a is not None and rate_b is not None and rate_b < rate_a:
        return "completion regressed"
    for result, accounting in ((a, accounting_a), (b, accounting_b)):
        _, failed, unknown = _effective_completion(result.get("runs", []))
        if not accounting["attempted_runs"] or failed or unknown:
            return "incomplete evidence"
        requested = result.get("requested_runs")
        slots = {(_cell(run), run.get("run")) for run in result.get("runs", [])
                 if run.get("completed") is True}
        if isinstance(requested, int) and not isinstance(requested, bool) and len(slots) < requested:
            return "incomplete evidence"
    return "complete"


def _coverage(runs: list[dict], key: str) -> dict:
    completed = [run for run in runs if run.get("completed") is True]
    available = [run for run in completed if key in _metric_values(run)]
    result = {"attempts": len(runs), "completed": len(completed),
              "failed": sum(run.get("completed") is False for run in runs),
              "unknown_outcome": sum(run.get("completed") is not True and run.get("completed") is not False
                                     for run in runs),
              "available": len(available), "missing_metric": len(completed) - len(available),
              "missing_cell": not runs}
    if key in {"fallback_rate", "first_try_ok_rate"}:
        result["pages"] = sum(run["pages"] for run in available)
        count_key = "fallback_pages" if key == "fallback_rate" else "first_try_ok"
        result["numerator"] = sum(run[count_key] for run in available)
    return result


def decide(a: dict, b: dict, rules: dict[str, tuple[float, int]] | None = None) -> dict:
    """Keep metric-key reports readable while exposing unsupported comparison claims."""
    rules = RULES if rules is None else rules
    means_a, means_b = cell_means(a), cell_means(b)
    cells_a, cells_b = _cells(a), _cells(b)
    cells = sorted(cells_a | cells_b)
    groups = [{cell: [run for run in result.get("runs", []) if _cell(run) == cell] for cell in cells}
              for result in (a, b)]
    condition_issues = {cell: issues for cell in cells
                        if (issues := (_identity_issues(groups[0][cell], groups[1][cell])
                                       + _runtime_issues(groups[0][cell], groups[1][cell])))}
    accounting_a = attempt_accounting(a.get("runs", []), a.get("requested_runs"))
    accounting_b = attempt_accounting(b.get("runs", []), b.get("requested_runs"))
    completion_status = _completion_status(a, b, accounting_a, accounting_b)
    report: dict[str, dict] = {"_comparison": {
        "method": "descriptive paired-cell threshold heuristic", "score_population": "completed decks only",
        "status": "not comparable" if condition_issues or not cells else "comparable",
        "condition_issues": condition_issues, "only_a": sorted(cells_a - cells_b), "only_b": sorted(cells_b - cells_a),
        "accounting_a": accounting_a, "accounting_b": accounting_b,
        "completion_status": completion_status,
        "cells": {cell: {"a": attempt_accounting(groups[0][cell]), "b": attempt_accounting(groups[1][cell])}
                  for cell in cells},
    }}
    for key in sorted(set(means_a) | set(means_b)):
        left, right = means_a.get(key, {}), means_b.get(key, {})
        paired = {cell: right[cell] - left[cell] for cell in cells if cell in left and cell in right}
        diffs = list(paired.values())
        overall = statistics.fmean(diffs) if diffs else None
        coverage = {cell: {"a": _coverage(groups[0][cell], key), "b": _coverage(groups[1][cell], key)} for cell in cells}
        issues = {cell: list(condition_issues.get(cell, [])) for cell in cells}
        for cell in cells:
            for arm in ("a", "b"):
                evidence = coverage[cell][arm]
                if not evidence["missing_cell"] and (evidence["missing_metric"] or not evidence["available"]):
                    issues[cell].append(f"missing metric in {arm}")
            if key.startswith("judge_"):
                judged = [[run for run in groups[index][cell]
                           if run.get("completed") is True and key in _metric_values(run)] for index in (0, 1)]
                if all(judged):
                    issues[cell].extend(_identity_issues(*judged, judge=True))
        issues = {cell: reasons for cell, reasons in issues.items() if reasons}
        entry = {"cells": {cell: round(diff, 3) for cell, diff in paired.items()}, "n_cells": len(diffs),
                 "mean_a": round(statistics.fmean(left[cell] for cell in paired), 3) if paired else None,
                 "mean_b": round(statistics.fmean(right[cell] for cell in paired), 3) if paired else None,
                 "diff": round(overall, 3) if overall is not None else None, "coverage": coverage}
        if not paired:
            entry["mean_a"] = round(statistics.fmean(left.values()), 3) if left else None
            entry["mean_b"] = round(statistics.fmean(right.values()), 3) if right else None
        if issues or overall is None:
            entry.update(verdict="not comparable", comparison_issues=issues)
            if key.startswith("judge_"):
                entry["judge_versions"] = [sorted(judge_versions(a)), sorted(judge_versions(b))]
        elif key in rules:
            threshold, direction = rules[key]
            sign = 1 if overall > 0 else -1
            entry.update(threshold=threshold, direction=direction,
                         agree=sum(1 for diff in diffs if diff * sign > 0),
                         verdict=verdict(overall, diffs, threshold, direction))
        else:
            entry["verdict"] = "report only"
        report[key] = entry
    blocked = [key for key, entry in report.items()
               if not key.startswith("_") and entry["verdict"] == "not comparable"]
    report["_comparison"]["blocked_metrics"] = blocked
    if blocked:
        report["_comparison"]["status"] = "not comparable" if len(blocked) == len(report) - 1 else "partially comparable"
    if completion_status != "complete":
        report["_comparison"]["status"] = completion_status
    report["_comparison"]["decision"] = ("eligible for descriptive review"
                                           if report["_comparison"]["status"] == "comparable" else "withheld")
    return report


def unpaired_cells(a: dict, b: dict) -> tuple[list[str], list[str]]:
    """Missing cells use attempted and declared cells, including entirely failed cells."""
    cells_a, cells_b = _cells(a), _cells(b)
    return sorted(cells_a - cells_b), sorted(cells_b - cells_a)


def format_table(report: dict, label_a: str = "A", label_b: str = "B") -> str:
    """Render metric reports plus attempt evidence, including legacy missing identities."""
    metrics = {key: value for key, value in report.items() if not key.startswith("_")}
    cells = sorted({cell for entry in metrics.values() for cell in entry["cells"]})
    ordered = sorted(metrics, key=lambda key: (key not in RULES, key))
    lines = [f"{'metric':<22}{label_a[:10]:>10}{label_b[:10]:>10}{'B-A':>8}{'cells':>7}  verdict"]
    for key in ordered:
        entry = metrics[key]
        agree = f"{entry['agree']}/{entry['n_cells']}" if "agree" in entry else f"-/{entry['n_cells']}"
        diff = f"{entry['diff']:+.2f}" if entry['diff'] is not None else "-"
        left, right = (entry[name] if entry[name] is not None else "-" for name in ("mean_a", "mean_b"))
        lines.append(f"{key:<22}{left:>10}{right:>10}{diff:>8}{agree:>7}  {entry['verdict']}")
    comparison = report.get("_comparison", {})
    if comparison:
        lines.append(f"\ncomparison: {comparison['status']}; descriptive heuristic; scores on completed decks")
        for label, field in ((label_a, "accounting_a"), (label_b, "accounting_b")):
            accounting = comparison[field]
            completion = f"{accounting['completion_rate']:.3f}" if accounting['completion_rate'] is not None else "unavailable"
            failure = f"{accounting['failure_rate']:.3f}" if accounting['failure_rate'] is not None else "unavailable"
            lines.append(f"{label}: attempts {accounting['attempted_runs']}, completed {accounting['completed_runs']}, "
                         f"failed {accounting['failed_runs']}, unknown {accounting['unknown_outcome_runs']}, "
                         f"retries {accounting['retry_attempts']}; completion_rate {completion}, failure_rate {failure}")
            for key in ("first_try_ok_rate", "fallback_rate"):
                rate = accounting[key]
                value = f"{rate['value']:.3f}" if rate['value'] is not None else "unavailable"
                lines.append(f"  {key} {value} ({rate['numerator']}/{rate['pages']} pages; "
                             f"missing on {rate['missing_runs']} completed runs)")
        for cell, issues in comparison.get("condition_issues", {}).items():
            lines.append(f"  {cell}: {', '.join(issues)}")
        for key in comparison.get("blocked_metrics", []):
            issues = metrics[key].get("comparison_issues", {})
            for cell, reasons in issues.items():
                additional = [reason for reason in reasons if reason not in comparison.get("condition_issues", {}).get(cell, [])]
                if additional:
                    lines.append(f"  {key} / {cell}: {', '.join(additional)}")
    lines.append("\nper-cell B-A (rule metrics): " + ", ".join(cells))
    for key in ordered:
        if key in RULES:
            lines.append(f"  {key:<20}" + "  ".join(f"{metrics[key]['cells'][cell]:+.2f}"
                                                    if cell in metrics[key]['cells'] else "-" for cell in cells))
    return "\n".join(lines)
