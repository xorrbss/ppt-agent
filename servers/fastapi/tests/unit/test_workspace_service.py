from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

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
from models.workspace import (
    WorkspaceAttachmentInput,
    WorkspaceBootstrapRequest,
    WorkspaceCommandRequest,
    WorkspaceEventPageResponse,
    WorkspaceOperationActionRequest,
)
from services.workspace_service import (
    WorkspaceServiceError,
    _safe_payload,
    apply_workspace_action,
    bootstrap_workspace,
    create_workspace_command,
    workspace_events,
    workspace_timeline,
)
from utils.datetime_utils import get_current_utc_datetime


WORKSPACE_SERVICE_TABLES = [
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


def test_event_page_contract_serializes_safe_retryable_flag():
    page = WorkspaceEventPageResponse.model_validate(
        {
            "schema_version": 1,
            "events": [
                {
                    "id": "00000000-0000-0000-0000-000000000001:1",
                    "operation_id": "00000000-0000-0000-0000-000000000001",
                    "operation_version": 3,
                    "sequence": 1,
                    "cursor": "42",
                    "type": "failed",
                    "status": "failed",
                    "error": "generation_admission_rejected",
                    "retryable": False,
                }
            ],
            "event_high_watermark": "42",
        }
    )

    wire = page.model_dump(mode="json")
    assert wire["events"][0]["retryable"] is False


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=WORKSPACE_SERVICE_TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create_presentation(maker) -> PresentationModel:
    async with maker() as session:
        presentation = PresentationModel(
            content="workspace service test",
            n_slides=1,
            language="en",
            layout={"name": "test"},
        )
        session.add(presentation)
        await session.commit()
        return presentation


def _bootstrap_request(
    *,
    key: str = "bootstrap-1",
    prompt: str = "Build a blue strategy deck",
    status: str = "ready",
) -> WorkspaceBootstrapRequest:
    return WorkspaceBootstrapRequest.from_internal_legacy_metadata(
        idempotency_key=key,
        prompt=prompt,
        generation_settings={
            "language": "ko",
            "tone": "executive",
            "n_slides": 8,
        },
        attachments=[
            WorkspaceAttachmentInput(
                file_name=r"C:\private\source\market.pdf",
                media_type="application/pdf",
                size_bytes=321,
                sha256="a" * 64,
                status=status,
            )
        ],
        expected_revision=0,
    )


@pytest.mark.parametrize(
    "secret_key",
    [
        "api_key",
        "api-key",
        "apiKey",
        "APIKey",
        "accessToken",
        "bearerToken",
    ],
)
def test_workspace_request_metadata_rejects_nested_credentials(secret_key: str):
    with pytest.raises(ValueError, match="must not contain credentials"):
        WorkspaceBootstrapRequest(
            idempotency_key="bootstrap-secret",
            prompt="Build a deck",
            generation_settings={"provider": [{secret_key: "do-not-store"}]},
        )

    with pytest.raises(ValueError, match="must not contain credentials"):
        WorkspaceCommandRequest(
            command="Shorten the title",
            idempotency_key="command-secret",
            context={"selection": [{secret_key: "do-not-store"}]},
        )


def test_timeline_payload_sanitizer_normalizes_private_camel_case_keys():
    assert _safe_payload(
        {
            "safe": "visible",
            "providerResponse": "private",
            "storageKey": "private",
            "stackTrace": "private",
            "nested": [{"attachmentUrl": "private", "title": "visible"}],
        }
    ) == {"safe": "visible", "nested": [{"title": "visible"}]}


def test_bootstrap_durably_projects_first_message_attachment_and_settings(tmp_path: Path):
    asyncio.run(_bootstrap_durably_projects_first_message_attachment_and_settings(tmp_path))


async def _bootstrap_durably_projects_first_message_attachment_and_settings(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "bootstrap-service.db")
    try:
        presentation = await _create_presentation(maker)
        request = _bootstrap_request()
        async with maker() as session:
            conversation, message, operation, duplicate = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=request,
            )
            assert duplicate is False
            assert message.position == 1
            assert operation.anchor_position == 2
            assert operation.state == "queued"

        # Reopen the database to prove this is durable history, not an identity-map
        # projection assembled from the request.
        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            assert timeline["conversation_id"] == conversation.id
            assert [item["anchor_type"] for item in timeline["items"]] == [
                "message",
                "operation",
            ]
            first_message = timeline["items"][0]
            assert first_message["id"] == str(message.id)
            assert first_message["content"] == request.prompt
            assert first_message["generation_settings"] == request.generation_settings
            assert first_message["attachments"] == [
                {
                    "id": first_message["attachments"][0]["id"],
                    "name": "market.pdf",
                    "status": "ready",
                    "mime_type": "application/pdf",
                    "size_bytes": 321,
                    "error": None,
                }
            ]
            assert timeline["items"][1]["events"][0]["type"] == "queued"
            assert timeline["items"][1]["events"][0]["operation_version"] == 0
            assert timeline["event_high_watermark"] is not None

            assert await session.scalar(select(func.count()).select_from(WorkspaceMessage)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceAsset)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceOperation)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceOutbox)) == 1
    finally:
        await engine.dispose()


