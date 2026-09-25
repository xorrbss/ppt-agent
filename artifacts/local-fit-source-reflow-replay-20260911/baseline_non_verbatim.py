"""Is ``fixed_horizontal`` 42.7% a property of one document, or of the contract?

Every number in the replay report came from one source document, so the rejection
breakdown could be that document's habits rather than the fitter's. This re-runs
the bounded search over the most recent non-verbatim authored decks -- different
prompts, different days, different authors' output -- and reports the rejection
mix per deck.

Only the ``fit=True`` render is needed: the question is which rule rejects the
candidates, not whether the result would publish. Stored ``slides.html_content``
is rendered as it sits, so decks that today's admission gate would refuse are
still valid evidence about the fitter.

Read-only: stored HTML in, Chromium render, JSON out. No generation, no provider
call, no database write, no stack contact.

    python baseline_non_verbatim.py <decks.json> <out.json> [--limit-slides N]

``decks.json`` is the deck list from the selection query: objects with ``id``,
``title``, ``day`` and ``slides``. Results are checkpointed after every deck, and
a rerun with the same output path resumes rather than repeating finished decks.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path.cwd()))

import psycopg  # noqa: E402

from services.authored_local_fit_policy import html_digest, local_fit_policy  # noqa: E402
from utils.slide_capture import render_html_to_png_with_metrics  # noqa: E402

#: Kept outside the repository; point ``LOCAL_FIT_DB_URL_FILE`` at it.
DB_URL = Path(os.environ.get("LOCAL_FIT_DB_URL_FILE")
              or Path(__file__).resolve().parent / "dburl.txt")


def database_url() -> str:
    return re.sub(r"^postgresql\+\w+://", "postgresql://", DB_URL.read_text().strip())


def slides_of(pid: str, limit: int | None) -> list[tuple[int, str]]:
    with psycopg.connect(
        database_url(), connect_timeout=15, options="-c default_transaction_read_only=on"
    ) as connection:
        connection.execute("set statement_timeout='60s'")
        rows = connection.execute(
            "select index, html_content from slides"
            " where presentation = %s and html_content is not null and html_content <> ''"
            " order by index",
            (pid,),
        ).fetchall()
    picked = [(int(index), html) for index, html in rows]
    if limit is not None and len(picked) > limit:
        # Deterministic spread rather than the first N, so a deck's later slides
        # (usually the dense ones) are represented.
        step = len(picked) / limit
        picked = [picked[int(position * step)] for position in range(limit)]
    return picked


def resume_from(path: Path) -> dict[str, Any] | None:
    """Decks already measured in a previous run, or None if there is no output yet."""

    if not path.exists():
        return None
    return {row["deck"]: row for row in read_json(path)}


def read_json(path: Path) -> Any:
    """File I/O kept out of the coroutine; this is a one-shot script, not a server."""

    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def rules_of(report: dict[str, Any]) -> tuple[Counter, int]:
    """Rejection rule counts for one slide, and how many candidates were judged."""

    rules: Counter = Counter()
    judged = 0
    for candidate in report.get("candidates") or []:
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("outcome") or "").startswith("removed:duplicate"):
            continue
        judged += 1
        rejection = candidate.get("rejection") or {}
        if not rejection:
            continue
        rules[str(rejection.get("rule") or rejection.get("code") or "?")] += 1
    return rules, judged


async def measure_slide(index: int, html: str) -> dict[str, Any]:
    digest = html_digest(html)
    policy = local_fit_policy(
        author_revision=digest, original_html_digest=digest, slide_index=index,
    )
    started = time.monotonic()
    rendered = await render_html_to_png_with_metrics(
        html, timeout=60, fit=True, fit_policy=policy, fresh=True,
    )
    report = rendered.fit_report or {}
    rules, judged = rules_of(report)
    return {
        "slide": index,
        "status": report.get("status"),
        "changed": bool(report.get("changed")),
        "unresolved": list(report.get("unresolved_reasons") or []),
        "candidates_judged": judged,
        "rules": dict(rules),
        "seconds": round(time.monotonic() - started, 2),
    }


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    decks = read_json(Path(argv[0]))
    out = Path(argv[1])
    limit = None
    if "--limit-slides" in argv:
        limit = int(argv[argv.index("--limit-slides") + 1])

    done: dict[str, Any] = {}
    if resume_from(out) is not None:
        done = resume_from(out)
        print(f"resuming: {len(done)} decks already measured", flush=True)

    for position, deck in enumerate(decks, start=1):
        pid = deck["id"]
        if pid in done:
            continue
        rows = slides_of(pid, limit)
        measured, failures = [], 0
        for index, html in rows:
            try:
                measured.append(await measure_slide(index, html))
            except Exception as error:  # a slide that will not render is data, not a stop
                failures += 1
                measured.append({"slide": index, "status": "render_error",
                                 "error": str(error)[:160], "rules": {}, "candidates_judged": 0})
        rules: Counter = Counter()
        for row in measured:
            rules.update(row["rules"])
        judged = sum(row["candidates_judged"] for row in measured)
        done[pid] = {
            "deck": pid, "title": deck.get("title"), "day": deck.get("day"),
            "slides_measured": len(measured), "render_errors": failures,
            "candidates_judged": judged, "rules": dict(rules), "slides": measured,
        }
        write_json(out, list(done.values()))
        share = (rules.get("fixed_horizontal", 0) / judged) if judged else 0.0
        print(
            f"[{position:>2}/{len(decks)}] {deck.get('day')} {str(deck.get('title'))[:34]:<34}"
            f" slides={len(measured):>3} judged={judged:>4}"
            f" fixed_horizontal={rules.get('fixed_horizontal', 0):>4} ({share:5.1%})",
            flush=True,
        )

    rows = list(done.values())
    overall: Counter = Counter()
    for row in rows:
        overall.update(row["rules"])
    judged = sum(row["candidates_judged"] for row in rows)
    print(f"\n=== {len(rows)} non-verbatim decks, {sum(r['slides_measured'] for r in rows)} slides ===")
    print(f"candidates judged: {judged}")
    for rule, count in overall.most_common():
        print(f"  {count:6d} ({count / judged:6.1%})  {rule}" if judged else f"  {count:6d}  {rule}")

    with_candidates = [row for row in rows if row["candidates_judged"]]
    shares = sorted(
        (row["rules"].get("fixed_horizontal", 0) / row["candidates_judged"], row["title"])
        for row in with_candidates
    )
    print(f"\ndecks whose search rejected anything: {len(with_candidates)} of {len(rows)}")
    if shares:
        print(f"  fixed_horizontal share, min {shares[0][0]:.1%} / median "
              f"{shares[len(shares) // 2][0]:.1%} / max {shares[-1][0]:.1%}")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
