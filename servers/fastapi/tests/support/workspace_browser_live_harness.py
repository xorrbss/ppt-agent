"""Browser harness around the production FastAPI app and lifespan.

The process starts from a blank SQLite file, imports ``api.main:app`` only
after the production environment is configured, and lets the real lifespan
upgrade the database with Alembic and start the embedded durable runner.  A
deterministic edit handler and a few test-control routes are layered onto that
real app so Cypress can prove disconnect/backfill and crash-safe receipts
without requiring an LLM or object store.

Run from ``servers/fastapi``::

    uv run python tests/support/workspace_browser_live_harness.py --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid

from fastapi import Request
from sqlalchemy import text
import uvicorn


PRESENTATION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
AUTHORED_OWNER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")
FIRST_PROMPT = "첨부한 기획서를 바탕으로 경영진용 발표를 만들어 줘"
SUCCESS_MARKER = "[live-success]"
BACKFILL_MARKER = "[live-backfill]"
FAILURE_MARKER = "[live-failure]"
CANONICAL_SOURCE = "canonical-live"
CANONICAL_PUBLISH_MARKER = "[canonical-publish]"
CANONICAL_CHECKPOINT_MARKER = "[canonical-checkpoint]"


def _configure_production_environment(
    database_path: Path,
    app_data: Path,
    temp_directory: Path,
) -> None:
    """Configure globals consumed while importing ``api.main``/database.py."""

    resolved_database = database_path.resolve()
    if resolved_database.exists() and resolved_database.stat().st_size:
        raise RuntimeError(
            "workspace browser production harness requires a blank database path"
        )
    resolved_database.parent.mkdir(parents=True, exist_ok=True)
    app_data.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        {
            "DATABASE_URL": f"sqlite:///{resolved_database.as_posix()}",
            "APP_DATA_DIRECTORY": str(app_data.resolve()),
            # The global TempFileService performs startup cleanup. Confine it
            # to this harness run so a browser or another API process cannot
            # hold a LevelDB file in the directory being cleaned on Windows.
            "TEMP_DIRECTORY": str(temp_directory.resolve()),
            "USER_CONFIG_PATH": str((app_data / "userConfig.json").resolve()),
            "DISABLE_AUTH": "true",
            "CAN_CHANGE_KEYS": "false",
            # Exercise the production provider-validation branch without
            # reaching a network service.  The Codex provider is a valid
            # runtime selection and has no boot-time availability probe.
            "LLM": "codex",
            "DISABLE_IMAGE_GENERATION": "true",
            "DISABLE_ANONYMOUS_TRACKING": "true",
            # Cypress's component dev server reserves an ephemeral origin.
            # Authentication is disabled in this deterministic browser harness,
            # so no credentialed wildcard request is permitted or required.
            "CORS_ALLOWED_ORIGINS": "*",
            "ENABLE_TEMPLATE_V2": "false",
            "CHAT_FIRST_WORKSPACE_ENABLED": "true",
            "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE": "sqlite-local",
            "CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT": "100",
            "CHAT_FIRST_WORKSPACE_SHADOW_ENABLED": "false",
            "AUTHORED_VNEXT_MASTER_ENABLED": "true",
            "AUTHORED_VNEXT_GENERATION_ENABLED": "true",
            "AUTHORED_VNEXT_EXPORT_ENABLED": "true",
            "AUTHORED_VNEXT_OOXML_PACKAGE_ENABLED": "true",
            "AUTHORED_VNEXT_REPOSITORY_MODE": "sql",
            "WORKSPACE_ASSET_MALWARE_SCAN_MODE": "disabled",
            "WORKSPACE_OPERATION_WORKER_MODE": "embedded",
            "WORKSPACE_OPERATION_POLL_SECONDS": "0.05",
            "TEMPLATE_V2_DEPLOYMENT_TIER": "local",
            "ENVIRONMENT": "test",
            "MIGRATE_DATABASE_ON_STARTUP": "true",
            "PYTHONUNBUFFERED": "1",
            "LOG_LEVEL": "INFO",
        }
    )
    os.environ.pop("CHAT_FIRST_WORKSPACE_KILL_SWITCH", None)


def create_app(database_path: Path | None = None):
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if database_path is None:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="workspace-browser-production-"
        )
        runtime_root = Path(temporary_directory.name)
        database_path = runtime_root / "workspace.db"
    else:
        runtime_root = database_path.resolve().parent
    _configure_production_environment(
        database_path,
        runtime_root / "app-data",
        runtime_root / "temp",
    )

    # These imports intentionally happen after DATABASE_URL is fixed.  The
    # production database module constructs its engine at import time.
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from api.main import app
    from models.sql.presentation import PresentationModel
    from models.sql.user import User
    from models.sql.workspace import WorkspaceAsset
    from models.workspace import (
        WorkspaceAssetTransitionRequest,
        WorkspaceBootstrapRequest,
    )
    from services.database import async_session_maker
    from services.workspace_operation_handler_registry import (
        NonRetryableWorkspaceOperationError,
        WorkspaceOperationResult,
        register_workspace_operation_handler,
    )
    from services.agent_runtime import AgentRunResult, TypedMutation
    from services.workspace_chat_operation_handler import (
        PresentationChatOperationHandler,
    )
    from services.workspace_asset_maintenance import stop_workspace_asset_maintenance
    from services.workspace_asset_service import transition_workspace_asset
    from services.workspace_service import bootstrap_workspace
    from utils.owner_context import reset_current_owner_id, set_current_owner_id

    alembic_config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    alembic_config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
    if expected_head is None:
        raise RuntimeError("Alembic has no single head")

    production_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def browser_test_lifespan(application):
        try:
            async with production_lifespan(application):
                # The production lifespan proves startup and performs its
                # initial reconciliation. Stop only the periodic test-process
                # loop before inserting the fixture so asset state advances
                # exclusively through the explicit control endpoint below.
                await stop_workspace_asset_maintenance()
                controls = SimpleNamespace(
                    allow_backfill=asyncio.Event(),
                    backfill_recorded=asyncio.Event(),
                    allow_complete=asyncio.Event(),
                    success_started=asyncio.Event(),
                    success_operation_id=None,
                    execution_counts={},
                )
                application.state.controls = controls
                application.state.session_factory = async_session_maker
                application.state.ready = False
                application.state.production_database_was_blank = True
                application.state.expected_alembic_head = expected_head

                class DeterministicCanonicalChatService:
                    """Offline provider fixture behind the real chat publisher."""

                    def __init__(
                        self,
                        *,
                        sql_session,
                        presentation_id,
                        conversation_id,
                        artifact_directory=None,
                    ):
                        self._session = sql_session
                        self._presentation_id = presentation_id
                        self._conversation_id = conversation_id
                        self._artifact_directory = artifact_directory

                    async def execute_run(self, context, sink):
                        command = context.user_message
                        controls.execution_counts[command] = (
                            controls.execution_counts.get(command, 0) + 1
                        )
                        controls.success_operation_id = str(context.run_id)
                        controls.success_started.set()
                        await sink.status("canonical_received")
                        await context.raise_if_cancelled()
                        if FAILURE_MARKER in command:
                            raise NonRetryableWorkspaceOperationError(
                                "browser_fixture_failure",
                                detail={
                                    "message": "Deterministic browser failure."
                                },
                            )

                        if BACKFILL_MARKER in command:
                            await controls.allow_backfill.wait()
                            await context.raise_if_cancelled()

                        assistant_output = (
                            "Canonical recovered answer"
                            if CANONICAL_CHECKPOINT_MARKER in command
                            else "Canonical deterministic answer"
                        )
                        await sink.assistant_delta(assistant_output)
                        await sink.finalize(assistant_output)

                        mutations = ()
                        if CANONICAL_PUBLISH_MARKER in command:
                            presentation = await self._session.get(
                                PresentationModel, self._presentation_id
                            )
                            if presentation is None:
                                raise NonRetryableWorkspaceOperationError(
                                    "presentation_not_found"
                                )
                            presentation.content = (
                                f"{presentation.content}\ncanonical-live-publication"
                            )
                            mutations = (
                                TypedMutation(
                                    kind="presentation.updated",
                                    tool="canonicalLiveFixture",
                                ),
                            )

                        if BACKFILL_MARKER in command:
                            # The checkpoint writer above commits independently,
                            # so reconnecting after this gate always has a
                            # durable cursor even though the run is still open.
                            controls.backfill_recorded.set()
                            await controls.allow_complete.wait()
                            await context.raise_if_cancelled()

                        return AgentRunResult(
                            conversation_id=self._conversation_id,
                            assistant_output=assistant_output,
                            mutations=mutations,
                        )

                canonical_chat_handler = PresentationChatOperationHandler(
                    service_factory=DeterministicCanonicalChatService
                )

                async def deterministic_edit(context):
                    command = str(context.claim.request_payload.get("command", ""))
                    raw_context = context.claim.request_payload.get("context")
                    operation_context = (
                        raw_context if isinstance(raw_context, dict) else {}
                    )
                    if operation_context.get("source") == CANONICAL_SOURCE:
                        return await canonical_chat_handler.execute(context)
                    controls.execution_counts[command] = (
                        controls.execution_counts.get(command, 0) + 1
                    )
                    if FAILURE_MARKER in command:
                        await context.progress(stage="live_failure", percent=55)
                        raise NonRetryableWorkspaceOperationError(
                            "browser_fixture_failure",
                            detail={"message": "Deterministic browser failure."},
                        )
                    if SUCCESS_MARKER not in command:
                        raise NonRetryableWorkspaceOperationError(
                            "browser_fixture_unknown_command"
                        )
                    controls.success_operation_id = str(context.claim.operation_id)
                    await context.progress(stage="browser_received", percent=25)
                    controls.success_started.set()
                    if BACKFILL_MARKER in command:
                        await controls.allow_backfill.wait()
                        await context.progress(stage="browser_backfill", percent=80)
                        controls.backfill_recorded.set()
                        await controls.allow_complete.wait()
                    published_revision = context.claim.expected_revision
                    return WorkspaceOperationResult(
                        payload={"revision": published_revision},
                        result_revision=published_revision,
                    )

                # The production lifespan registers all handlers first; only
                # the external LLM edit is replaced by a deterministic fault
                # harness. Claiming, fencing, heartbeats and persistence remain
                # the production runner implementation.
                register_workspace_operation_handler(
                    "presentation.edit", deterministic_edit, replace=True
                )
                # The canonical approval fixture submits an allow-listed
                # destructive kind, so admission/approve/cancel use the same
                # production state machine before deterministic execution.
                register_workspace_operation_handler(
                    "slide.delete", canonical_chat_handler, replace=True
                )

                async with async_session_maker() as session, session.begin():
                    session.add(
                        User(
                            id=AUTHORED_OWNER_ID,
                            username="authored-browser-owner",
                            hashed_password="browser-harness-no-login",
                            is_active=True,
                            is_superuser=False,
                            is_verified=True,
                        )
                    )
                    # PresentationModel intentionally omits its production FK
                    # from partial-fixture ORM metadata, so flush the real
                    # owner before inserting the owner-bound presentation.
                    await session.flush()
                    session.add(
                        PresentationModel(
                            id=PRESENTATION_ID,
                            owner_id=AUTHORED_OWNER_ID,
                            content="Live browser workspace fixture",
                            n_slides=5,
                            language="ko",
                            layout={"name": "live-browser"},
                            revision=0,
                        )
                    )

                async with async_session_maker() as session:
                    browser_asset = WorkspaceAsset(
                        owner_id=AUTHORED_OWNER_ID,
                        state="uploaded",
                        original_name="strategy-brief.pdf",
                        media_type="application/pdf",
                        size_bytes=4096,
                        storage_key="browser-fixture/strategy-brief.pdf",
                        sha256="b" * 64,
                    )
                    session.add(browser_asset)
                    await session.commit()
                controls.asset_id = browser_asset.id

                bootstrap_request = WorkspaceBootstrapRequest(
                    idempotency_key="workspace-browser-bootstrap-v1",
                    prompt=FIRST_PROMPT,
                    generation_settings={
                        "tone": "executive",
                        "language": "ko",
                        "n_slides": 5,
                    },
                    # Uploaded is intentionally non-ready: the generation card
                    # stays durable without consuming the edit runner.
                    asset_ids=[browser_asset.id],
                )
                owner_token = set_current_owner_id(AUTHORED_OWNER_ID)
                try:
                    async with async_session_maker() as session:
                        _, _, generation_operation, duplicate = await bootstrap_workspace(
                            session,
                            presentation_id=PRESENTATION_ID,
                            request=bootstrap_request,
                        )
                finally:
                    reset_current_owner_id(owner_token)
                if duplicate or generation_operation.state != "waiting_for_assets":
                    raise RuntimeError("browser production fixture was not created")

                async with async_session_maker() as session:
                    actual_head = await session.scalar(
                        text("SELECT version_num FROM alembic_version")
                    )
                if actual_head != expected_head:
                    raise RuntimeError(
                        f"production lifespan did not reach Alembic head: {actual_head}"
                    )
                application.state.actual_alembic_head = actual_head
                application.state.ready = True
                try:
                    yield
                finally:
                    application.state.ready = False
        finally:
            if temporary_directory is not None:
                temporary_directory.cleanup()

    app.router.lifespan_context = browser_test_lifespan

    @app.middleware("http")
    async def bind_authored_owner_context(request: Request, call_next):
        """Bind one seeded owner only inside this disposable browser process."""

        token = set_current_owner_id(AUTHORED_OWNER_ID)
        try:
            return await call_next(request)
        finally:
            reset_current_owner_id(token)

    @app.middleware("http")
    async def identify_live_harness(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Workspace-Live-Harness"] = (
            "production-lifespan-alembic-v1"
        )
        return response

    @app.get("/__test__/health")
    async def health(request: Request):
        return {
            "ready": bool(getattr(request.app.state, "ready", False)),
            "presentation_id": str(PRESENTATION_ID),
            "storage": "sqlite",
            "runner": "workspace-operation-runner",
            "application": "api.main:app",
            "database_was_blank": bool(
                getattr(request.app.state, "production_database_was_blank", False)
            ),
            "alembic_revision": getattr(
                request.app.state, "actual_alembic_head", None
            ),
            "alembic_head": getattr(
                request.app.state, "expected_alembic_head", None
            ),
        }

    @app.post("/__test__/advance/backfill")
    async def advance_backfill(request: Request):
        controls = request.app.state.controls
        await asyncio.wait_for(controls.success_started.wait(), timeout=10)
        controls.allow_backfill.set()
        await asyncio.wait_for(controls.backfill_recorded.wait(), timeout=10)
        return {
            "operation_id": controls.success_operation_id,
            "progress": 80,
        }

    @app.post("/__test__/advance/complete")
    async def advance_complete(request: Request):
        controls = request.app.state.controls
        await asyncio.wait_for(controls.backfill_recorded.wait(), timeout=10)
        controls.allow_complete.set()
        return {"operation_id": controls.success_operation_id, "released": True}

    @app.post("/__test__/asset/fail")
    async def fail_asset(request: Request):
        controls = request.app.state.controls
        async with request.app.state.session_factory() as session:
            asset = await session.get(WorkspaceAsset, controls.asset_id)
            if asset is None:
                raise RuntimeError("live browser asset is missing")
            if asset.state != "failed":
                asset = await transition_workspace_asset(
                    session,
                    asset_id=asset.id,
                    request=WorkspaceAssetTransitionRequest(
                        state="failed",
                        expected_version=asset.version,
                        failure_code="workspace_asset_failed",
                    ),
                )
        return {
            "asset_id": str(asset.id),
            "state": asset.state,
            "failure_code": asset.failure_code,
            "version": asset.version,
        }

    @app.post("/__test__/release")
    async def release_test_gates(request: Request):
        controls = request.app.state.controls
        controls.allow_backfill.set()
        controls.backfill_recorded.set()
        controls.allow_complete.set()
        return {"released": True}

    @app.get("/__test__/stats")
    async def stats(request: Request):
        return {"execution_counts": dict(request.app.state.controls.execution_counts)}

    @app.post("/__test__/presentation/revision/advance")
    async def advance_revision(request: Request):
        async with request.app.state.session_factory() as session, session.begin():
            presentation = await session.get(PresentationModel, PRESENTATION_ID)
            if presentation is None:
                raise RuntimeError("live browser presentation is missing")
            presentation.revision += 1
            revision = presentation.revision
        return {"presentation_id": str(PRESENTATION_ID), "revision": revision}

    @app.get("/__test__/presentation/revision")
    async def get_revision(request: Request):
        async with request.app.state.session_factory() as session:
            presentation = await session.get(PresentationModel, PRESENTATION_ID)
            if presentation is None:
                raise RuntimeError("live browser presentation is missing")
            return {
                "presentation_id": str(PRESENTATION_ID),
                "revision": presentation.revision,
            }

    @app.post("/__test__/shutdown")
    async def shutdown(request: Request):
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is None:
            return {"stopping": False}
        server.should_exit = True
        return {"stopping": True}

    return app


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--database", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    arguments = _arguments()
    application = create_app(arguments.database)
    configuration = uvicorn.Config(
        application,
        host=arguments.host,
        port=arguments.port,
        log_level="warning",
    )
    server = uvicorn.Server(configuration)
    application.state.uvicorn_server = server
    server.run()