def test_command_idempotency_is_scoped_by_owner_presentation_and_operation_type(
    tmp_path: Path,
):
    asyncio.run(_command_idempotency_scope(tmp_path))


async def _command_idempotency_scope(tmp_path: Path):
    engine, maker = await _database(tmp_path / "command-idempotency-scope.db")
    try:
        first_presentation = await _create_presentation(maker)
        second_presentation = await _create_presentation(maker)
        shared_key = "same-raw-key"

        async with maker() as session:
            _, _, first_bootstrap, first_duplicate = await bootstrap_workspace(
                session,
                presentation_id=first_presentation.id,
                request=_bootstrap_request(key=shared_key),
            )
        async with maker() as session:
            _, _, second_bootstrap, second_duplicate = await bootstrap_workspace(
                session,
                presentation_id=second_presentation.id,
                request=_bootstrap_request(key=shared_key),
            )
        assert first_duplicate is False
        assert second_duplicate is False
        assert first_bootstrap.id != second_bootstrap.id

        presentation_edit = WorkspaceCommandRequest(
            command="Tighten the executive summary",
            command_type="presentation.edit",
            idempotency_key=shared_key,
            expected_revision=0,
        )
        slide_edit = WorkspaceCommandRequest(
            command="Tighten the executive summary",
            command_type="slide.edit",
            idempotency_key=shared_key,
            expected_revision=0,
        )
        async with maker() as session:
            edit_operation, edit_duplicate = await create_workspace_command(
                session,
                presentation_id=first_presentation.id,
                request=presentation_edit,
            )
        async with maker() as session:
            slide_operation, slide_duplicate = await create_workspace_command(
                session,
                presentation_id=first_presentation.id,
                request=slide_edit,
            )
        assert edit_duplicate is False
        assert slide_duplicate is False
        assert edit_operation.id != slide_operation.id
        assert edit_operation.id != first_bootstrap.id

        async with maker() as session:
            replay_operation, replay_duplicate = await create_workspace_command(
                session,
                presentation_id=first_presentation.id,
                request=presentation_edit,
            )
        assert replay_duplicate is True
        assert replay_operation.id == edit_operation.id

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as conflict:
                await create_workspace_command(
                    session,
                    presentation_id=first_presentation.id,
                    request=WorkspaceCommandRequest(
                        command="A genuinely different edit",
                        command_type="presentation.edit",
                        idempotency_key=shared_key,
                        expected_revision=0,
                    ),
                )
        assert conflict.value.code == "idempotency_key_payload_conflict"
    finally:
        await engine.dispose()


def test_bootstrap_idempotent_duplicate_and_payload_conflict(tmp_path: Path):
    asyncio.run(_bootstrap_idempotent_duplicate_and_payload_conflict(tmp_path))


async def _bootstrap_idempotent_duplicate_and_payload_conflict(tmp_path: Path):
    engine, maker = await _database(tmp_path / "bootstrap-idempotency.db")
    try:
        presentation = await _create_presentation(maker)
        request = _bootstrap_request()
        async with maker() as session:
            first = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=request,
            )
        async with maker() as session:
            second = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=request,
            )
        assert second[3] is True
        assert second[1].id == first[1].id
        assert second[2].id == first[2].id

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as conflict:
                await bootstrap_workspace(
                    session,
                    presentation_id=presentation.id,
                    request=_bootstrap_request(prompt="A different payload"),
                )
        assert conflict.value.code == "idempotency_key_payload_conflict"
        assert conflict.value.status_code == 409

        async with maker() as session:
            assert await session.scalar(select(func.count()).select_from(WorkspaceMessage)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceOperation)) == 1
            assert await session.scalar(select(func.count()).select_from(WorkspaceActionReceipt)) == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("asset_state", "operation_state"),
    [
        ("uploaded", "waiting_for_assets"),
        ("scanning", "waiting_for_assets"),
        ("processing", "waiting_for_assets"),
        ("ready", "queued"),
        ("failed", "blocked"),
        ("quarantined", "blocked"),
        ("expired", "blocked"),
        ("deleted", "blocked"),
    ],
)
def test_bootstrap_asset_state_matrix(
    tmp_path: Path,
    asset_state: str,
    operation_state: str,
):
    asyncio.run(
        _bootstrap_asset_state_matrix(tmp_path, asset_state, operation_state)
    )


