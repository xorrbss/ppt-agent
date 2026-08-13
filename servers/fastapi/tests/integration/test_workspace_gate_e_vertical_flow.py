"""Gate E vertical evidence for the chat-first workspace.

This test intentionally crosses the HTTP, persistence, runner and timeline
boundaries with one real SQLite database.  The operation handler is
deterministic so the recovery/idempotency contract does not depend on an LLM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI
import httpx
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import workspace as workspace_endpoint
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
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
from services.database import get_async_session
from services.presentation_publication_service import publish_presentation_revision
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
    WorkspaceOperationResult,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_runtime_profile import resolve_workspace_runtime_profile


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
    PresentationExportAttempt.__table__,
    WorkspaceArtifact.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

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
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _drain(runner: WorkspaceOperationRunner) -> int:
    dispatched = await runner.dispatch_once()
    tasks = tuple(runner._active.values())
    if tasks:
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
    return dispatched


def _items_by_type(timeline: dict, anchor_type: str) -> list[dict]:
    return [
        item
        for item in timeline["items"]
        if item["anchor_type"] == anchor_type
    ]


def test_api_runner_timeline_reconnect_and_retry_are_one_durable_flow(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE", "sqlite-local")
    asyncio.run(_exercise_vertical_flow(tmp_path, monkeypatch))


async def _exercise_vertical_flow(tmp_path: Path, monkeypatch) -> None:
    engine, maker = await _database(tmp_path / "gate-e.db")
    app = FastAPI()
    app.include_router(workspace_endpoint.WORKSPACE_ROUTER, prefix="/api/v1/ppt")

    async def override_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_async_session] = override_session
    monkeypatch.setattr(
        workspace_endpoint,
        "_runtime_profile",
        lambda: resolve_workspace_runtime_profile(engine.url),
    )

    try:
        async with maker() as session, session.begin():
            presentation = PresentationModel(
                content="Gate E vertical workspace",
                n_slides=5,
                language="ko",
                layout={"name": "gate-e"},
                revision=0,
            )
            session.add(presentation)
            await session.flush()
            presentation_id = presentation.id
            asset = WorkspaceAsset(
                state="uploaded",
                original_name="strategy-brief.pdf",
                media_type="application/pdf",
                size_bytes=4096,
                storage_key="gate-e/strategy-brief.pdf",
                sha256="e" * 64,
            )
            session.add(asset)
            await session.flush()
            asset_id = asset.id

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://workspace.test",
        ) as client:
            capabilities = await client.get("/api/v1/ppt/workspace/capabilities")
            assert capabilities.status_code == 200
            capabilities_body = capabilities.json()
            required_capabilities = {
                "schema_version": 1,
                "supported": True,
                "enabled": True,
                "timeline": True,
                "commands": True,
                "events": True,
                "runtime_profile": "sqlite-local",
                "reason": None,
            }
            assert {
                key: capabilities_body[key]
                for key in required_capabilities
            } == required_capabilities
            assert capabilities_body["reads_supported"] is True
            assert capabilities_body["writes_supported"] is True
            assert capabilities_body["commands_enabled"] is True
            assert capabilities_body["cohort_eligible"] is True
            assert capabilities_body["cohort_bucket"] is None
            assert capabilities_body["kill_switch_active"] is False
            assert capabilities_body["shadow_mode"] is False

            bootstrap_payload = {
                "idempotency_key": "gate-e-bootstrap-1",
                "prompt": "첨부한 기획서를 바탕으로 경영진용 발표를 만들어 줘.",
                "generation_settings": {
                    "tone": "executive",
                    "language": "ko",
                    "n_slides": 5,
                },
                # The uploaded asset is deliberately still being processed.  Its
                # generation operation must wait without blocking a later edit.
                "attachments": [
                    {
                        "asset_id": str(asset_id),
                        "file_name": "strategy-brief.pdf",
                        "media_type": "application/pdf",
                        "size_bytes": 4096,
                        "sha256": "e" * 64,
                        "status": "uploaded",
                    }
                ],
            }
            bootstrap = await client.post(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/bootstrap",
                json=bootstrap_payload,
            )
            assert bootstrap.status_code == 200, bootstrap.text
            bootstrap_body = bootstrap.json()
            assert bootstrap_body["operation_state"] == "waiting_for_assets"
            initial_high_watermark = bootstrap_body["timeline"][
                "event_high_watermark"
            ]

            # A fresh GET models a page reload: the original prompt, attachment
            # snapshot and settings come back from durable server state.
            reload_response = await client.get(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline"
            )
            assert reload_response.status_code == 200
            reloaded = reload_response.json()
            assert [item["position"] for item in reloaded["items"]] == [1, 2]
            first_message = _items_by_type(reloaded, "message")[0]
            assert first_message["content"] == bootstrap_payload["prompt"]
            assert first_message["generation_settings"] == bootstrap_payload[
                "generation_settings"
            ]
            assert len(first_message["attachments"]) == 1
            attachment = first_message["attachments"][0]
            required_attachment = {
                "id": attachment["id"],
                "name": "strategy-brief.pdf",
                "status": "uploaded",
                "mime_type": "application/pdf",
                "size_bytes": 4096,
                "error": None,
            }
            assert {
                key: attachment[key]
                for key in required_attachment
            } == required_attachment
            assert attachment["schema_version"] == 1
            assert attachment["current_state"] == attachment["status"]
            assert attachment["sha256"] == "e" * 64
            assert attachment["safe_failure"] is None
            assert attachment["unavailable_reason"] == "workspace_asset_not_ready"
            assert attachment["download_url"] is None
            assert attachment["download_action"] is None
            assert attachment["preview_url"] is None
            assert attachment["preview_action"] is None

            command_payload = {
                "command": "표지 제목을 이사회 전략 보고로 바꿔 줘.",
                "command_type": "presentation.edit",
                "idempotency_key": "gate-e-edit-1",
                "expected_revision": 0,
                "context": {"source": "right_chat"},
            }
            command_response = await client.post(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/commands",
                json=command_payload,
            )
            assert command_response.status_code == 200, command_response.text
            queued_timeline = command_response.json()
            edit_cards = [
                item
                for item in _items_by_type(queued_timeline, "operation")
                if item["operation_type"] == "presentation.edit"
            ]
            assert len(edit_cards) == 1
            assert edit_cards[0]["status"] == "queued"

            # Save the last cursor seen before a network break.  Backfill must
            # resume operation events without changing message pagination.
            before_disconnect = await client.get(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/events",
                params={"after": initial_high_watermark},
            )
            assert before_disconnect.status_code == 200
            before_disconnect_body = before_disconnect.json()
            assert [event["type"] for event in before_disconnect_body["events"]] == [
                "message.created",
                "queued",
            ]
            reconnect_cursor = before_disconnect_body["event_high_watermark"]

            async def publish_edit(context):
                assert await context.progress(
                    stage="publishing",
                    current=1,
                    total=1,
                    percent=100,
                )
                async with maker() as session:
                    revision = await publish_presentation_revision(
                        session,
                        presentation_id=context.claim.presentation_id,
                        expected_revision=context.claim.expected_revision or 0,
                        operation_id=context.claim.operation_id,
                        attempt_token=context.claim.attempt_token,
                        result={"status": "completed", "revision": 1},
                    )
                    await session.commit()
                return WorkspaceOperationResult(
                    payload={"status": "completed", "revision": revision},
                    result_revision=revision,
                )

            registry = WorkspaceOperationHandlerRegistry()
            registry.register("presentation.edit", publish_edit)
            runner = WorkspaceOperationRunner(
                session_factory=maker,
                registry=registry,
                max_concurrency=1,
                heartbeat_seconds=60,
            )
            assert await _drain(runner) == 1

            reconnect = await client.get(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/events",
                headers={"Last-Event-ID": reconnect_cursor},
            )
            assert reconnect.status_code == 200
            reconnect_body = reconnect.json()
            reconnect_types = [event["type"] for event in reconnect_body["events"]]
            # Claim and handler progress are both durable progress events; their
            # payload stages distinguish ``started`` from ``publishing``.
            assert reconnect_types == [
                "operation.progress",
                "operation.progress",
                "operation.result",
                "presentation.published",
                "run.completed",
            ]
            assert all(
                int(event["cursor"]) > int(reconnect_cursor)
                for event in reconnect_body["events"]
            )

            completed_response = await client.get(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline"
            )
            assert completed_response.status_code == 200
            completed = completed_response.json()
            assert completed["revision"] == 1
            completed_edit = next(
                item
                for item in _items_by_type(completed, "operation")
                if item["operation_type"] == "presentation.edit"
            )
            assert completed_edit["status"] == "succeeded"
            assert completed_edit["result_revision"] == 1
            assert [event["type"] for event in completed_edit["events"]] == [
                "message.created",
                "queued",
                "operation.progress",
                "operation.progress",
                "operation.result",
                "presentation.published",
                "run.completed",
            ]

            item_count = len(completed["items"])
            # The client may not know whether its first response arrived.  The
            # same key+payload must return the original operation, even after
            # the deck revision advanced, and must not append another message.
            ambiguous_retry = await client.post(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/commands",
                json=command_payload,
            )
            assert ambiguous_retry.status_code == 200
            assert len(ambiguous_retry.json()["items"]) == item_count

            # A genuinely new command from a stale tab is rejected before it can
            # reserve timeline positions or run against the wrong publication.
            stale_tab = await client.post(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/commands",
                json={
                    **command_payload,
                    "command": "두 번째 탭의 오래된 수정",
                    "idempotency_key": "gate-e-stale-tab-2",
                },
            )
            assert stale_tab.status_code == 409
            assert stale_tab.json() == {"detail": "revision_conflict"}

            after_conflict = await client.get(
                f"/api/v1/ppt/workspace/presentations/{presentation_id}/timeline"
            )
            assert after_conflict.status_code == 200
            assert len(after_conflict.json()["items"]) == item_count
            assert after_conflict.json()["event_high_watermark"] == reconnect_body[
                "event_high_watermark"
            ]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
