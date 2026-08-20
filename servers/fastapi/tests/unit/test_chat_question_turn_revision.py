"""A turn that only answers must not invalidate everyone's expected_revision.

Publication used to advance the token for every chat turn, so asking "why is
page 5 empty?" bumped the deck revision and every other client's
``expected_revision`` went stale for an edit that never happened.  The turn
buffers its unit of work, so the handler can tell an answer from an edit by
what the turn actually holds rather than by which tools it named.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.chat_history_message import ChatHistoryMessageModel
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.presentation_export_attempt import PresentationExportAttempt
from models.sql.template_v2 import TemplateV2
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceArtifact,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    WorkspaceOperationEvent,
    WorkspaceOutbox,
)
from services.chat import ChatTurnResult
from services.chat.conversation_store import ChatConversationStore
from services.workspace_chat_operation_handler import (
    PresentationChatOperationHandler,
)
from services.workspace_operation_handler_registry import (
    WorkspaceOperationClaim,
)


TABLES = [
    User.__table__,
    ImageAsset.__table__,
    PresentationModel.__table__,
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
]

START_REVISION = 7


async def _database(path: Path):
    app_data = path.parent / f"{path.stem}-app-data"
    app_data.mkdir(parents=True, exist_ok=True)
    os.environ["APP_DATA_DIRECTORY"] = str(app_data)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(maker):
    operation_id = uuid.uuid4()
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="before",
            n_slides=1,
            language="ko",
            layout={"name": "deck"},
            revision=START_REVISION,
        )
        session.add(presentation)
        await session.flush()
        conversation = WorkspaceConversation(
            presentation_id=presentation.id, next_position=2
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
                request_payload={"command": "5페이지 비어있네 어떻게 해야해?"},
                expected_revision=START_REVISION,
                attempt_number=1,
                attempt_token="attempt-token",
                fencing_token=1,
            )
        )
    return presentation.id, operation_id, WorkspaceOperationClaim(
        operation_id=operation_id,
        owner_id=None,
        owner_scope="anonymous",
        conversation_id=conversation.id,
        presentation_id=presentation.id,
        kind="presentation.edit",
        request_payload={"command": "5페이지 비어있네 어떻게 해야해?"},
        expected_revision=START_REVISION,
        attempt_number=1,
        attempt_token="attempt-token",
        fencing_token=1,
    )


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


def _service_factory(*, edits: bool):
    class FakeChatService:
        def __init__(self, *, sql_session, presentation_id, conversation_id):
            self._session = sql_session
            self._presentation_id = presentation_id
            self._conversation_id = conversation_id

        async def generate_reply(self, _command):
            if edits:
                presentation = await self._session.get(
                    PresentationModel, self._presentation_id
                )
                presentation.content = "after"
                self._session.add(presentation)
            else:
                # A question still reads the deck; reading must not count as an
                # edit or the distinction is worthless.
                await self._session.get(PresentationModel, self._presentation_id)
            await ChatConversationStore(self._session).append_turn(
                presentation_id=self._presentation_id,
                conversation_id=self._conversation_id,
                user_message="5페이지 비어있네 어떻게 해야해?",
                assistant_message="이 페이지는 '페이지 다시 만들기'로 재생성해야 합니다.",
                tool_calls=["getSlideAtIndex"],
            )
            return ChatTurnResult(
                conversation_id=self._conversation_id,
                response_text="이 페이지는 '페이지 다시 만들기'로 재생성해야 합니다.",
                tool_calls=["getSlideAtIndex"],
            )

    return FakeChatService


def _admit_legacy_transaction_fixture(monkeypatch) -> None:
    async def allow_fixture(_session, _presentation):
        return SimpleNamespace(driver_id="authored")

    monkeypatch.setattr(
        "services.workspace_chat_operation_handler.require_authored_mutation_evidence",
        allow_fixture,
    )


def test_a_question_turn_answers_without_moving_the_revision(
    tmp_path: Path, monkeypatch
):
    _admit_legacy_transaction_fixture(monkeypatch)
    asyncio.run(_a_question_turn_answers_without_moving_the_revision(tmp_path))


async def _a_question_turn_answers_without_moving_the_revision(tmp_path: Path):
    engine, maker = await _database(tmp_path / "question.db")
    try:
        presentation_id, operation_id, claim = await _seed(maker)
        handler = PresentationChatOperationHandler(
            session_factory=maker, service_factory=_service_factory(edits=False)
        )

        result = await handler.execute(_Context(claim))

        assert result.result_revision == START_REVISION

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            # The deck is untouched, so every other client's expected_revision
            # stays valid.
            assert presentation.revision == START_REVISION
            assert presentation.content == "before"
            # The turn still completed and its answer is durable.
            assert operation.state == "succeeded"
            assert operation.result_revision == START_REVISION
            assert "페이지 다시 만들기" in operation.result["summary"]
            terminal = (
                await session.execute(
                    select(WorkspaceOperationEvent).where(
                        WorkspaceOperationEvent.operation_id == operation_id,
                        WorkspaceOperationEvent.event_type == "operation.result",
                    )
                )
            ).scalar_one()
            assert terminal.payload["result_revision"] == START_REVISION
            transcript = list(
                (
                    await session.execute(
                        select(WorkspaceMessage).order_by(
                            WorkspaceMessage.position
                        )
                    )
                ).scalars()
            )
            assert [row.role for row in transcript] == ["assistant"]
            assert transcript[0].content == operation.result["summary"]
    finally:
        await engine.dispose()


def test_an_editing_turn_still_advances_the_revision(tmp_path: Path, monkeypatch):
    _admit_legacy_transaction_fixture(monkeypatch)
    asyncio.run(_an_editing_turn_still_advances_the_revision(tmp_path))


async def _an_editing_turn_still_advances_the_revision(tmp_path: Path):
    """The gate must recognise a real edit, or nothing would ever publish."""

    engine, maker = await _database(tmp_path / "edit.db")
    try:
        presentation_id, operation_id, claim = await _seed(maker)
        handler = PresentationChatOperationHandler(
            session_factory=maker, service_factory=_service_factory(edits=True)
        )

        result = await handler.execute(_Context(claim))

        assert result.result_revision == START_REVISION + 1

        async with maker() as session:
            presentation = await session.get(PresentationModel, presentation_id)
            operation = await session.get(WorkspaceOperation, operation_id)
            assert presentation.revision == START_REVISION + 1
            assert presentation.content == "after"
            assert operation.state == "succeeded"
            assert operation.result_revision == START_REVISION + 1
    finally:
        await engine.dispose()