async def _bootstrap_asset_state_matrix(
    tmp_path: Path,
    asset_state: str,
    operation_state: str,
):
    engine, maker = await _database(tmp_path / f"asset-{asset_state}.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, operation, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(
                    key=f"asset-{asset_state}",
                    status=asset_state,
                ),
            )
            assert operation.state == operation_state
        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            assert timeline["items"][0]["attachments"][0]["status"] == asset_state
            assert timeline["items"][1]["status"] == operation_state
            assert timeline["items"][1]["events"][0]["type"] == operation_state
    finally:
        await engine.dispose()


def test_concurrent_commands_reserve_unique_positions_and_deduplicate_key(tmp_path: Path):
    asyncio.run(_concurrent_commands_reserve_unique_positions_and_deduplicate_key(tmp_path))


async def _concurrent_commands_reserve_unique_positions_and_deduplicate_key(tmp_path: Path):
    engine, maker = await _database(tmp_path / "concurrent-commands.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        async def send(command: str, key: str):
            async with maker() as session:
                return await create_workspace_command(
                    session,
                    presentation_id=presentation.id,
                    request=WorkspaceCommandRequest(
                        command=command,
                        idempotency_key=key,
                        expected_revision=0,
                    ),
                )

        independent = await asyncio.gather(
            send("Add risks", "tab-a"),
            send("Shorten conclusion", "tab-b"),
        )
        assert {result[0].id for result in independent}.__len__() == 2
        assert {result[1] for result in independent} == {False}

        duplicate_pair = await asyncio.gather(
            send("Use a darker blue", "same-command"),
            send("Use a darker blue", "same-command"),
        )
        assert len({result[0].id for result in duplicate_pair}) == 1
        assert sorted(result[1] for result in duplicate_pair) == [False, True]

        async with maker() as session:
            messages = list(
                (
                    await session.scalars(
                        select(WorkspaceMessage).order_by(WorkspaceMessage.position)
                    )
                ).all()
            )
            operations = list(
                (
                    await session.scalars(
                        select(WorkspaceOperation).order_by(
                            WorkspaceOperation.anchor_position
                        )
                    )
                ).all()
            )
            all_positions = [message.position for message in messages] + [
                operation.anchor_position for operation in operations
            ]
            assert len(all_positions) == len(set(all_positions))
            assert [message.content for message in messages].count("Use a darker blue") == 1
            assert len(operations) == 4  # bootstrap + two tabs + one deduplicated command
    finally:
        await engine.dispose()


def test_message_pagination_and_event_high_watermark_are_independent(tmp_path: Path):
    asyncio.run(_message_pagination_and_event_high_watermark_are_independent(tmp_path))


async def _message_pagination_and_event_high_watermark_are_independent(tmp_path: Path):
    engine, maker = await _database(tmp_path / "timeline-cursors.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )
        async with maker() as session:
            await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Add an appendix",
                    idempotency_key="appendix",
                    expected_revision=0,
                ),
            )

        async with maker() as session:
            latest = await workspace_timeline(
                session,
                presentation_id=presentation.id,
                limit=2,
            )
            assert [item["position"] for item in latest["items"]] == [3, 4]
            assert latest["next_before_position"] == 3
            assert latest["has_more"] is True
            event_hwm = latest["event_high_watermark"]
            assert event_hwm is not None

            older = await workspace_timeline(
                session,
                presentation_id=presentation.id,
                before_position=latest["next_before_position"],
                limit=2,
            )
            assert [item["position"] for item in older["items"]] == [1, 2]
            assert older["event_high_watermark"] == event_hwm

            all_events = await workspace_events(
                session,
                presentation_id=presentation.id,
            )
            assert len(all_events["events"]) == 2
            assert [
                event["operation_version"] for event in all_events["events"]
            ] == [0, 0]
            first_event_cursor = all_events["events"][0]["cursor"]
            backfill = await workspace_events(
                session,
                presentation_id=presentation.id,
                after=first_event_cursor,
            )
            assert len(backfill["events"]) == 1
            assert backfill["events"][0]["cursor"] == event_hwm
            assert backfill["event_high_watermark"] == event_hwm
    finally:
        await engine.dispose()


def test_timeline_watermark_cannot_skip_operation_committed_after_anchor_snapshot(
    tmp_path: Path,
):
    asyncio.run(
        _timeline_watermark_cannot_skip_operation_committed_after_anchor_snapshot(
            tmp_path
        )
    )


