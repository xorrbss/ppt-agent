from __future__ import annotations

import asyncio
import logging
import os
import signal

from migrations import migrate_database_on_startup
from services.database import create_db_and_tables, dispose_engines
from services.template_v2_pptx_ingestion_service import (
    start_template_v2_pptx_dispatcher,
    stop_template_v2_pptx_dispatcher,
)
from templates.v2.policy import get_structured_template_policy
from utils.get_env import get_app_data_directory_env


logger = logging.getLogger(__name__)
EMBEDDED_WORKER_MODE = "embedded"
EXTERNAL_WORKER_MODE = "external"


def get_template_v2_pptx_worker_mode() -> str:
    value = os.getenv("TEMPLATE_V2_PPTX_WORKER_MODE", EMBEDDED_WORKER_MODE)
    normalized = value.strip().lower()
    if normalized in {EMBEDDED_WORKER_MODE, EXTERNAL_WORKER_MODE}:
        return normalized
    return "invalid"


def should_start_embedded_worker() -> bool:
    return get_template_v2_pptx_worker_mode() == EMBEDDED_WORKER_MODE


async def run_external_worker(stop: asyncio.Event | None = None) -> None:
    if get_template_v2_pptx_worker_mode() != EXTERNAL_WORKER_MODE:
        raise RuntimeError("template_v2_external_worker_mode_required")
    policy = get_structured_template_policy()
    if not policy.creation_enabled or not policy.allowed_template_ids:
        raise RuntimeError("template_v2_canary_policy_not_ready")
    os.makedirs(get_app_data_directory_env(), exist_ok=True)
    await migrate_database_on_startup()
    await create_db_and_tables()
    stop = stop or asyncio.Event()
    try:
        await start_template_v2_pptx_dispatcher()
        logger.info("Template V2 PPTX external worker started")
        await stop.wait()
    finally:
        await stop_template_v2_pptx_dispatcher()
        await dispose_engines()


def main() -> None:
    logging.basicConfig(
        level=getattr(
            logging,
            (os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            logging.INFO,
        )
    )

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_stop(*_: object) -> None:
            loop.call_soon_threadsafe(stop.set)

        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, request_stop)
            except NotImplementedError:
                signal.signal(signum, request_stop)
        await run_external_worker(stop)

    asyncio.run(serve())


if __name__ == "__main__":
    main()
