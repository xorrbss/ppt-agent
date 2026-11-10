from __future__ import annotations

from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.conversation import (
    ConversationMessageContext,
    ConversationMessageCreateRequest,
    ConversationSlideReference,
    ConversationViewport,
    PresentationRevision,
)
from models.sql.presentation import PresentationModel
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
from services.conversations.service import (
    create_conversation,
)
from services.workspace_service import WorkspaceServiceError


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
    WorkspaceActionReceipt.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _presentation(maker):
    async with maker() as session:
        presentation = PresentationModel(
            content="canonical conversation test",
            n_slides=2,
            language="en",
            layout=None,
            mode="authored",
            theme={"mode": "authored"},
            generation_mode="standard",
            revision=4,
        )
        await add_existing_authored_presentation(session, presentation)
        await session.commit()
        return presentation


def _request(
    key: str = "message-1",
    *,
    command_type: str = "presentation.edit",
    operation_context: dict | None = None,
) -> ConversationMessageCreateRequest:
    return ConversationMessageCreateRequest(
        content="Improve the selected headline",
        idempotency_key=key,
        expected_revision=4,
        command_type=command_type,
        operation_context=operation_context or {},
        context=ConversationMessageContext(
            viewport=ConversationViewport(width=1440, height=900, zoom=1.25),
            current_slide=ConversationSlideReference(index=1),
        ),
    )


def test_revision_projection_rejects_false_publication_claims():
    presentation_id = uuid.uuid4()
    stable = PresentationRevision(
        presentation_id=presentation_id,
        source_revision=3,
        result_revision=3,
        published=False,
    )
    assert stable.published is False
    with pytest.raises(ValueError, match="advancing revision"):
        PresentationRevision(
            presentation_id=presentation_id,
            source_revision=3,
            result_revision=4,
            published=False,
        )


def test_pre_existing_authored_deck_has_no_conversation_surface(tmp_path: Path):
    """Conversation editing served authored decks. The authored driver was
    removed on 2026-11-10 (option A), so an existing authored deck answers
    ``unsupported_presentation_driver`` (422) like its own read, and no
    conversation, message or operation row is written for it."""
    asyncio.run(_pre_existing_authored_deck_has_no_conversation_surface(tmp_path))


async def _pre_existing_authored_deck_has_no_conversation_surface(tmp_path: Path):
    engine, maker = await _database(tmp_path / "authored-refused.db")
    try:
        presentation = await _presentation(maker)
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as refused:
                await create_conversation(session, presentation_id=presentation.id)
            assert refused.value.code == "unsupported_presentation_driver"
            assert refused.value.status_code == 422
            await session.rollback()
        async with maker() as session:
            for model in (WorkspaceConversation, WorkspaceMessage, WorkspaceOperation):
                assert await session.scalar(select(func.count()).select_from(model)) == 0
    finally:
        await engine.dispose()
