from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI

from api.v1.auth.bootstrap import bootstrap_database_admin
from migrations import migrate_database_on_startup
from services.database import create_db_and_tables, dispose_engines
from services.presentation_generation_job_service import (
    start_presentation_generation_dispatcher,
    stop_presentation_generation_dispatcher,
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
from templates.v2.policy import get_structured_template_policy
from utils.get_env import get_app_data_directory_env
from utils.model_availability import (
    check_llm_and_image_provider_api_or_model_availability,
)
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
    template_v2_policy = get_structured_template_policy()
    require_template_v2_database_safety(
        feature_enabled=template_v2_policy.creation_enabled,
    )
    os.makedirs(get_app_data_directory_env(), exist_ok=True)
    await migrate_database_on_startup()
    await create_db_and_tables()
    template_v2_dispatcher_started = False
    template_v2_cleanup_started = False
    presentation_generation_dispatcher_started = False
    try:
        try:
            await log_template_v2_operational_health()
        except Exception:
            logger.exception("Template V2 operational health check failed")
        await start_template_v2_private_source_cleanup()
        template_v2_cleanup_started = True
        await start_presentation_generation_dispatcher()
        presentation_generation_dispatcher_started = True
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
            if template_v2_dispatcher_started:
                await stop_template_v2_pptx_dispatcher()
        finally:
            try:
                if presentation_generation_dispatcher_started:
                    await stop_presentation_generation_dispatcher()
            finally:
                try:
                    if template_v2_cleanup_started:
                        await stop_template_v2_private_source_cleanup()
                finally:
                    # Release all database connections to prevent stale/leaked pools.
                    await dispose_engines()
