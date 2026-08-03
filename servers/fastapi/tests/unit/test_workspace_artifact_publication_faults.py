from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
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
from services import workspace_artifact_publication_service as publication
from services import workspace_operation_runner
from api.v1.ppt.endpoints.presentation_generate import _publish_staged_presentation
from services.account_deletion_service import mark_account_deleting
from utils.owner_context import reset_current_owner_id, set_current_owner_id


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


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_running_operation(maker, *, owner_id: uuid.UUID | None = None):
    async with maker() as session, session.begin():
        if owner_id is not None:
            session.add(
                User(
                    id=owner_id,
                    username=f"owner-{owner_id.hex}",
                    hashed_password="test-only",
                )
            )
            await session.flush()
        presentation = PresentationModel(
            content="artifact fault injection",
            n_slides=1,
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
            kind="presentation.generate",
            state="running",
            idempotency_key_hash="a" * 64,
            request_hash="b" * 64,
            request_payload={},
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


def _artifact_files(app_data: Path) -> list[Path]:
    artifact_root = app_data / "workspace-artifacts"
    if not artifact_root.exists():
        return []
    return [path for path in artifact_root.rglob("*") if path.is_file()]


@pytest.mark.parametrize(
    "fault_stage",
    ["copy", "file_fsync", "promote", "promote_after_link", "directory_fsync"],
)
def test_prepare_failure_never_leaves_a_promoted_or_temporary_artifact(
    tmp_path: Path,
    monkeypatch,
    fault_stage: str,
):
    async def scenario():
        engine, maker = await _database(tmp_path / f"{fault_stage}.db")
        app_data = tmp_path / fault_stage
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"complete export")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

        def fail(*_args, **_kwargs):
            raise OSError(f"injected {fault_stage} failure")

        if fault_stage == "copy":
            monkeypatch.setattr(publication, "_copy_and_hash", fail)
        elif fault_stage == "file_fsync":
            monkeypatch.setattr(publication, "_fsync_output", fail)
        elif fault_stage == "promote":
            monkeypatch.setattr(publication, "_atomic_promote", fail)
        elif fault_stage == "promote_after_link":
            def promote_then_fail(temporary: Path, destination: Path) -> None:
                os.link(temporary, destination)
                fail()

            monkeypatch.setattr(publication, "_atomic_promote", promote_then_fail)
        else:
            monkeypatch.setattr(publication, "_fsync_directory", fail)

        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                with pytest.raises(
                    OSError,
                    match=f"injected {fault_stage} failure",
                ):
                    await publication.prepare_workspace_generation_artifact(
                        session,
                        source_path=str(source),
                        operation_id=operation_id,
                        presentation_id=presentation_id,
                        source_revision=1,
                    )

            assert source.read_bytes() == b"complete export"
            assert _artifact_files(app_data) == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_prepare_failure_never_commits_pending_caller_publication_uow(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "caller-uow-isolation.db")
        app_data = tmp_path / "caller-uow-isolation-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"complete export")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

        def fail_copy(*_args, **_kwargs):
            raise OSError("injected copy failure")

        monkeypatch.setattr(publication, "_copy_and_hash", fail_copy)
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as caller:
                presentation = await caller.get(PresentationModel, presentation_id)
                assert presentation is not None
                presentation.content = "pending caller content"
                presentation.title = "pending caller title"
                caller.add(
                    SlideModel(
                        presentation=presentation_id,
                        layout_group="authored",
                        layout="authored:cover",
                        index=0,
                        content={"pending": True},
                        html_content="<html><body>pending</body></html>",
                        properties={},
                    )
                )

                with pytest.raises(OSError, match="injected copy failure"):
                    await publication.prepare_workspace_generation_artifact(
                        caller,
                        source_path=str(source),
                        operation_id=operation_id,
                        presentation_id=presentation_id,
                        source_revision=1,
                    )

                # The durable artifact intent used another transaction.  It must
                # not flush or commit the caller's pending presentation/slides.
                async with maker() as observer:
                    stored = await observer.get(PresentationModel, presentation_id)
                    slides = (
                        await observer.execute(
                            select(SlideModel).where(
                                SlideModel.presentation == presentation_id
                            )
                        )
                    ).scalars().all()
                    artifact = (
                        await observer.execute(select(WorkspaceArtifact))
                    ).scalar_one()
                    assert stored is not None
                    assert stored.content == "artifact fault injection"
                    assert stored.title is None
                    assert slides == []
                    assert artifact.status == "deleted"

                await caller.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_task_cancellation_during_preparation_compensates_and_propagates(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "prepare-cancelled.db")
        app_data = tmp_path / "prepare-cancelled-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"cancel before publication")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

        def cancel_after_promotion(_directory: Path) -> None:
            raise asyncio.CancelledError()

        monkeypatch.setattr(
            publication,
            "_fsync_directory",
            cancel_after_promotion,
        )
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                with pytest.raises(asyncio.CancelledError):
                    await publication.prepare_workspace_generation_artifact(
                        session,
                        source_path=str(source),
                        operation_id=operation_id,
                        presentation_id=presentation_id,
                        source_revision=1,
                    )

            assert _artifact_files(app_data) == []
            async with maker() as session:
                stored = (
                    await session.execute(select(WorkspaceArtifact))
                ).scalar_one()
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert stored.status == "deleted"
                assert presentation is not None
                assert presentation.revision == 0
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
                assert operation is not None
                assert operation.state == "running"
                assert (
                    await session.execute(select(WorkspaceOperationEvent))
                ).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cancel_request_after_staging_fences_publication_and_compensates(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "cancel-after-staging.db")
        app_data = tmp_path / "cancel-after-staging-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"staged before cancellation")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )

            assert await workspace_operation_runner.request_workspace_operation_cancellation(
                operation_id,
                session_factory=maker,
            )
            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError
                ) as caught:
                    await publication.publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="attempt-1",
                    )
                assert caught.value.code == "operation_lease_lost"

            assert not artifact.absolute_path.exists()
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                event_types = list(
                    await session.scalars(
                        select(WorkspaceOperationEvent.event_type).where(
                            WorkspaceOperationEvent.operation_id == operation_id
                        )
                    )
                )
                assert stored is not None
                assert stored.status == "deleted"
                assert presentation is not None
                assert presentation.revision == 0
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
                assert operation is not None
                assert operation.state == "cancelling"
                assert "operation.cancelling" in event_types
                assert "operation.result" not in event_types
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_account_deletion_marker_fences_generation_publication_and_compensates(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "account-deletion-fence.db")
        app_data = tmp_path / "account-deletion-fence-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"must not publish after account deletion")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        owner_id = uuid.uuid4()
        owner_token = set_current_owner_id(owner_id)
        try:
            presentation_id, operation_id = await _seed_running_operation(
                maker,
                owner_id=owner_id,
            )
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )

            async with maker() as session:
                marker = await mark_account_deleting(session, user_id=owner_id)
            assert marker.generation == 1

            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError
                ) as caught:
                    await publication.publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="attempt-1",
                    )
                assert caught.value.code == "account_deletion_in_progress"

            assert not artifact.absolute_path.exists()
            assert source.read_bytes() == b"must not publish after account deletion"
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                job = await session.scalar(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.workspace_operation_id
                        == operation_id
                    )
                )
                assert stored is not None and stored.status == "deleted"
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
                assert presentation.revision == 0
                assert operation is not None and operation.state == "running"
                assert job is not None and job.state == "running"
                assert (
                    await session.execute(select(WorkspaceOperationEvent))
                ).all() == []
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_presentation_tombstone_fences_generation_publication_and_compensates(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "generation-tombstone-fence.db")
        app_data = tmp_path / "generation-tombstone-fence-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"must not revive a deleted presentation")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                await session.execute(
                    update(PresentationModel)
                    .where(PresentationModel.id == presentation_id)
                    .values(
                        lifecycle_status=PRESENTATION_LIFECYCLE_DELETED,
                        deleted_at=datetime.now(timezone.utc),
                        deletion_operation_id=uuid.uuid4(),
                    )
                )
                await session.commit()

            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError
                ) as caught:
                    await publication.publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="attempt-1",
                    )
                assert caught.value.code == "presentation_deleted"

            assert not artifact.absolute_path.exists()
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                job = await session.scalar(
                    select(PresentationGenerationJob).where(
                        PresentationGenerationJob.workspace_operation_id
                        == operation_id
                    )
                )
                assert stored is not None and stored.status == "deleted"
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
                assert presentation.revision == 0
                assert operation is not None and operation.state == "running"
                assert job is not None and job.state == "running"
                assert (
                    await session.execute(select(WorkspaceOperationEvent))
                ).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_presentation_tombstone_fences_export_publication_and_compensates(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "export-tombstone-fence.db")
        app_data = tmp_path / "export-tombstone-fence-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"must not export a deleted presentation")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=0,
                    kind="export.pptx",
                )
            async with maker() as session:
                await session.execute(
                    update(PresentationModel)
                    .where(PresentationModel.id == presentation_id)
                    .values(
                        lifecycle_status=PRESENTATION_LIFECYCLE_DELETED,
                        deleted_at=datetime.now(timezone.utc),
                        deletion_operation_id=uuid.uuid4(),
                    )
                )
                await session.commit()

            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError
                ) as caught:
                    await publication.publish_workspace_export_artifact(
                        session,
                        artifact=artifact,
                        attempt_token="attempt-1",
                        fencing_token=0,
                        export_format="pptx",
                    )
                assert caught.value.code == "presentation_deleted"

            assert not artifact.absolute_path.exists()
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert stored is not None and stored.status == "deleted"
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
                assert presentation.revision == 0
                assert operation is not None and operation.state == "running"
                assert (
                    await session.execute(select(WorkspaceOperationEvent))
                ).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cancelled_intent_commit_ack_closes_intent_before_copy(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "intent-commit-cancelled.db")
        app_data = tmp_path / "intent-commit-cancelled-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"intent exists but copy must not start")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                original_factory = publication._new_independent_session

                def cancelling_intent_session(caller_session):
                    intent_session = original_factory(caller_session)
                    original_commit = intent_session.commit

                    async def commit_then_cancel():
                        await original_commit()
                        raise asyncio.CancelledError()

                    monkeypatch.setattr(intent_session, "commit", commit_then_cancel)
                    return intent_session

                monkeypatch.setattr(
                    publication,
                    "_new_independent_session",
                    cancelling_intent_session,
                )
                with pytest.raises(asyncio.CancelledError):
                    await publication.prepare_workspace_generation_artifact(
                        session,
                        source_path=str(source),
                        operation_id=operation_id,
                        presentation_id=presentation_id,
                        source_revision=1,
                    )

            assert _artifact_files(app_data) == []
            async with maker() as session:
                stored = (
                    await session.execute(select(WorkspaceArtifact))
                ).scalar_one()
                assert stored.status == "deleted"
                assert stored.deleted_at is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_db_commit_failure_preserves_pointer_and_revision_and_compensates_file(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "commit-failure.db")
        app_data = tmp_path / "app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"immutable bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                async def fail_commit():
                    raise RuntimeError("injected commit failure")

                monkeypatch.setattr(session, "commit", fail_commit)
                with pytest.raises(RuntimeError, match="injected commit failure"):
                    await publication.publish_workspace_generation_artifact(
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
                assert operation.result_revision is None
                job = (
                    await session.execute(
                        select(PresentationGenerationJob).where(
                            PresentationGenerationJob.workspace_operation_id
                            == operation_id
                        )
                    )
                ).scalar_one()
                assert job.state == "running"
                assert job.attempt_token == "attempt-1"
                stored_artifact = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                assert stored_artifact is not None
                assert stored_artifact.status == "deleted"
                assert (await session.execute(select(WorkspaceOperationEvent))).all() == []
                assert (await session.execute(select(WorkspaceOutbox))).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_preexisting_destination_collision_is_never_deleted(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "destination-collision.db")
        app_data = tmp_path / "app-data-collision"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"new artifact bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            artifact_id = uuid.uuid4()
            destination = (
                app_data
                / "workspace-artifacts"
                / "anonymous"
                / str(operation_id)
                / f"{artifact_id}.pptx"
            )
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"foreign immutable bytes")
            monkeypatch.setattr(publication.uuid, "uuid4", lambda: artifact_id)

            async with maker() as session:
                with pytest.raises(FileExistsError):
                    await publication.prepare_workspace_generation_artifact(
                        session,
                        source_path=str(source),
                        operation_id=operation_id,
                        presentation_id=presentation_id,
                        source_revision=1,
                    )

            assert destination.read_bytes() == b"foreign immutable bytes"
            assert not (destination.parent / f".{artifact_id}.tmp").exists()
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact_id)
                assert stored is not None
                assert stored.status == "deleted"
                assert stored.expires_at is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_authenticated_artifact_path_and_cleanup_are_owner_scoped(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "owner-scoped.db")
        app_data = tmp_path / "owner-scoped-app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"owner scoped immutable bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        owner_id = uuid.uuid4()
        owner_token = set_current_owner_id(owner_id)
        try:
            presentation_id, operation_id = await _seed_running_operation(
                maker,
                owner_id=owner_id,
            )
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )

            expected_key = (
                f"workspace-artifacts/users/{owner_id}/{operation_id}/"
                f"{artifact.artifact_id}.pptx"
            )
            assert artifact.owner_id == owner_id
            assert artifact.storage_key == expected_key
            assert artifact.public_path == f"/app_data/{expected_key}"
            assert artifact.absolute_path.read_bytes() == source.read_bytes()

            expired_at = datetime.now(timezone.utc) - timedelta(minutes=1)
            async with maker() as session:
                await session.execute(
                    update(WorkspaceArtifact)
                    .where(WorkspaceArtifact.id == artifact.artifact_id)
                    .values(expires_at=expired_at)
                )
                await session.commit()
            result = await publication.sweep_workspace_artifact_cleanup(
                session_factory=maker,
                now=datetime.now(timezone.utc),
                limit=1,
            )

            assert result == {
                "claimed": 1,
                "deleted": 1,
                "retry_pending": 0,
                "dead_lettered": 0,
                "stale": 0,
            }
            assert not artifact.absolute_path.exists()
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert stored is not None
                assert stored.owner_id == owner_id
                assert stored.status == "deleted"
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

    asyncio.run(scenario())


