"""Render every published slide of one presentation to PNG + DOM metrics.

The bench-era ``render_job.py`` reads a SQLite bench database; the live stack is
PostgreSQL, so this reads ``slides.html_content`` through the same read-only
connection the replay harnesses use and renders with the production capture
(``render_html_to_png_with_metrics``, ``fit=False`` -- the bytes as published).

    python render_live_deck.py <presentation-id> <out-dir>

Run from ``servers/fastapi`` with ``LOCAL_FIT_DB_URL_FILE`` pointing at a file
holding the database URL. Read-only: no generation, no provider call, no
database write, no stack contact. Chromium only.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

import psycopg  # noqa: E402

from utils.slide_capture import render_html_to_png_with_metrics  # noqa: E402

DB_URL = Path(os.environ.get("LOCAL_FIT_DB_URL_FILE")
              or Path(__file__).resolve().parent / "dburl.txt")


def fetch(pid: str) -> list[tuple[int, str]]:
    url = re.sub(r"^postgresql\+\w+://", "postgresql://", DB_URL.read_text().strip())
    with psycopg.connect(url, connect_timeout=15, options="-c default_transaction_read_only=on") as c:
        c.execute("set statement_timeout='60s'")
        rows = c.execute(
            "select index, html_content from slides"
            " where presentation = %s and html_content is not null and html_content <> ''"
            " order by index",
            (pid,),
        ).fetchall()
    return [(int(index), html) for index, html in rows]


async def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    pid, out = argv[0], Path(argv[1])
    slides = fetch(pid)
    for index, html in slides:
        capture = await render_html_to_png_with_metrics(html, timeout=60, fresh=True)
        (out / f"s{index:02d}.png").write_bytes(capture.png)
        (out / f"s{index:02d}.metrics.json").write_text(
            json.dumps(capture.browser_metrics or {}, ensure_ascii=False), encoding="utf-8",
        )
        (out / f"s{index:02d}.html").write_text(html, encoding="utf-8")
        print(f"s{index:02d} rendered", flush=True)
    print(f"{len(slides)} slides -> {out}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 2:
        Path(sys.argv[2]).mkdir(parents=True, exist_ok=True)
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
