"""Inspect rollback/queue health or run flag-independent source cleanup."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import os

from migrations import migrate_database_on_startup
from services.database import create_db_and_tables, dispose_engines
from services.template_v2_pptx_operations import (
    get_template_v2_operational_status,
)
from services.template_v2_pptx_retention_service import (
    cleanup_expired_private_sources,
)
from utils.get_env import get_app_data_directory_env


async def _run(mode: str) -> tuple[dict[str, object], bool]:
    os.makedirs(get_app_data_directory_env(), exist_ok=True)
    await migrate_database_on_startup()
    await create_db_and_tables()
    try:
        if mode == "cleanup":
            summary = await cleanup_expired_private_sources()
            payload = {"mode": mode, **asdict(summary)}
            return payload, summary.failed == 0
        status = await get_template_v2_operational_status()
        payload = {"mode": mode, **status.as_dict()}
        return payload, (
            status.rollback_safe if mode == "rollback" else status.healthy
        )
    finally:
        await dispose_engines()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("rollback", "health", "cleanup"),
        default="rollback",
    )
    args = parser.parse_args(argv)
    try:
        payload, ready = asyncio.run(_run(args.mode))
    except Exception:
        payload = {
            "mode": args.mode,
            "code": "template_v2_operations_check_failed",
        }
        ready = False
    print(json.dumps(payload, sort_keys=True))
    return 0 if ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