async def _timeline_watermark_cannot_skip_operation_committed_after_anchor_snapshot(
    tmp_path: Path,
):
    """Model PostgreSQL READ COMMITTED's per-statement snapshots deterministically.

    The competing command commits immediately after the operation-anchor SELECT
    has returned its rows. A timeline watermark captured after that point would
    acknowledge the new event even though its card is absent from the response.
    """

    database_path = tmp_path / "timeline-watermark-race.db"
    engine, maker = await _database(database_path)

    class OperationAnchorRaceSession(AsyncSession):
        after_operation_snapshot = None
        race_injected = False

        async def scalars(self, statement, *args, **kwargs):
            rows = await super().scalars(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", ())
            reads_operation_anchors = any(
                description.get("entity") is WorkspaceOperation
                for description in descriptions
            )
            if (
                reads_operation_anchors
                and not self.race_injected
                and self.after_operation_snapshot is not None
            ):
                self.race_injected = True
                await self.after_operation_snapshot()
            return rows

    # AUTOCOMMIT gives the reader a fresh SQLite snapshot for every statement,
    # matching the visibility that exposes this race under PostgreSQL's default
    # READ COMMITTED isolation. The writer still uses the normal transactional
    # session maker, so the injected command/event is committed atomically.
    race_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        isolation_level="AUTOCOMMIT",
    )
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        committed_operation_id = None

        async def commit_competing_command():
            nonlocal committed_operation_id
            async with maker() as writer:
                operation, duplicate = await create_workspace_command(
                    writer,
                    presentation_id=presentation.id,
                    request=WorkspaceCommandRequest(
                        command="Commit between anchor snapshot and response",
                        idempotency_key="timeline-read-committed-race",
                        expected_revision=0,
                    ),
                )
            assert duplicate is False
            committed_operation_id = operation.id

        async with OperationAnchorRaceSession(
            bind=race_engine,
            expire_on_commit=False,
        ) as timeline_session:
            timeline_session.after_operation_snapshot = commit_competing_command
            timeline = await workspace_timeline(
                timeline_session,
                presentation_id=presentation.id,
            )

        assert committed_operation_id is not None
        projected_operation_ids = {
            item["operation_id"]
            for item in timeline["items"]
            if item["anchor_type"] == "operation"
        }
        assert str(committed_operation_id) not in projected_operation_ids

        async with maker() as session:
            backfill = await workspace_events(
                session,
                presentation_id=presentation.id,
                after=timeline["event_high_watermark"],
            )
        assert [event["operation_id"] for event in backfill["events"]] == [
            str(committed_operation_id)
        ]
        assert int(backfill["event_high_watermark"]) > int(
            timeline["event_high_watermark"]
        )
    finally:
        await race_engine.dispose()
        await engine.dispose()


def test_timeline_anchor_snapshot_never_splits_atomic_message_operation_pair(
    tmp_path: Path,
):
    asyncio.run(
        _timeline_anchor_snapshot_never_splits_atomic_message_operation_pair(tmp_path)
    )


async def _timeline_anchor_snapshot_never_splits_atomic_message_operation_pair(
    tmp_path: Path,
):
    database_path = tmp_path / "timeline-anchor-snapshot-race.db"
    engine, maker = await _database(database_path)

    class MessageReadRaceSession(AsyncSession):
        after_message_read = None
        race_injected = False

        async def scalars(self, statement, *args, **kwargs):
            rows = await super().scalars(statement, *args, **kwargs)
            descriptions = getattr(statement, "column_descriptions", ())
            reads_messages = any(
                description.get("entity") is WorkspaceMessage
                for description in descriptions
            )
            if (
                reads_messages
                and not self.race_injected
                and self.after_message_read is not None
            ):
                self.race_injected = True
                await self.after_message_read()
            return rows

    race_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        isolation_level="AUTOCOMMIT",
    )
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        committed_message_id = None
        committed_operation_id = None

        async def commit_pair_after_message_read():
            nonlocal committed_message_id, committed_operation_id
            async with maker() as writer:
                operation, duplicate = await create_workspace_command(
                    writer,
                    presentation_id=presentation.id,
                    request=WorkspaceCommandRequest(
                        command="Keep this message and operation together",
                        idempotency_key="timeline-anchor-pair-race",
                        expected_revision=0,
                    ),
                )
            assert duplicate is False
            committed_operation_id = operation.id
            committed_message_id = uuid.UUID(operation.request_payload["user_message_id"])

        async with MessageReadRaceSession(
            bind=race_engine,
            expire_on_commit=False,
        ) as timeline_session:
            timeline_session.after_message_read = commit_pair_after_message_read
            timeline = await workspace_timeline(
                timeline_session,
                presentation_id=presentation.id,
            )

        assert committed_message_id is not None
        assert committed_operation_id is not None
        projected_message_ids = {
            item["id"]
            for item in timeline["items"]
            if item["anchor_type"] == "message"
        }
        projected_operation_ids = {
            item["operation_id"]
            for item in timeline["items"]
            if item["anchor_type"] == "operation"
        }
        message_projected = str(committed_message_id) in projected_message_ids
        operation_projected = str(committed_operation_id) in projected_operation_ids
        assert message_projected is operation_projected
        assert message_projected is False
    finally:
        await race_engine.dispose()
        await engine.dispose()


def test_empty_workspace_validates_and_canonicalizes_event_cursor(tmp_path: Path):
    asyncio.run(_empty_workspace_validates_and_canonicalizes_event_cursor(tmp_path))


