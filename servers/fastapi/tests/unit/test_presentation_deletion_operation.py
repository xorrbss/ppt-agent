from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path, PurePosixPath

import pytest
from alembic.config import Config
from sqlalchemy import delete, event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

# Importing the database module registers the complete model graph used by the
# deletion service before the focused SQLite database is created.
import services.database  # noqa: F401
import services.workspace_operation_runner as workspace_operation_runner_module
from alembic import command
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_DELETED,
    PRESENTATION_LIFECYCLE_GENERATION_FAILED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
from models.sql.template_v2_local_state import TemplateV2LocalState
from models.sql.template_v2_revision import TemplateV2Revision
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceCleanupJournal,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceMessageAttachment,
    WorkspaceOperation,
    WorkspaceOperationEvent,
)
from models.workspace import WorkspaceCommandRequest, WorkspaceOperationActionRequest
from services.account_deletion_service import delete_account_restricted_dependents
from services.presentation_deletion_operation_handler import (
    register_presentation_deletion_operation_handler,
)
from services.presentation_deletion_service import PresentationDeletionService
from services.workspace_cleanup_journal_service import (
    WorkspaceCleanupClaim,
    WorkspaceCleanupDeleteError,
    _delete_claimed_storage,
    account_owned_tree_cleanup_target,
    authored_checkpoint_cleanup_target,
    authored_image_tree_cleanup_target,
    claim_workspace_cleanup_target,
    enqueue_workspace_cleanup_targets,
    finalize_workspace_cleanup_target,
    redrive_workspace_cleanup_dead_letter,
    snapshot_account_workspace_cleanup_targets,
    sweep_workspace_cleanup_journal,
    template_v2_native_cleanup_target,
    workspace_artifact_cleanup_target,
    workspace_asset_cleanup_target,
)
from services.workspace_metrics import WORKSPACE_METRICS
from services.workspace_operation_handler_registry import (
    WorkspaceOperationHandlerRegistry,
)
from services.workspace_operation_runner import (
    WorkspaceOperationRunner,
    claim_next_workspace_operation,
    recover_expired_workspace_operations,
    sanitize_workspace_event_payload,
)
from services.workspace_service import (
    WorkspaceServiceError,
    apply_workspace_action,
    create_workspace_command,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
FASTAPI_ROOT = Path(__file__).resolve().parents[2]


def test_delete_result_payload_allowlist_is_operation_specific():
    payload = {
        "presentation_id": str(uuid.uuid4()),
        "lifecycle_status": "deleted",
        "cleanup_pending": True,
        "deletion_operation_id": str(uuid.uuid4()),
        "private_path": "C:/secret/source.pptx",
    }

    assert sanitize_workspace_event_payload(payload) == {
        "presentation_id": payload["presentation_id"]
    }
    assert sanitize_workspace_event_payload(
        payload, operation_kind="presentation.delete"
    ) == {
        "presentation_id": payload["presentation_id"],
        "lifecycle_status": "deleted",
        "cleanup_pending": True,
        "deletion_operation_id": payload["deletion_operation_id"],
    }


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _drain_once(runner: WorkspaceOperationRunner) -> int:
    dispatched = await runner.dispatch_once()
    tasks = tuple(runner._active.values())
    if tasks:
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
    return dispatched


def _asset_file(
    app_data: Path,
    *,
    owner_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: bytes,
) -> tuple[str, str, Path]:
    digest = sha256(payload).hexdigest()
    storage_key = PurePosixPath(
        str(owner_id), str(asset_id), f"{digest}.pdf"
    ).as_posix()
    path = app_data / "workspace_assets" / Path(*PurePosixPath(storage_key).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return storage_key, digest, path


def _artifact_file(
    app_data: Path,
    *,
    owner_id: uuid.UUID,
    operation_id: uuid.UUID,
    artifact_id: uuid.UUID,
    payload: bytes,
) -> tuple[str, str, Path]:
    digest = sha256(payload).hexdigest()
    storage_key = PurePosixPath(
        "workspace-artifacts",
        "users",
        str(owner_id),
        str(operation_id),
        f"{artifact_id}.pptx",
    ).as_posix()
    path = app_data / Path(*PurePosixPath(storage_key).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return storage_key, digest, path


def test_approved_delete_runner_tombstones_staging_and_failed_all_modes(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _approved_delete_runner_tombstones_staging_and_failed_all_modes(
            tmp_path, monkeypatch
        )
    )


async def _approved_delete_runner_tombstones_staging_and_failed_all_modes(
    tmp_path: Path, monkeypatch
):
    app_data = tmp_path / "app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    monkeypatch.delenv("AUTHORED_CHECKPOINT_DIRECTORY", raising=False)
    engine, maker = await _database(tmp_path / "presentation-delete.db")
    owner_token = set_current_owner_id(OWNER_ID)
    try:
        async with maker() as session, session.begin():
            session.add(
                User(
                    id=OWNER_ID,
                    username="delete-owner",
                    hashed_password="test-only",
                )
            )

        registry = WorkspaceOperationHandlerRegistry()
        handler = register_presentation_deletion_operation_handler(
            registry, session_factory=maker
        )
        assert registry.resolve("presentation.delete") is handler
        assert registry.recovery_policy("presentation.delete") == "automatic"
        runner = WorkspaceOperationRunner(
            session_factory=maker,
            registry=registry,
            max_concurrency=1,
            heartbeat_seconds=60,
            poll_seconds=0.01,
        )

        # Simulate a process loss at the old handler/finalizer boundary.  The
        # delete handler must commit its tombstone, result projection and
        # terminal event atomically without relying on the runner finalizer.
        finalizer_calls = 0

        async def _drop_runner_finalizer(*_args, **_kwargs):
            nonlocal finalizer_calls
            finalizer_calls += 1
            return False

        monkeypatch.setattr(
            workspace_operation_runner_module,
            "finalize_workspace_operation_success",
            _drop_runner_finalizer,
        )

        cases = (
            ("authored", PRESENTATION_LIFECYCLE_STAGING),
            ("template", PRESENTATION_LIFECYCLE_GENERATION_FAILED),
            ("adaptive", PRESENTATION_LIFECYCLE_STAGING),
        )
        for case_index, (mode, lifecycle_status) in enumerate(cases):
            async with maker() as session, session.begin():
                presentation = PresentationModel(
                    content=f"private {mode} source",
                    n_slides=1,
                    language="en",
                    title=f"private {mode} title",
                    layout=None if mode == "authored" else {"name": "private"},
                    structure={"private": True},
                    instructions="private instructions",
                    tone="private tone",
                    verbosity="private verbosity",
                    theme={"mode": mode, "secret": "brand"},
                    deck_plan={"slides": [{"private": True}]}
                    if mode == "adaptive"
                    else None,
                    mode=mode,
                    lifecycle_status=lifecycle_status,
                    revision=4,
                    share_token=f"private-share-{case_index}",
                )
                session.add(presentation)
                await session.flush()
                presentation_id = presentation.id

            command_request = WorkspaceCommandRequest(
                command=f"Delete the {mode} presentation",
                command_type="presentation.delete",
                idempotency_key=f"delete-{mode}",
                expected_revision=4,
                context={"source": "test"},
            )
            async with maker() as session:
                operation, replayed = await create_workspace_command(
                    session,
                    presentation_id=presentation_id,
                    request=command_request,
                )
                assert not replayed
                duplicate, replayed = await create_workspace_command(
                    session,
                    presentation_id=presentation_id,
                    request=command_request,
                )
                assert replayed and duplicate.id == operation.id
                operation_id = operation.id
                user_message_id = uuid.UUID(operation.request_payload["user_message_id"])

            asset_id = uuid.uuid4()
            asset_payload = f"{mode}-asset".encode()
            asset_key, asset_digest, asset_path = _asset_file(
                app_data,
                owner_id=OWNER_ID,
                asset_id=asset_id,
                payload=asset_payload,
            )
            artifact_id = uuid.uuid4()
            artifact_payload = f"{mode}-artifact".encode()
            artifact_key, artifact_digest, artifact_path = _artifact_file(
                app_data,
                owner_id=OWNER_ID,
                operation_id=operation_id,
                artifact_id=artifact_id,
                payload=artifact_payload,
            )
            mode_path: Path | None = None
            authored_image_path: Path | None = None
            async with maker() as session, session.begin():
                presentation = await session.get(PresentationModel, presentation_id)
                conversation = (
                    await session.scalars(
                        select(WorkspaceConversation).where(
                            WorkspaceConversation.presentation_id == presentation_id
                        )
                    )
                ).one()
                assert presentation is not None
                presentation.file_paths = [str(asset_path)]
                asset = WorkspaceAsset(
                    id=asset_id,
                    conversation_id=conversation.id,
                    state="ready",
                    original_name=f"{mode}.pdf",
                    media_type="application/pdf",
                    size_bytes=len(asset_payload),
                    storage_key=asset_key,
                    sha256=asset_digest,
                    version=3,
                )
                attachment = WorkspaceMessageAttachment(
                    message_id=user_message_id,
                    asset_id=asset_id,
                    asset_id_snapshot=asset_id,
                    ordinal=0,
                    display_name=f"{mode}.pdf",
                    media_type="application/pdf",
                    size_bytes=len(asset_payload),
                    asset_state="ready",
                    asset_sha256=asset_digest,
                )
                artifact = WorkspaceArtifact(
                    id=artifact_id,
                    operation_id=operation_id,
                    presentation_id=presentation_id,
                    kind="export.pptx",
                    status="ready",
                    storage_key=artifact_key,
                    sha256=artifact_digest,
                    size_bytes=len(artifact_payload),
                    source_revision=4,
                )
                slide = SlideModel(
                    presentation=presentation_id,
                    layout_group="private",
                    layout="private",
                    index=0,
                    content={"private": mode},
                    html_content=("<p>private</p>" if mode == "authored" else None),
                )
                session.add_all((asset, attachment, artifact, slide))

                if mode == "authored":
                    task = AsyncPresentationGenerationTaskModel(status="failed")
                    session.add(task)
                    await session.flush()
                    job = PresentationGenerationJob(
                        task_id=task.id,
                        presentation_id=presentation_id,
                        request_sha256="a" * 64,
                        request_payload={"strategy": "authored"},
                        state="failed",
                    )
                    session.add(job)
                    await session.flush()
                    mode_path = app_data / "authored-checkpoints" / str(job.id)
                    mode_path.mkdir(parents=True, exist_ok=True)
                    (mode_path / "slide-0.png").write_bytes(b"checkpoint")
                    authored_image_path = (
                        app_data
                        / "images"
                        / "users"
                        / str(OWNER_ID)
                        / "authored"
                        / str(presentation_id)
                        / "sets"
                        / ("d" * 64)
                        / "slide-0.png"
                    )
                    authored_image_path.parent.mkdir(parents=True, exist_ok=True)
                    authored_image_path.write_bytes(b"immutable-authored-image")
                elif mode == "template":
                    template_id = f"template-{uuid.uuid4()}"
                    native_payload = b"native-template-source"
                    native_digest = sha256(native_payload).hexdigest()
                    native_key = (
                        f"template-v2/native/{template_id}/revision-1/source.pptx"
                    )
                    mode_path = app_data / Path(*PurePosixPath(native_key).parts)
                    mode_path.parent.mkdir(parents=True, exist_ok=True)
                    mode_path.write_bytes(native_payload)
                    native_assets = {
                        "native_pptx": {
                            "storage_key": native_key,
                            "source_sha256": native_digest,
                            "source_size_bytes": len(native_payload),
                        }
                    }
                    session.add_all(
                        (
                            TemplateV2(
                                id=template_id,
                                presentation_id=presentation_id,
                                name="private template",
                                assets=native_assets,
                                revision=1,
                            ),
                            TemplateV2LocalState(
                                template_id=template_id,
                                presentation_id=presentation_id,
                                revision=1,
                            ),
                            TemplateV2Revision(
                                template_id=template_id,
                                revision=1,
                                reason="create",
                                name="private template",
                                assets=native_assets,
                                is_default=False,
                            ),
                        )
                    )

            action_request = WorkspaceOperationActionRequest(
                action="approve",
                idempotency_key=f"approve-{mode}",
                expected_operation_version=0,
                expected_revision=4,
            )
            async with maker() as session:
                receipt, approved, replayed = await apply_workspace_action(
                    session, operation_id=operation_id, request=action_request
                )
                assert not replayed and approved.state == "queued"
                assert approved.approval_expires_at is None
                duplicate_receipt, duplicate_operation, replayed = (
                    await apply_workspace_action(
                        session, operation_id=operation_id, request=action_request
                    )
                )
                assert replayed
                assert duplicate_receipt.id == receipt.id
                assert duplicate_operation.id == approved.id

            assert await _drain_once(runner) == 1

            async with maker() as session:
                stored = await session.get(PresentationModel, presentation_id)
                operation = await session.get(WorkspaceOperation, operation_id)
                attachment = await session.get(
                    WorkspaceMessageAttachment, attachment.id
                )
                assert stored is not None
                assert stored.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
                assert stored.deleted_at is not None
                assert stored.deletion_operation_id == operation_id
                assert stored.revision == 4
                assert stored.content == ""
                assert stored.n_slides == 0
                assert stored.title is None
                assert stored.file_paths is None
                assert stored.layout is None
                assert stored.structure is None
                assert stored.theme is None
                assert stored.deck_plan is None
                assert stored.mode is None
                assert stored.share_token is None

                assert operation is not None and operation.state == "succeeded"
                assert operation.result_revision == 4
                assert operation.result == {
                    "presentation_id": str(presentation_id),
                    "lifecycle_status": "deleted",
                    "cleanup_pending": True,
                    "deletion_operation_id": str(operation_id),
                    "result_revision": 4,
                }
                event_types = list(
                    await session.scalars(
                        select(WorkspaceOperationEvent.event_type)
                        .where(WorkspaceOperationEvent.operation_id == operation_id)
                        .order_by(WorkspaceOperationEvent.sequence)
                    )
                )
                assert event_types == [
                    "message.created",
                    "approval_required",
                    "approve",
                    "operation.progress",
                    "operation.result",
                ]
                assert await session.get(WorkspaceConversation, operation.conversation_id)
                assert await session.get(WorkspaceMessage, user_message_id)
                assert await session.get(WorkspaceActionReceipt, receipt.id)
                assert attachment is not None
                assert attachment.asset_id is None
                assert attachment.asset_id_snapshot == asset_id
                assert attachment.asset_sha256 == asset_digest
                assert attachment.display_name == f"{mode}.pdf"
                assert await session.get(WorkspaceAsset, asset_id) is None
                assert await session.get(WorkspaceArtifact, artifact_id) is None
                assert not list(
                    await session.scalars(
                        select(SlideModel).where(
                            SlideModel.presentation == presentation_id
                        )
                    )
                )
                cleanup_rows = list(
                    await session.scalars(
                        select(WorkspaceCleanupJournal).where(
                            WorkspaceCleanupJournal.presentation_id_snapshot
                            == presentation_id
                        )
                    )
                )
                expected_namespaces = {"workspace_asset", "workspace_artifact"}
                if mode == "authored":
                    expected_namespaces.update(
                        {"authored_checkpoint", "authored_image_tree"}
                    )
                    assert not list(
                        await session.scalars(
                            select(PresentationGenerationJob).where(
                                PresentationGenerationJob.presentation_id
                                == presentation_id
                            )
                        )
                    )
                elif mode == "template":
                    expected_namespaces.add("template_v2_native")
                    assert not list(
                        await session.scalars(
                            select(TemplateV2).where(
                                TemplateV2.presentation_id == presentation_id
                            )
                        )
                    )
                assert {row.storage_namespace for row in cleanup_rows} == (
                    expected_namespaces
                )
                assert all(row.status == "pending" for row in cleanup_rows)

                with pytest.raises(WorkspaceServiceError) as rejected_cancel:
                    await apply_workspace_action(
                        session,
                        operation_id=operation_id,
                        request=WorkspaceOperationActionRequest(
                            action="cancel",
                            idempotency_key=f"cancel-after-delete-{mode}",
                            expected_revision=4,
                        ),
                    )
                assert rejected_cancel.value.code == "presentation_deleted"

                # The durable idempotency record remains replayable after the
                # deck becomes a tombstone, but no fresh mutation is admitted.
                duplicate_after_delete, replayed = await create_workspace_command(
                    session,
                    presentation_id=presentation_id,
                    request=command_request,
                )
                assert replayed
                assert duplicate_after_delete.id == operation_id
                with pytest.raises(WorkspaceServiceError) as rejected:
                    await create_workspace_command(
                        session,
                        presentation_id=presentation_id,
                        request=WorkspaceCommandRequest(
                            command="Mutate a deleted presentation",
                            command_type="presentation.edit",
                            idempotency_key=f"post-delete-{mode}",
                            expected_revision=4,
                        ),
                    )
                assert rejected.value.code == "presentation_deleted"

            assert asset_path.exists() and artifact_path.exists()
            if mode_path is not None:
                assert mode_path.exists()
            if authored_image_path is not None:
                assert authored_image_path.exists()
            sweep = await sweep_workspace_cleanup_journal(
                session_factory=maker, limit=10
            )
            assert sweep["deleted"] == len(cleanup_rows)
            assert not asset_path.exists()
            assert not artifact_path.exists()
            if mode_path is not None:
                assert not mode_path.exists()
            if authored_image_path is not None:
                assert not authored_image_path.exists()
        assert finalizer_calls == len(cases)
    finally:
        reset_current_owner_id(owner_token)
        await engine.dispose()


def test_delete_recovery_treats_same_operation_tombstone_as_committed_effect(
    tmp_path: Path,
    monkeypatch,
):
    asyncio.run(
        _delete_recovery_treats_same_operation_tombstone_as_committed_effect(
            tmp_path,
            monkeypatch,
        )
    )


async def _delete_recovery_treats_same_operation_tombstone_as_committed_effect(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "recovery-app-data"))
    monkeypatch.delenv("AUTHORED_CHECKPOINT_DIRECTORY", raising=False)
    engine, maker = await _database(tmp_path / "presentation-delete-recovery.db")
    owner_token = set_current_owner_id(OWNER_ID)
    try:
        async with maker() as session, session.begin():
            session.add(
                User(
                    id=OWNER_ID,
                    username="delete-recovery-owner",
                    hashed_password="test-only",
                )
            )
            presentation = PresentationModel(
                content="recoverable private source",
                n_slides=1,
                language="en",
                lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
                revision=7,
            )
            session.add(presentation)
            await session.flush()
            presentation_id = presentation.id

        async with maker() as session:
            operation, replayed = await create_workspace_command(
                session,
                presentation_id=presentation_id,
                request=WorkspaceCommandRequest(
                    command="Delete after approval",
                    command_type="presentation.delete",
                    idempotency_key="delete-recovery",
                    expected_revision=7,
                ),
            )
            assert not replayed
            operation_id = operation.id
            _, approved, replayed = await apply_workspace_action(
                session,
                operation_id=operation_id,
                request=WorkspaceOperationActionRequest(
                    action="approve",
                    idempotency_key="approve-delete-recovery",
                    expected_operation_version=0,
                    expected_revision=7,
                ),
            )
            assert not replayed
            assert approved.state == "queued"
            assert approved.approval_expires_at is None

        claim = await claim_next_workspace_operation(
            session_factory=maker,
            lease_seconds=60,
        )
        assert claim is not None
        assert claim.operation_id == operation_id

        async with maker() as session, session.begin():
            other_presentation = PresentationModel(
                content="foreign tombstone private source",
                n_slides=1,
                language="en",
                lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
                revision=3,
            )
            session.add(other_presentation)
            await session.flush()
            other_presentation_id = other_presentation.id

        async with maker() as session:
            other_operation, replayed = await create_workspace_command(
                session,
                presentation_id=other_presentation_id,
                request=WorkspaceCommandRequest(
                    command="Delete with a foreign tombstone",
                    command_type="presentation.delete",
                    idempotency_key="delete-foreign-tombstone",
                    expected_revision=3,
                ),
            )
            assert not replayed
            other_operation_id = other_operation.id
            _, approved, replayed = await apply_workspace_action(
                session,
                operation_id=other_operation_id,
                request=WorkspaceOperationActionRequest(
                    action="approve",
                    idempotency_key="approve-delete-foreign-tombstone",
                    expected_operation_version=0,
                    expected_revision=3,
                ),
            )
            assert not replayed
            assert approved.state == "queued"

        other_claim = await claim_next_workspace_operation(
            session_factory=maker,
            lease_seconds=60,
        )
        assert other_claim is not None
        assert other_claim.operation_id == other_operation_id
        foreign_deletion_operation_id = uuid.uuid4()

        # Reproduce a row written by the old two-transaction implementation:
        # the tombstone committed, the running operation did not terminalize,
        # and a late cancel plus the original approval TTL remain on the row.
        async with maker() as session, session.begin():
            stored = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert stored is not None and operation is not None
            await PresentationDeletionService(session).tombstone(
                stored,
                operation_id=operation_id,
            )
            operation.approval_expires_at = (
                get_current_utc_datetime() - timedelta(seconds=1)
            )
            operation.cancel_requested_at = get_current_utc_datetime()
            operation.state = "cancelling"
            operation.lease_expires_at = (
                get_current_utc_datetime() - timedelta(seconds=1)
            )

            other_stored = await session.get(
                PresentationModel, other_presentation_id
            )
            other_operation = await session.get(
                WorkspaceOperation, other_operation_id
            )
            assert other_stored is not None and other_operation is not None
            await PresentationDeletionService(session).tombstone(
                other_stored,
                operation_id=foreign_deletion_operation_id,
            )
            other_operation.cancel_requested_at = get_current_utc_datetime()
            other_operation.state = "cancelling"
            other_operation.lease_expires_at = (
                get_current_utc_datetime() - timedelta(seconds=1)
            )

        registry = WorkspaceOperationHandlerRegistry()
        register_presentation_deletion_operation_handler(
            registry,
            session_factory=maker,
        )
        recovered = await recover_expired_workspace_operations(
            session_factory=maker,
            registry=registry,
        )
        assert recovered == 2

        async with maker() as session:
            stored = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert stored is not None
            assert stored.lifecycle_status == PRESENTATION_LIFECYCLE_DELETED
            assert stored.deletion_operation_id == operation_id
            assert operation is not None
            assert operation.state == "succeeded"
            assert operation.approval_expires_at is None
            assert operation.cancel_requested_at is None
            assert operation.cancelled_at is None
            assert operation.error_code is None
            assert operation.result == {
                "presentation_id": str(presentation_id),
                "lifecycle_status": "deleted",
                "cleanup_pending": False,
                "deletion_operation_id": str(operation_id),
                "result_revision": 7,
            }
            terminal_events = list(
                await session.scalars(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == operation_id,
                        WorkspaceOperationEvent.event_type == "operation.result",
                    )
                )
            )
            assert len(terminal_events) == 1
            assert terminal_events[0].payload == {
                "state": "succeeded",
                "presentation_id": str(presentation_id),
                "lifecycle_status": "deleted",
                "cleanup_pending": False,
                "deletion_operation_id": str(operation_id),
                "result_revision": 7,
                "operation_id": str(operation_id),
                "conversation_id": str(operation.conversation_id),
            }
            cancelled_events = list(
                await session.scalars(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == operation_id,
                        WorkspaceOperationEvent.event_type
                        == "operation.cancelled",
                    )
                )
            )
            assert cancelled_events == []

            other_stored = await session.get(
                PresentationModel, other_presentation_id
            )
            other_operation = await session.get(
                WorkspaceOperation, other_operation_id
            )
            assert other_stored is not None
            assert (
                other_stored.deletion_operation_id
                == foreign_deletion_operation_id
            )
            assert other_operation is not None
            assert other_operation.state == "cancelled"
            other_result_events = list(
                await session.scalars(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id
                        == other_operation_id,
                        WorkspaceOperationEvent.event_type
                        == "operation.result",
                    )
                )
            )
            assert other_result_events == []
    finally:
        reset_current_owner_id(owner_token)
        await engine.dispose()


