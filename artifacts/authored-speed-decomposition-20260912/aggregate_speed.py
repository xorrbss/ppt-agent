"""Per-stage speed decomposition of authored generation, from stored evidence only.

Two sources, both durable and both read-only:

1. ``provider_usage_receipts`` -- one row per logical provider call, with
   ``stage``, ``latency_ms``, token counts and ``retry_class``. It reaches back
   to 2026-08-18, which makes it the only source that spans the August baseline
   and today.
2. ``presentation_generation_jobs`` -- ``last_started_at`` / ``published_at``
   give each deck's wall clock, ``total_slides`` its size.

The worker log's ``[authored] done`` line carries the full stage split
(author thread-seconds, render, pipeline, persist, export) but the oldest
surviving log is 2026-09-04, so it can decompose today and not August. That
asymmetry is reported rather than papered over.

``DATABASE_URL`` is read from the process environment, which the sanctioned
wrapper populates in memory; it is never printed. Every statement is read-only
and the connection is opened read-only, because a live series is writing to the
same database.

    python aggregate_speed.py <out.json>
"""

from __future__ import annotations

import json
import os
import re
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

#: Everything on or after this instant is "today"; before it is the baseline.
AUGUST_END = datetime(2026, 9, 1, tzinfo=timezone.utc)
#: The stages the authored pipeline reports on a provider call.
STAGE_ORDER = ("outline", "header", "author", "repair", "vision", "persist", "export")


def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or ""
    if not url:
        raise SystemExit("DATABASE_URL is not in the environment; run through the wrapper")
    return re.sub(r"^postgresql\+\w+://", "postgresql://", url)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def _summarise(latencies: list[float]) -> dict[str, Any]:
    return {
        "calls": len(latencies),
        "seconds_total": round(sum(latencies) / 1000.0, 1),
        "p50_s": round((_percentile(latencies, 0.5) or 0) / 1000.0, 1),
        "p90_s": round((_percentile(latencies, 0.9) or 0) / 1000.0, 1),
    }


def collect(connection: psycopg.Connection) -> dict[str, Any]:
    connection.execute("set statement_timeout='300s'")

    jobs: dict[str, dict[str, Any]] = {}
    for job_id, created, started, published, state, slides in connection.execute(
        "select id, created_at, last_started_at, published_at, state, total_slides"
        " from presentation_generation_jobs order by created_at"
    ).fetchall():
        jobs[str(job_id)] = {
            "created_at": created,
            "started_at": started,
            "published_at": published,
            "state": state,
            "slides": int(slides or 0),
        }

    per_job: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"stages": defaultdict(list), "tokens": defaultdict(lambda: [0, 0, 0]),
                 "retries": defaultdict(int), "failed": defaultdict(int), "models": set()}
    )
    for job_id, stage, model, retry_class, outcome, latency, tokens_in, tokens_out, cached in connection.execute(
        "select job_id, stage, model, retry_class, outcome, latency_ms,"
        " input_tokens, output_tokens, cached_tokens"
        " from provider_usage_receipts where operation = 'authored_generation'"
    ).fetchall():
        row = per_job[str(job_id)]
        name = str(stage or "?")
        if isinstance(latency, (int, float)):
            row["stages"][name].append(float(latency))
        totals = row["tokens"][name]
        totals[0] += int(tokens_in or 0)
        totals[1] += int(tokens_out or 0)
        totals[2] += int(cached or 0)
        row["retries"][name] += 1 if (retry_class or "initial") != "initial" else 0
        row["failed"][name] += 1 if outcome != "succeeded" else 0
        if model:
            row["models"].add(str(model))

    decks: list[dict[str, Any]] = []
    for job_id, usage in per_job.items():
        job = jobs.get(job_id)
        if not job or not job["slides"]:
            continue
        started, published = job["started_at"] or job["created_at"], job["published_at"]
        wall = (published - started).total_seconds() if (published and started) else None
        author = usage["stages"].get("author") or []
        decks.append({
            "job": job_id,
            "created_at": job["created_at"].isoformat(),
            "period": "august" if job["created_at"] < AUGUST_END else "september",
            "state": job["state"],
            "slides": job["slides"],
            "wall_s": round(wall, 1) if wall else None,
            "wall_per_slide_s": round(wall / job["slides"], 1) if wall else None,
            "models": sorted(usage["models"]),
            "stages": {
                name: {
                    **_summarise(values),
                    "input_tokens": usage["tokens"][name][0],
                    "output_tokens": usage["tokens"][name][1],
                    "cached_tokens": usage["tokens"][name][2],
                    "retries": usage["retries"][name],
                    "failed": usage["failed"][name],
                }
                for name, values in sorted(usage["stages"].items())
            },
            # Thread-seconds of model time per slide, and how much of the deck's
            # wall clock that time actually occupied.
            "author_thread_s_per_slide": round(sum(author) / 1000.0 / job["slides"], 1) if author else None,
            "author_calls_per_slide": round(len(author) / job["slides"], 2) if author else None,
            "author_p50_s": round((_percentile(author, 0.5) or 0) / 1000.0, 1) if author else None,
            "effective_concurrency": (
                round(sum(author) / 1000.0 / wall, 1) if author and wall and wall > 0 else None
            ),
        })

    decks.sort(key=lambda item: item["created_at"])
    return {"decks": decks, "jobs_seen": len(jobs), "jobs_with_usage": len(per_job)}