async def _empty_workspace_validates_and_canonicalizes_event_cursor(tmp_path: Path):
    engine, maker = await _database(tmp_path / "empty-event-cursor.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            page = await workspace_events(
                session,
                presentation_id=presentation.id,
            )
            assert page == {
                "schema_version": 1,
                "events": [],
                "event_high_watermark": "0",
            }

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as invalid:
                await workspace_events(
                    session,
                    presentation_id=presentation.id,
                    after="not-a-cursor",
                )
        assert invalid.value.code == "invalid_event_cursor"
        assert invalid.value.status_code == 400
    finally:
        await engine.dispose()


def test_event_cursor_expiry_is_conversation_scoped_and_initial_poll_is_allowed(
    tmp_path: Path,
):
    asyncio.run(_event_cursor_expiry_is_conversation_scoped(tmp_path))


async def _event_cursor_expiry_is_conversation_scoped(tmp_path: Path):
    engine, maker = await _database(tmp_path / "event-cursor-expiry.db")
    try:
        presentation_a = await _create_presentation(maker)
        presentation_b = await _create_presentation(maker)
        async with maker() as session:
            conversation_a, _message, _operation, _duplicate = (
                await bootstrap_workspace(
                    session,
                    presentation_id=presentation_a.id,
                    request=_bootstrap_request(key="cursor-floor-a"),
                )
            )
            await bootstrap_workspace(
                session,
                presentation_id=presentation_b.id,
                request=_bootstrap_request(key="cursor-floor-b"),
            )
            page_a = await workspace_events(
                session,
                presentation_id=presentation_a.id,
            )
            floor_a = int(page_a["event_high_watermark"])
            conversation_a.event_retention_floor_cursor = floor_a
            session.add(conversation_a)
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as expired:
                await workspace_events(
                    session,
                    presentation_id=presentation_a.id,
                    after="0",
                )
            assert expired.value.code == "event_cursor_expired"
            assert expired.value.status_code == 410
            assert expired.value.retryable is True

            initial = await workspace_events(
                session,
                presentation_id=presentation_a.id,
            )
            assert initial["events"]
            assert int(initial["event_high_watermark"]) >= floor_a

            rebased_timeline = await workspace_timeline(
                session,
                presentation_id=presentation_a.id,
            )
            assert int(rebased_timeline["event_high_watermark"]) >= floor_a

            # Conversation B's first cursor is above zero only because A consumed
            # the global sequence. Its own floor remains zero, so this is valid.
            other_conversation = await workspace_events(
                session,
                presentation_id=presentation_b.id,
                after="0",
            )
            assert other_conversation["events"]
    finally:
        await engine.dispose()


def test_destructive_command_requires_fresh_revision_bound_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("WORKSPACE_DESTRUCTIVE_APPROVAL_TTL_SECONDS", "3600")
    asyncio.run(_destructive_command_requires_fresh_revision_bound_approval(tmp_path))


async def _destructive_command_requires_fresh_revision_bound_approval(tmp_path: Path):
    engine, maker = await _database(tmp_path / "destructive-approval.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            operation, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Delete slide 1",
                    command_type="slide.delete",
                    idempotency_key="delete-slide-1",
                    expected_revision=0,
                    context={"slide_index": 0},
                ),
            )
            assert duplicate is False
            assert operation.state == "awaiting_approval"
            assert operation.approval_expires_at is not None
            assert operation.next_attempt_at is None

        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            card = timeline["items"][-1]
            assert card["status"] == "awaiting_approval"
            assert card["approval_expires_at"] is not None
            assert card["events"][0]["type"] == "approval_required"

        async with maker() as session:
            _, approved, duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=WorkspaceOperationActionRequest(
                    action="approve",
                    idempotency_key="approve-delete-slide-1",
                    expected_operation_version=0,
                    expected_revision=0,
                ),
            )
            assert duplicate is False
            assert approved.state == "queued"
            assert approved.version == 1

        async with maker() as session:
            stale_operation, _ = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Delete slide 2",
                    command_type="slide.delete",
                    idempotency_key="delete-slide-2",
                    expected_revision=0,
                    context={"slide_index": 1},
                ),
            )
            stored_presentation = await session.get(PresentationModel, presentation.id)
            assert stored_presentation is not None
            stored_presentation.revision = 1
            await session.commit()

        # Approval is bound to the revision captured by the operation even when
        # an old client omits its optional expected_revision field.
        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await apply_workspace_action(
                    session,
                    operation_id=stale_operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="approve",
                        idempotency_key="approve-stale-delete",
                        expected_operation_version=0,
                    ),
                )
        assert stale.value.code == "revision_conflict"

        async with maker() as session:
            expired_operation, _ = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=WorkspaceCommandRequest(
                    command="Delete slide 3",
                    command_type="slide.delete",
                    idempotency_key="delete-slide-3",
                    expected_revision=1,
                    context={"slide_index": 2},
                ),
            )
            expired_operation.approval_expires_at = (
                get_current_utc_datetime() - timedelta(seconds=1)
            )
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as expired:
                await apply_workspace_action(
                    session,
                    operation_id=expired_operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="approve",
                        idempotency_key="approve-expired-delete",
                        expected_operation_version=0,
                        expected_revision=1,
                    ),
                )
        assert expired.value.code == "approval_expired"
        assert expired.value.status_code == 409
    finally:
        await engine.dispose()


