from __future__ import annotations

import asyncio
import uuid
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException, Request
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation_crud, presentation_generate
from enums.tone import Tone
from enums.verbosity import Verbosity
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
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services import workspace_asset_service, workspace_legacy_write_gate
from services.workspace_artifact_publication_service import (
    WorkspaceArtifactPublicationError,
    prepare_workspace_generation_artifact,
    publish_workspace_generation_artifact,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")
TABLES = [
    User.__table__,
    PresentationModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceAsset.__table__,
    WorkspaceMessageAttachment.__table__,
    WorkspaceOperation.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    WorkspaceArtifact.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
    PresentationGenerationJob.__table__,
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
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create(
    session,
    *,
    presentation_id: uuid.UUID,
    idempotency_key: str,
    content: str = "Build a durable strategy deck",
    theme: dict | None = None,
):
    return await presentation_crud.create_presentation(
        content=content,
        presentation_id=presentation_id,
        n_slides=8,
        language="en",
        file_paths=[],
        tone=Tone.PROFESSIONAL,
        verbosity=Verbosity.STANDARD,
        instructions="Use concise evidence",
        include_table_of_contents=True,
        include_title_slide=True,
        web_search=False,
        theme=theme,
        idempotency_key=idempotency_key,
        sql_session=session,
    )


def test_create_replay_is_single_turn_and_payload_mismatch_conflicts(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-replay.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"
        try:
            async with maker() as session:
                first = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                )
            async with maker() as session:
                replay = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                )
            assert first.id == replay.id == presentation_id

            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                assert operation.state == "awaiting_approval"
                assert operation.presentation_id == presentation_id

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=presentation_id,
                        idempotency_key=key,
                        content="A materially different request",
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "idempotency_key_payload_conflict"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_outline_stream_admits_a_staged_deck_at_full_rollout(
    tmp_path: Path,
    monkeypatch,
):
    """A fully enabled cohort must still be able to generate a new deck.

    ``legacy_write_requires_presentation_scope`` rejects before touching the
    database once the rollout reaches 100%, so calling it from the outline
    stream made the staging bootstrap allowance unreachable and left every new
    presentation stuck with no outline.
    """

    async def scenario():
        engine, maker = await _database(tmp_path / "outline-full-rollout.db")
        monkeypatch.setattr(
            presentation_crud, "_chat_first_create_enabled", lambda: True
        )
        presentation_id = uuid.uuid4()
        try:
            # The blanket gate the endpoint must not use rejects outright here.
            with pytest.raises(HTTPException) as preflight:
                workspace_legacy_write_gate.require_legacy_presentation_write_path()
            assert preflight.value.status_code == 409

            async with maker() as session:
                presentation = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                # The guard the endpoint actually relies on still admits it.
                assert (
                    await workspace_legacy_write_gate.require_outline_bootstrap_write_path(
                        session, presentation
                    )
                    is None
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_created_staging_deck_admits_outline_only_until_generation_activation(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "outline-bootstrap.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setenv("CHAT_FIRST_WORKSPACE_ENABLED", "true")
        monkeypatch.setenv(
            "CHAT_FIRST_WORKSPACE_RUNTIME_PROFILE",
            "sqlite-local",
        )
        presentation_id = uuid.uuid4()
        admit_outline = (
            workspace_legacy_write_gate.require_outline_bootstrap_write_path
        )
        try:
            async with maker() as session:
                presentation = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                assert (
                    await admit_outline(
                        session,
                        presentation,
                    )
                    is None
                )

                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                operation.state = "queued"
                session.add(operation)
                await session.commit()

            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                with pytest.raises(HTTPException) as caught:
                    await admit_outline(
                        session,
                        presentation,
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "chat_first_command_required"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_create_persists_theme_but_only_safe_theme_metadata_in_first_message(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-theme.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"
        theme = {
            "id": "customer-brand",
            "name": "Customer Brand",
            "description": "Approved customer palette",
            "user": "local",
            "logo": None,
            "logo_url": "/assets/brand-logo.png",
            "company_name": "Example Co",
            "data": {
                "colors": {"primary": "#123456"},
                "private_api_key": "must-never-appear-in-chat-metadata",
            },
        }
        try:
            async with maker() as session:
                first = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                    theme=theme,
                )
            assert first.theme == theme

            async with maker() as session:
                stored = await session.get(PresentationModel, presentation_id)
                message = (
                    await session.execute(select(WorkspaceMessage))
                ).scalar_one()
                assert stored is not None
                assert stored.theme == theme
                settings = message.message_metadata["generation_settings"]
                assert settings["theme_id"] == "customer-brand"
                assert settings["theme_name"] == "Customer Brand"
                assert settings["theme_source"] == "custom"
                assert "theme" not in settings
                assert "data" not in settings
                assert "logo_url" not in settings
                assert "private_api_key" not in str(message.message_metadata)

            async with maker() as session:
                replay = await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=key,
                    theme=theme,
                )
                assert replay.id == first.id

            changed_theme = {
                **theme,
                "data": {"colors": {"primary": "#654321"}},
            }
            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=presentation_id,
                        idempotency_key=key,
                        theme=changed_theme,
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "idempotency_key_payload_conflict"

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=uuid.uuid4(),
                        idempotency_key="presentation-create:invalid-theme",
                        theme={**theme, "unexpected_secret": "do-not-store"},
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "invalid_presentation_theme"

            oversized_theme = {
                **theme,
                "data": {"payload": "x" * (256 * 1024)},
            }
            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await _create(
                        session,
                        presentation_id=uuid.uuid4(),
                        idempotency_key="presentation-create:oversized-theme",
                        theme=oversized_theme,
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "presentation_theme_too_large"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_legacy_create_ignores_chat_first_theme_admission(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "legacy-create-theme.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: False,
        )
        try:
            async with maker() as session:
                created = await _create(
                    session,
                    presentation_id=uuid.uuid4(),
                    idempotency_key="legacy-create",
                    theme={"unexpected": "legacy clients do not admit themes"},
                )
            assert created.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
            assert created.theme is None
            async with maker() as session:
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_create_id_cannot_replay_across_owners(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-owner.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"
        try:
            async with maker() as session, session.begin():
                session.add_all(
                    [
                        User(id=OWNER_A, username="owner-a", hashed_password="x"),
                        User(id=OWNER_B, username="owner-b", hashed_password="x"),
                    ]
                )

            owner_a_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session:
                    await _create(
                        session,
                        presentation_id=presentation_id,
                        idempotency_key=key,
                    )
            finally:
                reset_current_owner_id(owner_a_token)

            owner_b_token = set_current_owner_id(OWNER_B)
            try:
                async with maker() as session:
                    with pytest.raises(HTTPException) as caught:
                        await _create(
                            session,
                            presentation_id=presentation_id,
                            idempotency_key=key,
                        )
                    assert caught.value.status_code == 409
                    assert caught.value.detail == "presentation_id_unavailable"
            finally:
                reset_current_owner_id(owner_b_token)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_create_copies_attachments_to_durable_workspace_storage(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "create-attachment.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_crud,
            "validate_document_paths",
            lambda paths: list(paths),
        )
        asset_root = tmp_path / "workspace-assets"
        monkeypatch.setattr(
            workspace_asset_service,
            "get_writable_path",
            lambda _name: str(asset_root),
        )
        source = tmp_path / "evidence.txt"
        source.write_text("durable evidence", encoding="utf-8")
        presentation_id = uuid.uuid4()
        key = f"presentation-create:{presentation_id}"

        async def create_with_source(session):
            return await presentation_crud.create_presentation(
                content="Use the attached evidence",
                presentation_id=presentation_id,
                n_slides=6,
                language="en",
                file_paths=[str(source)],
                tone=Tone.PROFESSIONAL,
                verbosity=Verbosity.STANDARD,
                instructions=None,
                include_table_of_contents=False,
                include_title_slide=True,
                web_search=False,
                idempotency_key=key,
                sql_session=session,
            )

        try:
            async with maker() as session:
                created = await create_with_source(session)
            assert created.file_paths is not None
            durable_path = Path(created.file_paths[0])
            assert durable_path != source
            assert durable_path.read_text(encoding="utf-8") == "durable evidence"

            # A lost create response may be retried with the same client IDs.
            # It must bind to the original asset instead of copying a second one.
            async with maker() as session:
                replay = await create_with_source(session)
            assert replay.id == created.id

            async with maker() as session:
                asset = (await session.execute(select(WorkspaceAsset))).scalar_one()
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                assert asset.state == "ready"
                assert asset.sha256 == sha256(b"durable evidence").hexdigest()
                assert str(asset.id) in operation.request_payload["asset_ids"]

            source.write_text("different evidence", encoding="utf-8")
            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await create_with_source(session)
                assert caught.value.status_code == 409
                assert caught.value.detail == "idempotency_key_payload_conflict"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_generate_activates_and_links_bootstrap_operation(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "activate-generation.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "wake_workspace_operation_runner",
            lambda: None,
        )

        async def accept_request(_request, _session):
            return (uuid.uuid4(),)

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            accept_request,
        )
        presentation_id = uuid.uuid4()
        create_key = f"presentation-create:{presentation_id}"
        generate_key = f"presentation-generate:{presentation_id}"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Build a durable strategy deck",
            n_slides=8,
            language="en",
            strategy="adaptive",
            files=["C:/temporary-upload/source.txt"],
        )
        validated_files: list[list[str]] = []

        async def load_document_context(paths, _language):
            validated_files.append(paths)
            return "durable context"

        monkeypatch.setattr(
            presentation_generate,
            "_load_document_context",
            load_document_context,
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=create_key,
                )
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation is not None
                presentation.file_paths = ["C:/workspace-assets/durable-source.txt"]
                await session.commit()
            async with maker() as session:
                first_status = await presentation_generate.queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    export_cookie_header="session=must-not-persist",
                    idempotency_key=generate_key,
                )
            async with maker() as session:
                replay_status = await presentation_generate.queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    export_cookie_header="session=must-not-persist",
                    idempotency_key=generate_key,
                )
            assert replay_status.id == first_status.id

            async with maker() as session:
                persisted_presentation = await session.get(
                    PresentationModel,
                    presentation_id,
                )
                assert persisted_presentation is not None
                assert persisted_presentation.file_paths is None
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                job = (
                    await session.execute(select(PresentationGenerationJob))
                ).scalar_one()
                status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    job.task_id,
                )
                assert status is not None
                assert operation.state == "queued"
                assert operation.request_payload["job_id"] == str(job.id)
                assert job.workspace_operation_id == operation.id
                assert job.presentation_id == presentation_id
                assert job.export_cookie_required is False
                assert job.request_payload["files"] is None
                assert "C:/workspace-assets/durable-source.txt" not in str(
                    job.request_payload
                )
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation is not None
                assert presentation.file_paths is None
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1
            assert validated_files == [["C:/workspace-assets/durable-source.txt"]]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_generate_admission_rejection_is_atomic_and_replays_failed_task(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "admission-rejected.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "wake_workspace_operation_runner",
            lambda: None,
        )
        validation_calls = 0

        async def reject_request(_request, _session):
            nonlocal validation_calls
            validation_calls += 1
            raise HTTPException(
                status_code=422,
                detail="template_v2_generation_invalid",
            )

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            reject_request,
        )
        presentation_id = uuid.uuid4()
        generate_key = f"presentation-generate:{presentation_id}"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Keep this first message after rejected admission",
            n_slides=8,
            language="en",
            strategy="adaptive",
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                    content=request.content,
                )

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=generate_key,
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "template_v2_generation_invalid"
                assert caught.value.headers == {
                    presentation_generate.CHAT_FIRST_WORKSPACE_PROJECTED_HEADER: "true"
                }

            async with maker() as session:
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                job = (
                    await session.execute(select(PresentationGenerationJob))
                ).scalar_one()
                status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    job.task_id,
                )
                event_row = (
                    await session.execute(
                        select(WorkspaceOperationEvent).where(
                            WorkspaceOperationEvent.operation_id == operation.id,
                            WorkspaceOperationEvent.event_type == "operation.error",
                        )
                    )
                ).scalar_one()
                outbox = (
                    await session.execute(
                        select(WorkspaceOutbox).where(
                            WorkspaceOutbox.operation_id == operation.id,
                            WorkspaceOutbox.event_cursor == event_row.cursor,
                        )
                    )
                ).scalar_one()
                first_message = (
                    await session.execute(select(WorkspaceMessage))
                ).scalar_one()

                assert first_message.role == "user"
                assert first_message.content == request.content
                assert first_message.message_metadata is not None
                assert first_message.message_metadata["generation_settings"] == {
                    "n_slides": 8,
                    "language": "en",
                    "tone": "professional",
                    "verbosity": "standard",
                    "instructions": "Use concise evidence",
                    "include_table_of_contents": True,
                    "include_title_slide": True,
                    "web_search": False,
                }
                assert operation.state == "failed"
                assert operation.error_code == "template_v2_generation_invalid"
                assert operation.error_detail == {
                    "code": "template_v2_generation_invalid",
                    "detail": "template_v2_generation_invalid",
                    "status_code": 422,
                    "retryable": False,
                    "stage": "admission",
                }
                assert operation.request_payload["job_id"] == str(job.id)
                assert job.workspace_operation_id == operation.id
                assert job.state == "failed"
                assert job.current_stage == "admission"
                assert job.resume_allowed is False
                assert job.request_payload[
                    presentation_generate._INTERNAL_ADMISSION_RETRY_KEY
                ] is True
                assert status is not None
                assert status.status == "error"
                assert status.error == operation.error_detail
                assert event_row.payload["retryable"] is False
                assert outbox.topic == "workspace.operation.event"
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1

            # A response-loss replay with the same key returns the durable
            # failure. It must not repeat validation or create another turn,
            # task, job, event, or outbox record.
            async with maker() as session:
                replay = await presentation_generate.queue_presentation_generation(
                    request,
                    BackgroundTasks(),
                    session,
                    idempotency_key=generate_key,
                )
                assert replay.status == "error"
                assert replay.error is not None
                assert replay.error["code"] == "template_v2_generation_invalid"
                assert await session.scalar(
                    select(func.count()).select_from(PresentationGenerationJob)
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(
                        AsyncPresentationGenerationTaskModel
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceOperationEvent)
                    .where(
                        WorkspaceOperationEvent.event_type == "operation.error"
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count())
                    .select_from(WorkspaceOutbox)
                    .where(
                        WorkspaceOutbox.event_cursor == event_row.cursor,
                        WorkspaceOutbox.operation_id == operation.id,
                    )
                ) == 1
                assert await session.scalar(
                    select(func.count()).select_from(WorkspaceMessage)
                ) == 1
            assert validation_calls == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_raw_path_admission_failure_requires_reattach_without_persisting_locator(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "admission-retry.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "wake_workspace_operation_runner",
            lambda: None,
        )

        async def accept_request(_request, _session):
            return (uuid.uuid4(),)

        async def unavailable_documents(_paths, _language):
            raise RuntimeError("private loader detail must not be persisted")

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            accept_request,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_load_document_context",
            unavailable_documents,
        )
        presentation_id = uuid.uuid4()
        durable_path = "C:/workspace-assets/retry-source.txt"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Retry document admission safely",
            n_slides=8,
            language="en",
            strategy="adaptive",
            files=["C:/temporary-upload/source.txt"],
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                presentation = await session.get(PresentationModel, presentation_id)
                assert presentation is not None
                presentation.file_paths = [durable_path]
                await session.commit()

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=f"presentation-generate:{presentation_id}",
                    )
                assert caught.value.status_code == 409
                assert caught.value.detail == "legacy_file_reference_reattach_required"
                assert "private loader detail" not in str(caught.value.detail)
                assert caught.value.headers == {
                    presentation_generate.CHAT_FIRST_WORKSPACE_PROJECTED_HEADER: "true"
                }

            async with maker() as session:
                operation = (
                    await session.execute(select(WorkspaceOperation))
                ).scalar_one()
                job = (
                    await session.execute(select(PresentationGenerationJob))
                ).scalar_one()
                status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    job.task_id,
                )
                assert status is not None
                assert operation.state == "failed"
                assert operation.error_code == "legacy_file_reference_reattach_required"
                assert operation.error_detail is not None
                assert operation.error_detail["retryable"] is False
                assert "private loader detail" not in str(operation.error_detail)
                assert job.state == "failed"
                assert job.resume_allowed is False
                assert job.request_payload["files"] is None
                assert durable_path not in str(job.request_payload)

                restored = presentation_generate._request_from_durable_payload(
                    job.request_payload
                )
                assert restored.files is None

                async def retry_rejected(_request, _session):
                    raise HTTPException(
                        status_code=422,
                        detail="template_v2_generation_invalid",
                    )

                monkeypatch.setattr(
                    presentation_generate,
                    "check_if_api_request_is_valid",
                    retry_rejected,
                )
                with pytest.raises(
                    presentation_generate.GenerationAdmissionRetryError
                ) as caught:
                    await presentation_generate.generate_presentation_handler(
                        restored,
                        presentation_id,
                        async_status=status,
                        generation_job=job,
                        sql_session=session,
                    )
                assert caught.value.code == "template_v2_generation_invalid"
                assert caught.value.status_code == 422
                assert caught.value.retryable is False
                await session.refresh(status)
                assert status.status == "error"
                assert status.error == caught.value.detail
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_admission_projection_race_does_not_mark_the_response(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "admission-projection-race.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )

        async def reject_request(_request, _session):
            raise HTTPException(
                status_code=422,
                detail="template_v2_generation_invalid",
            )

        async def lose_projection_race(*_args, **_kwargs):
            return False

        monkeypatch.setattr(
            presentation_generate,
            "check_if_api_request_is_valid",
            reject_request,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_record_chat_first_generation_admission_failure",
            lose_projection_race,
        )
        presentation_id = uuid.uuid4()
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Do not claim a projection that lost its race",
            n_slides=8,
            language="en",
            strategy="adaptive",
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=f"presentation-generate:{presentation_id}",
                    )
                assert caught.value.status_code == 422
                assert caught.value.detail == "template_v2_generation_invalid"
                assert not caught.value.headers
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_generate_async_wraps_unprojected_non_http_failure_without_marker(
    monkeypatch,
):
    async def fail_before_projection(*_args, **_kwargs):
        raise RuntimeError("private admission implementation detail")

    monkeypatch.setattr(
        presentation_generate,
        "queue_presentation_generation",
        fail_before_projection,
    )

    async def scenario():
        request_http = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/ppt/presentation/generate/async",
                "raw_path": b"/api/v1/ppt/presentation/generate/async",
                "query_string": b"",
                "headers": [],
                "client": ("test", 1234),
                "server": ("test", 80),
            }
        )
        request_http.state.request_id = "request-before-projection"
        request = GeneratePresentationRequest(
            presentation_id=uuid.uuid4(),
            content="Fail safely before projection",
            n_slides=8,
            language="en",
        )
        with pytest.raises(HTTPException) as caught:
            await presentation_generate.generate_presentation_async(
                request_http,
                request,
                BackgroundTasks(),
                idempotency_key="unprojected-failure",
                sql_session=object(),
            )
        assert caught.value.status_code == 500
        assert caught.value.detail == "Presentation generation failed"
        assert "private admission implementation detail" not in str(
            caught.value.detail
        )
        assert not caught.value.headers

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status_code", "detail"),
    (
        (400, "invalid_generation_request"),
        (401, "not_authenticated"),
        (403, "generation_forbidden"),
        (404, "presentation_not_found"),
        (409, "idempotency_key_payload_conflict"),
        (409, "idempotency_record_incomplete"),
    ),
)
def test_generate_async_preserves_unprojected_http_failure_without_marker(
    monkeypatch,
    status_code: int,
    detail: str,
):
    async def fail_before_projection(*_args, **_kwargs):
        raise HTTPException(status_code=status_code, detail=detail)

    monkeypatch.setattr(
        presentation_generate,
        "queue_presentation_generation",
        fail_before_projection,
    )

    async def scenario():
        request_http = Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/api/v1/ppt/presentation/generate/async",
                "raw_path": b"/api/v1/ppt/presentation/generate/async",
                "query_string": b"",
                "headers": [],
                "client": ("test", 1234),
                "server": ("test", 80),
            }
        )
        request = GeneratePresentationRequest(
            presentation_id=uuid.uuid4(),
            content="Fail before a workspace projection",
            n_slides=8,
            language="en",
        )
        with pytest.raises(HTTPException) as caught:
            await presentation_generate.generate_presentation_async(
                request_http,
                request,
                BackgroundTasks(),
                idempotency_key="unprojected-http-failure",
                sql_session=object(),
            )
        assert caught.value.status_code == status_code
        assert caught.value.detail == detail
        assert not caught.value.headers

    asyncio.run(scenario())


