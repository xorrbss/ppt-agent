from __future__ import annotations

import asyncio
import uuid
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
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
    WorkspaceArtifact,
    WorkspaceConversation,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services import authored_presentation_service as authored
from services.authored_checkpoint_service import AuthoredStageError
from utils.llm_calls.author_deck import AuthoredDeckResult


TABLES = [
    User.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    WorkspaceArtifact.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
]

ATTEMPT_TOKEN = "authored-attempt-1"
HTMLS = [
    (
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head>"
        f"<body><h1>Durable slide {index}</h1></body></html>"
    )
    for index in (1, 2)
]
PNGS = [b"rendered-png-one", b"rendered-png-two"]
EXPORT_BYTES = b"durable-authored-pptx"


async def _database(path: Path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"timeout": 10},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_publication(
    maker,
    *,
    operation_attempt_token: str = ATTEMPT_TOKEN,
):
    presentation_id = uuid.uuid4()
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            id=presentation_id,
            content="Durable authored workbench",
            n_slides=2,
            language="English",
            lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
            revision=0,
        )
        session.add(presentation)
        await session.flush()

        conversation = WorkspaceConversation(
            presentation_id=presentation_id,
            next_position=2,
        )
        session.add(conversation)
        await session.flush()

        operation = WorkspaceOperation(
            conversation_id=conversation.id,
            presentation_id=presentation_id,
            anchor_position=1,
            kind="presentation.generate",
            state="running",
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            request_payload={"template": "authored"},
            expected_revision=0,
            attempt_number=1,
            attempt_token=operation_attempt_token,
            fencing_token=1,
        )
        session.add(operation)
        await session.flush()

        task = AsyncPresentationGenerationTaskModel(
            status="processing",
            message="Generating authored presentation",
        )
        session.add(task)
        await session.flush()

        job = PresentationGenerationJob(
            task_id=task.id,
            presentation_id=presentation_id,
            workspace_operation_id=operation.id,
            idempotency_key_hash="c" * 64,
            request_id="authored-sqlite-publication",
            request_sha256="d" * 64,
            request_payload={"template": "authored"},
            state="running",
            attempt_number=1,
            attempt_token=ATTEMPT_TOKEN,
            total_slides=2,
            current_stage="render",
        )
        session.add(job)
        await session.flush()
        return presentation_id, operation.id, task.id, job.id


def _request(presentation_id: uuid.UUID) -> GeneratePresentationRequest:
    return GeneratePresentationRequest(
        presentation_id=presentation_id,
        content="Durable authored workbench",
        n_slides=2,
        language="English",
        template="authored",
        strategy="authored",
        vision_qa=False,
    )


def _outline() -> PresentationOutlineModel:
    return PresentationOutlineModel(
        slides=[
            SlideOutlineModel(content="Publication contract"),
            SlideOutlineModel(content="Recovery contract"),
        ]
    )


def _mock_external_generation(monkeypatch, app_data: Path) -> None:
    async def fake_pipeline(*_args, **_kwargs):
        return authored.AuthoredGenerationPipelineResult(
            deck=AuthoredDeckResult(
                htmls=HTMLS,
                design_system="SQLite publication integration fixture",
            ),
            pngs=PNGS,
            elapsed=0.01,
            author_work=0.01,
            illustration_work=0.0,
            render_work=0.01,
        )

    def fake_export(*_args, **_kwargs):
        path = app_data / "exports" / "authored-publication.pptx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(EXPORT_BYTES)
        return str(path)

    async def skip_checkpoint_cleanup(_job_id):
        return None

    monkeypatch.setattr(authored, "find_chrome", lambda: "fixture-chrome")
    monkeypatch.setattr(
        authored,
        "_author_illustrate_render_pipeline",
        fake_pipeline,
    )
    monkeypatch.setattr(authored, "_build_authored_export", fake_export)
    monkeypatch.setattr(
        authored,
        "_cleanup_published_checkpoints",
        skip_checkpoint_cleanup,
    )