def test_cleanup_journal_recovers_crash_and_fences_two_workers(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_cleanup_journal_recovers_crash_and_fences_two_workers(tmp_path, monkeypatch))


async def _cleanup_journal_recovers_crash_and_fences_two_workers(
    tmp_path: Path, monkeypatch
):
    app_data = tmp_path / "journal-app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'journal.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: WorkspaceCleanupJournal.__table__.create(
                sync_connection
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = get_current_utc_datetime()
    WORKSPACE_METRICS.reset()
    try:
        assets: list[WorkspaceAsset] = []
        paths: list[Path] = []
        for index in range(2):
            asset_id = uuid.uuid4()
            payload = f"worker-{index}".encode()
            key, digest, path = _asset_file(
                app_data,
                owner_id=OWNER_ID,
                asset_id=asset_id,
                payload=payload,
            )
            assets.append(
                WorkspaceAsset(
                    id=asset_id,
                    owner_id=OWNER_ID,
                    conversation_id=uuid.uuid4(),
                    state="ready",
                    original_name=f"worker-{index}.pdf",
                    media_type="application/pdf",
                    size_bytes=len(payload),
                    storage_key=key,
                    sha256=digest,
                    version=1,
                )
            )
            paths.append(path)
        targets = [workspace_asset_cleanup_target(asset) for asset in assets]
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(
                    session, targets=[targets[0], targets[0], targets[1]]
                )
                == 2
            )
        async with maker() as session, session.begin():
            assert await enqueue_workspace_cleanup_targets(session, targets=targets) == 0
        assert WORKSPACE_METRICS.snapshot()["cleanup"] == {"duplicate": 3}

        # A duplicate observed by a transaction that later rolls back is not a
        # durable replay outcome and must never be exported.
        async with maker() as session:
            assert (
                await enqueue_workspace_cleanup_targets(
                    session,
                    targets=[targets[0]],
                )
                == 0
            )
            await session.rollback()
        assert WORKSPACE_METRICS.snapshot()["cleanup"] == {"duplicate": 3}

        first, second = await asyncio.gather(
            claim_workspace_cleanup_target(
                session_factory=maker, now=now, lease_seconds=1
            ),
            claim_workspace_cleanup_target(
                session_factory=maker, now=now, lease_seconds=1
            ),
        )
        assert first is not None and second is not None
        assert first.journal_id != second.journal_id

        # Worker one deletes the file and crashes before persisting success.
        _delete_claimed_storage(first)
        _delete_claimed_storage(second)
        assert (
            await finalize_workspace_cleanup_target(
                second, succeeded=True, session_factory=maker, now=now
            )
            == "succeeded"
        )

        recovered = await claim_workspace_cleanup_target(
            session_factory=maker,
            now=now + timedelta(seconds=2),
            lease_seconds=1,
        )
        assert recovered is not None
        assert recovered.journal_id == first.journal_id
        assert recovered.token != first.token
        assert recovered.fencing_token == first.fencing_token + 1
        assert recovered.attempts == 2
        _delete_claimed_storage(recovered)  # Missing files are an idempotent success.
        assert (
            await finalize_workspace_cleanup_target(
                first,
                succeeded=True,
                session_factory=maker,
                now=now + timedelta(seconds=2),
            )
            == "stale"
        )
        assert (
            await finalize_workspace_cleanup_target(
                recovered,
                succeeded=True,
                session_factory=maker,
                now=now + timedelta(seconds=2),
            )
            == "succeeded"
        )
        async with maker() as session:
            rows = list(await session.scalars(select(WorkspaceCleanupJournal)))
            assert {row.status for row in rows} == {"succeeded"}
            assert sorted(row.cleanup_attempts for row in rows) == [1, 2]
        assert all(not path.exists() for path in paths)
        assert WORKSPACE_METRICS.snapshot()["cleanup"] == {
            "duplicate": 3,
            "claimed": 3,
            "succeeded": 2,
            "stale": 1,
        }
    finally:
        WORKSPACE_METRICS.reset()
        await engine.dispose()