def test_ambiguous_commit_ack_reconciles_complete_publication_without_deleting_file(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "ambiguous-commit.db")
        app_data = tmp_path / "app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"committed immutable bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                original_commit = session.commit

                async def commit_then_lose_ack():
                    await original_commit()
                    raise RuntimeError("injected lost commit acknowledgement")

                monkeypatch.setattr(session, "commit", commit_then_lose_ack)
                revision = await publication.publish_workspace_generation_artifact(
                    session,
                    artifact=artifact,
                    expected_revision=0,
                    attempt_token="attempt-1",
                )

            assert revision == 1
            assert artifact.absolute_path.read_bytes() == b"committed immutable bytes"
            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
                assert presentation.revision == 1
                assert operation is not None
                assert operation.state == "succeeded"
                assert operation.result == artifact.result_payload()
                job = (
                    await session.execute(
                        select(PresentationGenerationJob).where(
                            PresentationGenerationJob.workspace_operation_id
                            == operation_id
                        )
                    )
                ).scalar_one()
                assert job.state == "published"
                assert job.attempt_token is None
                assert stored is not None
                assert stored.status == "ready"
                terminal_event = (
                    await session.execute(select(WorkspaceOperationEvent))
                ).scalar_one()
                assert terminal_event.event_type == "operation.result"
                assert terminal_event.payload == {
                    "state": "succeeded",
                    "artifact_id": str(artifact.artifact_id),
                    "operation_id": str(operation_id),
                    "presentation_id": str(presentation_id),
                    "conversation_id": str(operation.conversation_id),
                }
                notification = (
                    await session.execute(select(WorkspaceOutbox))
                ).scalar_one()
                assert notification.status == "pending"
                assert notification.event_cursor == terminal_event.cursor
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_compensation_records_cleanup_pending_in_independent_transaction(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "cleanup-pending.db")
        app_data = tmp_path / "app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"orphan requiring cleanup")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            async with maker() as session:
                async def fail_commit():
                    raise RuntimeError("injected commit failure")

                monkeypatch.setattr(session, "commit", fail_commit)
                monkeypatch.setattr(publication, "_remove_prepared_file", lambda _artifact: False)
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError
                ) as caught:
                    await publication.publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="attempt-1",
                    )
                assert caught.value.code == "artifact_cleanup_pending"

            assert artifact.absolute_path.read_bytes() == b"orphan requiring cleanup"
            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                cleanup = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
                assert presentation.revision == 0
                assert operation is not None
                assert operation.state == "running"
                assert operation.result_revision is None
                job = (
                    await session.execute(
                        select(PresentationGenerationJob).where(
                            PresentationGenerationJob.workspace_operation_id
                            == operation_id
                        )
                    )
                ).scalar_one()
                assert job.state == "running"
                assert job.attempt_token == "attempt-1"
                assert cleanup is not None
                assert cleanup.status == "cleanup_pending"
                assert cleanup.cleanup_attempts == 1
                assert cleanup.cleanup_error == {
                    "code": "artifact_compensation_delete_failed"
                }
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_terminal_event_or_outbox_failure_rolls_back_every_publication_projection(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "terminal-event-failure.db")
        app_data = tmp_path / "app-data"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"publication must be all or nothing")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as prepare_session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    prepare_session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )

            async def fail_terminal_event(*_args, **_kwargs):
                raise RuntimeError("injected terminal event/outbox failure")

            monkeypatch.setattr(
                workspace_operation_runner,
                "append_operation_event",
                fail_terminal_event,
            )
            async with maker() as session:
                with pytest.raises(
                    RuntimeError, match="injected terminal event/outbox failure"
                ):
                    await publication.publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=0,
                        attempt_token="attempt-1",
                    )

            assert not artifact.absolute_path.exists()
            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                job = (
                    await session.execute(
                        select(PresentationGenerationJob).where(
                            PresentationGenerationJob.workspace_operation_id
                            == operation_id
                        )
                    )
                ).scalar_one()
                assert presentation is not None
                assert presentation.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
                assert presentation.revision == 0
                assert operation is not None
                assert operation.state == "running"
                assert operation.result_revision is None
                assert job.state == "running"
                assert job.attempt_token == "attempt-1"
                stored_artifact = await session.get(
                    WorkspaceArtifact,
                    artifact.artifact_id,
                )
                assert stored_artifact is not None
                assert stored_artifact.status == "deleted"
                assert (await session.execute(select(WorkspaceOperationEvent))).all() == []
                assert (await session.execute(select(WorkspaceOutbox))).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("crash_point", ["copy_in_progress", "after_promote"])
