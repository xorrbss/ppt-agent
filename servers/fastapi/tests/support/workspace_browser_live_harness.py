"""Standalone live backend for the workspace browser component test.

The harness deliberately keeps the HTTP router, SQLite persistence, timeline
projection, event stream, and durable operation runner real.  Only the edit
handler is deterministic: control endpoints pause it around a durable progress
event so Cypress can disconnect and prove that a later mount backfills the
missed state from the server.

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

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
import uvicorn

from api.v1.ppt.endpoints import workspace as workspace_endpoint
from models.sql.presentation import PresentationModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import WorkspaceBootstrapRequest
from services.database import get_async_session
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_runtime_profile import resolve_workspace_runtime_profile
from services.workspace_service import bootstrap_workspace


PRESENTATION_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
FIRST_PROMPT = "첨부한 기획서를 바탕으로 경영진용 발표를 만들어 줘."
SUCCESS_MARKER = "[live-success]"
FAILURE_MARKER = "[live-failure]"

WORKSPACE_TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceAsset.__table__,
    WorkspaceMessageAttachment.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    WorkspaceActionReceipt.__table__,
    WorkspaceArtifact.__table__,
]


def create_app(database_path: Path | None = None) -> FastAPI:
    temporary_directory = None
    if database_path is None:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="workspace-browser-live-"
        )
        database_path = Path(temporary_directory.name) / "workspace.db"

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.environ["CHAT_FIRST_WORKSPACE_ENABLED"] = "true"
        os.environ["CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE"] = "sqlite-local"

        engine = create_async_engine(
            f"sqlite+aiosqlite:///{database_path.as_posix()}"
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_safety(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")

        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: SQLModel.metadata.create_all(
                    sync_connection,
                    tables=WORKSPACE_TABLES,
                )
            )

        maker = async_sessionmaker(engine, expire_on_commit=False)

        async def override_session():
            async with maker() as session:
                yield session

        original_session_maker = workspace_endpoint.async_session_maker
        original_runtime_profile = workspace_endpoint._runtime_profile
        app.dependency_overrides[get_async_session] = override_session
        # The streaming route creates its own sessions, so both dependency and
        # module-level factories must point at the same live test database.
        workspace_endpoint.async_session_maker = maker
        workspace_endpoint._runtime_profile = lambda: resolve_workspace_runtime_profile(
            engine.url
        )

        controls = SimpleNamespace(
            allow_backfill=asyncio.Event(),
            backfill_recorded=asyncio.Event(),
            allow_complete=asyncio.Event(),
            success_started=asyncio.Event(),
            success_operation_id=None,
            execution_counts={},
        )
        app.state.controls = controls
        app.state.session_factory = maker
        app.state.ready = False

        async with maker() as session, session.begin():
            session.add(
                PresentationModel(
                    id=PRESENTATION_ID,
                    content="Live browser workspace fixture",
                    n_slides=5,
                    language="ko",
                    layout={"name": "live-browser"},
                    revision=0,
                )
            )

        async with maker() as session:
            browser_asset = WorkspaceAsset(
                state="uploaded",
                original_name="strategy-brief.pdf",
                media_type="application/pdf",
                size_bytes=4096,
                storage_key="browser-fixture/strategy-brief.pdf",
                sha256="b" * 64,
            )
            session.add(browser_asset)
            await session.commit()

        bootstrap_request = WorkspaceBootstrapRequest(
            idempotency_key="workspace-browser-bootstrap-v1",
            prompt=FIRST_PROMPT,
            generation_settings={
                "tone": "executive",
                "language": "ko",
                "n_slides": 5,
            },
            # Uploaded is intentionally non-ready: the initial generation card
            # is durable and visible, but it cannot consume the edit runner.
            asset_ids=[browser_asset.id],
        )
        async with maker() as session:
            _, _, generation_operation, duplicate = await bootstrap_workspace(
                session,
                presentation_id=PRESENTATION_ID,
                request=bootstrap_request,
            )
        if duplicate or generation_operation.state != "waiting_for_assets":
            raise RuntimeError("live browser bootstrap fixture was not created")

        async def deterministic_edit(context):
            command = str(context.claim.request_payload.get("command", ""))
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
            await controls.allow_backfill.wait()
            await context.progress(stage="browser_backfill", percent=80)
            controls.backfill_recorded.set()
            await controls.allow_complete.wait()
            published_revision = context.claim.expected_revision
            return WorkspaceOperationResult(
                payload={"revision": published_revision},
                result_revision=published_revision,
            )

        registry = WorkspaceOperationHandlerRegistry()
        registry.register("presentation.edit", deterministic_edit)
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            lease_seconds=30,
            heartbeat_seconds=5,
            poll_seconds=0.05,
        )
        app.state.runner = runner
        await runner.start()
        app.state.ready = True

        try:
            yield
        finally:
            app.state.ready = False
            await runner.stop()
            app.dependency_overrides.clear()
            workspace_endpoint.async_session_maker = original_session_maker
            workspace_endpoint._runtime_profile = original_runtime_profile
            await engine.dispose()
            if temporary_directory is not None:
                temporary_directory.cleanup()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Workspace-Live-Harness"],
    )

    @app.middleware("http")
    async def identify_live_harness(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Workspace-Live-Harness"] = "sqlite-runner-v1"
        return response

    app.include_router(workspace_endpoint.WORKSPACE_ROUTER, prefix="/api/v1/ppt")

    @app.get("/__test__/health")
    async def health(request: Request):
        return {
            "ready": bool(getattr(request.app.state, "ready", False)),
            "presentation_id": str(PRESENTATION_ID),
            "storage": "sqlite",
            "runner": "workspace-operation-runner",
        }

    @app.post("/__test__/advance/backfill")
    async def advance_backfill(request: Request):
        controls = request.app.state.controls
        await asyncio.wait_for(controls.success_started.wait(), timeout=10)
        controls.allow_backfill.set()
        # Return only after the missed progress event has committed durably.
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

    @app.get("/__test__/stats")
    async def stats(request: Request):
        return {"execution_counts": dict(request.app.state.controls.execution_counts)}

    @app.post("/__test__/presentation/revision/advance")
    async def advance_revision(request: Request):
        """Commit the revision change made by a deterministic second browser tab."""
        async with request.app.state.session_factory() as session, session.begin():
            presentation = await session.get(PresentationModel, PRESENTATION_ID)
            if presentation is None:
                raise RuntimeError("live browser presentation is missing")
            presentation.revision += 1
            revision = presentation.revision
        return {"presentation_id": str(PRESENTATION_ID), "revision": revision}

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
