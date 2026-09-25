"""Read-only export of ``provider_usage_receipts`` for one generation job.

Built for the authored verbatim placeholder-authoring live validation series
(``docs/authored-verbatim-placeholder-authoring-design-20260911.md`` section 4;
harness under ``artifacts/authored-live-series-20260915/``, see that
directory's ``RUNBOOK.md``). Never invoke this script directly with a raw
``DATABASE_URL`` typed on the command line or in a checked-in file -- run it
through ``run_authored_live_series_provider_usage.ps1``, which decrypts the
local stack's environment in memory only and never prints it, mirroring
``run_authored_checkpoint_repair_probe.ps1``.

``provider_usage_receipts`` (``models/sql/provider_usage_receipt.py``) is
deliberately an accounting table with no prompt, request id, URL or exception
text -- only scalars (provider, model, stage, retry_class, outcome, latency,
token counts, cost). This script performs exactly one read-only, read-only-
transaction SELECT by ``job_id`` and writes the rows plus a small aggregate to
a JSON file. It writes nothing to the database and prints no row content to
the console (only a one-line count on success).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FASTAPI_ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, type=uuid.UUID, help="presentation_generation_jobs.id")
    parser.add_argument("--out", required=True, type=Path, help="Output JSON path")
    return parser


if __name__ == "__main__" and any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
    build_parser().parse_args()


def require_database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        raise RuntimeError("DATABASE_URL must start with postgresql; refusing SQLite")
    return url


# Only the accounting columns this script is documented to read -- no prompt,
# request id, URL, or exception text exists on this table to accidentally
# select (see models/sql/provider_usage_receipt.py's own module docstring).
_COLUMNS = (
    "id", "operation", "stage", "provider", "model", "retry_class", "outcome",
    "latency_ms", "usage_state", "input_tokens", "output_tokens", "cached_tokens",
    "request_count", "reported_cost_microunits", "estimated_cost_microunits",
    "pricing_version", "created_at",
)


async def fetch_rows(job_id: uuid.UUID) -> list[dict[str, object]]:
    engine = create_async_engine(require_database_url())
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("SET TRANSACTION READ ONLY"))
            result = await session.execute(
                text(
                    f"SELECT {', '.join(_COLUMNS)} FROM provider_usage_receipts "
                    "WHERE job_id = :job_id ORDER BY created_at"
                ),
                {"job_id": job_id},
            )
            return [dict(row) for row in result.mappings().all()]
    finally:
        await engine.dispose()


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    def sum_or_none(field: str) -> int | None:
        values = [row[field] for row in rows if row.get(field) is not None]
        return sum(values) if values else None

    retry_class_counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("retry_class") or "unknown")
        retry_class_counts[key] = retry_class_counts.get(key, 0) + 1

    return {
        "receiptCount": len(rows),
        "requestCountTotal": sum(int(row.get("request_count") or 0) for row in rows),
        "inputTokensTotal": sum_or_none("input_tokens"),
        "outputTokensTotal": sum_or_none("output_tokens"),
        "cachedTokensTotal": sum_or_none("cached_tokens"),
        "reportedCostMicrounitsTotal": sum_or_none("reported_cost_microunits"),
        "estimatedCostMicrounitsTotal": sum_or_none("estimated_cost_microunits"),
        "retryClassCounts": retry_class_counts,
        "outcomeCounts": {
            key: sum(1 for row in rows if row.get("outcome") == key)
            for key in sorted({str(row.get("outcome")) for row in rows})
        },
    }


async def run(args: argparse.Namespace) -> None:
    rows = await fetch_rows(args.job)
    payload = {
        "schemaVersion": "authored-live-series-provider-usage/v1",
        "jobId": str(args.job),
        "rows": rows,
        "summary": summarize(rows),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(f"WROTE {args.out} rows={len(rows)}")


def main() -> None:
    args = build_parser().parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()

