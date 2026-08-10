from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.conversation import (
    AgentRunActionRequest,
    ConversationMessageContext,
    ConversationMessageCreateRequest,
    ConversationSlideReference,
    ConversationViewport,
    PresentationRevision,
    SmartHtmlSelection,
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
from services.conversation_event_contract import ConversationEventType
from services.conversations.domain import (
    AgentRun,
    Asset,
    Conversation,
    Message,
    RunEvent,
)
from services.conversations.events import conversation_events
from services.conversations.service import (
    act_on_run,
    conversation_timeline,
    create_conversation,
    send_message,
)


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


async def _presentation(maker, *, generation_mode: str = "standard"):
    async with maker() as session:
        presentation = PresentationModel(
            content="canonical conversation test",
            n_slides=2,
            language="en",
            layout={"name": "test"},
            generation_mode=generation_mode,
            revision=4,
        )
        session.add(presentation)
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
            smart_selection=SmartHtmlSelection(
                html="<h1>Selected headline</h1>",
                selected_text="Selected headline",
                slide_index=1,
            ),
        ),
    )


def test_canonical_domain_names_are_aliases_not_second_sql_models():
    assert Conversation is WorkspaceConversation
    assert Message is WorkspaceMessage
    assert AgentRun is WorkspaceOperation
    assert RunEvent is WorkspaceOperationEvent
    assert Asset is WorkspaceAsset


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


def test_standard_mode_drops_smart_selection_and_reuses_single_store(tmp_path: Path):
    asyncio.run(_standard_mode_drops_smart_selection_and_reuses_single_store(tmp_path))


async def _standard_mode_drops_smart_selection_and_reuses_single_store(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "canonical-standard.db")
    try:
        presentation = await _presentation(maker)
        async with maker() as session:
            first = await create_conversation(
                session, presentation_id=presentation.id
            )
        async with maker() as session:
            second = await create_conversation(
                session, presentation_id=presentation.id
            )
            assert second.id == first.id
            assert second.capability.generation_mode == "standard"
            assert second.capability.supports_smart_selection is False

        async with maker() as session:
            receipt = await send_message(
                session,
                conversation_id=first.id,
                request=_request(),
            )
            operation = await session.get(WorkspaceOperation, receipt.run_id)
            assert operation is not None
            assert operation.request_payload["context"] == {
                "runtime": {
                    "generation_mode": "standard",
                    "presentation_mode": "template",
                    "tool_profile": "standard",
                    "can_edit": True,
                },
                "viewport": {
                    "width": 1440,
                    "height": 900,
                    "zoom": 1.25,
                    "visible_slide_ids": [],
                },
                "current_slide": {"index": 1},
            }
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceConversation)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceMessage)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("command_type", "operation_context", "expected_state"),
    [
        (
            "version.restore",
            {"version_id": "11111111-1111-4111-8111-111111111111"},
            "awaiting_approval",
        ),
        ("quality.review", {"scope": "all", "apply_fixes": False}, "queued"),
        (
            "slide.regenerate",
            {"slide_index": 1, "instruction": "Use a stronger visual hierarchy"},
            "queued",
        ),
    ],
)
def test_typed_message_commands_reuse_workspace_state_machine_and_preserve_context(
    tmp_path: Path,
    command_type: str,
    operation_context: dict,
    expected_state: str,
):
    asyncio.run(
        _typed_message_commands_reuse_workspace_state_machine_and_preserve_context(
            tmp_path,
            command_type=command_type,
            operation_context=operation_context,
            expected_state=expected_state,
        )
    )