def test_account_cascade_keeps_cleanup_snapshot_recoverable(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_account_cascade_keeps_cleanup_snapshot_recoverable(tmp_path, monkeypatch))


async def _account_cascade_keeps_cleanup_snapshot_recoverable(
    tmp_path: Path, monkeypatch
):
    app_data = tmp_path / "account-app-data"
    temp_directory = tmp_path / "account-temp"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    monkeypatch.setenv("TEMP_DIRECTORY", str(temp_directory))
    database_path = tmp_path / "account-cascade.db"
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "head")

    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    maker = async_sessionmaker(engine, expire_on_commit=False)
    owner_token = set_current_owner_id(OWNER_ID)
    try:
        asset_id = uuid.uuid4()
        asset_payload = b"account-owned-asset"
        asset_key, asset_digest, asset_path = _asset_file(
            app_data,
            owner_id=OWNER_ID,
            asset_id=asset_id,
            payload=asset_payload,
        )
        legacy_asset_id = uuid.uuid4()
        legacy_asset_payload = b"account-owned-legacy-anonymous-asset"
        legacy_asset_digest = sha256(legacy_asset_payload).hexdigest()
        legacy_asset_key = PurePosixPath(
            "anonymous",
            str(legacy_asset_id),
            f"{legacy_asset_digest}.pdf",
        ).as_posix()
        legacy_asset_path = app_data / "workspace_assets" / Path(
            *PurePosixPath(legacy_asset_key).parts
        )
        legacy_asset_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_asset_path.write_bytes(legacy_asset_payload)
        legacy_operation_id = uuid.uuid4()
        legacy_artifact_id = uuid.uuid4()
        legacy_artifact_payload = b"account-owned-legacy-anonymous-artifact"
        legacy_artifact_digest = sha256(legacy_artifact_payload).hexdigest()
        legacy_artifact_key = PurePosixPath(
            "workspace-artifacts",
            "anonymous",
            str(legacy_operation_id),
            f"{legacy_artifact_id}.pptx",
        ).as_posix()
        legacy_artifact_path = app_data / Path(
            *PurePosixPath(legacy_artifact_key).parts
        )
        legacy_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_artifact_path.write_bytes(legacy_artifact_payload)
        account_directories = [
            app_data / name / "users" / str(OWNER_ID)
            for name in (
                "images",
                "exports",
                "uploads",
                "pptx-to-html",
                "pptx-to-json",
            )
        ] + [temp_directory / str(OWNER_ID)]
        for index, directory in enumerate(account_directories):
            directory.mkdir(parents=True)
            directory.joinpath(f"legacy-{index}.bin").write_bytes(b"private")
        async with maker() as session, session.begin():
            session.add(
                User(
                    id=OWNER_ID,
                    username="cascade-owner",
                    hashed_password="test-only",
                )
            )
            await session.flush()
            presentation = PresentationModel(
                content="account private",
                n_slides=1,
                language="en",
                layout={"name": "private"},
                lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
            )
            session.add(presentation)
            await session.flush()
            presentation_id = presentation.id
            template_id = f"account-template-{uuid.uuid4()}"
            session.add(
                TemplateV2(
                    id=template_id,
                    presentation_id=presentation_id,
                    name="Account private template",
                    revision=1,
                )
            )
            session.add(
                TemplateV2LocalState(
                    template_id=template_id,
                    presentation_id=presentation_id,
                    revision=1,
                )
            )
            conversation = WorkspaceConversation(presentation_id=presentation_id)
            session.add(conversation)
            await session.flush()
            session.add(
                WorkspaceAsset(
                    id=asset_id,
                    conversation_id=conversation.id,
                    state="ready",
                    original_name="account.pdf",
                    media_type="application/pdf",
                    size_bytes=len(asset_payload),
                    storage_key=asset_key,
                    sha256=asset_digest,
                    version=1,
                )
            )
            session.add(
                WorkspaceAsset(
                    id=legacy_asset_id,
                    owner_id=None,
                    conversation_id=conversation.id,
                    state="ready",
                    original_name="legacy-account.pdf",
                    media_type="application/pdf",
                    size_bytes=len(legacy_asset_payload),
                    storage_key=legacy_asset_key,
                    sha256=legacy_asset_digest,
                    version=1,
                )
            )
            session.add(
                WorkspaceOperation(
                    id=legacy_operation_id,
                    conversation_id=conversation.id,
                    presentation_id=presentation_id,
                    anchor_position=1,
                    kind="legacy.account-cleanup",
                    idempotency_key_hash="a" * 64,
                    request_hash="b" * 64,
                    request_payload={},
                )
            )
            await session.flush()
            session.add(
                WorkspaceArtifact(
                    id=legacy_artifact_id,
                    owner_id=None,
                    operation_id=legacy_operation_id,
                    presentation_id=None,
                    kind="export.pptx",
                    status="ready",
                    storage_key=legacy_artifact_key,
                    sha256=legacy_artifact_digest,
                    size_bytes=len(legacy_artifact_payload),
                    source_revision=1,
                )
            )
            await session.flush()
            # These rows emulate pre-ownership legacy data. ORM column defaults
            # populate the active owner even when ``None`` is passed, so clear
            # the denormalized owner only after the graph itself is durable.
            await session.execute(
                update(WorkspaceAsset)
                .where(WorkspaceAsset.id == legacy_asset_id)
                .values(owner_id=None)
            )
            await session.execute(
                update(WorkspaceArtifact)
                .where(WorkspaceArtifact.id == legacy_artifact_id)
                .values(owner_id=None)
            )

        async with maker() as session, session.begin():
            assert (
                await snapshot_account_workspace_cleanup_targets(
                    session, owner_id=OWNER_ID
                )
                == 9
            )
            assert (
                await delete_account_restricted_dependents(
                    session,
                    owner_id=OWNER_ID,
                )
                == 1
            )
            await session.execute(delete(User).where(User.id == OWNER_ID))

        async with maker() as session:
            assert await session.get(User, OWNER_ID) is None
            assert await session.get(PresentationModel, presentation_id) is None
            assert await session.get(TemplateV2, template_id) is None
            assert await session.get(TemplateV2LocalState, template_id) is None
            assert await session.get(WorkspaceAsset, asset_id) is None
            assert await session.get(WorkspaceAsset, legacy_asset_id) is None
            assert await session.get(WorkspaceArtifact, legacy_artifact_id) is None
            cleanup_rows = list(
                await session.scalars(select(WorkspaceCleanupJournal))
            )
            assert len(cleanup_rows) == 9
            assert {row.owner_id_snapshot for row in cleanup_rows} == {
                None,
                OWNER_ID,
            }
            assert {row.status for row in cleanup_rows} == {"pending"}
            assert sum(
                row.storage_namespace == "account_owned_tree"
                for row in cleanup_rows
            ) == 6
            asset_cleanup = next(
                row
                for row in cleanup_rows
                if row.storage_namespace == "workspace_asset"
            )
            assert asset_cleanup.presentation_id_snapshot == presentation_id
            assert sum(
                row.storage_namespace == "workspace_asset"
                for row in cleanup_rows
            ) == 2
            legacy_artifact_cleanup = next(
                row
                for row in cleanup_rows
                if row.storage_namespace == "workspace_artifact"
            )
            assert legacy_artifact_cleanup.operation_id_snapshot == legacy_operation_id
            assert legacy_artifact_cleanup.presentation_id_snapshot is None

        assert asset_path.exists()
        assert legacy_asset_path.exists()
        assert legacy_artifact_path.exists()
        result = await sweep_workspace_cleanup_journal(
            session_factory=maker, limit=16
        )
        assert result["deleted"] == 9
        assert not asset_path.exists()
        assert not legacy_asset_path.exists()
        assert not legacy_artifact_path.exists()
        assert all(not directory.exists() for directory in account_directories)
        async with maker() as session:
            cleanup_rows = list(
                await session.scalars(select(WorkspaceCleanupJournal))
            )
            assert {row.status for row in cleanup_rows} == {"succeeded"}
            assert all(row.deleted_at is not None for row in cleanup_rows)
    finally:
        reset_current_owner_id(owner_token)
        await engine.dispose()


