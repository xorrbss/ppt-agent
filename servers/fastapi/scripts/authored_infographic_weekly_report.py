"""Weekly per-archetype outcome report for the authored infographic loop.

Two evidence sources, joined on the archetype:

* the per-slide quality receipts under ``<app data>/authored-quality-receipts``
  (``deck-*-slide-quality.json``), which say what the plan chose and how the
  export scored it -- archetype, density, chosen variant, critic score and
  raster-fallback count;
* the ``authored_slide_editor_events`` table, which says what humans then did
  to those slides -- regenerated, switched archetype, rewrote, deleted.

It is read-only: it never writes a receipt, a row, or a file unless ``--json``
names an output path outside the app-data tree.

Run it (Windows, this fork -- never ``uv run``, which re-syncs the shared venv
and collides with a running backend):

    cd servers/fastapi
    APP_DATA_DIRECTORY=<app data> \\
      .venv/Scripts/python.exe scripts/authored_infographic_weekly_report.py \\
      --database-url "sqlite+aiosqlite:///<app data>/presenton.db" --days 7

``--receipts-dir`` overrides receipt discovery; ``--database-url`` defaults to
the backend's own resolution, and ``--no-database`` reports receipts alone.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

EDIT_EVENT_TYPES = (
    "slide_regenerated",
    "archetype_switched",
    "bulk_text_edit",
    "slide_deleted",
    "export_completed",
)
#: Events that mean a human was not satisfied with the figure as generated.
CORRECTIVE_EVENT_TYPES = (
    "slide_regenerated",
    "archetype_switched",
    "bulk_text_edit",
    "slide_deleted",
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--receipts-dir",
        default=None,
        help="Directory of slide-quality receipts (default: the app-data root).",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Async SQLAlchemy URL for the editor-event table.",
    )
    parser.add_argument(
        "--no-database",
        action="store_true",
        help="Report receipts only; do not open the editor-event table.",
    )
    parser.add_argument(
        "--days", type=int, default=7, help="Window in days (0 = all history)."
    )
    parser.add_argument("--json", default=None, help="Also write the report as JSON.")
    return parser.parse_args(argv)


def resolve_receipts_dir(configured: str | None) -> Path:
    if configured:
        return Path(configured).resolve()
    override = (os.getenv("AUTHORED_QUALITY_RECEIPT_DIRECTORY") or "").strip()
    if override:
        return Path(override).resolve()
    from utils.get_env import get_app_data_directory_env

    app_data = (get_app_data_directory_env() or "").strip()
    if not app_data:
        raise SystemExit("APP_DATA_DIRECTORY is required (or pass --receipts-dir)")
    return (Path(app_data) / "authored-quality-receipts").resolve()


def load_receipt_slides(receipts_dir: Path, *, since: float | None) -> list[dict]:
    """Return every per-slide record in the window, newest file wins per deck."""

    slides: list[dict] = []
    if not receipts_dir.is_dir():
        return slides
    for path in sorted(receipts_dir.glob("deck-*-slide-quality.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        updated_at = payload.get("updatedAt")
        if since is not None and isinstance(updated_at, int) and updated_at < since:
            continue
        entries = payload.get("slides")
        if not isinstance(entries, dict):
            continue
        for record in entries.values():
            if isinstance(record, dict):
                slides.append({**record, "presentationId": payload.get("presentationId")})
    return slides


def new_bucket() -> dict[str, Any]:
    return {
        "slides": 0,
        "decks": set(),
        "densities": defaultdict(int),
        "variants": defaultdict(int),
        "criticScoreSum": 0.0,
        "criticScoreN": 0,
        "fallbackSum": 0,
        "fallbackN": 0,
        "slidesOverFallbackBudget": 0,
        "events": defaultdict(int),
    }


#: Hybrid export budget from the programme brief: more than two raster
#: fallbacks on one slide is a quality failure, not a rounding error.
FALLBACK_BUDGET_PER_SLIDE = 2


def aggregate(slides: list[dict], events: list[tuple]) -> dict[str, dict]:
    buckets: dict[str, dict[str, Any]] = defaultdict(new_bucket)
    for record in slides:
        archetype = str(record.get("archetype") or "none")
        bucket = buckets[archetype]
        bucket["slides"] += 1
        if record.get("presentationId"):
            bucket["decks"].add(record["presentationId"])
        if record.get("density"):
            bucket["densities"][str(record["density"])] += 1
        if record.get("variantChosen"):
            bucket["variants"][str(record["variantChosen"])] += 1
        score = record.get("criticScore")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            bucket["criticScoreSum"] += float(score)
            bucket["criticScoreN"] += 1
        fallback = record.get("fallbackCount")
        if isinstance(fallback, int) and not isinstance(fallback, bool):
            bucket["fallbackSum"] += fallback
            bucket["fallbackN"] += 1
            if fallback > FALLBACK_BUDGET_PER_SLIDE:
                bucket["slidesOverFallbackBudget"] += 1
    for archetype, event_type, count in events:
        buckets[str(archetype or "none")]["events"][event_type] += count
    return buckets


def summarize(buckets: dict[str, dict]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for archetype in sorted(buckets):
        bucket = buckets[archetype]
        slide_count = bucket["slides"]
        corrective = sum(
            bucket["events"].get(name, 0) for name in CORRECTIVE_EVENT_TYPES
        )
        rows.append(
            {
                "archetype": archetype,
                "slides": slide_count,
                "decks": len(bucket["decks"]),
                "topDensity": _top(bucket["densities"]),
                "topVariant": _top(bucket["variants"]),
                "criticScore": (
                    round(bucket["criticScoreSum"] / bucket["criticScoreN"], 2)
                    if bucket["criticScoreN"]
                    else None
                ),
                "criticScoreMeasured": bucket["criticScoreN"],
                "fallbacksPerSlide": (
                    round(bucket["fallbackSum"] / bucket["fallbackN"], 2)
                    if bucket["fallbackN"]
                    else None
                ),
                "fallbackMeasured": bucket["fallbackN"],
                "slidesOverFallbackBudget": bucket["slidesOverFallbackBudget"],
                "events": {name: bucket["events"].get(name, 0) for name in EDIT_EVENT_TYPES},
                "correctiveEvents": corrective,
                "editRate": (
                    round(corrective / slide_count, 3) if slide_count else None
                ),
                "regenerationRate": (
                    round(bucket["events"].get("slide_regenerated", 0) / slide_count, 3)
                    if slide_count
                    else None
                ),
            }
        )
    return rows


def _top(counter: dict[str, int]) -> str | None:
    if not counter:
        return None
    name, count = max(counter.items(), key=lambda item: (item[1], item[0]))
    return f"{name}({count})"


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)


def print_table(headers: list[str], rows: list[list[Any]]) -> None:
    cells = [[_fmt(value) for value in row] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in cells)) if cells else len(header)
        for index, header in enumerate(headers)
    ]
    def line(values: list[str]) -> str:
        return " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        )

    print(line(headers))
    print("-+-".join("-" * width for width in widths))
    for row in cells:
        print(line(row))


async def load_events(database_url: str | None, since: datetime | None) -> list[tuple]:
    from services.authored_slide_editor_event_service import (
        count_editor_events_by_archetype,
    )

    if database_url:
        os.environ["DATABASE_URL"] = database_url
    from sqlmodel.ext.asyncio.session import AsyncSession

    from services.database import sql_engine

    async with AsyncSession(sql_engine) as session:
        return list(await count_editor_events_by_archetype(session, since=since))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    window_start = (
        datetime.now(timezone.utc) - timedelta(days=args.days) if args.days else None
    )
    receipts_dir = resolve_receipts_dir(args.receipts_dir)
    slides = load_receipt_slides(
        receipts_dir, since=window_start.timestamp() if window_start else None
    )

    events: list[tuple] = []
    if not args.no_database:
        try:
            events = asyncio.run(load_events(args.database_url, window_start))
        except Exception as error:  # pragma: no cover - operator-facing path
            print(f"editor events unavailable: {error}", file=sys.stderr)

    rows = summarize(aggregate(slides, events))
    window = "all history" if not args.days else f"last {args.days} day(s)"
    print(f"Receipts: {receipts_dir}")
    print(f"Window: {window} | slide records: {len(slides)} | event groups: {len(events)}")
    print()
    print_table(
        [
            "archetype",
            "slides",
            "decks",
            "density",
            "variant",
            "critic",
            "fallback/slide",
            ">budget",
            "regen",
            "edit rate",
        ],
        [
            [
                row["archetype"],
                row["slides"],
                row["decks"],
                row["topDensity"],
                row["topVariant"],
                row["criticScore"],
                row["fallbacksPerSlide"],
                row["slidesOverFallbackBudget"],
                row["events"]["slide_regenerated"],
                row["editRate"],
            ]
            for row in rows
        ],
    )
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"receiptsDir": str(receipts_dir), "window": window, "archetypes": rows},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