def test_process_boundary_intent_is_discoverable_and_swept(
    tmp_path: Path,
    monkeypatch,
    crash_point: str,
):
    async def scenario():
        engine, maker = await _database(tmp_path / f"crash-{crash_point}.db")
        app_data = tmp_path / f"app-data-{crash_point}"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"process boundary bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )

            # Simulate a hard process stop at either external-write boundary.
            # No exception handler gets a chance to register cleanup work.
            if crash_point == "copy_in_progress":
                artifact.absolute_path.unlink()
                artifact.temporary_path.write_bytes(b"partial copy")

            current = datetime.now(timezone.utc) + timedelta(hours=2)
            result = await publication.sweep_workspace_artifact_cleanup(
                session_factory=maker,
                now=current,
            )

            assert result["claimed"] == 1
            assert result["deleted"] == 1
            assert not artifact.absolute_path.exists()
            assert not artifact.temporary_path.exists()
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert stored is not None
                assert stored.status == "deleted"
                assert stored.cleanup_token is None
                assert stored.deleted_at is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_cleanup_failure_releases_lease_and_retry_finishes_idempotently(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "cleanup-retry.db")
        app_data = tmp_path / "app-data-retry"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"retry cleanup bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        monkeypatch.setenv("WORKSPACE_ARTIFACT_CLEANUP_RETRY_SECONDS", "1")
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )

            original_delete = publication._delete_claimed_artifact_files

            def fail_delete(_claim):
                raise OSError("injected cleanup unlink failure")

            monkeypatch.setattr(
                publication,
                "_delete_claimed_artifact_files",
                fail_delete,
            )
            first_now = datetime.now(timezone.utc) + timedelta(hours=2)
            first = await publication.sweep_workspace_artifact_cleanup(
                session_factory=maker,
                now=first_now,
                limit=1,
            )
            assert first["retry_pending"] == 1
            assert artifact.absolute_path.exists()
            async with maker() as session:
                pending = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert pending is not None
                assert pending.status == "cleanup_pending"
                assert pending.cleanup_attempts == 1
                assert pending.cleanup_token is None
                assert pending.cleanup_lease_expires_at is not None

            monkeypatch.setattr(
                publication,
                "_delete_claimed_artifact_files",
                original_delete,
            )
            second = await publication.sweep_workspace_artifact_cleanup(
                session_factory=maker,
                now=first_now + timedelta(seconds=2),
                limit=1,
            )
            assert second["deleted"] == 1
            assert not artifact.absolute_path.exists()
            async with maker() as session:
                deleted = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert deleted is not None
                assert deleted.status == "deleted"
                assert deleted.cleanup_attempts == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_stale_cleanup_token_cannot_finalize_newer_claim(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "cleanup-fence.db")
        app_data = tmp_path / "app-data-fence"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"fenced cleanup bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            current = datetime.now(timezone.utc) + timedelta(hours=2)
            async with maker() as session:
                stale_claim = await publication.claim_workspace_artifact_cleanup(
                    session,
                    now=current,
                )
            assert stale_claim is not None
            async with maker() as session, session.begin():
                await session.execute(
                    update(WorkspaceArtifact)
                    .where(WorkspaceArtifact.id == artifact.artifact_id)
                    .values(cleanup_token="newer-claim-token")
                )

            finalized = await publication._finish_workspace_artifact_cleanup(
                maker,
                stale_claim,
                now=current,
            )
            assert finalized is False
            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert stored is not None
                assert stored.status == "cleanup_pending"
                assert stored.cleanup_token == "newer-claim-token"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_publication_compensation_cannot_steal_active_cleanup_lease(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "cleanup-lease-preserved.db")
        app_data = tmp_path / "app-data-lease-preserved"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"lease preservation bytes")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            current = datetime.now(timezone.utc) + timedelta(hours=2)
            async with maker() as session:
                active_claim = await publication.claim_workspace_artifact_cleanup(
                    session,
                    now=current,
                )
            assert active_claim is not None

            async with maker() as session:
                cleanup_recorded = await publication._record_cleanup_pending(
                    session,
                    artifact,
                    error_code="concurrent_compensation_failure",
                )
                marked_deleted = await publication._mark_artifact_deleted(
                    session,
                    artifact,
                )
            assert cleanup_recorded is True
            assert marked_deleted is False

            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                assert stored is not None
                assert stored.status == "cleanup_pending"
                assert stored.cleanup_token == active_claim.token
                assert stored.cleanup_attempts == active_claim.attempts
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("expected_revision", "attempt_token"),
    [(9, "attempt-1"), (0, "")],
)
def test_precondition_unlink_failure_is_durably_cleanup_pending(
    tmp_path: Path,
    monkeypatch,
    expected_revision: int,
    attempt_token: str,
):
    async def scenario():
        engine, maker = await _database(
            tmp_path / f"precondition-{expected_revision}-{bool(attempt_token)}.db"
        )
        app_data = tmp_path / f"precondition-{expected_revision}-{bool(attempt_token)}"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"precondition orphan")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                artifact = await publication.prepare_workspace_generation_artifact(
                    session,
                    source_path=str(source),
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    source_revision=1,
                )
            monkeypatch.setattr(
                publication,
                "_remove_prepared_file",
                lambda _artifact: False,
            )
            async with maker() as session:
                with pytest.raises(
                    publication.WorkspaceArtifactPublicationError
                ) as caught:
                    await publication.publish_workspace_generation_artifact(
                        session,
                        artifact=artifact,
                        expected_revision=expected_revision,
                        attempt_token=attempt_token,
                    )
                assert caught.value.code == "artifact_cleanup_pending"

            async with maker() as session:
                stored = await session.get(WorkspaceArtifact, artifact.artifact_id)
                presentation = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                assert stored is not None
                assert stored.status == "cleanup_pending"
                assert stored.cleanup_attempts == 1
                assert presentation is not None
                assert presentation.revision == 0
                assert operation is not None
                assert operation.state == "running"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_async_task_immutable_path_commits_with_publication_on_lost_ack(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "task-publication-ack.db")
        app_data = tmp_path / "app-data-task-publication"
        source = app_data / "exports" / "deck.pptx"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"task and publication transaction")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
        try:
            presentation_id, operation_id = await _seed_running_operation(maker)
            async with maker() as session:
                presentation = await session.get(PresentationModel, presentation_id)
                generation_job = (
                    await session.execute(
                        select(PresentationGenerationJob).where(
                            PresentationGenerationJob.workspace_operation_id
                            == operation_id
                        )
                    )
                ).scalar_one()
                async_status = await session.get(
                    AsyncPresentationGenerationTaskModel,
                    generation_job.task_id,
                )
                assert presentation is not None
                assert async_status is not None

                original_commit = session.commit
                commit_calls = 0

                async def lose_publication_ack_only():
                    nonlocal commit_calls
                    await original_commit()
                    commit_calls += 1
                    if commit_calls == 1:
                        raise RuntimeError("injected publication ACK loss")

                monkeypatch.setattr(session, "commit", lose_publication_ack_only)
                published_path = await _publish_staged_presentation(
                    session,
                    presentation,
                    str(source),
                    generation_job,
                    async_status,
                )
                # Artifact intent durability uses a separate session.  The only
                # caller commit is the final publication tuple.
                assert commit_calls == 1

            assert published_path.startswith(
                "/app_data/workspace-artifacts/anonymous/"
            )
            async with maker() as session:
                task = (
                    await session.execute(
                        select(AsyncPresentationGenerationTaskModel)
                    )
                ).scalar_one()
                artifact = (
                    await session.execute(select(WorkspaceArtifact))
                ).scalar_one()
                assert task.status == "completed"
                assert task.error is None
                assert task.data is not None
                assert task.data["path"] == published_path
                assert artifact.status == "ready"
                assert f"/app_data/{artifact.storage_key}" == published_path
        finally:
            await engine.dispose()

    asyncio.run(scenario())