def test_account_owned_tree_is_exact_idempotent_and_fenced(
    tmp_path: Path, monkeypatch
):
    asyncio.run(_account_owned_tree_is_exact_idempotent_and_fenced(tmp_path, monkeypatch))


async def _account_owned_tree_is_exact_idempotent_and_fenced(
    tmp_path: Path, monkeypatch
):
    app_data = tmp_path / "tree-app-data"
    temp_directory = tmp_path / "tree-temp"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    monkeypatch.setenv("TEMP_DIRECTORY", str(temp_directory))
    root = (app_data / "uploads" / "users").resolve()
    owned_directory = root / str(OWNER_ID)
    owned_directory.mkdir(parents=True)
    owned_directory.joinpath("private.bin").write_bytes(b"private")
    target = account_owned_tree_cleanup_target(
        owner_id=OWNER_ID,
        root_kind="app_data.uploads",
        root=root,
    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tree.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: WorkspaceCleanupJournal.__table__.create(
                sync_connection
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = get_current_utc_datetime()
    try:
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(
                    session, targets=[target, target]
                )
                == 1
            )
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(session, targets=[target])
                == 0
            )

        claim = await claim_workspace_cleanup_target(
            session_factory=maker, now=now, lease_seconds=1
        )
        assert claim is not None

        escaped = replace(
            claim,
            storage_key=str(root / str(OWNER_ID) / ".." / str(OWNER_ID)),
        )
        with pytest.raises(
            WorkspaceCleanupDeleteError,
            match="workspace_cleanup_account_tree_path_invalid",
        ):
            _delete_claimed_storage(escaped)
        assert owned_directory.exists()

        _delete_claimed_storage(claim)
        assert not owned_directory.exists()

        # Simulate a crash after physical deletion. The expired lease is
        # reclaimed with a higher fence, and deleting the missing tree succeeds.
        recovered = await claim_workspace_cleanup_target(
            session_factory=maker,
            now=now + timedelta(seconds=2),
            lease_seconds=1,
        )
        assert recovered is not None
        assert recovered.journal_id == claim.journal_id
        assert recovered.fencing_token == claim.fencing_token + 1
        _delete_claimed_storage(recovered)
        assert (
            await finalize_workspace_cleanup_target(
                claim,
                succeeded=True,
                session_factory=maker,
                now=now + timedelta(seconds=2),
            )
            == "stale"
        )
        assert (
            await finalize_workspace_cleanup_target(
                recovered,
                succeeded=True,
                session_factory=maker,
                now=now + timedelta(seconds=2),
            )
            == "succeeded"
        )

        victim = root / str(uuid.uuid4())
        victim.mkdir(parents=True)
        victim_file = victim / "keep.bin"
        victim_file.write_bytes(b"keep")
        symlink_created = False
        try:
            owned_directory.symlink_to(victim, target_is_directory=True)
            symlink_created = True
        except OSError:
            # Windows hosts without Developer Mode cannot create a symlink.
            # Keep the guard contract covered there by simulating only the
            # exact owner leaf as a link; Linux CI exercises the real link.
            original_is_symlink = Path.is_symlink
            monkeypatch.setattr(
                Path,
                "is_symlink",
                lambda path: (
                    True
                    if path == owned_directory
                    else original_is_symlink(path)
                ),
            )
        symlink_claim = WorkspaceCleanupClaim(
            journal_id=uuid.uuid4(),
            owner_id=OWNER_ID,
            presentation_id=None,
            operation_id=None,
            storage_namespace=target.storage_namespace,
            resource_kind=target.resource_kind,
            storage_key=target.storage_key,
            sha256=None,
            size_bytes=None,
            source_revision=None,
            resource_metadata=dict(target.metadata or {}),
            token="symlink-test",
            fencing_token=1,
            attempts=1,
            max_attempts=8,
        )
        with pytest.raises(
            WorkspaceCleanupDeleteError,
            match="workspace_cleanup_account_tree_path_invalid",
        ):
            _delete_claimed_storage(symlink_claim)
        assert owned_directory.is_symlink()
        assert victim_file.read_bytes() == b"keep"
    finally:
        if "symlink_created" in locals() and symlink_created:
            os.unlink(owned_directory)
        await engine.dispose()


