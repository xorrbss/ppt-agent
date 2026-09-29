from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI

from api.v1.auth.bootstrap import bootstrap_database_admin
from migrations import migrate_database_on_startup
from services.account_deletion_controller import (
    start_account_deletion_controller,
    stop_account_deletion_controller,
)
from services.database import (
    create_db_and_tables,
    dispose_engines,
    require_public_api_database_authority,
    sql_engine,
)
from services.export_retention_service import (
    start_export_retention_sweeper,
    stop_export_retention_sweeper,
)
from services.presentation_generation_job_service import (
    start_presentation_generation_dispatcher,
    stop_presentation_generation_dispatcher,
)
from services.runtime_settings_coordinator import (
    start_runtime_settings_coordinator,
    stop_runtime_settings_coordinator,
)
from services.source_locked.maintenance import (
    start_source_locked_maintenance,
    stop_source_locked_maintenance,
)
from services.workspace_operation_outbox_dispatcher import (
    start_workspace_operation_outbox_dispatcher,
    stop_workspace_operation_outbox_dispatcher,
)
from services.workspace_operation_runner import (
    start_workspace_operation_runner,
    stop_workspace_operation_runner,
)
from services.workspace_operation_worker import (
    create_external_export_controller_runtime,
    embedded_workspace_operation_kinds,
    get_workspace_operation_worker_mode,
    register_workspace_operation_handlers,
    should_start_embedded_workspace_operation_worker,
)
from services.artifact_lifecycle_runtime import (
    start_public_artifact_lifecycle_runtime,
    stop_public_artifact_lifecycle_runtime,
)
from services.isolated_worker_process import install_trusted_worker_dispatcher
from services.workspace_cleanup_journal_service import (
    start_workspace_cleanup_maintenance,
    stop_workspace_cleanup_maintenance,
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
from services.provider_pricing_admission import pricing_gap_message
from utils.env_settings.public_service import (
    get_log_level,
    get_public_process_identity_settings,
    get_public_worker_node_id,
)
from utils.get_env import get_app_data_directory_env, get_can_change_keys_env, get_environment_name
from utils.model_availability import (
    check_llm_and_image_provider_api_or_model_availability,
)
from utils.renderer_warmup import warm_up_authored_renderer
from services.mem0_presentation_memory_service import MEM0_PRESENTATION_MEMORY_SERVICE

logger = logging.getLogger(__name__)
_LOCAL_ENVIRONMENTS = {"local", "development", "dev", "test"}


def _configure_application_logging() -> None:
    """Honor LOG_LEVEL (default INFO) so template/export diagnostics are visible."""
    raw = get_log_level()
    level = getattr(logging, raw, logging.INFO)
    # A level alone is inert: uvicorn's default LOGGING_CONFIG attaches handlers only to
    # the uvicorn.* loggers, so application INFO records fall through to
    # logging.lastResort (WARNING) and are dropped. basicConfig no-ops when the root
    # logger already has handlers (pytest caplog, an operator-supplied --log-config).
    logging.basicConfig(level=level)
    logging.getLogger().setLevel(level)


def _should_start_local_account_deletion_controller() -> bool:
    """Keep cross-owner deletion work out of every public PostgreSQL API."""

    environment = get_environment_name("development")
    return (
        environment in _LOCAL_ENVIRONMENTS
        and sql_engine.dialect.name == "sqlite"
        and get_workspace_operation_worker_mode() == "embedded"
    )


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
    await require_public_api_database_authority()
    if (
        get_environment_name("development")
        not in {"local", "development", "dev", "test"}
        and get_public_process_identity_settings().oidc_issuer
        and should_start_embedded_workspace_operation_worker()
    ):
        raise RuntimeError("public_controller_process_separation_required")
    await start_runtime_settings_coordinator()
    runtime_settings_coordinator_started = True
    export_retention_started = False
    source_locked_maintenance_started = False
    presentation_generation_dispatcher_started = False
    workspace_operation_runner_started = False
    workspace_outbox_dispatcher_started = False
    workspace_asset_maintenance_started = False
    workspace_cleanup_maintenance_started = False
    workspace_event_retention_started = False
    account_deletion_controller_started = False
    trusted_worker_runtime = None
    artifact_lifecycle_started = False
    try:
        await start_export_retention_sweeper()
        export_retention_started = True
        await start_source_locked_maintenance()
        source_locked_maintenance_started = True
        await start_presentation_generation_dispatcher()
        presentation_generation_dispatcher_started = True
        if should_start_embedded_workspace_operation_worker():
            await start_workspace_cleanup_maintenance()
            workspace_cleanup_maintenance_started = True
        register_workspace_operation_handlers()
        if (
            should_start_embedded_workspace_operation_worker()
            and get_public_process_identity_settings().v2_integration_profile
            == "postgresql-strict"
        ):
            # The public embedded/split profile owns export claims in this
            # process, so it must compose the real sandbox transport before
            # the runner can claim. Unsupported OS isolation stops startup.
            trusted_worker_runtime = create_external_export_controller_runtime()
            install_trusted_worker_dispatcher(trusted_worker_runtime.dispatcher)
            await start_public_artifact_lifecycle_runtime()
            artifact_lifecycle_started = True
        await start_workspace_operation_outbox_dispatcher()
        workspace_outbox_dispatcher_started = True
        if should_start_embedded_workspace_operation_worker():
            await start_workspace_operation_runner(
                allowed_kinds=embedded_workspace_operation_kinds()
            )
            workspace_operation_runner_started = True
        if _should_start_local_account_deletion_controller():
            node_id = get_public_worker_node_id()
            await start_account_deletion_controller(
                worker_id=f"{node_id or 'embedded-local'}:account-deletion"
            )
            account_deletion_controller_started = True
        if should_start_embedded_workspace_operation_worker():
            await start_workspace_asset_maintenance()
            workspace_asset_maintenance_started = True
            await start_workspace_event_retention()
            workspace_event_retention_started = True
        if should_start_embedded_workspace_operation_worker():
            # Pay the renderer's cold start (Chromium bring-up, or the
            # external worker binary/isolation check) at boot instead of on
            # the first slide of the first real deck. Never fails startup.
            await warm_up_authored_renderer()
            # Pay Mem0's one-time client init (about 8 s) off the first deck's path.
            MEM0_PRESENTATION_MEMORY_SERVICE.enqueue(MEM0_PRESENTATION_MEMORY_SERVICE.warm_up)
        await bootstrap_database_admin()
        # Settings also arrive without passing the admin route -- env, compose,
        # a seeded secret store -- so the model/pricing invariant is compared
        # again here. A locked configuration cannot be repaired from the UI, so
        # it must not boot into a stack whose every provider call fails closed;
        # a mutable one is reported and left to the operator to fix.
        pricing_gap = pricing_gap_message()
        if pricing_gap is not None:
            if get_can_change_keys_env() == "false":
                raise RuntimeError(pricing_gap)
            logger.error("provider_pricing_selection_gap %s", pricing_gap)
        if os.getenv("PRESENTON_SKIP_PROVIDER_AVAILABILITY_CHECK") != "true":
            await check_llm_and_image_provider_api_or_model_availability()
        else:
            logger.info(
                "provider_availability_check_skipped profile=export_only_validation"
            )
        yield
    finally:
        cleanup_steps = (
            (
                account_deletion_controller_started,
                stop_account_deletion_controller,
            ),
            (runtime_settings_coordinator_started, stop_runtime_settings_coordinator),
            (workspace_event_retention_started, stop_workspace_event_retention),
            (workspace_cleanup_maintenance_started, stop_workspace_cleanup_maintenance),
            (workspace_asset_maintenance_started, stop_workspace_asset_maintenance),
            (workspace_operation_runner_started, stop_workspace_operation_runner),
            (workspace_outbox_dispatcher_started, stop_workspace_operation_outbox_dispatcher),
            (
                presentation_generation_dispatcher_started,
                stop_presentation_generation_dispatcher,
            ),
            (export_retention_started, stop_export_retention_sweeper),
            (source_locked_maintenance_started, stop_source_locked_maintenance),
            (artifact_lifecycle_started, stop_public_artifact_lifecycle_runtime),
        )
        # Let queued Mem0 writes finish (bounded) before the engines go away.
        try:
            await MEM0_PRESENTATION_MEMORY_SERVICE.drain()
        except Exception:
            logger.exception("Mem0 background write drain failed")
        for started, stop in cleanup_steps:
            if not started:
                continue
            try:
                await stop()
            except Exception:
                logger.exception("Application shutdown step failed: %s", stop.__name__)
        if trusted_worker_runtime is not None:
            install_trusted_worker_dispatcher(None)
            try:
                await trusted_worker_runtime.close()
            except Exception:
                logger.exception("Trusted worker runtime shutdown failed")
        # A7's local stage-timing queue batches DB writes on its own timer;
        # drain it before the engines it writes through go away. The
        # function may not exist yet on older checkouts, so import it lazily
        # and skip silently if it is missing.
        try:
            from services.local_stage_receipt_queue import flush_local_stage_queue
        except ImportError:
            flush_local_stage_queue = None
        if flush_local_stage_queue is not None:
            try:
                await flush_local_stage_queue()
            except Exception:
                logger.exception("Local stage receipt queue flush failed")
        # Renderer worker leases hold long-lived worker processes when the
        # framed protocol is on; close them so no orphan Chromium survives.
        try:
            from utils.slide_capture_worker_lease import close_all_leases

            await close_all_leases()
        except Exception:
            logger.exception("Renderer worker lease shutdown failed")
        # Release all database connections to prevent stale/leaked pools.
        await dispose_engines()