def test_chat_first_generate_rejects_unlinked_legacy_idempotency_replay(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "unlinked-generation-replay.db")
        monkeypatch.setattr(
            presentation_crud,
            "_chat_first_create_enabled",
            lambda: True,
        )
        monkeypatch.setattr(
            presentation_generate,
            "_chat_first_generation_enabled",
            lambda: True,
        )
        presentation_id = uuid.uuid4()
        generate_key = f"presentation-generate:{presentation_id}"
        request = GeneratePresentationRequest(
            presentation_id=presentation_id,
            content="Build a durable strategy deck",
            n_slides=8,
            language="en",
            strategy="adaptive",
        )
        try:
            async with maker() as session:
                await _create(
                    session,
                    presentation_id=presentation_id,
                    idempotency_key=f"presentation-create:{presentation_id}",
                )
                task = AsyncPresentationGenerationTaskModel(
                    status="pending",
                    message="Legacy queue",
                )
                session.add(task)
                await session.flush()
                session.add(
                    PresentationGenerationJob(
                        task_id=task.id,
                        presentation_id=presentation_id,
                        workspace_operation_id=None,
                        idempotency_key_hash=(
                            presentation_generate.idempotency_key_hash(generate_key)
                        ),
                        request_sha256=presentation_generate.request_sha256(
                            presentation_generate._durable_request_payload(request)
                        ),
                        request_payload=(
                            presentation_generate._durable_request_payload(request)
                        ),
                        export_cookie_required=False,
                    )
                )
                await session.commit()

            async with maker() as session:
                with pytest.raises(HTTPException) as caught:
                    await presentation_generate.queue_presentation_generation(
                        request,
                        BackgroundTasks(),
                        session,
                        idempotency_key=generate_key,
                    )
                assert caught.value.status_code == 409
                assert (
                    caught.value.detail
                    == "chat_first_idempotency_record_unlinked"
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


async def _seed_running_operation(
    maker,
    *,
    kind="presentation.generate",
    request_payload=None,
):
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="artifact publication",
            n_slides=2,
            language="en",
            lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
            revision=0,
        )
        session.add(presentation)
        await session.flush()
        conversation = WorkspaceConversation(presentation_id=presentation.id)
        session.add(conversation)
        await session.flush()
        operation = WorkspaceOperation(
            conversation_id=conversation.id,
            presentation_id=presentation.id,
            anchor_position=1,
            kind=kind,
            state="running",
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            request_payload=request_payload or {},
            expected_revision=0,
            attempt_token="attempt-1",
        )
        session.add(operation)
        await session.flush()
        task = AsyncPresentationGenerationTaskModel(
            status="processing",
            message="Publishing presentation",
        )
        session.add(task)
        await session.flush()
        session.add(
            PresentationGenerationJob(
                task_id=task.id,
                presentation_id=presentation.id,
                workspace_operation_id=operation.id,
                request_sha256="c" * 64,
                request_payload={},
                state="running",
                attempt_number=1,
                attempt_token="attempt-1",
            )
        )
    return presentation.id, operation.id