def test_cleanup_dead_letter_redrive_is_audited_fenced_and_eventual(
    tmp_path: Path,
):
    asyncio.run(
        _cleanup_dead_letter_redrive_is_audited_fenced_and_eventual(tmp_path)
    )


async def _cleanup_dead_letter_redrive_is_audited_fenced_and_eventual(
    tmp_path: Path,
):
    owner_id = uuid.uuid4()
    retry_root = (tmp_path / "redrive-retryable").resolve()
    retry_directory = retry_root / str(owner_id)
    retry_directory.mkdir(parents=True)
    retry_directory.joinpath("private.bin").write_bytes(b"private")
    retry_target = account_owned_tree_cleanup_target(
        owner_id=owner_id,
        root_kind="temp",
        root=retry_root,
    )

    unsafe_owner_id = uuid.uuid4()
    unsafe_target = account_owned_tree_cleanup_target(
        owner_id=unsafe_owner_id,
        root_kind="temp",
        root=(tmp_path / "redrive-unsafe").resolve(),
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'cleanup-redrive.db'}"
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: WorkspaceCleanupJournal.__table__.create(
                sync_connection
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    now = get_current_utc_datetime()
    first_admin_id = uuid.uuid4()
    second_admin_id = uuid.uuid4()
    try:
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(
                    session,
                    targets=[retry_target],
                )
                == 1
            )
            await session.execute(
                update(WorkspaceCleanupJournal).values(max_attempts=1)
            )

        retry_claim = await claim_workspace_cleanup_target(
            session_factory=maker,
            now=now,
        )
        assert retry_claim is not None
        assert (
            await finalize_workspace_cleanup_target(
                retry_claim,
                succeeded=False,
                error=WorkspaceCleanupDeleteError(
                    "object_store_temporarily_unavailable",
                    retryable=True,
                ),
                session_factory=maker,
                now=now,
            )
            == "dead_letter"
        )

        async def redrive_from_admin(admin_id: uuid.UUID):
            async with maker() as session:
                result = await redrive_workspace_cleanup_dead_letter(
                    session,
                    journal_id=retry_claim.journal_id,
                    requested_by=admin_id,
                    now=now + timedelta(seconds=1),
                )
                await session.commit()
                return result

        first, second = await asyncio.gather(
            redrive_from_admin(first_admin_id),
            redrive_from_admin(second_admin_id),
        )
        assert {first.outcome, second.outcome} == {
            "redriven",
            "already_active",
        }
        assert {first.status, second.status} == {"retry_wait"}
        assert {first.max_attempts, second.max_attempts} == {9}
        assert {first.redrive_count, second.redrive_count} == {1}

        async with maker() as session:
            row = await session.get(
                WorkspaceCleanupJournal,
                retry_claim.journal_id,
            )
            assert row is not None
            assert row.status == "retry_wait"
            assert row.cleanup_attempts == 1
            assert row.max_attempts == 9
            assert row.fencing_token == retry_claim.fencing_token + 1
            assert row.redrive_count == 1
            assert row.last_redriven_at is not None
            assert row.last_redriven_by_snapshot in {
                first_admin_id,
                second_admin_id,
            }
            assert row.last_error is not None
            assert row.last_error["code"] == "workspace_cleanup_redrive_requested"
            assert row.last_error["retryable"] is True
            assert uuid.UUID(row.last_error["requested_by"]) in {
                first_admin_id,
                second_admin_id,
            }

        recovered = await claim_workspace_cleanup_target(
            session_factory=maker,
            now=now + timedelta(seconds=1),
        )
        assert recovered is not None
        assert recovered.journal_id == retry_claim.journal_id
        assert recovered.attempts == 2
        assert recovered.fencing_token == retry_claim.fencing_token + 2
        _delete_claimed_storage(recovered)
        assert not retry_directory.exists()
        assert (
            await finalize_workspace_cleanup_target(
                recovered,
                succeeded=True,
                session_factory=maker,
                now=now + timedelta(seconds=1),
            )
            == "succeeded"
        )

        async with maker() as session:
            succeeded_row = await session.get(
                WorkspaceCleanupJournal,
                retry_claim.journal_id,
            )
            assert succeeded_row is not None
            assert succeeded_row.status == "succeeded"
            assert succeeded_row.last_error is None
            assert succeeded_row.redrive_count == 1
            assert succeeded_row.last_redriven_at is not None
            assert succeeded_row.last_redriven_by_snapshot in {
                first_admin_id,
                second_admin_id,
            }

        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(
                    session,
                    targets=[unsafe_target],
                )
                == 1
            )
            await session.execute(
                update(WorkspaceCleanupJournal)
                .where(
                    WorkspaceCleanupJournal.owner_id_snapshot == unsafe_owner_id
                )
                .values(max_attempts=1)
            )

        unsafe_claim = await claim_workspace_cleanup_target(
            session_factory=maker,
            now=now + timedelta(seconds=2),
        )
        assert unsafe_claim is not None
        assert unsafe_claim.journal_id != retry_claim.journal_id
        assert (
            await finalize_workspace_cleanup_target(
                unsafe_claim,
                succeeded=False,
                error=WorkspaceCleanupDeleteError(
                    "workspace_cleanup_account_tree_path_invalid",
                    retryable=False,
                ),
                session_factory=maker,
                now=now + timedelta(seconds=2),
            )
            == "dead_letter"
        )
        async with maker() as session:
            unsafe_result = await redrive_workspace_cleanup_dead_letter(
                session,
                journal_id=unsafe_claim.journal_id,
                requested_by=first_admin_id,
                now=now + timedelta(seconds=3),
            )
            await session.commit()
        assert unsafe_result.outcome == "non_retryable"
        assert unsafe_result.status == "dead_letter"
        assert unsafe_result.max_attempts == 1
        assert unsafe_result.redrive_count == 0
        async with maker() as session:
            unsafe_row = await session.get(
                WorkspaceCleanupJournal,
                unsafe_claim.journal_id,
            )
            assert unsafe_row is not None
            assert unsafe_row.status == "dead_letter"
            assert unsafe_row.fencing_token == unsafe_claim.fencing_token
            assert unsafe_row.redrive_count == 0
            assert unsafe_row.last_redriven_at is None
            assert unsafe_row.last_redriven_by_snapshot is None
            assert unsafe_row.last_error == {
                "code": "workspace_cleanup_account_tree_path_invalid",
                "retryable": False,
            }
    finally:
        await engine.dispose()


