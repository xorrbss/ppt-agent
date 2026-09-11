"""Paired offline replay: does the deterministic local fit repair the slides a
verbatim source-reflow deck published with a review flag?

For every flagged slide of the published OFF runs, the stored HTML is rendered
twice in the real headless Chromium with the production policy: once as it
shipped (fit off) and once through the fitter, whose output is then re-rendered
fit-disabled and judged by the pipeline's own gate (``inspect_authored_dom`` +
``blocking_safety_issues``). Nothing is written: no database row, no provider
call, no generation.

    python replay_fit_on_flagged.py <validation-dir> <out.json> [run-name ...]

Run it from ``servers/fastapi`` with APP_DATA_DIRECTORY/USER_CONFIG_PATH set.
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


def flagged_slides(run_dir: Path) -> tuple[str | None, list[int]]:
    status = json.loads((run_dir / "06-final-status.json").read_text(encoding="utf-8-sig"))
    pid = (_walk(status, "presentation_id") or _walk(status, "presentationId") or [None])[0]
    flagged = _walk(status, "flaggedSlides")
    indices = sorted(
        {
            int(item["index"])
            for block in flagged
            if isinstance(block, list)
            for item in block
            if isinstance(item, dict) and "index" in item
        }
    )
    return pid, indices


def fetch_html(pid: str, indices: list[int]) -> dict[int, str]:
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB_URL.read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='30s'")
        rows = c.execute(
            "select index, html_content from slides where presentation = %s and index = any(%s)",
            (pid, indices),
        ).fetchall()
    return {int(index): html for index, html in rows if html}


def _categories(report: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in blocking_safety_issues(report):
        name = str(getattr(issue, "category", "?"))
        counts[name] = counts.get(name, 0) + 1
    return counts


def _shrink(report: dict[str, Any]) -> dict[str, Any]:
    shrunk, minimum = [], None
    for run in report.get("runs") or []:
        final, original = run.get("final_pt"), run.get("original_pt")
        if isinstance(final, (int, float)):
            minimum = final if minimum is None else min(minimum, final)
        if (
            isinstance(final, (int, float))
            and isinstance(original, (int, float))
            and original > 0
            and final < original - 1e-6
        ):
            shrunk.append(round(1 - final / original, 6))
    return {
        "shrunk_runs": len(shrunk),
        "worst_shrink": max(shrunk) if shrunk else 0.0,
        "min_final_pt": minimum,
    }


async def replay_one(run: str, index: int, html: str) -> dict[str, Any]:
    digest = html_digest(html)
    policy = local_fit_policy(
        author_revision=digest, original_html_digest=digest, slide_index=index,
    )
    shipped = await render_html_to_png_with_metrics(
        html, timeout=60, fit=False, fit_policy=policy, fresh=True,
    )
    before = _categories(inspect_authored_dom(html, shipped.browser_metrics))

    fitted = await render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=policy, fresh=True,
    )
    report = fitted.fit_report or {}
    result: dict[str, Any] = {
        "run": run,
        "slide": index,
        "before": before,
        "fit_status": report.get("status"),
        "changed": bool(report.get("changed")),
        "operations": [
            {"kind": item.get("kind"), "qid": item.get("qid")}
            for item in report.get("operations") or []
            if isinstance(item, dict)
        ],
        "unresolved": list(report.get("unresolved_reasons") or []),
        "termination": [
            item.get("code")
            for item in report.get("search_termination") or []
            if isinstance(item, dict)
        ],
        **_shrink(report),
    }
    if not fitted.fitted_html or fitted.fitted_html == html:
        result["after"] = before
        result["verdict"] = "unchanged"
        return result

    verified = await render_html_to_png_with_metrics(
        fitted.fitted_html, timeout=60, fit=False,
        fit_policy={**policy, "baseline": report.get("baseline") or {}}, fresh=True,
    )
    after = _categories(inspect_authored_dom(fitted.fitted_html, verified.browser_metrics))
    result["after"] = after
    total_before, total_after = sum(before.values()), sum(after.values())
    result["verdict"] = (
        "resolved" if total_after == 0 and total_before > 0
        else "improved" if total_after < total_before
        else "worse" if total_after > total_before
        else "no_change"
    )
    return result


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root, out = Path(argv[0]), Path(argv[1])
    names = argv[2:] or ["run-11", "run-15", "run-16"]
    results: list[dict[str, Any]] = []
    for name in names:
        pid, indices = flagged_slides(root / name)
        if not pid or not indices:
            print(f"{name}: no flagged slides or no presentation id; skipped")
            continue
        htmls = fetch_html(pid, indices)
        print(f"{name}: {len(htmls)}/{len(indices)} flagged slides fetched ({pid})", flush=True)
        for index in indices:
            html = htmls.get(index)
            if not html:
                print(f"  slide {index}: no stored html", flush=True)
                continue
            row = await replay_one(name, index, html)
            results.append(row)
            print(
                f"  slide {index:>2}: {row['verdict']:<10} {row['fit_status']:<18}"
                f" before={row['before']} after={row['after']}"
                f" shrunk={row['shrunk_runs']} worst={row['worst_shrink']:.2%}",
                flush=True,
            )
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")

    verdicts: dict[str, int] = {}
    for row in results:
        verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
    shrunk_slides = [row["slide"] for row in results if row["shrunk_runs"]]
    print(f"\n=== {len(results)} flagged slides replayed ===")
    print("verdicts:", verdicts)
    print("slides whose verbatim text the fitter shrank:", shrunk_slides)
    worst = max((row["worst_shrink"] for row in results), default=0.0)
    floors = [row["min_final_pt"] for row in results if row["min_final_pt"] is not None]
    print(f"worst shrink {worst:.2%}; smallest final type {min(floors) if floors else None}pt")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
