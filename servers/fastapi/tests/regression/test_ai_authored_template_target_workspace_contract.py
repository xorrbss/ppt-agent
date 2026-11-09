from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import workspace as workspace_endpoint
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_from_template import RetemplatePresentationRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import WorkspaceTemplateApplicationRequest
from services import workspace_authored_template_confirmation_service as confirmation


CONTRACT_TABLES = [
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
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_contract(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=CONTRACT_TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_source(maker) -> PresentationModel:
    async with maker() as session:
        source = PresentationModel(
            content="Source-authored manuscript",
            n_slides=4,
            language="Korean",
            title="Source deck",
            outlines={"slides": [{"content": "Source slide"}]},
            instructions="Preserve the source facts",
            theme={"mode": "authored", "style": "default"},
            mode="authored",
            lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
            revision=7,
            share_token=f"source-{uuid.uuid4().hex}",
        )
        session.add(source)
        await session.commit()
        return source


def _request(*, prompt: str = "Apply Signal without changing the source"):
    return WorkspaceTemplateApplicationRequest(
        source_revision=7,
        prompt=prompt,
        authored_style="signal",
        vision_qa=True,
    )


async def _generation_request(
    source_id: uuid.UUID,
    request: RetemplatePresentationRequest,
    _session,
) -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        presentation_id=source_id,
        content="Source-authored manuscript",
        n_slides=4,
        language="Korean",
        template="authored",
        strategy="authored",
        authored_style=request.authored_style,
        vision_qa=request.vision_qa,
    )


async def _rows(session, model):
    return (await session.execute(select(model))).scalars().all()


def _stable_presentation_snapshot(presentation: PresentationModel) -> dict:
    snapshot = presentation.model_dump(mode="json")
    # SQLite round-trips timezone-aware UTC datetimes as naive values. Those
    # transport details are unrelated to confirmation mutations.
    snapshot.pop("created_at", None)
    snapshot.pop("updated_at", None)
    snapshot.pop("profile_backfilled_at", None)
    return snapshot


def test_openapi_requires_idempotency_header_and_excludes_it_from_body():
    app = FastAPI()
    app.include_router(workspace_endpoint.WORKSPACE_ROUTER)

    operation = app.openapi()["paths"][
        "/workspace/presentations/{source_presentation_id}/template-applications"
    ]["post"]
    header = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )

    assert operation["responses"].get("202") is not None
    assert header["in"] == "header"
    assert header["required"] is True
    request_schema = app.openapi()["components"]["schemas"][
        "WorkspaceTemplateApplicationRequest"
    ]
    assert set(request_schema["required"]) == {
        "source_revision",
        "prompt",
        "authoredStyle",
    }
    assert "idempotency_key" not in request_schema["properties"]
    assert "idempotencyKey" not in request_schema["properties"]
    response_schema = app.openapi()["components"]["schemas"][
        "WorkspaceTemplateApplicationResponse"
    ]
    assert {
        "source_presentation_id",
        "source_revision",
        "target_presentation_id",
        "conversation_id",
        "first_user_message_id",
        "operation_id",
        "status",
        "operation_state",
        "timeline",
    }.issubset(response_schema["required"])


def test_confirmation_refuses_authored_regeneration_and_leaves_the_source_untouched(tmp_path: Path):
    """Template application regenerates with authored, removed on 2026-11-09."""

    async def scenario():
        engine, maker = await _database(tmp_path / "target-contract.db")
        try:
            source = await _seed_source(maker)
            source_snapshot = _stable_presentation_snapshot(source)

            async with maker() as session:
                with pytest.raises(HTTPException) as refused:
                    await confirmation.confirm_authored_template_application(
                        session,
                        source_presentation_id=source.id,
                        request=_request(),
                        idempotency_key="target-contract-key",
                        generation_request_builder=_generation_request,
                    )
                await session.rollback()
            assert refused.value.status_code == 422
            assert refused.value.detail == "authored_strategy_removed"

            async with maker() as verification:
                assert len(await _rows(verification, PresentationModel)) == 1
                assert await _rows(verification, WorkspaceConversation) == []
                assert await _rows(verification, WorkspaceMessage) == []
                assert await _rows(verification, WorkspaceOperation) == []
                assert await _rows(verification, WorkspaceOperationEvent) == []
                assert await _rows(verification, WorkspaceOutbox) == []
                assert await _rows(
                    verification,
                    AsyncPresentationGenerationTaskModel,
                ) == []
                assert await _rows(verification, PresentationGenerationJob) == []
                persisted_source = await verification.get(PresentationModel, source.id)
                assert persisted_source is not None
                assert _stable_presentation_snapshot(persisted_source) == source_snapshot
                assert persisted_source.revision == 7
                assert persisted_source.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
        finally:
            await engine.dispose()

    asyncio.run(scenario())
