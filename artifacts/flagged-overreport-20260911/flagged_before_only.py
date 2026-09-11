"""Does each flagged slide actually carry a blocking defect on its published bytes?

One render per flagged slide (fit disabled), judged by the pipeline's own gate.
Read-only: no generation, no provider call, no database write.
"""
from __future__ import annotations
import asyncio, json, re, sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path.cwd()))
import psycopg  # noqa: E402
from services.authored_composition_policy import blocking_safety_issues  # noqa: E402
from services.authored_local_fit_policy import html_digest, local_fit_policy  # noqa: E402
from utils.authored_dom_quality import inspect_authored_dom  # noqa: E402
from utils.slide_capture import render_html_to_png_with_metrics  # noqa: E402

DB = Path(__file__).resolve().parent / "dburl.txt"

def walk(o: Any, k: str, a: list | None = None) -> list:
    a = [] if a is None else a
    if isinstance(o, dict):
        for kk, v in o.items():
            if kk == k: a.append(v)
            walk(v, k, a)
    elif isinstance(o, list):
        for v in o: walk(v, k, a)
    return a

async def main(root: str, run: str, out: str) -> int:
    st = json.loads((Path(root) / run / "06-final-status.json").read_text(encoding="utf-8-sig"))
    pid = (walk(st, "presentation_id") or walk(st, "presentationId") or [None])[0]
    flags = [i for b in walk(st, "flaggedSlides") if isinstance(b, list) for i in b if isinstance(i, dict)]
    indices = sorted({int(i["index"]) for i in flags})
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB.read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='30s'")
        rows = c.execute("select index, html_content from slides where presentation=%s and index=any(%s)", (pid, indices)).fetchall()
    htmls = {int(i): h for i, h in rows if h}
    results = []
    for index in indices:
        html = htmls.get(index)
        if not html:
            print(f"  s{index}: no stored html", flush=True); continue
        digest = html_digest(html)
        policy = local_fit_policy(author_revision=digest, original_html_digest=digest, slide_index=index)
        cap = await render_html_to_png_with_metrics(html, timeout=60, fit=False, fit_policy=policy, fresh=True)
        report = inspect_authored_dom(html, cap.browser_metrics)
        cats: dict[str, int] = {}
        for issue in blocking_safety_issues(report):
            name = str(getattr(issue, "category", "?")); cats[name] = cats.get(name, 0) + 1
        results.append({"run": run, "slide": index, "blocking": cats})
        print(f"  s{index:>2}: {cats or 'clean'}", flush=True)
    Path(out).write_text(json.dumps(results, indent=1), encoding="utf-8")
    clean = [r["slide"] for r in results if not r["blocking"]]
    print(f"\n{run}: {len(results)} flagged, {len(clean)} clean on published bytes -> {clean}")
    return 0

if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(*sys.argv[1:])))
