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
from services.presentation_generation_operation_handler import (
    register_presentation_generation_operation_handler,
)
from services.raster_analysis_operation_handler import (
    register_raster_analysis_operation_handler,
)
from services.runtime_settings_coordinator import (
    start_runtime_settings_coordinator,
    stop_runtime_settings_coordinator,
)
from services.source_locked.maintenance import (
    start_source_locked_maintenance,
    stop_source_locked_maintenance,
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
    create_external_export_controller_runtime,
    embedded_workspace_operation_kinds,
    get_workspace_operation_worker_mode,
    should_start_embedded_workspace_operation_worker,
)
from services.artifact_lifecycle_runtime import (
    start_public_artifact_lifecycle_runtime,
    stop_public_artifact_lifecycle_runtime,
)
from services.isolated_worker_process import install_trusted_worker_dispatcher
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
from services.workspace_batch_remake_operation_handler import (
    register_workspace_batch_remake_operation_handler,
)
from services.workspace_authored_preview_operation_handlers import (
    register_workspace_authored_preview_operation_handlers,
)
from services.workspace_slide_order_operation_handler import (
    register_workspace_slide_order_operation_handler,
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
from templates.packages.defaults import import_template_packages_on_startup
from templates.packages.registry import get_template_package_registry
from templates.v2.policy import get_structured_template_policy
from services.provider_pricing_admission import pricing_gap_message
from utils.get_env import get_app_data_directory_env, get_can_change_keys_env
from utils.model_availability import (
    check_llm_and_image_provider_api_or_model_availability,
)
from utils.renderer_warmup import warm_up_authored_renderer

logger = logging.getLogger(__name__)
_LOCAL_ENVIRONMENTS = {"local", "development", "dev", "test"}


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


def _should_start_local_account_deletion_controller() -> bool:
    """Keep cross-owner deletion work out of every public PostgreSQL API."""

    environment = (os.getenv("ENVIRONMENT") or "development").strip().lower()
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
    # Validate the immutable package catalog before migrations or workers make
    # an API with an untrusted bundled-template source appear healthy.
    template_package_registry = get_template_package_registry()
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
    await require_public_api_database_authority()
    if (
        (os.getenv("ENVIRONMENT") or "development").strip().lower()
        not in {"local", "development", "dev", "test"}
        and (os.getenv("PPT_PUBLIC_OIDC_ISSUER") or "").strip()
        and should_start_embedded_workspace_operation_worker()
    ):
        raise RuntimeError("public_controller_process_separation_required")
    await import_template_packages_on_startup(template_package_registry)
    await start_runtime_settings_coordinator()
    runtime_settings_coordinator_started = True
    template_v2_dispatcher_started = False
    template_v2_cleanup_started = False
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
        try:
            await log_template_v2_operational_health()
        except Exception:
            logger.exception("Template V2 operational health check failed")
        await start_template_v2_private_source_cleanup()
        template_v2_cleanup_started = True
        await start_export_retention_sweeper()
        export_retention_started = True
        await start_source_locked_maintenance()
        source_locked_maintenance_started = True
        await start_presentation_generation_dispatcher()
        presentation_generation_dispatcher_started = True
        if should_start_embedded_workspace_operation_worker():
            await start_workspace_cleanup_maintenance()
            workspace_cleanup_maintenance_started = True
        register_presentation_generation_operation_handler(replace=True)
        register_raster_analysis_operation_handler(replace=True)
        register_presentation_deletion_operation_handler(replace=True)
        register_presentation_chat_operation_handlers(replace=True)
        register_workspace_page_remake_operation_handler(replace=True)
        register_workspace_batch_remake_operation_handler(replace=True)
        # Drifted out of this hand-duplicated list at some point: the durable
        # preview handlers (single-page and batch) that
        # services/workspace_operation_worker.py::register_workspace_operation_handlers
        # already includes for the external-worker path. Without this, both
        # admit_workspace_authored_task callers (queue_authored_page_remake_preview,
        # queue_authored_batch_remake_preview) create a WorkspaceOperation the
        # embedded runner has no handler for, so it sits "queued" forever.
        # Found live (2026-09-26): a batch preview operation with zero
        # progress and zero events past admission after 20+ minutes.
        register_workspace_authored_preview_operation_handlers(replace=True)
        register_workspace_slide_order_operation_handler(replace=True)
        register_workspace_quality_export_operation_handlers(replace=True)
        register_workspace_authored_template_operation_handler(replace=True)
        if (
            should_start_embedded_workspace_operation_worker()
            and os.getenv("PUBLIC_V2_INTEGRATION_PROFILE", "").strip()
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
            node_id = (os.getenv("PUBLIC_WORKER_NODE_ID") or "").strip()
            await start_account_deletion_controller(
                worker_id=f"{node_id or 'embedded-local'}:account-deletion"
            )
            account_deletion_controller_started = True
        if should_start_embedded_workspace_operation_worker():
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
        if should_start_embedded_workspace_operation_worker():
            # Pay the renderer's cold start (Chromium bring-up, or the
            # external worker binary/isolation check) at boot instead of on
            # the first slide of the first real deck. Never fails startup.
            await warm_up_authored_renderer()
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
            (template_v2_dispatcher_started, stop_template_v2_pptx_dispatcher),
            (
                presentation_generation_dispatcher_started,
                stop_presentation_generation_dispatcher,
            ),
            (export_retention_started, stop_export_retention_sweeper),
            (source_locked_maintenance_started, stop_source_locked_maintenance),
            (template_v2_cleanup_started, stop_template_v2_private_source_cleanup),
            (artifact_lifecycle_started, stop_public_artifact_lifecycle_runtime),
        )
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
