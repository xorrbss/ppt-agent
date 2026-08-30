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
from services.artifact_store import configured_public_artifact_worker_object_port
from services.artifact_lifecycle_runtime import (
    start_public_artifact_lifecycle_runtime,
    stop_public_artifact_lifecycle_runtime,
)
from services.isolated_worker_process import install_trusted_worker_dispatcher
from services.presentation_deletion_operation_handler import (
    register_presentation_deletion_operation_handler,
)
from services.presentation_generation_operation_handler import (
    PRESENTATION_GENERATION_OPERATION_KIND,
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
    EXPORT_OPERATION_KINDS,
    register_workspace_quality_export_operation_handlers,
)
from services.workspace_page_remake_operation_handler import (
    register_workspace_page_remake_operation_handler,
)
from services.workspace_batch_remake_operation_handler import (
    register_workspace_batch_remake_operation_handler,
)
from services.workspace_operation_handler_registry import (
    workspace_operation_handler_registry,
)
from services.workspace_authored_preview_operation_handlers import (
    register_workspace_authored_preview_operation_handlers,
)
from services.workspace_authored_template_operation_handler import (
    register_workspace_authored_template_operation_handler,
)
from services.workspace_runtime_profile import require_supported_workspace_runtime
from services.public_worker_profiles import require_public_worker_profile_from_environment
from services.trusted_executor.composition import (
    TrustedWorkerExecutorRuntime,
    create_configured_trusted_worker_executor_runtime,
)
from utils.get_env import get_app_data_directory_env


logger = logging.getLogger(__name__)
EMBEDDED_WORKER_MODE = "embedded"
EXTERNAL_WORKER_MODE = "external"
SPLIT_WORKER_MODE = "split"
_WORKER_MODE_ENV = "WORKSPACE_OPERATION_WORKER_MODE"
_EXTERNAL_CONTROLLER_ENV = "WORKSPACE_OPERATION_EXTERNAL_CONTROLLER"
EXPORT_CONTROLLER = "export"


def get_workspace_operation_worker_mode() -> str:
    value = os.getenv(_WORKER_MODE_ENV, EMBEDDED_WORKER_MODE).strip().lower()
    if value not in {
        EMBEDDED_WORKER_MODE,
        EXTERNAL_WORKER_MODE,
        SPLIT_WORKER_MODE,
    }:
        raise RuntimeError("workspace_operation_worker_mode_invalid")
    return value


def should_start_embedded_workspace_operation_worker() -> bool:
    return get_workspace_operation_worker_mode() in {
        EMBEDDED_WORKER_MODE,
        SPLIT_WORKER_MODE,
    }


def embedded_workspace_operation_kinds() -> tuple[str, ...] | None:
    """Return the API runner claim profile for embedded or split operation control."""

    mode = get_workspace_operation_worker_mode()
    if mode == EMBEDDED_WORKER_MODE:
        return None
    if mode == EXTERNAL_WORKER_MODE:
        return ()
    return tuple(
        kind
        for kind in workspace_operation_handler_registry.kinds
        if kind != PRESENTATION_GENERATION_OPERATION_KIND
    )


def external_workspace_operation_kinds(profile_role: str) -> tuple[str, ...]:
    """Resolve one explicit DB-claim controller profile.

    ``ppt-core`` here is the metadata-authority controller, never the
    sessionless converter child.  The latter remains ``PUBLIC_WORKER_ROLE=export``
    with no canonical DB credentials.
    """

    controller = os.getenv(_EXTERNAL_CONTROLLER_ENV, "").strip().lower()
    if profile_role == "generation":
        if controller not in {"", "generation"}:
            raise RuntimeError("workspace_operation_external_controller_invalid")
        return (PRESENTATION_GENERATION_OPERATION_KIND,)
    if profile_role == "ppt-core" and controller == EXPORT_CONTROLLER:
        return EXPORT_OPERATION_KINDS
    raise RuntimeError("workspace_operation_external_controller_required")


def create_external_export_controller_runtime() -> TrustedWorkerExecutorRuntime:
    """Compose the controller dispatcher before any export claim can start."""

    return create_configured_trusted_worker_executor_runtime(
        artifact_authority=configured_public_artifact_worker_object_port(),
    )


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
    profile = require_public_worker_profile_from_environment()
    allowed_kinds = external_workspace_operation_kinds(profile.role)
    trusted_worker_runtime = (
        create_external_export_controller_runtime()
        if profile.role == "ppt-core"
        else None
    )
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
    artifact_lifecycle_started = False
    if trusted_worker_runtime is not None:
        install_trusted_worker_dispatcher(trusted_worker_runtime.dispatcher)
    try:
        if profile.role == "generation":
            register_presentation_generation_operation_handler(replace=True)
        else:
            register_workspace_quality_export_operation_handlers(replace=True)
        metrics_exporter = await start_workspace_worker_metrics_exporter()
        await start_workspace_operation_runner(allowed_kinds=allowed_kinds)
        runner_started = True
        if profile.role == "ppt-core":
            await start_public_artifact_lifecycle_runtime()
            artifact_lifecycle_started = True
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
                            if artifact_lifecycle_started:
                                await stop_public_artifact_lifecycle_runtime()
                        finally:
                            try:
                                if runner_started:
                                    await stop_workspace_operation_runner()
                            finally:
                                try:
                                    if metrics_exporter is not None:
                                        await metrics_exporter.close()
                                finally:
                                    try:
                                        if trusted_worker_runtime is not None:
                                            install_trusted_worker_dispatcher(None)
                                            await trusted_worker_runtime.close()
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
