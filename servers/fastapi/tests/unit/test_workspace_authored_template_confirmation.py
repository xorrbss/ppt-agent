import asyncio
from pathlib import Path
import uuid

import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.generate_presentation_request import GeneratePresentationRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from models.workspace import WorkspaceTemplateApplicationRequest
from api.v1.ppt.endpoints import workspace as workspace_endpoint
from api.v1.ppt.endpoints.presentation_crud import get_presentation
from services.workspace_authored_template_confirmation_service import (
    confirm_authored_template_application,
)
from services.workspace_rollout_policy import (
    WorkspaceWriteRolloutDecision,
    resolve_active_workspace_write_rollout,
    workspace_cohort_bucket,
)
from services.workspace_service import WorkspaceServiceError


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceMessageAttachment.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _source() -> PresentationModel:
    return PresentationModel(
        content="source manuscript",
        n_slides=2,
        language="Korean",
        outlines={"slides": [{"content": "one"}, {"content": "two"}]},
        mode="authored",
        layout=None,
        lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
        revision=7,
    )


def _request(*, style: str = "signal", revision: int = 7):
    return WorkspaceTemplateApplicationRequest(
        source_revision=revision,
        prompt="새 템플릿으로 다시 만들어 주세요",
        authored_style=style,
        vision_qa=True,
    )


async def _builder(source_id, request, _session):
    return GeneratePresentationRequest(
        content="source manuscript",
        slides_markdown=["one", "two"],
        language="Korean",
        template="authored",
        authored_style=request.authored_style,
        vision_qa=request.vision_qa,
    )


def test_confirm_is_atomic_idempotent_and_target_owned(tmp_path: Path):
    asyncio.run(_confirm_is_atomic_idempotent_and_target_owned(tmp_path))


async def _confirm_is_atomic_idempotent_and_target_owned(tmp_path: Path):
    engine, maker = await _database(tmp_path / "confirm.db")
    try:
        async with maker() as session:
            source = _source()
            session.add(source)
            await session.commit()
            source_id = source.id

        async with maker() as session:
            first = await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=_request(),
                idempotency_key="confirm-1",
                generation_request_builder=_builder,
            )
        async with maker() as session:
            replay = await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=_request(),
                idempotency_key="confirm-1",
                generation_request_builder=_builder,
            )

        assert first.duplicate is False
        assert replay.duplicate is True
        assert replay.target_presentation_id == first.target_presentation_id
        assert replay.operation.id == first.operation.id
        assert replay.message.id == first.message.id

        async with maker() as session:
            presentations = (
                await session.execute(select(PresentationModel))
            ).scalars().all()
            operations = (
                await session.execute(select(WorkspaceOperation))
            ).scalars().all()
            conversations = (
                await session.execute(select(WorkspaceConversation))
            ).scalars().all()
            messages = (
                await session.execute(select(WorkspaceMessage))
            ).scalars().all()
            jobs = (
                await session.execute(select(PresentationGenerationJob))
            ).scalars().all()
            tasks = (
                await session.execute(select(AsyncPresentationGenerationTaskModel))
            ).scalars().all()

        assert len(presentations) == 2
        target = next(item for item in presentations if item.id != source_id)
        assert target.id == first.target_presentation_id
        assert target.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
        assert target.revision == 0
        assert len(conversations) == len(messages) == len(operations) == len(jobs) == len(tasks) == 1
        assert conversations[0].presentation_id == target.id
        assert messages[0].content == _request().prompt
        assert operations[0].presentation_id == target.id
        assert operations[0].kind == "template.apply"
        assert operations[0].expected_revision == 0
        assert operations[0].request_payload["source_presentation_id"] == str(source_id)
        assert operations[0].request_payload["source_revision"] == 7
        assert jobs[0].presentation_id == target.id
        assert jobs[0].workspace_operation_id == operations[0].id
        assert jobs[0].request_payload["presentation_id"] == str(target.id)

        async with maker() as session:
            staging_view = await get_presentation(target.id, session)
        assert staging_view.id == target.id
    finally:
        await engine.dispose()


def test_confirm_openapi_contract_uses_required_idempotency_header():
    app = FastAPI()
    app.include_router(workspace_endpoint.WORKSPACE_ROUTER)

    operation = app.openapi()["paths"][
        "/workspace/presentations/{source_presentation_id}/template-applications"
    ]["post"]

    header = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["in"] == "header" and parameter["name"] == "Idempotency-Key"
    )
    assert header["required"] is True
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkspaceTemplateApplicationRequest"
    }
    assert operation["responses"]["202"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/WorkspaceTemplateApplicationResponse"
    }


def test_target_cohort_remains_enabled_whenever_source_cohort_is_enabled(
    monkeypatch,
):
    monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ROLLOUT_SALT", "target-inheritance")
    source_id = uuid.uuid4()
    source_bucket = workspace_cohort_bucket(source_id)
    source_decision = WorkspaceWriteRolloutDecision(
        enabled=True,
        cohort_eligible=True,
        cohort_bucket=source_bucket,
        kill_switch_active=False,
        shadow_mode=False,
        reason=None,
    )

    target_id = workspace_endpoint._template_application_target_id(
        source_presentation_id=source_id,
        idempotency_key="stable-target-key",
        source_decision=source_decision,
    )

    target_bucket = workspace_cohort_bucket(target_id)
    assert target_id != source_id
    assert target_bucket <= source_bucket
    monkeypatch.setenv(
        "CHAT_FIRST_WORKSPACE_ROLLOUT_PERCENT",
        str((source_bucket + 1) / 100),
    )
    assert resolve_active_workspace_write_rollout(source_id).enabled is True
    assert resolve_active_workspace_write_rollout(target_id).enabled is True


def test_confirm_rejects_stale_source_revision_and_key_conflict(tmp_path: Path):
    asyncio.run(_confirm_rejects_stale_source_revision_and_key_conflict(tmp_path))


async def _confirm_rejects_stale_source_revision_and_key_conflict(tmp_path: Path):
    engine, maker = await _database(tmp_path / "conflicts.db")
    try:
        async with maker() as session:
            source = _source()
            session.add(source)
            await session.commit()
            source_id = source.id

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await confirm_authored_template_application(
                    session,
                    source_presentation_id=source_id,
                    request=_request(revision=6),
                    idempotency_key="stale",
                    generation_request_builder=_builder,
                )
        assert stale.value.code == "revision_conflict"

        async with maker() as session:
            await confirm_authored_template_application(
                session,
                source_presentation_id=source_id,
                request=_request(),
                idempotency_key="same-key",
                generation_request_builder=_builder,
            )
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as conflict:
                await confirm_authored_template_application(
                    session,
                    source_presentation_id=source_id,
                    request=_request(style="poster"),
                    idempotency_key="same-key",
                    generation_request_builder=_builder,
                )
        assert conflict.value.code == "idempotency_key_payload_conflict"
    finally:
        await engine.dispose()
