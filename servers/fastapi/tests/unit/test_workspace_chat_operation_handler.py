from __future__ import annotations

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
from models.sql.slide import SlideModel
from models.sql.template_v2 import TemplateV2
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
from services.chat.conversation_store import ChatConversationStore
from services.workspace_chat_memory_outbox import (
    WORKSPACE_CHAT_MEMORY_TOPIC,
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
    RetryableWorkspaceOperationError,
    WorkspaceOperationCancelled,
    WorkspaceOperationClaim,
)
TABLES = [
    User.__table__,
    ImageAsset.__table__,
    PresentationModel.__table__,
    SlideModel.__table__,
    TemplateV2.__table__,
    ChatHistoryMessageModel.__table__,
    WorkspaceConversation.__table__,
    WorkspaceMessage.__table__,
    WorkspaceOperation.__table__,
    WorkspaceActionReceipt.__table__,
    WorkspaceOperationEvent.__table__,
    WorkspaceOutbox.__table__,
    PresentationExportAttempt.__table__,
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
        session.add(presentation)
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
            await ChatConversationStore(self._session).append_turn(
                presentation_id=self._presentation_id,
                conversation_id=self._conversation_id,
                user_message="change the deck",
                assistant_message="Deck updated.",
                tool_calls=["saveSlide"],
            )
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


def test_chat_mutations_and_revision_publish_in_one_commit(tmp_path: Path):
    asyncio.run(_chat_mutations_and_revision_publish_in_one_commit(tmp_path))


async def _chat_mutations_and_revision_publish_in_one_commit(tmp_path: Path):
    engine, maker = await _database(tmp_path / "publish.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=3, expected_revision=3
        )
        context = _Context(claim)
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_service_factory(),
        )

        with patch(
            "services.chat.conversation_store.CHAT_MEMORY_STORE.store_chat_turn",
            new=AsyncMock(),
        ) as direct_memory_write:
            result = await handler.execute(context)

        # The SQL turn and publication commit first; the external write is
        # represented only by a durable outbox record at this point.
        direct_memory_write.assert_not_awaited()

        assert result.result_revision == 4
        assert result.payload["summary"] == "Deck updated."
        assert context.stages == [
            "reading_presentation",
            "applying_command",
            "publishing_revision",
        ]
        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert presentation.content == "after"
            assert presentation.revision == 4
            assert operation.state == "succeeded"
            assert operation.result_revision == 4
            assert operation.result["tool_calls"] == ["saveSlide"]
            turn_rows = list(
                (
                    await session.execute(
                        select(WorkspaceMessage).order_by(
                            WorkspaceMessage.position
                        )
                    )
                ).scalars()
            )
            assert [row.role for row in turn_rows] == ["assistant"]
            assert [row.content for row in turn_rows] == [
                "Deck updated.",
            ]
            outbox = (
                await session.execute(
                    select(WorkspaceOutbox).where(
                        WorkspaceOutbox.topic == WORKSPACE_CHAT_MEMORY_TOPIC
                    )
                )
            ).scalar_one()
            assert outbox.topic == WORKSPACE_CHAT_MEMORY_TOPIC
            assert outbox.status == "pending"
            assert outbox.payload == {
                "operation_id": str(operation_id),
                "presentation_id": str(presentation_id),
                "conversation_id": str(claim.conversation_id),
                "result_revision": 4,
            }
            serialized_payload = str(outbox.payload)
            assert "change the deck" not in serialized_payload
            assert "Deck updated" not in serialized_payload
            assert "secret" not in serialized_payload

        external_memory = SimpleNamespace(store_chat_turn=AsyncMock())

        async def deliver(payload):
            await deliver_workspace_chat_memory(
                payload,
                session_factory=maker,
                memory_store=external_memory,
            )

        async def deliver_workspace_event(_payload):
            return None

        # Delivery itself is covered here; dispatcher ordering/retry behavior
        # has a dedicated suite and is unrelated to the runtime boundary.
        await deliver(outbox.payload)

        external_memory.store_chat_turn.assert_awaited_once_with(
            presentation_id=presentation_id,
            conversation_id=claim.conversation_id,
            user_message="change the deck",
            assistant_message="Deck updated.",
            delivery_id=f"workspace-chat:{operation_id}:revision:4",
            strict=True,
        )
    finally:
        await engine.dispose()


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


def test_failure_after_inner_commit_rolls_back_all_mutations(tmp_path: Path):
    asyncio.run(_failure_after_inner_commit_rolls_back_all_mutations(tmp_path))


