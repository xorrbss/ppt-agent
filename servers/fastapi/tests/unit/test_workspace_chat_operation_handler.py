from __future__ import annotations

from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
import os
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.public_artifact import PublicArtifact
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.chat import ChatTurnResult
from services.agent_runtime import AgentRunResult
from services.workspace_chat_memory_outbox import (
    deliver_workspace_chat_memory,
)
from services.workspace_chat_operation_handler import (
    PresentationChatOperationHandler,
)
from services.workspace_artifact_publication_service import (
    prepare_workspace_chat_artifact_staging,
    sweep_workspace_artifact_cleanup,
)
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationClaim,
)
TABLES = [
    User.__table__,
    ImageAsset.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    ChatHistoryMessageModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceOperation.__table__,
    WorkspaceActionReceipt.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    PresentationExportAttempt.__table__,
    # workspace_artifacts.public_artifact_id carries a FK to public_artifacts.
    # SQLite resolves FK parent tables at DML time, so omitting this table
    # fails every insert here even while the column stays NULL.
    PublicArtifact.__table__,
    WorkspaceArtifact.__table__,
    WorkspaceAsset.__table__,
]


async def _database(path: Path):
    app_data = path.parent / f"{path.stem}-app-data"
    app_data.mkdir(parents=True, exist_ok=True)
    os.environ["APP_DATA_DIRECTORY"] = str(app_data)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(maker, *, presentation_revision: int, expected_revision: int):
    operation_id = uuid.uuid4()
    attempt_token = "attempt-token"
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="before",
            n_slides=1,
            language="en",
            layout=None,
            mode="authored",
            theme={"mode": "authored"},
            revision=presentation_revision,
        )
        await add_existing_authored_presentation(session, presentation)
        await session.flush()
        conversation = WorkspaceConversation(
            presentation_id=presentation.id,
            next_position=2,
        )
        session.add(conversation)
        await session.flush()
        session.add(
            WorkspaceOperation(
                id=operation_id,
                conversation_id=conversation.id,
                presentation_id=presentation.id,
                anchor_position=1,
                kind="presentation.edit",
                state="running",
                idempotency_key_hash="1" * 64,
                request_hash="2" * 64,
                request_payload={"command": "change the deck"},
                expected_revision=expected_revision,
                attempt_number=1,
                attempt_token=attempt_token,
                fencing_token=1,
            )
        )
    claim = WorkspaceOperationClaim(
        operation_id=operation_id,
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=conversation.id,
        presentation_id=presentation.id,
        kind="presentation.edit",
        request_payload={"command": "change the deck"},
        expected_revision=expected_revision,
        attempt_number=1,
        attempt_token=attempt_token,
        fencing_token=1,
    )
    return presentation.id, operation_id, claim


class _Context:
    def __init__(self, claim: WorkspaceOperationClaim):
        self.claim = claim
        self.stages: list[str] = []

    async def progress(self, *, stage, current=None, total=None, percent=None):
        self.stages.append(stage)
        return True

    async def cancellation_requested(self):
        return False

    async def raise_if_cancelled(self):
        return None


class _CancelAfterApplyContext(_Context):
    def __init__(self, claim: WorkspaceOperationClaim):
        super().__init__(claim)
        self._checks = 0

    async def raise_if_cancelled(self):
        self._checks += 1
        if self._checks >= 2:
            raise WorkspaceOperationCancelled("workspace_operation_cancelled")


def _service_factory(*, fail_after_flush: bool = False):
    class FakeChatService:
        def __init__(
            self,
            *,
            sql_session,
            presentation_id,
            conversation_id,
            artifact_directory,
        ):
            self._session = sql_session
            self._presentation_id = presentation_id
            self._conversation_id = conversation_id
            self._artifact_directory = artifact_directory

        async def generate_reply(self, _command):
            presentation = await self._session.get(
                PresentationModel, self._presentation_id
            )
            presentation.content = "after"
            self._session.add(presentation)
            # The adapter must turn this legacy commit into a flush.
            await self._session.commit()
            await self._session.commit()
            if fail_after_flush:
                raise RuntimeError("provider failed after a tool mutation")
            return ChatTurnResult(
                conversation_id=self._conversation_id,
                response_text="Deck updated.",
                tool_calls=["saveSlide"],
            )

    return FakeChatService


def _image_writing_service_factory(*, fail_after_write: bool = False):
    class FakeImageWritingChatService:
        def __init__(
            self,
            *,
            sql_session,
            presentation_id,
            conversation_id,
            artifact_directory,
        ):
            self._session = sql_session
            self._presentation_id = presentation_id
            self._conversation_id = conversation_id
            self._artifact_directory = artifact_directory

        async def generate_reply(self, _command):
            image_path = Path(self._artifact_directory) / (
                f"{uuid.uuid4()}.png"
            )
            image_path.write_bytes(b"stable-provider-image")
            self._session.add(ImageAsset(path=str(image_path)))
            presentation = await self._session.get(
                PresentationModel, self._presentation_id
            )
            presentation.content = str(image_path)
            await self._session.commit()
            if fail_after_write:
                raise RuntimeError("provider failed after image write")
            return ChatTurnResult(
                conversation_id=self._conversation_id,
                response_text=str(image_path),
                tool_calls=[str(image_path)],
            )

    return FakeImageWritingChatService


