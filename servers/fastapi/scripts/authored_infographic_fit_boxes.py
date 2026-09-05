"""The ``boxes`` half of the fit sweep: what box real slides gave their figures.

Split out of ``authored_infographic_fit_sweep`` to keep both files inside the
repository's source-size policy. The sweep matrix and this survey share nothing
but the root-QID pattern, so the seam costs one import.
"""

from __future__ import annotations

import collections
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT_QID_RE = re.compile(r"^ig\.s\d+\.root$")
_ROOT_CLASS_RE = re.compile(r'class="ig ig-([\w-]+) (n\d)"')


def _figure_slides_by_job(db_path: Path) -> dict[str, list[tuple[int, str]]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    query = (
        "select job_id, slide_index, illustrated_html, author_html "
        "from authored_slide_checkpoints order by job_id, slide_index"
    )
    by_job: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    for job, index, illustrated, authored in con.execute(query):
        html = illustrated or authored or ""
        if 'data-qid="ig.' in html:
            by_job[job].append((index, html))
    return by_job


async def run_boxes(sample: Sequence[tuple[str, int, str]]) -> list[dict[str, Any]]:
    """The ``ig.*.root`` box every sampled real slide gave its figure."""
    from utils import slide_capture as sc

    records: list[dict[str, Any]] = []
    for job, index, html in sample:
        capture = await sc.render_html_to_png_with_metrics(html, timeout=60)
        elements = (capture.browser_metrics or {}).get("elements") or []
        root = next((e for e in elements if ROOT_QID_RE.match(str(e.get("qid") or ""))), None)
        if root is None:
            continue
        match = _ROOT_CLASS_RE.search(html)
        records.append(
            {
                "job": job,
                "slide": index,
                "archetype": match.group(1) if match else "?",
                "size_class": match.group(2) if match else "?",
                "width": round(root["bbox"]["width"]),
                "height": round(root["bbox"]["height"]),
            }
        )
        print(records[-1], flush=True)
    return records


def report_distribution(records: Sequence[Mapping[str, Any]]) -> None:
    for name in ("width", "height"):
        values = sorted(int(record[name]) for record in records)
        if values:
            quarter, half = len(values) // 4, len(values) // 2
            print(f"{name:6} n={len(values)} min={values[0]} p25={values[quarter]} "
                  f"med={values[half]} max={values[-1]}")



def sample_slides(db_path: Path, per_job: int) -> list[tuple[str, int, str]]:
    """``per_job`` figure slides spread across each job in the checkpoint DB."""
    return [
        (job, index, html)
        for job, items in _figure_slides_by_job(db_path).items()
        for index, html in items[:: max(1, len(items) // per_job)][:per_job]
    ]


__all__ = ["report_distribution", "run_boxes", "sample_slides"]