def test_version_restore_command_and_approval_are_durably_idempotent(
    tmp_path: Path,
):
    asyncio.run(_version_restore_command_and_approval_are_durably_idempotent(tmp_path))


async def _version_restore_command_and_approval_are_durably_idempotent(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "version-restore-admission.db")
    try:
        presentation = await _create_presentation(maker)
        command = WorkspaceCommandRequest(
            command="어제 저장한 버전으로 복원해 줘",
            command_type="version.restore",
            idempotency_key="restore-command-once",
            expected_revision=0,
            context={
                "version_id": "11111111-1111-1111-1111-111111111111",
                "source": "version_history",
            },
        )
        async with maker() as session:
            operation, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=command,
            )
        assert duplicate is False
        assert operation.kind == "version.restore"
        assert operation.state == "awaiting_approval"
        assert operation.expected_revision == 0

        async with maker() as session:
            replayed, duplicate = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=command,
            )
        assert duplicate is True
        assert replayed.id == operation.id

        approval = WorkspaceOperationActionRequest(
            action="approve",
            idempotency_key="restore-approval-once",
            expected_operation_version=0,
            expected_revision=0,
        )
        async with maker() as session:
            receipt, approved, duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=approval,
            )
        assert duplicate is False
        assert approved.state == "queued"
        assert approved.version == 1

        async with maker() as session:
            replay_receipt, replay_operation, duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=approval,
            )
        assert duplicate is True
        assert replay_receipt.id == receipt.id
        assert replay_operation.id == operation.id
        assert replay_operation.state == "queued"

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceMessage)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperationEvent)
            ) == 2
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOutbox)
            ) == 2

        # A later approval is still bound to the revision captured by the card,
        # even if that client omits its own optional expected_revision.
        stale_command = WorkspaceCommandRequest(
            command="다른 저장 버전으로 복원해 줘",
            command_type="version.restore",
            idempotency_key="restore-command-stale",
            expected_revision=0,
            context={
                "version_id": "22222222-2222-2222-2222-222222222222",
                "source": "version_history",
            },
        )
        async with maker() as session:
            stale_operation, _ = await create_workspace_command(
                session,
                presentation_id=presentation.id,
                request=stale_command,
            )
            stored_presentation = await session.get(
                PresentationModel,
                presentation.id,
            )
            stored_presentation.revision = 1
            await session.commit()

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await apply_workspace_action(
                    session,
                    operation_id=stale_operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="approve",
                        idempotency_key="restore-stale-approval",
                        expected_operation_version=0,
                    ),
                )
        assert stale.value.code == "revision_conflict"
        async with maker() as session:
            stored = await session.get(WorkspaceOperation, stale_operation.id)
            assert stored.state == "awaiting_approval"
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 1
    finally:
        await engine.dispose()


def test_action_receipt_is_idempotent_and_stale_operation_version_conflicts(tmp_path: Path):
    asyncio.run(_action_receipt_is_idempotent_and_stale_operation_version_conflicts(tmp_path))


async def _action_receipt_is_idempotent_and_stale_operation_version_conflicts(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "action-receipts.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, operation, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(),
            )

        cancel = WorkspaceOperationActionRequest(
            action="cancel",
            idempotency_key="cancel-once",
            action_id="cancel-click-1",
            expected_operation_version=0,
            expected_revision=0,
        )
        async with maker() as session:
            first_receipt, first_operation, first_duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=cancel,
            )
            assert first_duplicate is False
            assert first_operation.state == "cancelled"
            assert first_operation.version == 1

        async with maker() as session:
            second_receipt, second_operation, second_duplicate = await apply_workspace_action(
                session,
                operation_id=operation.id,
                request=cancel,
            )
            assert second_duplicate is True
            assert second_receipt.id == first_receipt.id
            assert second_operation.version == 1

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as stale:
                await apply_workspace_action(
                    session,
                    operation_id=operation.id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key="stale-retry",
                        expected_operation_version=0,
                        expected_revision=0,
                    ),
                )
        assert stale.value.code == "operation_state_conflict"
        assert stale.value.status_code == 409

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 1
            events = list(
                (
                    await session.scalars(
                        select(WorkspaceOperationEvent)
                        .where(WorkspaceOperationEvent.operation_id == operation.id)
                        .order_by(WorkspaceOperationEvent.sequence)
                    )
                ).all()
            )
            assert [event.operation_version for event in events] == [0, 1]
    finally:
        await engine.dispose()


def test_action_idempotency_is_scoped_by_owner_operation_and_action(tmp_path: Path):
    asyncio.run(_action_idempotency_scope(tmp_path))


