from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI

from api.v1.auth.bootstrap import bootstrap_database_admin
from migrations import migrate_database_on_startup
from services.database import create_db_and_tables, dispose_engines, sql_engine
from services.export_retention_service import (
    start_export_retention_sweeper,
    stop_export_retention_sweeper,
)
from services.presentation_generation_job_service import (
    start_presentation_generation_dispatcher,
    stop_presentation_generation_dispatcher,
)
from services.presentation_generation_operation_handler import (
    register_presentation_generation_operation_handler,
)
from services.presentation_deletion_operation_handler import (
    register_presentation_deletion_operation_handler,
)
from services.template_v2_pptx_ingestion_service import (
    start_template_v2_pptx_dispatcher,
    stop_template_v2_pptx_dispatcher,
)
from services.template_v2_pptx_operations import (
    log_template_v2_operational_health,
    require_template_v2_database_safety,
)
from services.template_v2_pptx_retention_service import (
    start_template_v2_private_source_cleanup,
    stop_template_v2_private_source_cleanup,
)
from services.template_v2_pptx_worker import should_start_embedded_worker
from services.workspace_operation_outbox_dispatcher import (
    start_workspace_operation_outbox_dispatcher,
    stop_workspace_operation_outbox_dispatcher,
)
from services.workspace_operation_runner import (
    start_workspace_operation_runner,
    stop_workspace_operation_runner,
)
from services.workspace_operation_worker import (
    get_workspace_operation_worker_mode,
    should_start_embedded_workspace_operation_worker,
)
from services.workspace_chat_operation_handler import (
    register_presentation_chat_operation_handlers,
)
from services.workspace_cleanup_journal_service import (
    start_workspace_cleanup_maintenance,
    stop_workspace_cleanup_maintenance,
)
from services.workspace_quality_export_operation_handlers import (
    register_workspace_quality_export_operation_handlers,
)
from services.workspace_page_remake_operation_handler import (
    register_workspace_page_remake_operation_handler,
)
from services.workspace_authored_template_operation_handler import (
    register_workspace_authored_template_operation_handler,
)
from services.workspace_asset_maintenance import (
    start_workspace_asset_maintenance,
    stop_workspace_asset_maintenance,
)
from services.workspace_asset_service import (
    require_workspace_asset_malware_scan_safety,
)
from services.workspace_event_retention import (
    start_workspace_event_retention,
    stop_workspace_event_retention,
)
from services.workspace_runtime_profile import require_supported_workspace_runtime
from templates.v2.policy import get_structured_template_policy
from utils.get_env import get_app_data_directory_env
from utils.model_availability import (
    check_llm_and_image_provider_api_or_model_availability,
)
from utils.user_config import refresh_process_runtime_settings

logger = logging.getLogger(__name__)


def _configure_application_logging() -> None:
    """Honor LOG_LEVEL (default INFO) so template/export diagnostics are visible."""
    raw = (os.getenv("LOG_LEVEL") or "INFO").strip().upper()
    level = getattr(logging, raw, logging.INFO)
    # A level alone is inert: uvicorn's default LOGGING_CONFIG attaches handlers only to
    # the uvicorn.* loggers, so application INFO records fall through to
    # logging.lastResort (WARNING) and are dropped. basicConfig no-ops when the root
    # logger already has handlers (pytest caplog, an operator-supplied --log-config).
    logging.basicConfig(level=level)
    logging.getLogger().setLevel(level)