def test_authored_image_tree_is_exact_idempotent_and_owner_scoped(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _authored_image_tree_is_exact_idempotent_and_owner_scoped(
            tmp_path, monkeypatch
        )
    )


async def _authored_image_tree_is_exact_idempotent_and_owner_scoped(
    tmp_path: Path, monkeypatch
):
    app_data = tmp_path / "authored-image-app-data"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))
    presentation_id = uuid.uuid4()
    other_presentation_id = uuid.uuid4()
    owner_root = app_data / "images" / "users" / str(OWNER_ID)
    owned_tree = owner_root / "authored" / str(presentation_id)
    sibling_tree = owner_root / "authored" / str(other_presentation_id)
    owned_file = owned_tree / "sets" / ("a" * 64) / "slide-0.png"
    sibling_file = sibling_tree / "sets" / ("b" * 64) / "slide-0.png"
    owned_file.parent.mkdir(parents=True)
    sibling_file.parent.mkdir(parents=True)
    owned_file.write_bytes(b"delete")
    sibling_file.write_bytes(b"keep")
    target = authored_image_tree_cleanup_target(
        owner_id=OWNER_ID,
        presentation_id=presentation_id,
    )

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'authored-image.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: WorkspaceCleanupJournal.__table__.create(
                sync_connection
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(
                    session, targets=[target, target]
                )
                == 1
            )

        claim = await claim_workspace_cleanup_target(session_factory=maker)
        assert claim is not None
        escaped = replace(
            claim,
            storage_key=f"authored/{other_presentation_id}",
        )
        with pytest.raises(
            WorkspaceCleanupDeleteError,
            match="workspace_cleanup_authored_image_path_invalid",
        ):
            _delete_claimed_storage(escaped)
        assert owned_file.exists()
        assert sibling_file.read_bytes() == b"keep"

        _delete_claimed_storage(claim)
        _delete_claimed_storage(claim)
        assert not owned_tree.exists()
        assert sibling_file.read_bytes() == b"keep"
        assert (
            await finalize_workspace_cleanup_target(
                claim,
                succeeded=True,
                session_factory=maker,
            )
            == "succeeded"
        )
    finally:
        await engine.dispose()