async def _action_idempotency_scope(tmp_path: Path):
    engine, maker = await _database(tmp_path / "action-idempotency-scope.db")
    try:
        first_presentation = await _create_presentation(maker)
        second_presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, first_operation, _ = await bootstrap_workspace(
                session,
                presentation_id=first_presentation.id,
                request=_bootstrap_request(key="action-scope-first"),
                initial_operation_state="awaiting_approval",
            )
        async with maker() as session:
            _, _, second_operation, _ = await bootstrap_workspace(
                session,
                presentation_id=second_presentation.id,
                request=_bootstrap_request(key="action-scope-second"),
            )

        shared_key = "same-action-raw-key"
        approve = WorkspaceOperationActionRequest(
            action="approve",
            idempotency_key=shared_key,
            expected_operation_version=0,
            expected_revision=0,
        )
        cancel_first = WorkspaceOperationActionRequest(
            action="cancel",
            idempotency_key=shared_key,
            expected_operation_version=1,
            expected_revision=0,
        )
        cancel_second = WorkspaceOperationActionRequest(
            action="cancel",
            idempotency_key=shared_key,
            expected_operation_version=0,
            expected_revision=0,
        )

        async with maker() as session:
            approve_receipt, approved, approve_duplicate = await apply_workspace_action(
                session,
                operation_id=first_operation.id,
                request=approve,
            )
        assert approve_duplicate is False
        assert approved.version == 1

        # The same raw key is independent for a different action on the same
        # operation, even though the default action_id is also derived from it.
        async with maker() as session:
            cancel_receipt, cancelled, cancel_duplicate = await apply_workspace_action(
                session,
                operation_id=first_operation.id,
                request=cancel_first,
            )
        assert cancel_duplicate is False
        assert cancelled.version == 2
        assert cancel_receipt.id != approve_receipt.id

        # The same action/key is also independent on another operation.
        async with maker() as session:
            second_receipt, second_cancelled, second_duplicate = await apply_workspace_action(
                session,
                operation_id=second_operation.id,
                request=cancel_second,
            )
        assert second_duplicate is False
        assert second_cancelled.version == 1
        assert second_receipt.id != cancel_receipt.id

        async with maker() as session:
            replay_receipt, replay_operation, replay_duplicate = await apply_workspace_action(
                session,
                operation_id=second_operation.id,
                request=cancel_second,
            )
        assert replay_duplicate is True
        assert replay_receipt.id == second_receipt.id
        assert replay_operation.id == second_operation.id

        async with maker() as session:
            receipts = list((await session.scalars(select(WorkspaceActionReceipt))).all())
            assert len(receipts) == 3
            assert {
                (receipt.operation_id, receipt.action_type, receipt.idempotency_key_hash)
                for receipt in receipts
            }.__len__() == 3
    finally:
        await engine.dispose()


def test_terminal_retry_creates_one_new_operation_and_preserves_source(tmp_path: Path):
    asyncio.run(_terminal_retry_creates_one_new_operation_and_preserves_source(tmp_path))


async def _terminal_retry_creates_one_new_operation_and_preserves_source(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "terminal-retry.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            conversation, _, source, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(key="retry-source"),
            )
            source.state = "failed"
            source.error_code = "operation_dead_lettered"
            source.error_detail = {"safe": "original failure"}
            source.version = 4
            await session.commit()

        retry_request = WorkspaceOperationActionRequest(
            action="retry",
            idempotency_key="retry-source-click-1",
            action_id="retry-click-1",
            expected_operation_version=4,
            expected_revision=0,
        )
        async with maker() as session:
            receipt, retry, duplicate = await apply_workspace_action(
                session,
                operation_id=source.id,
                request=retry_request,
            )
            assert duplicate is False
            assert retry.id != source.id
            assert retry.retry_of_operation_id == source.id
            assert retry.state == "queued"
            assert retry.anchor_position == 3
            assert retry.expected_revision == 0
            assert retry.version == 0
            assert receipt.operation_id == source.id
            assert receipt.result_operation_id == retry.id
            assert receipt.result_payload == {
                "state": "queued",
                "operation_id": str(retry.id),
                "retry_of_operation_id": str(source.id),
            }

        async with maker() as session:
            replay_receipt, replay_operation, replay_duplicate = (
                await apply_workspace_action(
                    session,
                    operation_id=source.id,
                    request=retry_request,
                )
            )
            assert replay_duplicate is True
            assert replay_receipt.id == receipt.id
            assert replay_operation.id == retry.id

            persisted_source = await session.get(WorkspaceOperation, source.id)
            assert persisted_source is not None
            assert persisted_source.state == "failed"
            assert persisted_source.version == 4
            assert persisted_source.error_code == "operation_dead_lettered"
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 2

            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            assert [item["position"] for item in timeline["items"]] == [1, 2, 3]
            assert timeline["items"][1]["retryable"] is True
            assert timeline["items"][2]["retry_of_operation_id"] == str(source.id)

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as already_retried:
                await apply_workspace_action(
                    session,
                    operation_id=source.id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key="retry-source-click-2",
                        expected_operation_version=4,
                        expected_revision=0,
                    ),
                )
        assert already_retried.value.code == "operation_state_conflict"

        async with maker() as session:
            stored_conversation = await session.get(
                WorkspaceConversation, conversation.id
            )
            assert stored_conversation is not None
            assert stored_conversation.next_position == 4
    finally:
        await engine.dispose()