async def _failure_after_inner_commit_rolls_back_all_mutations(tmp_path: Path):
    engine, maker = await _database(tmp_path / "rollback.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=1, expected_revision=1
        )
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_service_factory(fail_after_flush=True),
        )

        with patch(
            "services.chat.conversation_store.CHAT_MEMORY_STORE.store_chat_turn",
            new=AsyncMock(),
        ) as direct_memory_write, pytest.raises(
            RetryableWorkspaceOperationError
        ) as caught:
            await handler.execute(_Context(claim))
        assert caught.value.code == "presentation_edit_failed"
        direct_memory_write.assert_not_awaited()

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert presentation.content == "before"
            assert presentation.revision == 1
            assert operation.state == "running"
            assert operation.result_revision is None
            assert (await session.execute(select(WorkspaceOutbox))).first() is None
            assert (
                await session.execute(select(ChatHistoryMessageModel))
            ).first() is None
    finally:
        await engine.dispose()


def test_stale_revision_rolls_back_tool_mutations(tmp_path: Path):
    asyncio.run(_stale_revision_rolls_back_tool_mutations(tmp_path))


async def _stale_revision_rolls_back_tool_mutations(tmp_path: Path):
    engine, maker = await _database(tmp_path / "stale.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=2, expected_revision=1
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
        assert caught.value.code == "stale_presentation_revision"
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


def test_cancel_after_sql_turn_rolls_back_without_external_memory(tmp_path: Path):
    asyncio.run(_cancel_after_sql_turn_rolls_back_without_external_memory(tmp_path))


async def _cancel_after_sql_turn_rolls_back_without_external_memory(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "cancel.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=5, expected_revision=5
        )
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_service_factory(),
        )

        with patch(
            "services.chat.conversation_store.CHAT_MEMORY_STORE.store_chat_turn",
            new=AsyncMock(),
        ) as direct_memory_write, pytest.raises(WorkspaceOperationCancelled):
            await handler.execute(_CancelAfterApplyContext(claim))
        direct_memory_write.assert_not_awaited()

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert presentation.content == "before"
            assert presentation.revision == 5
            assert operation.state == "running"
            assert operation.result_revision is None
            assert (await session.execute(select(WorkspaceOutbox))).first() is None
            assert (
                await session.execute(select(ChatHistoryMessageModel))
            ).first() is None
    finally:
        await engine.dispose()


def test_image_write_is_hash_promoted_with_revision_publication(tmp_path: Path):
    asyncio.run(_image_write_is_hash_promoted_with_revision_publication(tmp_path))


async def _image_write_is_hash_promoted_with_revision_publication(tmp_path: Path):
    engine, maker = await _database(tmp_path / "image-publish.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=7, expected_revision=7
        )
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_image_writing_service_factory(),
        )

        result = await handler.execute(_Context(claim))
        assert result.result_revision == 8

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            image = (await session.execute(select(ImageAsset))).scalar_one()
            rows = list(
                (
                    await session.execute(
                        select(WorkspaceArtifact).order_by(WorkspaceArtifact.kind)
                    )
                ).scalars()
            )
            journal = next(row for row in rows if row.kind == "chat.image.journal")
            artifact = next(row for row in rows if row.kind == "chat.image")
            final_path = Path(os.environ["APP_DATA_DIRECTORY"]) / artifact.storage_key
            assert journal.status == "deleted"
            assert artifact.status == "ready"
            assert artifact.source_revision == 8
            assert final_path.is_file()
            assert final_path.name == f"{artifact.sha256}.png"
            assert str(operation_id) in artifact.storage_key
            assert image.path == str(final_path)
            assert presentation.content == str(final_path)
            assert result.payload["summary"] == str(final_path)
            assert not list(
                Path(os.environ["APP_DATA_DIRECTORY"]).rglob("*.png")
            ) == []
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", ["cancel", "stale", "provider"])
def test_unpublished_image_write_is_exactly_compensated(
    tmp_path: Path, failure: str
):
    asyncio.run(_unpublished_image_write_is_exactly_compensated(tmp_path, failure))


async def _unpublished_image_write_is_exactly_compensated(
    tmp_path: Path, failure: str
):
    engine, maker = await _database(tmp_path / f"image-{failure}.db")
    try:
        current_revision = 2 if failure == "stale" else 1
        expected_revision = 1
        presentation_id, _operation_id, claim = await _seed(
            maker,
            presentation_revision=current_revision,
            expected_revision=expected_revision,
        )
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_image_writing_service_factory(
                fail_after_write=failure == "provider"
            ),
        )
        context = (
            _CancelAfterApplyContext(claim)
            if failure == "cancel"
            else _Context(claim)
        )
        expected_error = {
            "cancel": WorkspaceOperationCancelled,
            "stale": NonRetryableWorkspaceOperationError,
            "provider": RetryableWorkspaceOperationError,
        }[failure]

        with pytest.raises(expected_error):
            await handler.execute(context)

        app_data = Path(os.environ["APP_DATA_DIRECTORY"])
        assert not list(app_data.rglob("*.png"))
        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            assert presentation.content == "before"
            assert (await session.execute(select(ImageAsset))).first() is None
            artifacts = list(
                (await session.execute(select(WorkspaceArtifact))).scalars()
            )
            assert len(artifacts) == 1
            assert artifacts[0].kind == "chat.image.journal"
            assert artifacts[0].status == "deleted"
    finally:
        await engine.dispose()