def test_memory_delivery_rejects_an_unpublished_operation(tmp_path: Path):
    asyncio.run(_memory_delivery_rejects_an_unpublished_operation(tmp_path))


async def _memory_delivery_rejects_an_unpublished_operation(tmp_path: Path):
    engine, maker = await _database(tmp_path / "unpublished.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=3, expected_revision=3
        )
        external_memory = SimpleNamespace(store_chat_turn=AsyncMock())

        with pytest.raises(
            ValueError, match="workspace_chat_memory_publication_missing"
        ):
            await deliver_workspace_chat_memory(
                {
                    "operation_id": str(operation_id),
                    "presentation_id": str(presentation_id),
                    "conversation_id": str(claim.conversation_id),
                    "result_revision": 4,
                },
                session_factory=maker,
                memory_store=external_memory,
            )

        external_memory.store_chat_turn.assert_not_awaited()
    finally:
        await engine.dispose()


def test_memory_delivery_serializes_non_atomic_provider_idempotency(tmp_path: Path):
    asyncio.run(
        _memory_delivery_serializes_non_atomic_provider_idempotency(tmp_path)
    )


async def _memory_delivery_serializes_non_atomic_provider_idempotency(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "memory-delivery-race.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=3, expected_revision=3
        )
        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, operation_id)
            operation.state = "succeeded"
            operation.result_revision = 4
            operation.result = {"summary": "Deck updated."}
            operation.attempt_token = None

        class NonAtomicMemoryStore:
            """Model the provider's separate get_all() then add() calls."""

            def __init__(self):
                self.records: list[str] = []
                self.checks = 0
                self.both_checked = asyncio.Event()

            async def store_chat_turn(self, *, delivery_id, **_kwargs):
                observed_absent = delivery_id not in self.records
                self.checks += 1
                if self.checks == 2:
                    self.both_checked.set()
                try:
                    await asyncio.wait_for(self.both_checked.wait(), timeout=0.1)
                except TimeoutError:
                    pass
                if observed_absent:
                    self.records.append(delivery_id)

        external_memory = NonAtomicMemoryStore()
        payload = {
            "operation_id": str(operation_id),
            "presentation_id": str(presentation_id),
            "conversation_id": str(claim.conversation_id),
            "result_revision": 4,
        }
        outcomes = await asyncio.gather(
            deliver_workspace_chat_memory(
                payload,
                session_factory=maker,
                memory_store=external_memory,
            ),
            deliver_workspace_chat_memory(
                payload,
                session_factory=maker,
                memory_store=external_memory,
            ),
            return_exceptions=True,
        )

        delivery_id = f"workspace-chat:{operation_id}:revision:4"
        assert external_memory.records == [delivery_id]
        assert sum(outcome is None for outcome in outcomes) >= 1
        assert all(
            outcome is None
            or (
                isinstance(outcome, RuntimeError)
                and str(outcome) == "workspace_chat_memory_delivery_in_progress"
            )
            for outcome in outcomes
        ), outcomes
        assert external_memory.checks == 1

        # An applied receipt is a durable replay short-circuit; the provider
        # is not consulted again on a later outbox retry.
        await deliver_workspace_chat_memory(
            payload,
            session_factory=maker,
            memory_store=external_memory,
        )
        assert external_memory.checks == 1
        async with maker() as session:
            receipt = (
                await session.execute(select(WorkspaceActionReceipt))
            ).scalar_one()
            assert receipt.status == "applied"
            assert receipt.operation_id == operation_id
    finally:
        await engine.dispose()


def test_pre_existing_authored_deck_is_refused_before_any_mutation(tmp_path: Path):
    """Chat editing served only authored decks. The authored driver was
    removed on 2026-11-10, so an existing authored deck no longer resolves
    and the handler refuses it, non-retryably, before any tool, memory or
    revision write. (Admission and the runner refuse it earlier with
    ``unsupported_presentation_driver``; this is the handler's own fence.)"""
    asyncio.run(_pre_existing_authored_deck_is_refused_before_any_mutation(tmp_path))


async def _pre_existing_authored_deck_is_refused_before_any_mutation(tmp_path: Path):
    engine, maker = await _database(tmp_path / "authored-refused.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=2, expected_revision=2
        )
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_service_factory(),
        )

        with patch(
            "services.chat.conversation_store.CHAT_MEMORY_STORE.store_chat_turn",
            new=AsyncMock(),
        ) as direct_memory_write, pytest.raises(
            NonRetryableWorkspaceOperationError
        ) as caught:
            await handler.execute(_Context(claim))
        assert caught.value.code == "compatibility_presentation_quarantined"
        direct_memory_write.assert_not_awaited()

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert presentation.content == "before"
            assert presentation.revision == 2
            assert operation.state == "running"
            assert operation.result_revision is None
            assert (await session.execute(select(WorkspaceOutbox))).first() is None
            assert (
                await session.execute(select(ChatHistoryMessageModel))
            ).first() is None
    finally:
        await engine.dispose()