@pytest.mark.parametrize("state", ["failed", "dead_letter"])
def test_terminal_non_retryable_operation_rejects_retry_and_projects_safe_gate(
    tmp_path: Path,
    state: str,
):
    asyncio.run(
        _terminal_non_retryable_operation_rejects_retry_and_projects_safe_gate(
            tmp_path,
            state,
        )
    )


async def _terminal_non_retryable_operation_rejects_retry_and_projects_safe_gate(
    tmp_path: Path,
    state: str,
):
    engine, maker = await _database(tmp_path / f"non-retryable-{state}.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, source, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(key=f"non-retryable-{state}"),
            )
            source.state = state
            source.error_code = "generation_admission_rejected"
            source.error_detail = {
                "retryable": False,
                "private_detail": "must-not-leak",
            }
            source.version = 5
            await session.commit()

        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            anchor = next(
                item
                for item in timeline["items"]
                if item.get("operation_id") == str(source.id)
            )
            assert anchor["retryable"] is False
            assert "error_detail" not in anchor
            assert "must-not-leak" not in str(anchor)

        async with maker() as session:
            with pytest.raises(WorkspaceServiceError) as rejected:
                await apply_workspace_action(
                    session,
                    operation_id=source.id,
                    request=WorkspaceOperationActionRequest(
                        action="retry",
                        idempotency_key=f"retry-{state}",
                        expected_operation_version=5,
                        expected_revision=0,
                    ),
                )
        assert rejected.value.code == "operation_not_retryable"
        assert rejected.value.status_code == 409
        assert rejected.value.retryable is False
        assert rejected.value.safe_message == (
            "This operation cannot be retried. Submit a new command instead."
        )

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceActionReceipt)
            ) == 0
    finally:
        await engine.dispose()


def test_cancelled_operation_remains_retryable_with_legacy_error_detail(tmp_path: Path):
    asyncio.run(_cancelled_operation_remains_retryable_with_legacy_error_detail(tmp_path))


async def _cancelled_operation_remains_retryable_with_legacy_error_detail(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "cancelled-retryable.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, source, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(key="cancelled-retryable"),
            )
            source.state = "cancelled"
            source.error_detail = {"retryable": False}
            source.version = 2
            await session.commit()

        async with maker() as session:
            timeline = await workspace_timeline(
                session,
                presentation_id=presentation.id,
            )
            anchor = next(
                item
                for item in timeline["items"]
                if item.get("operation_id") == str(source.id)
            )
            assert anchor["retryable"] is True
            _, retry, duplicate = await apply_workspace_action(
                session,
                operation_id=source.id,
                request=WorkspaceOperationActionRequest(
                    action="retry",
                    idempotency_key="retry-cancelled",
                    expected_operation_version=2,
                    expected_revision=0,
                ),
            )
            assert duplicate is False
            assert retry.retry_of_operation_id == source.id
            assert retry.state == "queued"
    finally:
        await engine.dispose()


def test_blocked_retry_resumes_same_operation_after_assets_are_ready(tmp_path: Path):
    asyncio.run(_blocked_retry_resumes_same_operation_after_assets_are_ready(tmp_path))


async def _blocked_retry_resumes_same_operation_after_assets_are_ready(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "blocked-retry.db")
    try:
        presentation = await _create_presentation(maker)
        async with maker() as session:
            _, _, blocked, _ = await bootstrap_workspace(
                session,
                presentation_id=presentation.id,
                request=_bootstrap_request(
                    key="blocked-retry-source",
                    status="failed",
                ),
            )
            asset = (await session.scalars(select(WorkspaceAsset))).one()
            asset.state = "ready"
            await session.commit()

        async with maker() as session:
            _, resumed, duplicate = await apply_workspace_action(
                session,
                operation_id=blocked.id,
                request=WorkspaceOperationActionRequest(
                    action="retry",
                    idempotency_key="blocked-retry-click",
                    expected_operation_version=0,
                    expected_revision=0,
                ),
            )
            assert duplicate is False
            assert resumed.id == blocked.id
            assert resumed.retry_of_operation_id is None
            assert resumed.state == "queued"
            assert resumed.version == 1

        async with maker() as session:
            assert await session.scalar(
                select(func.count()).select_from(WorkspaceOperation)
            ) == 1
    finally:
        await engine.dispose()