@asynccontextmanager
async def app_lifespan(_: FastAPI):
    """
    Lifespan context manager for FastAPI application.
    Initializes the application data directory, runs Alembic migrations when
    MIGRATE_DATABASE_ON_STARTUP=true, creates any missing tables, bootstraps
    the primary administrator from legacy/env credentials (if provided), and checks LLM model
    availability.
    """
    _configure_application_logging()
    # Process-start seed: userConfig/env are read once and published as an
    # immutable snapshot. Requests and jobs never copy values into os.environ.
    refresh_process_runtime_settings()
    template_v2_policy = get_structured_template_policy()
    require_template_v2_database_safety(
        feature_enabled=template_v2_policy.creation_enabled,
    )
    # The operation runner owns every presentation write, so an unusable SQL
    # runtime must stop startup rather than surface as a half-working API.
    require_supported_workspace_runtime(sql_engine.url)
    # Validate this process boundary before migrations or background tasks
    # make the API appear healthy with an unusable worker configuration.
    get_workspace_operation_worker_mode()
    require_workspace_asset_malware_scan_safety(feature_enabled=True)
    os.makedirs(get_app_data_directory_env(), exist_ok=True)
    await migrate_database_on_startup()
    await create_db_and_tables()
    template_v2_dispatcher_started = False
    template_v2_cleanup_started = False
    export_retention_started = False
    presentation_generation_dispatcher_started = False
    workspace_operation_runner_started = False
    workspace_outbox_dispatcher_started = False
    workspace_asset_maintenance_started = False
    workspace_cleanup_maintenance_started = False
    workspace_event_retention_started = False
    try:
        try:
            await log_template_v2_operational_health()
        except Exception:
            logger.exception("Template V2 operational health check failed")
        await start_template_v2_private_source_cleanup()
        template_v2_cleanup_started = True
        await start_export_retention_sweeper()
        export_retention_started = True
        await start_presentation_generation_dispatcher()
        presentation_generation_dispatcher_started = True
        await start_workspace_cleanup_maintenance()
        workspace_cleanup_maintenance_started = True
        register_presentation_generation_operation_handler(replace=True)
        register_presentation_deletion_operation_handler(replace=True)
        register_presentation_chat_operation_handlers(replace=True)
        register_workspace_page_remake_operation_handler(replace=True)
        register_workspace_quality_export_operation_handlers(replace=True)
        register_workspace_authored_template_operation_handler(replace=True)
        await start_workspace_operation_outbox_dispatcher()
        workspace_outbox_dispatcher_started = True
        if should_start_embedded_workspace_operation_worker():
            await start_workspace_operation_runner()
            workspace_operation_runner_started = True
        await start_workspace_asset_maintenance()
        workspace_asset_maintenance_started = True
        await start_workspace_event_retention()
        workspace_event_retention_started = True
        if (
            template_v2_policy.creation_enabled
            and template_v2_policy.allowed_template_ids
            and should_start_embedded_worker()
        ):
            await start_template_v2_pptx_dispatcher()
            template_v2_dispatcher_started = True
        await bootstrap_database_admin()
        await check_llm_and_image_provider_api_or_model_availability()
        yield
    finally:
        try:
            if workspace_event_retention_started:
                await stop_workspace_event_retention()
        finally:
            try:
                if workspace_cleanup_maintenance_started:
                    await stop_workspace_cleanup_maintenance()
            finally:
                try:
                    if workspace_asset_maintenance_started:
                        await stop_workspace_asset_maintenance()
                finally:
                    try:
                        if workspace_operation_runner_started:
                            await stop_workspace_operation_runner()
                    finally:
                        try:
                            if workspace_outbox_dispatcher_started:
                                await stop_workspace_operation_outbox_dispatcher()
                        finally:
                            try:
                                if template_v2_dispatcher_started:
                                    await stop_template_v2_pptx_dispatcher()
                            finally:
                                try:
                                    if presentation_generation_dispatcher_started:
                                        await stop_presentation_generation_dispatcher()
                                finally:
                                    try:
                                        if export_retention_started:
                                            await stop_export_retention_sweeper()
                                    finally:
                                        try:
                                            if template_v2_cleanup_started:
                                                await stop_template_v2_private_source_cleanup()
                                        finally:
                                            # Release all database connections to prevent stale/leaked pools.
                                            await dispose_engines()