async def _typed_message_commands_reuse_workspace_state_machine_and_preserve_context(
    tmp_path: Path,
    *,
    command_type: str,
    operation_context: dict,
    expected_state: str,
):
    engine, maker = await _database(
        tmp_path / f"typed-{command_type.replace('.', '-')}.db"
    )
    try:
        presentation = await _presentation(maker)
        async with maker() as session:
            conversation = await create_conversation(
                session, presentation_id=presentation.id
            )
            receipt = await send_message(
                session,
                conversation_id=conversation.id,
                request=_request(
                    f"typed-{command_type}",
                    command_type=command_type,
                    operation_context={
                        **operation_context,
                        # These generic keys are never authoritative.
                        "runtime": {"generation_mode": "smart"},
                        "smart_selection": {"html": "<script>spoofed()</script>"},
                    },
                ),
            )
            operation = await session.get(WorkspaceOperation, receipt.run_id)
            assert operation is not None
            assert operation.kind == command_type
            assert operation.state == expected_state
            for key, value in operation_context.items():
                assert operation.request_payload["context"][key] == value
            assert operation.request_payload["context"]["runtime"] == {
                "generation_mode": "standard",
                "presentation_mode": "template",
                "tool_profile": "standard",
                "can_edit": True,
            }
            assert "smart_selection" not in operation.request_payload["context"]
    finally:
        await engine.dispose()


def test_smart_mode_persists_allowlisted_selection_and_replays_standard_events(
    tmp_path: Path,
):
    asyncio.run(
        _smart_mode_persists_allowlisted_selection_and_replays_standard_events(
            tmp_path
        )
    )


async def _smart_mode_persists_allowlisted_selection_and_replays_standard_events(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "canonical-smart.db")
    try:
        presentation = await _presentation(maker, generation_mode="smart")
        async with maker() as session:
            conversation = await create_conversation(
                session, presentation_id=presentation.id
            )
            assert conversation.capability.tool_profile == "smart"
        async with maker() as session:
            receipt = await send_message(
                session,
                conversation_id=conversation.id,
                request=_request(),
            )
            operation = await session.get(WorkspaceOperation, receipt.run_id)
            assert operation is not None
            assert operation.request_payload["context"]["smart_selection"] == {
                "html": "<h1>Selected headline</h1>",
                "selected_text": "Selected headline",
                "slide_index": 1,
            }

        async with maker() as session:
            page = await conversation_events(
                session, conversation_id=conversation.id
            )
            assert [event.type for event in page.events] == [
                ConversationEventType.MESSAGE_CREATED,
                ConversationEventType.RUN_QUEUED,
            ]
            assert page.events[0].message_id is not None
            assert page.events[0].data == {
                "role": "user",
                "content": "Improve the selected headline",
            }
            first_cursor = page.events[0].cursor
            assert first_cursor is not None

        # Reconnect from the first durable cursor receives only later state.
        async with maker() as session:
            resumed = await conversation_events(
                session,
                conversation_id=conversation.id,
                after=first_cursor,
            )
            assert [event.type for event in resumed.events] == [
                ConversationEventType.RUN_QUEUED
            ]
            assert int(resumed.events[0].cursor or 0) > int(first_cursor)

        async with maker() as session:
            timeline = await conversation_timeline(
                session, conversation_id=conversation.id
            )
            assert [item.item_type for item in timeline.items] == [
                "message",
                "run",
            ]
            assert timeline.revision == 4
            run = timeline.items[1]
            assert run.item_type == "run"
            assert run.revision.published is False
    finally:
        await engine.dispose()


def test_event_projection_satisfies_required_public_data_and_downgrades_legacy_tools(
    tmp_path: Path,
):
    asyncio.run(
        _event_projection_satisfies_required_public_data_and_downgrades_legacy_tools(
            tmp_path
        )
    )