def _capturing_runtime_factory(captured: list[object]):
    class CapturingRuntime:
        def __init__(self, *, sql_session, presentation_id, conversation_id):
            self._presentation_id = presentation_id
            self._conversation_id = conversation_id

        async def execute_run(self, context, sink):
            captured.append(context)
            await sink.assistant_delta("Canonical answer.")
            return AgentRunResult(
                conversation_id=self._conversation_id,
                assistant_output="Canonical answer.",
                tool_calls=("getSlideAtIndex",),
            )

    return CapturingRuntime


async def _handler_passes_only_db_vetted_authored_context(tmp_path: Path):
    engine, maker = await _database(tmp_path / "canonical-context.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=5, expected_revision=5
        )
        user_message_id = uuid.uuid4()
        ready_asset_id = uuid.uuid4()
        raw_context = {
            "viewport": {"width": 1440, "height": 900, "zoom": 1.25},
            "current_slide": {"index": 2},
        }
        request_payload = {
            **dict(claim.request_payload),
            "user_message_id": str(user_message_id),
            "asset_ids": [str(ready_asset_id)],
            "context": raw_context,
            # Client metadata is never used as authoritative attachment data.
            "attachments": [{"name": "spoofed.exe", "content": "secret"}],
        }
        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, operation_id)
            operation.request_payload = request_payload
            session.add_all(
                (
                    WorkspaceMessage(
                        id=user_message_id,
                        conversation_id=claim.conversation_id,
                        position=1,
                        role="user",
                        content="change the deck",
                    ),
                    WorkspaceAsset(
                        id=ready_asset_id,
                        conversation_id=claim.conversation_id,
                        state="ready",
                        original_name="authoritative.pdf",
                        media_type="application/pdf",
                        size_bytes=123,
                        storage_key="ready/authoritative.pdf",
                        sha256="a" * 64,
                    ),
                )
            )

        captured: list[object] = []
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_capturing_runtime_factory(captured),
        )
        result = await handler.execute(
            _Context(replace(claim, request_payload=request_payload))
        )

        assert result.result_revision == 5
        assert len(captured) == 1
        context = captured[0]
        assert context.generation_mode == "standard"
        assert context.history == ()  # current canonical user is not duplicated
        assert context.viewport.slide_index == 2
        assert [asset.name for asset in context.assets] == ["authoritative.pdf"]
        assert all(asset.name != "spoofed.exe" for asset in context.assets)

        async with maker() as session:
            messages = list(
                (
                    await session.scalars(
                        select(WorkspaceMessage).order_by(
                            WorkspaceMessage.position
                        )
                    )
                ).all()
            )
            legacy = list(
                (await session.scalars(select(ChatHistoryMessageModel))).all()
            )
            operation = await session.get(WorkspaceOperation, operation_id)
            assert [message.role for message in messages] == ["user", "assistant"]
            assert messages[-1].content == "Canonical answer."
            assert operation.result["assistant_message_id"] == str(messages[-1].id)
            assert legacy == []
    finally:
        await engine.dispose()


def test_expired_chat_image_journal_recovers_crash_orphan(tmp_path: Path):
    asyncio.run(_expired_chat_image_journal_recovers_crash_orphan(tmp_path))


async def _expired_chat_image_journal_recovers_crash_orphan(tmp_path: Path):
    engine, maker = await _database(tmp_path / "image-crash-recovery.db")
    try:
        _presentation_id, _operation_id, claim = await _seed(
            maker, presentation_revision=4, expected_revision=4
        )
        async with maker() as session:
            staging = await prepare_workspace_chat_artifact_staging(
                session,
                operation_id=claim.operation_id,
                presentation_id=claim.presentation_id,
                source_revision=5,
                attempt_token=claim.attempt_token,
                fencing_token=claim.fencing_token,
            )
        orphan = staging.staging_directory / f"{uuid.uuid4()}.png"
        orphan.write_bytes(b"crash-orphan")
        expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        async with maker() as session, session.begin():
            journal = await session.get(WorkspaceArtifact, staging.artifact_id)
            journal.expires_at = expired_at

        outcome = await sweep_workspace_artifact_cleanup(
            session_factory=maker,
            now=datetime.now(timezone.utc),
        )
        assert outcome["deleted"] == 1
        assert not staging.staging_directory.parent.exists()
        async with maker() as session:
            journal = await session.get(WorkspaceArtifact, staging.artifact_id)
            assert journal.status == "deleted"
    finally:
        await engine.dispose()