def test_account_owned_tree_uses_snapshot_after_environment_root_rotation(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _account_owned_tree_uses_snapshot_after_environment_root_rotation(
            tmp_path, monkeypatch
        )
    )


async def _account_owned_tree_uses_snapshot_after_environment_root_rotation(
    tmp_path: Path, monkeypatch
):
    old_app_data = tmp_path / "old-app-data"
    old_temp = tmp_path / "old-temp"
    new_app_data = tmp_path / "new-app-data"
    new_temp = tmp_path / "new-temp"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(old_app_data))
    monkeypatch.setenv("TEMP_DIRECTORY", str(old_temp))

    old_roots = [
        ("app_data.uploads", (old_app_data / "uploads" / "users").resolve()),
        ("temp", old_temp.resolve()),
    ]
    new_roots = [
        (new_app_data / "uploads" / "users").resolve(),
        new_temp.resolve(),
    ]
    other_owner_id = uuid.uuid4()
    old_owner_directories: list[Path] = []
    old_other_directories: list[Path] = []
    for _kind, root in old_roots:
        owner_directory = root / str(OWNER_ID)
        other_directory = root / str(other_owner_id)
        owner_directory.mkdir(parents=True)
        other_directory.mkdir(parents=True)
        owner_directory.joinpath("delete.bin").write_bytes(b"delete")
        other_directory.joinpath("keep.bin").write_bytes(b"keep")
        old_owner_directories.append(owner_directory)
        old_other_directories.append(other_directory)

    targets = [
        account_owned_tree_cleanup_target(
            owner_id=OWNER_ID,
            root_kind=root_kind,
            root=root,
        )
        for root_kind, root in old_roots
    ]
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rotation.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: WorkspaceCleanupJournal.__table__.create(
                sync_connection
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(session, targets=targets)
                == 2
            )

        # Simulate a restart/deploy that points both storage settings elsewhere
        # after the original locations were durably snapshotted.
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(new_app_data))
        monkeypatch.setenv("TEMP_DIRECTORY", str(new_temp))
        new_owner_directories: list[Path] = []
        new_other_directories: list[Path] = []
        for root in new_roots:
            owner_directory = root / str(OWNER_ID)
            other_directory = root / str(other_owner_id)
            owner_directory.mkdir(parents=True)
            other_directory.mkdir(parents=True)
            owner_directory.joinpath("keep-owner.bin").write_bytes(b"keep-owner")
            other_directory.joinpath("keep-other.bin").write_bytes(b"keep-other")
            new_owner_directories.append(owner_directory)
            new_other_directories.append(other_directory)

        result = await sweep_workspace_cleanup_journal(
            session_factory=maker,
            limit=4,
        )
        assert result["deleted"] == 2
        assert result["dead_lettered"] == 0
        assert all(not path.exists() for path in old_owner_directories)
        assert all(path.joinpath("keep.bin").exists() for path in old_other_directories)
        assert all(
            path.joinpath("keep-owner.bin").exists()
            for path in new_owner_directories
        )
        assert all(
            path.joinpath("keep-other.bin").exists()
            for path in new_other_directories
        )
    finally:
        await engine.dispose()