async def _event_projection_satisfies_required_public_data_and_downgrades_legacy_tools(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "canonical-event-data.db")
    try:
        presentation = await _presentation(maker)
        async with maker() as session:
            conversation = await create_conversation(
                session, presentation_id=presentation.id
            )
            receipt = await send_message(
                session,
                conversation_id=conversation.id,
                request=_request(),
            )

        async with maker() as session, session.begin():
            operation = await session.get(WorkspaceOperation, receipt.run_id)
            assert operation is not None
            base_sequence = int(
                await session.scalar(
                    select(func.max(WorkspaceOperationEvent.sequence)).where(
                        WorkspaceOperationEvent.operation_id == operation.id
                    )
                )
                or 0
            ) + 1
            operation.state = "failed"
            operation.error_code = "operation_dead_lettered"
            operation.next_event_sequence = base_sequence + 5
            session.add_all(
                (
                    WorkspaceOperationEvent(
                        operation_id=operation.id,
                        sequence=base_sequence,
                        operation_version=operation.version,
                        event_type="tool.started",
                        payload={
                            "state": "running",
                            "tool_name": "saveSlide",
                            "tool_summary": "Saving the selected slide.",
                        },
                    ),
                    WorkspaceOperationEvent(
                        operation_id=operation.id,
                        sequence=base_sequence + 1,
                        operation_version=operation.version,
                        event_type="tool.completed",
                        payload={
                            "state": "running",
                            "tool_name": "saveSlide",
                            "tool_summary": "Slide saved.",
                        },
                    ),
                    WorkspaceOperationEvent(
                        operation_id=operation.id,
                        sequence=base_sequence + 2,
                        operation_version=operation.version,
                        event_type="approval.required",
                        payload={"state": "awaiting_approval"},
                    ),
                    WorkspaceOperationEvent(
                        operation_id=operation.id,
                        sequence=base_sequence + 3,
                        operation_version=operation.version,
                        event_type="run.failed",
                        payload={"state": "failed"},
                    ),
                    WorkspaceOperationEvent(
                        operation_id=operation.id,
                        sequence=base_sequence + 4,
                        operation_version=operation.version,
                        event_type="tool.started",
                        payload={"state": "running"},
                    ),
                )
            )

        async with maker() as session:
            page = await conversation_events(
                session, conversation_id=conversation.id
            )
        projected = {event.cursor: event for event in page.events}
        ordered = page.events[-5:]
        assert [event.type for event in ordered] == [
            ConversationEventType.TOOL_STARTED,
            ConversationEventType.TOOL_COMPLETED,
            ConversationEventType.APPROVAL_REQUIRED,
            ConversationEventType.RUN_FAILED,
            ConversationEventType.RUN_STATUS,
        ]
        assert ordered[0].data["tool"] == "saveSlide"
        assert ordered[0].data["summary"] == "Saving the selected slide."
        assert ordered[1].data["tool"] == "saveSlide"
        assert ordered[1].data["summary"] == "Slide saved."
        assert ordered[2].data["approval_id"] == str(receipt.run_id)
        assert ordered[3].data["status"] == "failed"
        assert ordered[3].data["error_code"] == "operation_dead_lettered"
        assert "error" not in ordered[3].data
        assert ordered[4].data["status"] == "running"
        assert projected[ordered[4].cursor or ""].type == ConversationEventType.RUN_STATUS
    finally:
        await engine.dispose()


def test_undo_is_a_version_restore_run_in_the_same_state_machine(tmp_path: Path):
    asyncio.run(_undo_is_a_version_restore_run_in_the_same_state_machine(tmp_path))


async def _undo_is_a_version_restore_run_in_the_same_state_machine(tmp_path: Path):
    engine, maker = await _database(tmp_path / "canonical-undo.db")
    try:
        presentation = await _presentation(maker)
        async with maker() as session:
            conversation = await create_conversation(
                session, presentation_id=presentation.id
            )
        async with maker() as session:
            message = await send_message(
                session,
                conversation_id=conversation.id,
                request=_request(),
            )
        async with maker() as session:
            source = await session.get(WorkspaceOperation, message.run_id)
            deck = await session.get(PresentationModel, presentation.id)
            assert source is not None and deck is not None
            source.state = "succeeded"
            source.result_revision = 5
            deck.revision = 5
            session.add_all((source, deck))
            await session.commit()
        async with maker() as session:
            undo = await act_on_run(
                session,
                conversation_id=conversation.id,
                run_id=message.run_id,
                request=AgentRunActionRequest(
                    action="undo",
                    idempotency_key="undo-1",
                    expected_revision=5,
                ),
            )
            restore = await session.get(WorkspaceOperation, undo.run_id)
            assert restore is not None
            assert restore.kind == "version.restore"
            assert restore.state == "awaiting_approval"
            assert restore.request_payload["context"] == {
                "strategy": "previous",
                "source": "undo",
                "source_run_id": str(message.run_id),
            }
            assert restore.conversation_id == conversation.id
            assert restore.expected_revision == 5
    finally:
        await engine.dispose()
