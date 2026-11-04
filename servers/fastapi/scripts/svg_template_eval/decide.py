"""Paired decision rule for two evaluation results (pure; unit-tested in tests/unit/test_svg_template_eval_decide.py).

A result is ``run_eval.py``'s JSON: ``runs`` is a list of run records, each with a ``cell`` id (``default`` when a
result was made by ``run`` without cells). For one metric the runs of a cell are averaged, the cells of the two
variants are paired, and the difference B - A is taken per cell. A verdict needs both an overall mean difference
beyond the metric's threshold and the same sign in a majority of cells. Judge scores are compared only when every
judged run of both results carries the same ``judge_version`` (a run without one is version 1); otherwise every
``judge_*`` metric is ``not comparable``.
"""
from __future__ import annotations

import statistics

DEFAULT_CELL = "default"
JUDGE_AXES = ("structure", "density", "hierarchy", "tone", "visuals")
#: metric -> (threshold on the overall mean paired difference, direction: +1 higher is better, -1 lower is better).
#: Judge scores move about 0.2 between calls of the same deck; fallback pages and first-try pages are counts of
#: pages out of ten, so half a page is the smallest difference worth naming.
RULES: dict[str, tuple[float, int]] = {
    **{f"judge_{axis}": (0.2, 1) for axis in JUDGE_AXES},
    "judge_overall": (0.2, 1),
    "fallback_pages": (0.5, -1),
    "first_try_ok": (0.5, 1),
}
_SKIP = {"run", "seconds", "pages", "cell", "judge_version"}


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def judge_versions(result: dict) -> set[int]:
    """The judge versions of the completed runs that carry judge scores (a run without ``judge_version`` was
    scored by version 1, before the field existed)."""
    return {run.get("judge_version", 1) for run in result.get("runs", [])
            if run.get("completed") and any(key.startswith("judge_") and key != "judge_version" and _numeric(value)
                                            for key, value in run.items())}


def cell_means(result: dict) -> dict[str, dict[str, float]]:
    """metric -> cell -> mean over the completed runs of that cell that have the metric."""
    values: dict[str, dict[str, list[float]]] = {}
    for run in result.get("runs", []):
        if not run.get("completed"):
            continue
        cell = run.get("cell") or DEFAULT_CELL
        for key, value in run.items():
            if key not in _SKIP and _numeric(value):
                values.setdefault(key, {}).setdefault(cell, []).append(float(value))
    return {key: {cell: statistics.fmean(items) for cell, items in cells.items()} for key, cells in values.items()}


def verdict(overall: float, diffs: list[float], threshold: float, direction: int) -> str:
    """``better`` / ``worse`` only when |overall| > threshold and more than half of the cells share its sign."""
    if abs(overall) <= threshold:
        return "no difference"
    sign = 1 if overall > 0 else -1
    agree = sum(1 for diff in diffs if diff * sign > 0)
    if agree * 2 <= len(diffs):
        return "inconsistent"
    return "better" if sign == direction else "worse"


def decide(a: dict, b: dict, rules: dict[str, tuple[float, int]] | None = None) -> dict:
    """Per metric: paired per-cell differences B - A, their mean, how many cells agree and the verdict."""
    rules = RULES if rules is None else rules
    means_a, means_b = cell_means(a), cell_means(b)
    versions = [sorted(judge_versions(a)), sorted(judge_versions(b))]
    judge_comparable = len(set(versions[0]) | set(versions[1])) <= 1
    report: dict[str, dict] = {}
    for key in sorted(set(means_a) & set(means_b)):
        paired = {cell: means_b[key][cell] - means_a[key][cell]
                  for cell in means_a[key] if cell in means_b[key]}
        if not paired:
            continue
        diffs = list(paired.values())
        overall = statistics.fmean(diffs)
        entry = {"cells": {cell: round(diff, 3) for cell, diff in paired.items()}, "n_cells": len(diffs),
                 "mean_a": round(statistics.fmean(means_a[key][cell] for cell in paired), 3),
                 "mean_b": round(statistics.fmean(means_b[key][cell] for cell in paired), 3),
                 "diff": round(overall, 3)}
        if key.startswith("judge_") and not judge_comparable:
            entry.update(verdict="not comparable", judge_versions=versions)
        elif key in rules:
            threshold, direction = rules[key]
            sign = 1 if overall > 0 else -1
            entry.update(threshold=threshold, direction=direction,
                         agree=sum(1 for diff in diffs if diff * sign > 0),
                         verdict=verdict(overall, diffs, threshold, direction))
        else:
            entry["verdict"] = "report only"
        report[key] = entry
    return report


def unpaired_cells(a: dict, b: dict) -> tuple[list[str], list[str]]:
    """Cells present in only one of the two results (they are left out of every pair)."""
    cells = [{run.get("cell") or DEFAULT_CELL for run in result.get("runs", []) if run.get("completed")}
             for result in (a, b)]
    return sorted(cells[0] - cells[1]), sorted(cells[1] - cells[0])


def format_table(report: dict, label_a: str = "A", label_b: str = "B") -> str:
    """Compact table: rules first (judge, fallback, first try), then the report-only metrics."""
    cells = sorted({cell for entry in report.values() for cell in entry["cells"]})
    ordered = sorted(report, key=lambda key: (key not in RULES, key))
    lines = [f"{'metric':<22}{label_a[:10]:>10}{label_b[:10]:>10}{'B-A':>8}{'cells':>7}  verdict"]
    for key in ordered:
        entry = report[key]
        agree = f"{entry['agree']}/{entry['n_cells']}" if "agree" in entry else f"-/{entry['n_cells']}"
        lines.append(f"{key:<22}{entry['mean_a']:>10}{entry['mean_b']:>10}{entry['diff']:>+8.2f}{agree:>7}  {entry['verdict']}")
    lines.append("")
    lines.append("per-cell B-A (rule metrics): " + ", ".join(cells))
    for key in ordered:
        if key in RULES:
            lines.append(f"  {key:<20}" + "  ".join(f"{report[key]['cells'].get(cell, float('nan')):+.2f}" for cell in cells))
    return "\n".join(lines)