def test_cleanup_targets_use_snapshot_after_storage_root_rotation(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _cleanup_targets_use_snapshot_after_storage_root_rotation(
            tmp_path, monkeypatch
        )
    )


async def _cleanup_targets_use_snapshot_after_storage_root_rotation(
    tmp_path: Path, monkeypatch
):
    old_app_data = tmp_path / "old-target-app-data"
    new_app_data = tmp_path / "new-target-app-data"
    old_checkpoints = tmp_path / "old-checkpoints"
    new_checkpoints = tmp_path / "new-checkpoints"
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(old_app_data))
    monkeypatch.setenv("AUTHORED_CHECKPOINT_DIRECTORY", str(old_checkpoints))

    presentation_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    job_id = uuid.uuid4()
    template_id = f"template-{uuid.uuid4()}"
    asset_payload = b"root-generation-asset"
    artifact_payload = b"root-generation-artifact"
    template_payload = b"root-generation-template"
    asset_key, asset_digest, old_asset_path = _asset_file(
        old_app_data,
        owner_id=OWNER_ID,
        asset_id=asset_id,
        payload=asset_payload,
    )
    artifact_key, artifact_digest, old_artifact_path = _artifact_file(
        old_app_data,
        owner_id=OWNER_ID,
        operation_id=operation_id,
        artifact_id=artifact_id,
        payload=artifact_payload,
    )
    old_checkpoint_path = old_checkpoints / str(job_id) / "slide-0.png"
    old_checkpoint_path.parent.mkdir(parents=True)
    old_checkpoint_path.write_bytes(b"checkpoint")
    template_key = f"template-v2/native/{template_id}/revision-1/source.pptx"
    old_template_path = old_app_data / Path(*PurePosixPath(template_key).parts)
    old_template_path.parent.mkdir(parents=True)
    old_template_path.write_bytes(template_payload)
    old_authored_path = (
        old_app_data
        / "images"
        / "users"
        / str(OWNER_ID)
        / "authored"
        / str(presentation_id)
        / "sets"
        / ("c" * 64)
        / "slide-0.png"
    )
    old_authored_path.parent.mkdir(parents=True)
    old_authored_path.write_bytes(b"authored")

    asset = WorkspaceAsset(
        id=asset_id,
        owner_id=OWNER_ID,
        state="ready",
        original_name="root-generation.pdf",
        media_type="application/pdf",
        size_bytes=len(asset_payload),
        storage_key=asset_key,
        sha256=asset_digest,
        version=1,
    )
    artifact = WorkspaceArtifact(
        id=artifact_id,
        owner_id=OWNER_ID,
        operation_id=operation_id,
        presentation_id=presentation_id,
        kind="export.pptx",
        status="ready",
        storage_key=artifact_key,
        sha256=artifact_digest,
        size_bytes=len(artifact_payload),
        source_revision=1,
    )

    def cleanup_targets():
        return [
            workspace_asset_cleanup_target(
                asset, presentation_id=presentation_id
            ),
            workspace_artifact_cleanup_target(artifact),
            authored_checkpoint_cleanup_target(
                job_id,
                owner_id=OWNER_ID,
                presentation_id=presentation_id,
                operation_id=operation_id,
            ),
            template_v2_native_cleanup_target(
                template_id=template_id,
                revision=1,
                storage_key=template_key,
                sha256_value=sha256(template_payload).hexdigest(),
                size_bytes=len(template_payload),
                owner_id=OWNER_ID,
                presentation_id=presentation_id,
            ),
            authored_image_tree_cleanup_target(
                owner_id=OWNER_ID,
                presentation_id=presentation_id,
            ),
        ]

    old_targets = cleanup_targets()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'target-rotation.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: WorkspaceCleanupJournal.__table__.create(
                sync_connection
            )
        )
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(
                    session, targets=old_targets
                )
                == len(old_targets)
            )

        monkeypatch.setenv("APP_DATA_DIRECTORY", str(new_app_data))
        monkeypatch.setenv(
            "AUTHORED_CHECKPOINT_DIRECTORY", str(new_checkpoints)
        )
        _, _, new_asset_path = _asset_file(
            new_app_data,
            owner_id=OWNER_ID,
            asset_id=asset_id,
            payload=asset_payload,
        )
        _, _, new_artifact_path = _artifact_file(
            new_app_data,
            owner_id=OWNER_ID,
            operation_id=operation_id,
            artifact_id=artifact_id,
            payload=artifact_payload,
        )
        new_checkpoint_path = new_checkpoints / str(job_id) / "slide-0.png"
        new_checkpoint_path.parent.mkdir(parents=True)
        new_checkpoint_path.write_bytes(b"checkpoint")
        new_template_path = new_app_data / Path(
            *PurePosixPath(template_key).parts
        )
        new_template_path.parent.mkdir(parents=True)
        new_template_path.write_bytes(template_payload)
        new_authored_path = (
            new_app_data
            / "images"
            / "users"
            / str(OWNER_ID)
            / "authored"
            / str(presentation_id)
            / "sets"
            / ("c" * 64)
            / "slide-0.png"
        )
        new_authored_path.parent.mkdir(parents=True)
        new_authored_path.write_bytes(b"authored")

        result = await sweep_workspace_cleanup_journal(
            session_factory=maker,
            limit=len(old_targets),
        )
        assert result["deleted"] == len(old_targets)
        assert result["dead_lettered"] == 0
        assert all(
            not path.exists()
            for path in (
                old_asset_path,
                old_artifact_path,
                old_checkpoint_path,
                old_template_path,
                old_authored_path,
            )
        )
        assert all(
            path.exists()
            for path in (
                new_asset_path,
                new_artifact_path,
                new_checkpoint_path,
                new_template_path,
                new_authored_path,
            )
        )

        new_targets = cleanup_targets()
        assert {
            target.metadata["storage_backend_generation"]
            for target in old_targets
        }.isdisjoint(
            {
                target.metadata["storage_backend_generation"]
                for target in new_targets
            }
        )
        async with maker() as session, session.begin():
            assert (
                await enqueue_workspace_cleanup_targets(
                    session, targets=new_targets
                )
                == len(new_targets)
            )
        assert all(
            path.exists()
            for path in (
                new_asset_path,
                new_artifact_path,
                new_checkpoint_path,
                new_template_path,
                new_authored_path,
            )
        )
    finally:
        await engine.dispose()
