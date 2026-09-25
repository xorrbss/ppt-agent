"""After the image-text evidence producer: how many of the slides that produced
zero candidates now run, and how many get repaired?

Input is the list of (deck, slide) pairs the non-verbatim baseline left
``unresolved`` with reason ``font_unverifiable``. Each is rendered again through
the same path production uses (``render_html_to_png_with_metrics`` with a fit
policy, which goes through ``capture_with_policy`` and therefore through the
evidence producer). Only the ``fit=True`` render is needed to see whether the
search ran; a slide that changed is additionally re-rendered ``fit=False`` and
judged by the same gate the baseline used, so "repaired" means what it meant
there.

Read-only: stored HTML in, Chromium render, JSON out. No generation, no provider
call, no database write, no stack contact.

    python remeasure_font_unverifiable.py <targets.json> <out.json>
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

import psycopg  # noqa: E402

from services.authored_composition_policy import blocking_safety_issues  # noqa: E402
from services.authored_local_fit_policy import html_digest, local_fit_policy  # noqa: E402
from utils.authored_dom_quality import inspect_authored_dom  # noqa: E402
from utils.slide_capture import render_html_to_png_with_metrics  # noqa: E402

#: Kept outside the repository; point ``LOCAL_FIT_DB_URL_FILE`` at it.
DB_URL = Path(os.environ.get("LOCAL_FIT_DB_URL_FILE")
              or Path(__file__).resolve().parent / "dburl.txt")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch(targets: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB_URL.read_text().strip())
    wanted: dict[str, list[int]] = defaultdict(list)
    for item in targets:
        wanted[item["deck"]].append(int(item["slide"]))
    htmls: dict[tuple[str, int], str] = {}
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='180s'")
        for pid, indices in wanted.items():
            rows = c.execute(
                "select index, html_content from slides where presentation = %s and index = any(%s)",
                (pid, indices),
            ).fetchall()
            for index, html in rows:
                if html:
                    htmls[(pid, int(index))] = html
    return htmls


def categories(report: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in blocking_safety_issues(report):
        name = str(getattr(issue, "category", "?"))
        counts[name] = counts.get(name, 0) + 1
    return counts


async def measure(index: int, html: str) -> dict[str, Any]:
    digest = html_digest(html)
    policy = local_fit_policy(
        author_revision=digest, original_html_digest=digest, slide_index=index,
    )
    fitted = await render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=policy, fresh=True,
    )
    report = fitted.fit_report or {}
    image_text = [
        {"qid": item.get("qid"), "scope": item.get("scope")}
        for item in report.get("image_text") or []
        if isinstance(item, dict)
    ]
    row: dict[str, Any] = {
        "status": report.get("status"),
        "unresolved": list(report.get("unresolved_reasons") or []),
        "candidates": len([c for c in report.get("candidates") or []
                           if isinstance(c, dict)
                           and not str(c.get("outcome") or "").startswith("removed:duplicate")]),
        "image_text": image_text,
        "evidence_records": len(policy.get("image_text_evidence") or []),
    }
    if not fitted.fitted_html or fitted.fitted_html == html:
        row["verdict"] = "no_edit"
        return row
    shipped = await render_html_to_png_with_metrics(
        html, timeout=60, fit=False, fit_policy=policy, fresh=True,
    )
    before = categories(inspect_authored_dom(html, shipped.browser_metrics))
    verified = await render_html_to_png_with_metrics(
        fitted.fitted_html, timeout=60, fit=False,
        fit_policy={**policy, "baseline": report.get("baseline") or {}}, fresh=True,
    )
    after = categories(inspect_authored_dom(fitted.fitted_html, verified.browser_metrics))
    total_before, total_after = sum(before.values()), sum(after.values())
    row.update(before=before, after=after, verdict=(
        "resolved" if total_after == 0 and total_before > 0
        else "improved" if total_after < total_before
        else "worse" if total_after > total_before
        else "edited_no_defect_change"
    ))
    return row


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    targets = read_json(Path(argv[0]))
    out = Path(argv[1])
    htmls = fetch(targets)
    results: list[dict[str, Any]] = []
    for position, item in enumerate(targets, start=1):
        key = (item["deck"], int(item["slide"]))
        html = htmls.get(key)
        if not html:
            results.append({**item, "status": "missing_html"})
            continue
        try:
            row = await measure(key[1], html)
        except Exception as error:  # a slide that will not render is data, not a stop
            row = {"status": "render_error", "error": str(error)[:160]}
        results.append({**item, **row})
        if position % 25 == 0 or position == len(targets):
            write_json(out, results)
            print(f"[{position:>3}/{len(targets)}]", flush=True)
    write_json(out, results)

    still = [r for r in results if "font_unverifiable" in (r.get("unresolved") or [])]
    ran = [r for r in results if r.get("status") not in ("render_error", "missing_html")
           and "font_unverifiable" not in (r.get("unresolved") or [])]
    print(f"\n=== {len(results)} slides that produced zero candidates before ===")
    print(f"still font_unverifiable: {len(still)}")
    print(f"search now runs:         {len(ran)}")
    print("  outcome:", dict(Counter(str(r.get("status")) for r in ran)))
    print("  verdict:", dict(Counter(str(r.get("verdict")) for r in ran)))
    scopes = Counter(s["scope"] for r in results for s in r.get("image_text") or [])
    print("image-text verdicts over all records:", dict(scopes))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
