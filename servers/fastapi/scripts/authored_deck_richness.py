"""Objective, repeatable slide-richness measurement for authored decks.

Reads stored slide HTML from up to three sources in one invocation, renders
each slide exactly once in the real headless Chromium
(``utils.slide_capture.render_html_to_png_with_metrics``), and emits
per-slide and per-deck richness metrics so "this deck looks richer than that
one" stops being an opinion. See ``authored_deck_richness_html.py`` for the
exact chart/table/css-rule detection rules and
``authored_deck_richness_geometry.py`` for the ink-fill/dead-space/defect
rules; this module is orchestration (CLI, source dispatch, output) only.

Sources (repeatable, may be combined in one run):

* ``--database-url <async SQLAlchemy URL>`` + ``--presentation-id <id>``
  (repeatable, short prefix ok) or ``--since <iso8601>``: published decks'
  ``slides.html_content``. PostgreSQL and SQLite both supported.
* ``--checkpoint-database-url`` (defaults to ``--database-url``) +
  ``--checkpoint-job-id <id>`` (repeatable, short prefix ok): decks that
  never published, from ``authored_slide_checkpoints``. A separate URL flag
  exists because the checkpoint source is commonly a different database
  (e.g. a local SQLite bench db) than the published-presentation source
  (e.g. production PostgreSQL) -- see ``authored_deck_richness_sources.py``.
* ``--html-dir <dir>`` (repeatable): a directory of ``*.html`` files, one
  slide per file, treated as one deck.

Never writes to any database; never starts or stops a server -- rendering
spawns its own disposable Chromium subprocess per slide via the existing
``utils.slide_capture`` machinery.

Example (Windows, this fork -- never ``uv run`` against a live venv):

    cd servers/fastapi
    .venv/Scripts/python.exe scripts/authored_deck_richness.py \\
      --database-url "postgresql+asyncpg://user:pass@127.0.0.1:5432/postgres" \\
      --presentation-id 2c89713d --presentation-id e5c9cfb0 \\
      --checkpoint-database-url "sqlite+aiosqlite:///C:/bench/bench.db" \\
      --checkpoint-job-id bb82b56f --checkpoint-job-id 715406b1 \\
      --out artifacts/deck-richness
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime
from pathlib import Path

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from scripts.authored_deck_richness_geometry import compute_geometry  # noqa: E402
from scripts.authored_deck_richness_html import parse_structural_metrics  # noqa: E402
from scripts.authored_deck_richness_render import render_slide_metrics  # noqa: E402
from scripts.authored_deck_richness_report import (  # noqa: E402
    DeckReport,
    SlideMetrics,
    build_deck_reports,
    render_summary_md,
    write_deck_json,
    write_slides_jsonl,
    write_summary_md,
)
from scripts.authored_deck_richness_sources import (  # noqa: E402
    SourceSlide,
    load_checkpoint_slides,
    load_database_slides,
    load_html_dir_slides,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--presentation-id", action="append", default=[])
    parser.add_argument("--since", default=None, help="ISO 8601 timestamp.")
    parser.add_argument("--checkpoint-database-url", default=None)
    parser.add_argument("--checkpoint-job-id", action="append", default=[])
    parser.add_argument("--html-dir", action="append", default=[])
    parser.add_argument(
        "--owner-id", default=None, help="Postgres RLS scoping; leave unset to see everything."
    )
    parser.add_argument("--tenant-id", default=None, help="Postgres RLS tenant; defaults to 'default'.")
    parser.add_argument(
        "--limit", type=int, default=None, help="Cap slides read per deck (first N by index)."
    )
    parser.add_argument("--render-timeout", type=int, default=60)
    parser.add_argument("--render-concurrency", type=int, default=4)
    parser.add_argument("--out", required=True)
    return parser.parse_args(argv)


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


async def _gather_source_slides(args: argparse.Namespace) -> list[SourceSlide]:
    slides: list[SourceSlide] = []
    for directory in args.html_dir:
        slides.extend(load_html_dir_slides(Path(directory), limit=args.limit))

    owner_id = uuid.UUID(args.owner_id) if args.owner_id else None
    if args.database_url and (args.presentation_id or args.since):
        slides.extend(
            await load_database_slides(
                database_url=args.database_url,
                presentation_ids=args.presentation_id,
                since=_parse_since(args.since),
                owner_id=owner_id,
                tenant_id=args.tenant_id,
                limit=args.limit,
            )
        )
    if args.checkpoint_job_id:
        checkpoint_url = args.checkpoint_database_url or args.database_url
        if not checkpoint_url:
            raise SystemExit(
                "--checkpoint-job-id requires --checkpoint-database-url or --database-url"
            )
        slides.extend(
            await load_checkpoint_slides(
                database_url=checkpoint_url,
                job_ids=args.checkpoint_job_id,
                owner_id=owner_id,
                tenant_id=args.tenant_id,
                limit=args.limit,
            )
        )
    return slides


async def measure_slide(
    source: SourceSlide, *, timeout_s: int, semaphore: asyncio.Semaphore
) -> SlideMetrics:
    structural = parse_structural_metrics(source.html)
    async with semaphore:
        outcome = await render_slide_metrics(source.html, timeout_s=timeout_s)
    geometry = compute_geometry(source.html, outcome.browser_metrics)

    block_count = structural.distinct_block_count
    nesting_depth = structural.max_nesting_depth
    structure_source = "html_fallback"
    if geometry.block_count_from_metrics is not None:
        block_count = geometry.block_count_from_metrics
        nesting_depth = geometry.nesting_depth_from_metrics or 0
        structure_source = "browser_metrics"

    return SlideMetrics(
        deck_id=source.deck_id,
        deck_label=source.deck_label,
        source=source.source,
        slide_index=source.slide_index,
        html_bytes=structural.html_bytes,
        uses_library=structural.uses_library,
        image_count=structural.image_count,
        inline_image_bytes=structural.inline_image_bytes,
        has_chart=structural.has_chart,
        chart_evidence=structural.chart_evidence,
        has_table=structural.has_table,
        css_rule_count=structural.css_rule_count,
        distinct_block_count=block_count,
        max_nesting_depth=nesting_depth,
        structure_source=structure_source,
        ink_fill=geometry.ink_fill,
        bottom_dead_band=geometry.bottom_dead_band,
        geometry_defects=geometry.geometry_defects,
        telemetry_status=geometry.telemetry_status,
        render_error=outcome.error,
    )


async def measure_all(
    sources: list[SourceSlide], *, timeout_s: int, concurrency: int
) -> list[SlideMetrics]:
    semaphore = asyncio.Semaphore(max(1, concurrency))
    tasks = [measure_slide(source, timeout_s=timeout_s, semaphore=semaphore) for source in sources]
    return list(await asyncio.gather(*tasks))


def _print_progress(decks: list[DeckReport]) -> None:
    for deck in decks:
        m = deck.metrics
        print(
            f"{deck.deck_label} [{deck.source}]: {deck.slide_count} slides, "
            f"chart={m.get('share_has_chart')}, ink_fill={m.get('mean_ink_fill')}, "
            f"defects={m.get('total_geometry_defects')}"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not (args.html_dir or args.database_url or args.checkpoint_database_url):
        raise SystemExit("at least one of --html-dir/--database-url/--checkpoint-database-url is required")

    sources = asyncio.run(_gather_source_slides(args))
    if not sources:
        raise SystemExit("no slides matched any requested source")

    all_slides = asyncio.run(
        measure_all(
            sources,
            timeout_s=args.render_timeout,
            concurrency=args.render_concurrency,
        )
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    decks = build_deck_reports(all_slides)
    write_slides_jsonl(out_dir / "slides.jsonl", all_slides)
    write_deck_json(out_dir / "deck.json", decks)
    write_summary_md(out_dir / "summary.md", decks)

    print(render_summary_md(decks))
    _print_progress(decks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