def test_retry_after_provider_write_failure_has_no_duplicate_artifact(
    tmp_path: Path,
):
    asyncio.run(_retry_after_provider_write_failure_has_no_duplicate_artifact(tmp_path))


async def _retry_after_provider_write_failure_has_no_duplicate_artifact(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "image-retry.db")
    try:
        _presentation_id, _operation_id, claim = await _seed(
            maker, presentation_revision=1, expected_revision=1
        )
        failing_handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_image_writing_service_factory(fail_after_write=True),
        )
        with pytest.raises(RetryableWorkspaceOperationError):
            await failing_handler.execute(_Context(claim))

        succeeding_handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_image_writing_service_factory(),
        )
        await succeeding_handler.execute(_Context(claim))

        async with maker() as session:
            ready = list(
                (
                    await session.execute(
                        select(WorkspaceArtifact).where(
                            WorkspaceArtifact.kind == "chat.image",
                            WorkspaceArtifact.status == "ready",
                        )
                    )
                ).scalars()
            )
            journals = list(
                (
                    await session.execute(
                        select(WorkspaceArtifact).where(
                            WorkspaceArtifact.kind == "chat.image.journal"
                        )
                    )
                ).scalars()
            )
            assert len(ready) == 1
            assert len(journals) == 2
            assert {journal.status for journal in journals} == {"deleted"}
        assert len(list(Path(os.environ["APP_DATA_DIRECTORY"]).rglob("*.png"))) == 1
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


def test_handler_passes_only_db_vetted_authored_context_and_publishes_one_message(
    tmp_path: Path,
):
    asyncio.run(_handler_passes_only_db_vetted_smart_context(tmp_path))


async def _handler_passes_only_db_vetted_smart_context(tmp_path: Path):
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
            "smart_selection": {
                "html": "<section><h1>Selected</h1></section>",
                "selected_text": "Selected",
                "slide_index": 2,
                # Unknown fields must not enter the typed context.
                "provider_response": "secret",
            },
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
        assert context.smart_selection is None
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


def test_handler_revalidates_assets_before_runtime(tmp_path: Path):
    asyncio.run(_handler_revalidates_assets_before_runtime(tmp_path))


async def _handler_revalidates_assets_before_runtime(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-revalidation.db")
    try:
        presentation_id, operation_id, claim = await _seed(
            maker, presentation_revision=9, expected_revision=9
        )
        asset_id = uuid.uuid4()
        request_payload = {
            **dict(claim.request_payload),
            "asset_ids": [str(asset_id)],
        }
        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, operation_id)
            operation.request_payload = request_payload
            session.add(
                WorkspaceAsset(
                    id=asset_id,
                    conversation_id=claim.conversation_id,
                    state="quarantined",
                    original_name="unsafe.pdf",
                    media_type="application/pdf",
                    size_bytes=12,
                    storage_key="quarantine/unsafe.pdf",
                    sha256="b" * 64,
                )
            )

        captured: list[object] = []
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_capturing_runtime_factory(captured),
        )
        with pytest.raises(NonRetryableWorkspaceOperationError) as caught:
            await handler.execute(
                _Context(replace(claim, request_payload=request_payload))
            )
        assert caught.value.code == "workspace_asset_not_ready"
        assert captured == []
        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            assert presentation.revision == 9
            assert list(
                (await session.scalars(select(WorkspaceMessage))).all()
            ) == []
    finally:
        await engine.dispose()


def test_standard_deck_discards_spoofed_smart_selection(tmp_path: Path):
    asyncio.run(_standard_deck_discards_spoofed_smart_selection(tmp_path))


async def _standard_deck_discards_spoofed_smart_selection(tmp_path: Path):
    engine, maker = await _database(tmp_path / "standard-selection.db")
    try:
        _presentation_id, _operation_id, claim = await _seed(
            maker, presentation_revision=3, expected_revision=3
        )
        request_payload = {
            **dict(claim.request_payload),
            "context": {
                "smart_selection": {
                    "html": "<script>untrusted()</script>",
                    "selected_text": "untrusted",
                }
            },
        }
        captured: list[object] = []
        handler = PresentationChatOperationHandler(
            session_factory=maker,
            service_factory=_capturing_runtime_factory(captured),
        )
        await handler.execute(
            _Context(replace(claim, request_payload=request_payload))
        )
        assert len(captured) == 1
        assert captured[0].generation_mode == "standard"
        assert captured[0].smart_selection is None
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
