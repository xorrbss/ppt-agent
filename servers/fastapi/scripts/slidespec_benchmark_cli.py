"""Command-line adapter for the SlideSpec pipeline benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable
import uuid


def benchmark_main(
    *,
    run_benchmark: Callable[..., Awaitable[dict[str, Any]]],
    summary_only: Callable[[dict[str, Any]], dict[str, Any]],
    default_presentation_id: str,
) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--presentation-id", default=default_presentation_id)
    parser.add_argument("--cold-runs", type=int, default=10)
    parser.add_argument("--warm-runs", type=int, default=10)
    parser.add_argument("--expected-slides", type=int, default=90)
    parser.add_argument(
        "--positions",
        help="Comma-separated zero-based slide positions for a bounded diagnostic replay",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Omit per-candidate run diagnostics from stdout/output JSON",
    )
    args = parser.parse_args()
    if args.cold_runs < 0 or args.warm_runs < 0 or args.cold_runs + args.warm_runs < 1:
        parser.error("run counts must be non-negative with at least one replay")
    positions = (
        tuple(int(value.strip()) for value in args.positions.split(",") if value.strip())
        if args.positions
        else None
    )
    result = asyncio.run(
        run_benchmark(
            presentation_id=uuid.UUID(args.presentation_id),
            cold_runs=args.cold_runs,
            warm_runs=args.warm_runs,
            expected_slides=args.expected_slides,
            positions=positions,
        )
    )
    payload = summary_only(result) if args.summary_only else result
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
