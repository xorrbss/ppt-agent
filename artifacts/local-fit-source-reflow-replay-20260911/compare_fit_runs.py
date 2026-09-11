"""Compare the verbatim 35-slide runs with and without the deterministic local fit.

Reads only the run directories produced by
``artifacts/authored-full-product-validation-20260910/run.ps1`` (and the audit
files ``audit-full-run.ps1`` leaves beside them). No database, no stack.

    python compare_fit_runs.py <validation-dir> [run-name ...]

With no run names it picks up every ``run-*`` directory it can read. The local
fit columns are empty for OFF runs by construction: that is the control.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _walk(obj: Any, key: str, out: list[Any] | None = None) -> list[Any]:
    out = [] if out is None else out
    if isinstance(obj, dict):
        for name, value in obj.items():
            if name == key:
                out.append(value)
            _walk(value, key, out)
    elif isinstance(obj, list):
        for value in obj:
            _walk(value, key, out)
    return out


def _first(obj: Any, *keys: str) -> Any:
    for key in keys:
        found = _walk(obj, key)
        if found:
            return found[0]
    return None


def _quality_gate(*sources: Any) -> dict[str, Any]:
    for source in sources:
        gate = _first(source, "authoredQualityGate", "quality_gate_receipt", "qualityGate")
        if isinstance(gate, dict):
            return gate
    return {}


def _local_fit(gate: dict[str, Any]) -> dict[str, Any]:
    """Summarise the per-slide local-fit receipts, including verbatim shrink."""

    block = gate.get("localFit")
    if not isinstance(block, dict):
        return {"present": False}
    slides = [item for item in block.get("slides") or [] if isinstance(item, dict)]
    statuses: dict[str, int] = {}
    operations: dict[str, int] = {}
    unresolved: dict[str, int] = {}
    shrunk: list[tuple[int, float, float]] = []
    min_final_pt: float | None = None
    for slide in slides:
        statuses[str(slide.get("status"))] = statuses.get(str(slide.get("status")), 0) + 1
        for operation in slide.get("operations") or []:
            if isinstance(operation, dict):
                kind = str(operation.get("kind"))
                operations[kind] = operations.get(kind, 0) + 1
        for reason in slide.get("unresolvedReasons") or []:
            unresolved[str(reason)] = unresolved.get(str(reason), 0) + 1
        for run in slide.get("runs") or []:
            if not isinstance(run, dict):
                continue
            final = run.get("final_pt")
            original = run.get("original_pt")
            if isinstance(final, (int, float)):
                min_final_pt = final if min_final_pt is None else min(min_final_pt, final)
            if (
                isinstance(final, (int, float))
                and isinstance(original, (int, float))
                and original > 0
                and final < original - 1e-6
            ):
                shrunk.append((int(slide.get("slideIndex", -1)), float(original), float(final)))
    return {
        "present": True,
        "policy": block.get("policyRevision"),
        "slides": len(slides),
        "statuses": statuses,
        "operations": operations,
        "unresolved": unresolved,
        "shrunk_runs": len(shrunk),
        "shrunk_slides": sorted({index for index, _, _ in shrunk}),
        "worst_shrink": (
            max((1 - final / original) for _, original, final in shrunk) if shrunk else 0.0
        ),
        "min_final_pt": min_final_pt,
    }


def summarise(run_dir: Path) -> dict[str, Any]:
    status = _load(run_dir / "06-final-status.json")
    audit = _load(run_dir / "08-audit.json")
    download = _load(run_dir / "07-download-evidence.json")
    gate = _quality_gate(audit, status)
    readback = _first(audit, "sourceReadback") or _first(status, "sourceReadback") or {}
    flagged = gate.get("flaggedSlides")
    flagged_indices = (
        sorted(item["index"] for item in flagged if isinstance(item, dict) and "index" in item)
        if isinstance(flagged, list)
        else None
    )
    published = _first(audit, "jobState") == "published" or bool(download)
    return {
        "run": run_dir.name,
        "published": published,
        "failure": _first(status, "code") or _first(status, "failureCode"),
        "slides": gate.get("slideCount") or _first(status, "total_slides"),
        "flagged": len(flagged_indices) if flagged_indices is not None else None,
        "flagged_indices": flagged_indices,
        "review_pages": len(gate.get("reviewPages") or []) or None,
        "visibility_failures": len(_walk(readback, "visibilityFailures")[0] or [])
        if _walk(readback, "visibilityFailures")
        else None,
        "residual_issues": gate.get("residualIssueCount"),
        "repaired": gate.get("repairedSlideIndices"),
        "local_fit": _local_fit(gate),
    }


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    root = Path(argv[0])
    names = argv[1:] or sorted(
        (path.name for path in root.glob("run-*") if path.is_dir()),
        key=lambda name: (len(name), name),
    )
    rows = []
    for name in names:
        directory = root / name
        if (directory / "06-final-status.json").exists():
            rows.append(summarise(directory))
    if not rows:
        print("no readable run directories under", root)
        return 1

    print(f"{'run':<22}{'pub':<5}{'flag':<6}{'vis':<5}{'resid':<7}{'fit':<5}{'failure'}")
    for row in rows:
        fit = "ON" if row["local_fit"]["present"] else "off"
        print(
            f"{row['run']:<22}{('yes' if row['published'] else 'no'):<5}"
            f"{str(row['flagged']):<6}{str(row['visibility_failures']):<5}"
            f"{str(row['residual_issues']):<7}{fit:<5}{row['failure'] or ''}"
        )
    print()
    for row in rows:
        print(f"--- {row['run']}: flagged={row['flagged_indices']} repaired={row['repaired']}")
        fit = row["local_fit"]
        if fit["present"]:
            print(f"    local fit {fit['policy']}: {fit['slides']} slides {fit['statuses']}")
            print(f"    operations {fit['operations']}  unresolved {fit['unresolved']}")
            print(
                f"    verbatim text shrunk: {fit['shrunk_runs']} runs on slides "
                f"{fit['shrunk_slides']}, worst {fit['worst_shrink']:.3%}, "
                f"min final {fit['min_final_pt']}pt"
            )
    print("\nwrote nothing; this reader is read-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
