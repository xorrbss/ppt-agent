"""Offline fit replay over stored authored slide HTML.

M3 of ``docs/authored-fit-magnitude-measurement-design-20260904.md``. Renders
stored checkpoint (and optionally published) HTML in the real headless
Chromium, runs the pipeline's own two gates at scale 1.0, then walks the
uniform text scale ladder for every slide that misses, and reports the bucket
distribution plus the M5 decision inputs *S* and *N*.

No provider call is made and nothing is written to the database. Run it from
``servers/fastapi``::

    .venv\\Scripts\\python.exe scripts/authored_fit_replay.py \\
        --database-url sqlite+aiosqlite:///C:/tmp/bench.db \\
        --app-data C:/tmp/bench-appdata \\
        --job-id 8a138084-abae-4ee2-ab3c-73686a8f2313 \\
        --out C:/tmp/fit-replay

Exit codes: 0 replay completed, 2 no slide was replayable, 3 input error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from scripts import authored_fit_replay_gate as gate_lib  # noqa: E402
from scripts import authored_fit_replay_inputs as inputs_lib  # noqa: E402
from scripts import authored_fit_replay_parity as parity_lib  # noqa: E402
from scripts import authored_fit_replay_report as report_lib  # noqa: E402

DEFAULT_SCALES = (0.95, 0.90, 0.85, 0.80)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--app-data", required=True)
    parser.add_argument("--job-id", action="append", default=[])
    parser.add_argument("--since", default=None, help="ISO timestamp")
    parser.add_argument("--include-published", action="store_true")
    parser.add_argument("--out", required=True)
    parser.add_argument("--scales", default=",".join(f"{s:.2f}" for s in DEFAULT_SCALES))
    parser.add_argument("--limit", type=int, default=None, help="max slides to replay")
    parser.add_argument("--owner-id", default=None)
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument("--parity", action="store_true")
    return parser.parse_args(argv)


def configure_environment(args: argparse.Namespace) -> None:
    """Bind app data and the database before any product module is imported."""

    app_data = str(Path(args.app_data).resolve())
    os.environ["APP_DATA_DIRECTORY"] = app_data
    os.environ["DATABASE_URL"] = args.database_url
    os.environ.setdefault("USER_CONFIG_PATH", str(Path(app_data) / "userConfig.json"))
    os.environ.setdefault("DISABLE_IMAGE_GENERATION", "true")


def parse_scales(raw: str) -> tuple[float, ...]:
    values = tuple(float(part) for part in raw.split(",") if part.strip())
    if not values or any(not 0.0 < value <= 1.0 for value in values):
        raise inputs_lib.ReplayInputError(f"invalid --scales {raw!r}")
    return values


def _parse_since(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    value = datetime.fromisoformat(raw)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def collect_slides(
    session: Any, args: argparse.Namespace, state: gate_lib.ReplayState
) -> list[inputs_lib.SlideInput]:
    jobs = await inputs_lib.select_jobs(
        session, job_ids=args.job_id, since=_parse_since(args.since)
    )
    slides: list[inputs_lib.SlideInput] = []
    for job in jobs:
        try:
            context = await inputs_lib.build_job_context(session, job)
        except inputs_lib.ReplayInputError as error:
            state.skip(job.id.hex[:8], -1, "job_not_replayable", str(error))
            continue
        job_slides = await inputs_lib.load_checkpoint_slides(session, context)
        if args.include_published:
            job_slides.extend(await inputs_lib.load_published_slides(session, context))
        if not job_slides:
            continue
        context.code_version = (
            "current"
            if inputs_lib.is_current_code([item.html for item in job_slides])
            else "legacy"
        )
        planned = len(context.capacity_plan.slides)
        for item in job_slides:
            refs = inputs_lib.external_image_refs(item.html)
            if refs:
                state.skip(
                    context.short,
                    item.slide_index,
                    "external_image_reference",
                    ", ".join(sorted(set(refs))[:3]),
                )
            elif not 0 <= item.slide_index < planned:
                state.skip(
                    context.short,
                    item.slide_index,
                    "slide_index_outside_capacity_plan",
                    f"plan has {planned} slides",
                )
            else:
                slides.append(item)
        state.contexts.append(context)
    return slides


async def run(args: argparse.Namespace, state: gate_lib.ReplayState) -> None:
    from services.database import async_session_maker, dispose_engines
    from utils.owner_context import set_current_owner_id, set_current_tenant_id
    from utils.renderer_worker_policy import RendererPolicyError

    if args.owner_id:
        set_current_owner_id(uuid.UUID(args.owner_id))
    if args.tenant_id:
        set_current_tenant_id(args.tenant_id)
    try:
        async with async_session_maker() as session:
            slides = await collect_slides(session, args, state)
            if args.limit is not None:
                slides = slides[: args.limit]
            for slide in slides:
                try:
                    state.rows.append(await gate_lib.replay_slide(state, slide))
                except RendererPolicyError as error:
                    # Pre-illustrate ``author_html`` still carries the author's
                    # illustration placeholder attributes, which the renderer's
                    # admission policy refuses. Reproducing the illustrate stage
                    # offline would need a provider call, so the slide is listed
                    # rather than measured on an edited document.
                    state.skip(
                        slide.job.short,
                        slide.slide_index,
                        "renderer_rejected_source",
                        str(error),
                    )
            if args.parity:
                state.parity = await parity_lib.check_parity(
                    session, state.contexts, state.rows
                )
    finally:
        await dispose_engines()


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_environment(args)
    try:
        out_dir = Path(args.out).resolve()
        state = gate_lib.ReplayState(
            out_dir=out_dir, png_dir=out_dir / "png", scales=parse_scales(args.scales)
        )
        state.png_dir.mkdir(parents=True, exist_ok=True)
        asyncio.run(run(args, state))
    except inputs_lib.ReplayInputError as error:
        print(f"input error: {error}", file=sys.stderr)
        return 3
    if not state.rows:
        print("no replayable slide was found", file=sys.stderr)
        return 2
    print(report_lib.write_outputs(state))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