def test_authored_workspace_publication_commits_one_sqlite_tuple(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        app_data = tmp_path / "app-data"
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        monkeypatch.setenv("LLM", "openai")
        _mock_external_generation(monkeypatch, app_data)
        engine, maker = await _database(tmp_path / "authored-success.db")
        try:
            presentation_id, operation_id, task_id, job_id = (
                await _seed_publication(maker)
            )
            async with maker() as session:
                job = await session.get(PresentationGenerationJob, job_id)
                assert job is not None
                result = await authored.generate_authored_presentation(
                    _request(presentation_id),
                    presentation_id,
                    _outline(),
                    "English",
                    session,
                    job,
                )

            # A brand-new connection is the durable truth. Every public pointer,
            # terminal projection, and notification must be visible together.
            async with maker() as verification:
                presentation = await verification.get(
                    PresentationModel,
                    presentation_id,
                )
                operation = await verification.get(WorkspaceOperation, operation_id)
                task = await verification.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                job = await verification.get(PresentationGenerationJob, job_id)
                slides = (
                    await verification.execute(
                        select(SlideModel)
                        .where(SlideModel.presentation == presentation_id)
                        .order_by(SlideModel.index)
                    )
                ).scalars().all()
                artifact = (
                    await verification.execute(
                        select(WorkspaceArtifact).where(
                            WorkspaceArtifact.operation_id == operation_id
                        )
                    )
                ).scalar_one()
                terminal_event = (
                    await verification.execute(
                        select(WorkspaceOperationEvent).where(
                            WorkspaceOperationEvent.operation_id == operation_id,
                            WorkspaceOperationEvent.event_type == "operation.result",
                        )
                    )
                ).scalar_one()
                outbox = (
                    await verification.execute(
                        select(WorkspaceOutbox).where(
                            WorkspaceOutbox.operation_id == operation_id,
                            WorkspaceOutbox.event_cursor == terminal_event.cursor,
                        )
                    )
                ).scalar_one()

            assert presentation is not None
            assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
            assert presentation.revision == 1
            assert presentation.mode == "authored"
            assert len(slides) == 2
            assert [slide.index for slide in slides] == [0, 1]
            assert [slide.html_content for slide in slides] == [
                authored.ensure_authored_qids(html) for html in HTMLS
            ]
            image_refs = [slide.content["image"] for slide in slides]
            assert all("/sets/" in image_ref for image_ref in image_refs)
            assert [
                (app_data / "images" / image_ref).read_bytes()
                for image_ref in image_refs
            ] == PNGS

            assert artifact.status == "ready"
            assert artifact.source_revision == 1
            assert artifact.sha256 == sha256(EXPORT_BYTES).hexdigest()
            assert artifact.size_bytes == len(EXPORT_BYTES)
            artifact_path = app_data.joinpath(*Path(artifact.storage_key).parts)
            assert artifact_path.read_bytes() == EXPORT_BYTES

            assert operation is not None
            assert operation.state == "succeeded"
            assert operation.result_revision == 1
            assert operation.attempt_token is None
            assert operation.result == {
                "artifact_id": str(artifact.id),
                "artifact_kind": artifact.kind,
                "artifact_sha256": artifact.sha256,
                "artifact_size_bytes": artifact.size_bytes,
                "path": f"/app_data/{artifact.storage_key}",
            }
            assert result.path == operation.result["path"]

            assert job is not None
            assert job.state == "published"
            assert job.attempt_token is None
            assert job.current_stage == "completed"
            assert task is not None
            assert task.status == "completed"
            assert task.error is None
            assert task.data == result.model_dump(mode="json")

            assert terminal_event.sequence == 1
            assert terminal_event.payload["state"] == "succeeded"
            assert terminal_event.payload["artifact_id"] == str(artifact.id)
            assert outbox.topic == "workspace.operation.event"
            assert outbox.status == "pending"
            assert outbox.payload["event_cursor"] == terminal_event.cursor
            assert outbox.payload["status"] == "succeeded"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_authored_workspace_fence_loss_preserves_hidden_sqlite_state(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        app_data = tmp_path / "app-data"
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        monkeypatch.setenv("LLM", "openai")
        _mock_external_generation(monkeypatch, app_data)
        engine, maker = await _database(tmp_path / "authored-fence-loss.db")
        try:
            presentation_id, operation_id, task_id, job_id = (
                await _seed_publication(
                    maker,
                    operation_attempt_token="new-operation-owner",
                )
            )
            async with maker() as session:
                job = await session.get(PresentationGenerationJob, job_id)
                assert job is not None
                with pytest.raises(AuthoredStageError) as caught:
                    await authored.generate_authored_presentation(
                        _request(presentation_id),
                        presentation_id,
                        _outline(),
                        "English",
                        session,
                        job,
                    )
                assert caught.value.code == "authored_attempt_lost"

            async with maker() as verification:
                presentation = await verification.get(
                    PresentationModel,
                    presentation_id,
                )
                operation = await verification.get(WorkspaceOperation, operation_id)
                task = await verification.get(
                    AsyncPresentationGenerationTaskModel,
                    task_id,
                )
                job = await verification.get(PresentationGenerationJob, job_id)
                slides = (
                    await verification.execute(
                        select(SlideModel).where(
                            SlideModel.presentation == presentation_id
                        )
                    )
                ).scalars().all()
                artifacts = (
                    await verification.execute(
                        select(WorkspaceArtifact).where(
                            WorkspaceArtifact.operation_id == operation_id
                        )
                    )
                ).scalars().all()
                events = (
                    await verification.execute(
                        select(WorkspaceOperationEvent).where(
                            WorkspaceOperationEvent.operation_id == operation_id
                        )
                    )
                ).scalars().all()
                outbox = (
                    await verification.execute(
                        select(WorkspaceOutbox).where(
                            WorkspaceOutbox.operation_id == operation_id
                        )
                    )
                ).scalars().all()

            assert presentation is not None
            assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
            assert presentation.revision == 0
            assert presentation.mode is None
            assert slides == []
            assert operation is not None
            assert operation.state == "running"
            assert operation.attempt_token == "new-operation-owner"
            assert operation.result is None
            assert operation.result_revision is None
            assert job is not None
            assert job.state == "running"
            assert job.attempt_token == ATTEMPT_TOKEN
            assert task is not None
            assert task.status == "processing"
            assert task.data is None
            assert events == []
            assert outbox == []

            assert len(artifacts) == 1
            assert artifacts[0].status == "deleted"
            artifact_path = app_data.joinpath(*Path(artifacts[0].storage_key).parts)
            assert not artifact_path.exists()
            image_sets = app_data / "images" / "authored" / str(presentation_id) / "sets"
            assert not image_sets.exists() or not any(image_sets.iterdir())
        finally:
            await engine.dispose()

    asyncio.run(scenario())
