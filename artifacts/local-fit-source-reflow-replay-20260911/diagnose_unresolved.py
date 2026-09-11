"""Why the fitter cannot repair the slides that shipped with a review flag.

(1) For every slide the paired replay left ``unresolved``, dump the bounded
    search's own evidence: termination code, and each candidate's tier, factors
    and rejection code. That says whether the two capabilities parked as strict
    xfail -- an auto-height scope root, and slack outside the grid's parent --
    would have resolved these real slides, or whether something else blocks.

(2) For every slide whose published bytes still carry ``overlap``, check whether
    the overlapping elements are marked intentional. ``utils/slide_capture.py``
    emits ``intentional`` from ``data-allow-overlap`` and the gate honours it
    (``utils/authored_dom_quality.py``), so a still-reported overlap should be a
    genuine defect; this verifies that rather than assuming it.

Read-only: no generation, no provider call, no database write.

    python diagnose_unresolved.py <validation-dir> <out.json>
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

import psycopg  # noqa: E402

from services.authored_composition_policy import blocking_safety_issues  # noqa: E402
from services.authored_local_fit_policy import html_digest, local_fit_policy  # noqa: E402
from utils.authored_dom_quality import inspect_authored_dom  # noqa: E402
from utils.slide_capture import render_html_to_png_with_metrics  # noqa: E402

DB_URL = Path(__file__).resolve().parent / "dburl.txt"

# From fit-replay-run11.json / fit-replay-run15-16.json: the slides the fitter
# could not repair, plus every slide whose published bytes still show overlap.
TARGETS: dict[str, list[int]] = {
    "run-11": [16, 26, 29],
    "run-15": [21, 24],
    "run-16": [9, 19, 25, 26],
}


def _walk(obj: Any, key: str, acc: list[Any] | None = None) -> list[Any]:
    acc = [] if acc is None else acc
    if isinstance(obj, dict):
        for name, value in obj.items():
            if name == key:
                acc.append(value)
            _walk(value, key, acc)
    elif isinstance(obj, list):
        for value in obj:
            _walk(value, key, acc)
    return acc


def presentation_id(run_dir: Path) -> str | None:
    status = json.loads((run_dir / "06-final-status.json").read_text(encoding="utf-8-sig"))
    found = _walk(status, "presentation_id") or _walk(status, "presentationId")
    return found[0] if found else None


def fetch(pid: str, indices: list[int]) -> dict[int, str]:
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB_URL.read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='30s'")
        rows = c.execute(
            "select index, html_content from slides where presentation = %s and index = any(%s)",
            (pid, indices),
        ).fetchall()
    return {int(index): html for index, html in rows if html}


def overlap_detail(report: Any, metrics: dict[str, Any], html: str) -> list[dict[str, Any]]:
    """Each reported overlap with whether its elements are marked intentional."""

    elements = {
        str(item.get("qid")): item
        for item in (metrics or {}).get("elements") or []
        if isinstance(item, dict)
    }
    detail = []
    for issue in blocking_safety_issues(report):
        if str(getattr(issue, "category", "")) != "overlap":
            continue
        qids = [str(qid) for qid in getattr(issue, "qids", ()) or ()]
        marked = {}
        for qid in qids:
            element = elements.get(qid) or {}
            marked[qid] = {
                "metrics_intentional": bool(element.get("intentional")),
                "attribute_in_html": bool(
                    re.search(
                        r'data-qid="' + re.escape(qid) + r'"[^>]*data-allow-overlap'
                        r'|data-allow-overlap[^>]*data-qid="' + re.escape(qid) + '"',
                        html,
                    )
                ),
            }
        detail.append(
            {
                "qids": qids,
                "detail": str(getattr(issue, "detail", ""))[:120],
                "severity": str(getattr(issue, "severity", "")),
                "marked": marked,
            }
        )
    return detail


async def diagnose(run: str, index: int, html: str) -> dict[str, Any]:
    digest = html_digest(html)
    policy = local_fit_policy(
        author_revision=digest, original_html_digest=digest, slide_index=index,
    )
    shipped = await render_html_to_png_with_metrics(
        html, timeout=60, fit=False, fit_policy=policy, fresh=True,
    )
    report_shipped = inspect_authored_dom(html, shipped.browser_metrics)

    fitted = await render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=policy, fresh=True,
    )
    report = fitted.fit_report or {}
    rejections: dict[str, int] = {}
    rules: dict[str, int] = {}
    candidates = []
    for candidate in report.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        outcome = str(candidate.get("outcome") or "")
        if outcome.startswith("removed:duplicate"):
            continue
        rejection = candidate.get("rejection") or {}
        code = str(rejection.get("code") or outcome)
        rejections[code] = rejections.get(code, 0) + 1
        rule_key = str(rejection.get("rule") or "-")
        rules[rule_key] = rules.get(rule_key, 0) + 1
        candidates.append(
            {
                "id": candidate.get("candidate_id"),
                "font_factor": candidate.get("font_factor"),
                "spacing_factor": candidate.get("spacing_factor"),
                "uses_parent_space": candidate.get("uses_parent_space"),
                "outcome": outcome,
                "rule": rejection.get("rule"),
                "qid": rejection.get("qid"),
                "space": candidate.get("space"),
            }
        )
    return {
        "run": run,
        "slide": index,
        "fit_status": report.get("status"),
        "unresolved": list(report.get("unresolved_reasons") or []),
        "termination": report.get("search_termination") or [],
        "scopes": [
            {"root": scope.get("root_qid"), "targets": scope.get("target_qids")}
            for scope in report.get("scopes") or []
            if isinstance(scope, dict)
        ],
        "parent_exclusions": report.get("parent_exclusions") or [],
        "geometry_seeds": report.get("geometry_seeds") or [],
        "rejection_counts": rejections,
        "rule_counts": rules,
        "candidates": candidates,
        "overlaps": overlap_detail(report_shipped, shipped.browser_metrics, html),
    }


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root, out = Path(argv[0]), Path(argv[1])
    results = []
    for run, indices in TARGETS.items():
        pid = presentation_id(root / run)
        if not pid:
            print(f"{run}: no presentation id")
            continue
        htmls = fetch(pid, indices)
        for index in indices:
            html = htmls.get(index)
            if not html:
                print(f"{run} slide {index}: no stored html", flush=True)
                continue
            row = await diagnose(run, index, html)
            results.append(row)
            print(
                f"{run} s{index:>2} {str(row['fit_status']):<18}"
                f" unresolved={row['unresolved']}"
                f" rejections={row['rejection_counts']}",
                flush=True,
            )
            for exclusion in row["parent_exclusions"][:3]:
                print(f"      exclusion: {exclusion}", flush=True)
            for overlap in row["overlaps"]:
                print(f"      overlap {overlap['qids']} marked={overlap['marked']}", flush=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

    totals: dict[str, int] = {}
    for row in results:
        for code, count in row["rejection_counts"].items():
            totals[code] = totals.get(code, 0) + count
    print("\n=== rejection codes over every unresolved slide ===")
    for code, count in sorted(totals.items(), key=lambda item: -item[1]):
        print(f"  {count:4d}  {code}")
    intentional = [
        (row["run"], row["slide"], qid)
        for row in results
        for overlap in row["overlaps"]
        for qid, mark in overlap["marked"].items()
        if mark["metrics_intentional"] or mark["attribute_in_html"]
    ]
    print("overlaps whose elements are marked intentional:", intentional or "none")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