def periods(decks: list[dict[str, Any]]) -> dict[str, Any]:
    """Published decks only, so a failed attempt's partial wall cannot skew a median."""

    def median_of(rows: list[dict[str, Any]], key: str) -> float | None:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return round(statistics.median(values), 1) if values else None

    def stage_table(rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Median thread-seconds per slide for each stage that recorded any."""
        names = sorted({name for r in rows for name in r["stages"]})
        table: dict[str, Any] = {}
        for name in names:
            per_slide = [
                r["stages"][name]["seconds_total"] / r["slides"]
                for r in rows if name in r["stages"] and r["slides"]
            ]
            calls = [r["stages"][name]["calls"] / r["slides"] for r in rows if name in r["stages"] and r["slides"]]
            table[name] = {
                "decks_with_stage": len(per_slide),
                "median_thread_s_per_slide": round(statistics.median(per_slide), 2) if per_slide else None,
                "median_calls_per_slide": round(statistics.median(calls), 2) if calls else None,
            }
        return table

    out: dict[str, Any] = {}
    for period in ("august", "september"):
        for model_filter, label in ((None, ""), ("gpt-5.6-sol", "_sol_only")):
            rows = [
                d for d in decks
                if d["period"] == period and d["wall_per_slide_s"] and d["state"] == "published"
                and (model_filter is None or model_filter in d["models"])
            ]
            if not rows:
                out[period + label] = {"decks": 0}
                continue
            author_in = [
                d["stages"].get("author", {}).get("input_tokens", 0)
                / max(1, d["stages"].get("author", {}).get("calls", 1))
                for d in rows
            ]
            out[period + label] = {
                "decks": len(rows),
                "slides_total": sum(d["slides"] for d in rows),
                "median_slides": median_of(rows, "slides"),
                "median_wall_per_slide_s": median_of(rows, "wall_per_slide_s"),
                "median_author_thread_s_per_slide": median_of(rows, "author_thread_s_per_slide"),
                "median_author_p50_call_s": median_of(rows, "author_p50_s"),
                "median_author_calls_per_slide": median_of(rows, "author_calls_per_slide"),
                "median_effective_concurrency": median_of(rows, "effective_concurrency"),
                "median_input_tokens_per_author_call": round(statistics.median(author_in)) if author_in else None,
                "models": sorted({m for d in rows for m in d["models"]}),
                "stages_thread_s_per_slide": stage_table(rows),
            }
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    with psycopg.connect(
        database_url(), connect_timeout=15, options="-c default_transaction_read_only=on"
    ) as connection:
        data = collect(connection)
    data["periods"] = periods(data["decks"])
    Path(argv[0]).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(data["periods"], ensure_ascii=False, indent=1))
    print(f"decks with usage rows: {data['jobs_with_usage']} of {data['jobs_seen']} jobs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