def test_artifact_publication_hashes_and_commits_one_revision(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "artifact-success.db")
        app_data = tmp_path / "app-data"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        payload = b"immutable-presentation-bytes"
        export.write_bytes(payload)
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            assert artifact.sha256 == sha256(payload).hexdigest()
            assert artifact.size_bytes == len(payload)
            async with maker() as session:
                revision = await publish_workspace_generation_artifact(
                    session,
                    artifact=artifact,
                    expected_revision=0,
                    attempt_token="attempt-1",
                )
            assert revision == 1

            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                stored_artifact = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
                assert presentation.revision == 1
                assert operation is not None
                assert operation.state == "succeeded"
                assert operation.result_revision == 1
                assert operation.result == artifact.result_payload()
                assert stored_artifact is not None
                assert stored_artifact.status == "ready"
                assert artifact.absolute_path.read_bytes() == payload
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_template_application_publication_completes_same_target_with_source_metadata(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "template-application-publish.db")
        app_data = tmp_path / "app-data-template-application"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        export.write_bytes(b"target-owned-template-application")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        source_id = uuid.uuid4()
        try:
            presentation_id, operation_id = await _seed_running_operation(
                maker,
                kind="template.apply",
                request_payload={
                    "generation_settings": {
                        "source_presentation_id": str(source_id),
                        "source_revision": 7,
                        "authored_style": "signal",
                        "vision_qa": True,
                    }
                },
            )
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                revision = await publish_workspace_generation_artifact(
                    session,
                    artifact=artifact,
                    expected_revision=0,
                    attempt_token="attempt-1",
                )
            assert revision == 1

            async with maker() as session:
                target = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
            assert target is not None
            assert target.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
            assert target.revision == 1
            assert operation is not None
            assert operation.presentation_id == presentation_id
            assert operation.result == {
                **artifact.result_payload(),
                "kind": "template.apply",
                "source_presentation_id": str(source_id),
                "source_revision": 7,
                "target_presentation_id": str(presentation_id),
                "presentation_id": str(presentation_id),
                "authored_style": "signal",
            }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_db_commit_compensates_exact_promoted_artifact(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "artifact-compensation.db")
        app_data = tmp_path / "app-data"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        export.write_bytes(b"partial external write")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                async def fail_commit():
                    raise RuntimeError("simulated commit failure")

                monkeypatch.setattr(session, "commit", fail_commit)
                with pytest.raises(RuntimeError, match="simulated commit failure"):
                    await publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="attempt-1",
                    )
            assert not artifact.absolute_path.exists()

            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
                assert presentation.revision == 0
                assert operation is not None
                assert operation.state == "running"
                stored_artifact = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                assert stored_artifact is not None
                assert stored_artifact.status == "deleted"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_stale_attempt_never_publishes_artifact(tmp_path: Path, monkeypatch):
    async def scenario():
        engine, maker = await _database(tmp_path / "artifact-stale.db")
        app_data = tmp_path / "app-data"
        export = app_data / "exports" / "deck.pptx"
        export.parent.mkdir(parents=True)
        export.write_bytes(b"stale attempt")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(export),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                with pytest.raises(WorkspaceArtifactPublicationError) as caught:
                    await publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="stale-attempt",
                    )
                assert caught.value.code == "operation_lease_lost"
            assert not artifact.absolute_path.exists()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
