"""Standalone process boundary for durable workspace operations.

The API keeps the legacy embedded runner as the local-development default.
Managed deployments can set ``WORKSPACE_OPERATION_WORKER_MODE=external`` so
that API lifespans only admit commands while this process owns operation
claims, heartbeats, recovery, and execution.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from migrations import migrate_database_on_startup
from services.database import create_db_and_tables, dispose_engines, sql_engine
from services.presentation_deletion_operation_handler import (
    register_presentation_deletion_operation_handler,
)
from services.presentation_generation_operation_handler import (
    register_presentation_generation_operation_handler,
)
from services.runtime_settings_coordinator import (
    start_runtime_settings_coordinator,
    stop_runtime_settings_coordinator,
)
from services.workspace_asset_service import (
    require_workspace_asset_malware_scan_safety,
)
from services.workspace_asset_maintenance import (
    start_workspace_asset_maintenance,
    stop_workspace_asset_maintenance,
)
from services.workspace_cleanup_journal_service import (
    start_workspace_cleanup_maintenance,
    stop_workspace_cleanup_maintenance,
)
from services.workspace_event_retention import (
    start_workspace_event_retention,
    stop_workspace_event_retention,
)
from services.workspace_chat_operation_handler import (
    register_presentation_chat_operation_handlers,
)
from services.workspace_operation_runner import (
    start_workspace_operation_runner,
    stop_workspace_operation_runner,
)
from services.workspace_metrics_exporter import (
    start_workspace_worker_metrics_exporter,
)
from services.workspace_quality_export_operation_handlers import (
    register_workspace_quality_export_operation_handlers,
)
from services.workspace_page_remake_operation_handler import (
    register_workspace_page_remake_operation_handler,
)
from services.workspace_batch_remake_operation_handler import (
    register_workspace_batch_remake_operation_handler,
)
from services.workspace_authored_preview_operation_handlers import (
    register_workspace_authored_preview_operation_handlers,
)
from services.workspace_authored_template_operation_handler import (
    register_workspace_authored_template_operation_handler,
)
from services.workspace_runtime_profile import require_supported_workspace_runtime
from utils.get_env import get_app_data_directory_env


logger = logging.getLogger(__name__)
EMBEDDED_WORKER_MODE = "embedded"
EXTERNAL_WORKER_MODE = "external"
_WORKER_MODE_ENV = "WORKSPACE_OPERATION_WORKER_MODE"


def get_workspace_operation_worker_mode() -> str:
    value = os.getenv(_WORKER_MODE_ENV, EMBEDDED_WORKER_MODE).strip().lower()
    if value not in {EMBEDDED_WORKER_MODE, EXTERNAL_WORKER_MODE}:
        raise RuntimeError("workspace_operation_worker_mode_invalid")
    return value


def should_start_embedded_workspace_operation_worker() -> bool:
    return get_workspace_operation_worker_mode() == EMBEDDED_WORKER_MODE


def register_workspace_operation_handlers() -> None:
    register_presentation_generation_operation_handler(replace=True)
    register_presentation_deletion_operation_handler(replace=True)
    register_presentation_chat_operation_handlers(replace=True)
    register_workspace_page_remake_operation_handler(replace=True)
    register_workspace_batch_remake_operation_handler(replace=True)
    register_workspace_authored_preview_operation_handlers(replace=True)
    register_workspace_quality_export_operation_handlers(replace=True)
    register_workspace_authored_template_operation_handler(replace=True)


async def run_external_worker(stop: asyncio.Event | None = None) -> None:
    if get_workspace_operation_worker_mode() != EXTERNAL_WORKER_MODE:
        raise RuntimeError("workspace_operation_external_worker_mode_required")
    require_supported_workspace_runtime(sql_engine.url)
    require_workspace_asset_malware_scan_safety(feature_enabled=True)
    os.makedirs(get_app_data_directory_env(), exist_ok=True)
    await migrate_database_on_startup()
    await create_db_and_tables()
    await start_runtime_settings_coordinator()
    coordinator_started = True
    stop = stop or asyncio.Event()
    runner_started = False
    asset_maintenance_started = False
    cleanup_maintenance_started = False
    event_retention_started = False
    metrics_exporter = None
    try:
        register_workspace_operation_handlers()
        metrics_exporter = await start_workspace_worker_metrics_exporter()
        await start_workspace_operation_runner()
        runner_started = True
        await start_workspace_cleanup_maintenance()
        cleanup_maintenance_started = True
        await start_workspace_asset_maintenance()
        asset_maintenance_started = True
        await start_workspace_event_retention()
        event_retention_started = True
        logger.info("Workspace operation external worker started")
        await stop.wait()
    finally:
        try:
            if coordinator_started:
                await stop_runtime_settings_coordinator()
        finally:
            try:
                if event_retention_started:
                    await stop_workspace_event_retention()
            finally:
                try:
                    if asset_maintenance_started:
                        await stop_workspace_asset_maintenance()
                finally:
                    try:
                        if cleanup_maintenance_started:
                            await stop_workspace_cleanup_maintenance()
                    finally:
                        try:
                            if runner_started:
                                await stop_workspace_operation_runner()
                        finally:
                            try:
                                if metrics_exporter is not None:
                                    await metrics_exporter.close()
                            finally:
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
