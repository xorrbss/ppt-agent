"""Live PostgreSQL coverage for chat-first workspace durability contracts.

The database is destructive and must be explicitly provided through
PPT_AGENT_POSTGRES_TEST_URL. Dedicated CI also sets
PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION=1 so a missing URL cannot become a
false-green skip.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
import re
import uuid

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, select, text, update
from sqlalchemy.engine import Engine, URL, make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

import migrations
from models.sql.presentation import PresentationModel
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceActionReceipt,
    WorkspaceAsset,
    WorkspaceConversation,
    WorkspaceMessage,
    WorkspaceOperation,
    WorkspaceOperationEvent,
)
from models.workspace import WorkspaceCommandRequest, WorkspaceOperationActionRequest
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.workspace_db_adapter import (
    reserve_conversation_positions,
    reserve_operation_event_sequence,
)
from services.workspace_operation_handler_registry import WorkspaceOperationResult
from services.workspace_operation_runner import (
    claim_next_workspace_operation,
    finalize_workspace_operation_success,
    heartbeat_workspace_operation,
    recover_expired_workspace_operations,
)
from services.workspace_service import (
    apply_workspace_action,
    create_workspace_command,
    workspace_timeline,
)
from utils.datetime_utils import get_current_utc_datetime
from utils.db_utils import to_sync_sqlalchemy_url
from utils.owner_context import reset_current_owner_id, set_current_owner_id


FASTAPI_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_TABLES = {
    "workspace_action_receipts",
    "workspace_artifacts",
    "workspace_assets",
    "workspace_conversations",
    "workspace_message_attachments",
    "workspace_messages",
    "workspace_operation_events",
    "workspace_operations",
    "workspace_outbox",
}
OWNER_A = uuid.UUID("11111111-1111-4111-8111-111111111111")
OWNER_B = uuid.UUID("22222222-2222-4222-8222-222222222222")


def _alembic_config(database_url: str) -> Config:
    config = Config(str(FASTAPI_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _reset_public_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def postgres_workspace_database() -> tuple[Engine, Config, URL]:
    require_postgres = os.getenv("PPT_AGENT_REQUIRE_POSTGRES_INTEGRATION") == "1"
    configured_url = os.getenv("PPT_AGENT_POSTGRES_TEST_URL")
    if not configured_url:
        if require_postgres:
            pytest.fail(
                "PPT_AGENT_POSTGRES_TEST_URL is required by the PostgreSQL "
                "integration gate"
            )
        pytest.skip("set PPT_AGENT_POSTGRES_TEST_URL to run live PostgreSQL tests")

    database_url = to_sync_sqlalchemy_url(configured_url)
    parsed_url = make_url(database_url)
    if parsed_url.get_backend_name() != "postgresql":
        pytest.fail("PPT_AGENT_POSTGRES_TEST_URL must use PostgreSQL")
    database_name = parsed_url.database or ""
    if not re.search(r"(?:^|[_-])tests?$", database_name, re.IGNORECASE):
        pytest.fail(
            "refusing destructive integration test against a database whose "
            "name does not end in test or tests"
        )

    engine = create_engine(database_url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        _reset_public_schema(engine)
        yield (
            engine,
            _alembic_config(database_url),
            parsed_url.set(drivername="postgresql+asyncpg"),
        )
    finally:
        _reset_public_schema(engine)
        engine.dispose()


def _assert_workspace_schema(engine: Engine) -> None:
    inspector = inspect(engine)
    assert WORKSPACE_TABLES.issubset(inspector.get_table_names())
    assert "revision" in {
        column["name"] for column in inspector.get_columns("presentations")
    }
    assert "workspace_operation_id" in {
        column["name"]
        for column in inspector.get_columns("presentation_generation_jobs")
    }
    assert "approval_expires_at" in {
        column["name"]
        for column in inspector.get_columns("workspace_operations")
    }
    conversation_columns = {
        column["name"]: column
        for column in inspector.get_columns("workspace_conversations")
    }
    retention_floor = conversation_columns["event_retention_floor_cursor"]
    assert retention_floor["nullable"] is False
    assert str(retention_floor["type"]).upper() == "BIGINT"
    assert str(retention_floor["default"]) in {"0", "'0'::bigint"}
    assert "ck_workspace_conversations_event_retention_floor_cursor" in {
        constraint["name"]
        for constraint in inspector.get_check_constraints("workspace_conversations")
    }
    assert {
        "uq_workspace_operations_command_idempotency",
        "uq_workspace_operations_conversation_position",
        "uq_workspace_operations_retry_of",
    }.issubset(
        {
            constraint["name"]
            for constraint in inspector.get_unique_constraints(
                "workspace_operations"
            )
        }
    )
    assert {
        "ck_workspace_operations_fencing_token",
        "ck_workspace_operations_next_event_sequence",
        "ck_workspace_operations_state",
        "ck_workspace_operations_version",
    }.issubset(
        {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "workspace_operations"
            )
        }
    )
    assert {
        "ix_workspace_operations_dispatch",
        "ix_workspace_operations_owner_created",
    }.issubset(
        {index["name"] for index in inspector.get_indexes("workspace_operations")}
    )
    event_cursor = next(
        column
        for column in inspector.get_columns("workspace_operation_events")
        if column["name"] == "cursor"
    )
    assert str(event_cursor["type"]).upper() == "BIGINT"


async def _seed_owner(
    maker: async_sessionmaker[AsyncSession], owner_id: uuid.UUID
) -> None:
    async with maker() as session, session.begin():
        session.add(
            User(
                id=owner_id,
                username=f"postgres-workspace-{owner_id}",
                hashed_password="test-only",
            )
        )
        await session.flush()


async def _seed_presentation(
    maker: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    *,
    conversation_next_position: int | None = None,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    owner_token = set_current_owner_id(owner_id)
    try:
        async with maker() as session, session.begin():
            presentation = PresentationModel(
                content="PostgreSQL workspace integration",
                n_slides=1,
                language="en",
                layout={"name": "test"},
            )
            session.add(presentation)
            await session.flush()
            if conversation_next_position is None:
                return presentation.id, None
            conversation = WorkspaceConversation(
                presentation_id=presentation.id,
                next_position=conversation_next_position,
            )
            session.add(conversation)
            await session.flush()
            return presentation.id, conversation.id
    finally:
        reset_current_owner_id(owner_token)


async def _seed_operation(
    maker: async_sessionmaker[AsyncSession],
    owner_id: uuid.UUID,
    *,
    presentation_id: uuid.UUID,
    conversation_id: uuid.UUID,
    anchor_position: int,
    key_seed: int,
    state: str = "queued",
    kind: str = "test.operation",
) -> uuid.UUID:
    owner_token = set_current_owner_id(owner_id)
    try:
        async with maker() as session, session.begin():
            operation = WorkspaceOperation(
                conversation_id=conversation_id,
                presentation_id=presentation_id,
                anchor_position=anchor_position,
                kind=kind,
                state=state,
                idempotency_key_hash=f"{key_seed:064x}",
                request_hash=f"{key_seed + 100:064x}",
                request_payload={"key_seed": key_seed},
            )
            session.add(operation)
            await session.flush()
            return operation.id
    finally:
        reset_current_owner_id(owner_token)


async def _operation(
    maker: async_sessionmaker[AsyncSession], operation_id: uuid.UUID
) -> WorkspaceOperation:
    async with maker() as session:
        return (
            await session.execute(
                select(WorkspaceOperation)
                .where(WorkspaceOperation.id == operation_id)
                .execution_options(skip_owner_scope=True)
            )
        ).scalar_one()


async def _operation_events(
    maker: async_sessionmaker[AsyncSession], operation_id: uuid.UUID
) -> list[WorkspaceOperationEvent]:
    async with maker() as session:
        return list(
            (
                await session.execute(
                    select(WorkspaceOperationEvent)
                    .where(WorkspaceOperationEvent.operation_id == operation_id)
                    .order_by(WorkspaceOperationEvent.sequence)
                    .execution_options(skip_owner_scope=True)
                )
            )
            .scalars()
            .all()
        )


async def _exercise_postgresql_workspace(async_url: URL) -> None:
    engine = create_async_engine(async_url, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        await _seed_owner(maker, OWNER_A)
        await _seed_owner(maker, OWNER_B)

        runner_presentation_id, runner_conversation_id = await _seed_presentation(
            maker, OWNER_A, conversation_next_position=3
        )
        assert runner_conversation_id is not None
        runner_operation_ids = [
            await _seed_operation(
                maker,
                OWNER_A,
                presentation_id=runner_presentation_id,
                conversation_id=runner_conversation_id,
                anchor_position=index,
                key_seed=index,
                # The crash-recovery branch under test is explicitly replay
                # safe.  Unknown handlers intentionally fail closed to manual
                # recovery and are covered separately by runner unit tests.
                kind="presentation.generate",
            )
            for index in (1, 2)
        ]

        claims = await asyncio.gather(
            claim_next_workspace_operation(session_factory=maker),
            claim_next_workspace_operation(session_factory=maker),
        )
        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        assert await claim_next_workspace_operation(session_factory=maker) is None

        live_claim = claimed[0]
        stale_claim = replace(live_claim, attempt_token="stale-token")
        assert not await heartbeat_workspace_operation(
            stale_claim.operation_id,
            stale_claim.attempt_token,
            session_factory=maker,
        )
        assert not await finalize_workspace_operation_success(
            stale_claim,
            WorkspaceOperationResult(),
            session_factory=maker,
        )
        assert await heartbeat_workspace_operation(
            live_claim.operation_id,
            live_claim.attempt_token,
            session_factory=maker,
        )
        assert await finalize_workspace_operation_success(
            live_claim,
            WorkspaceOperationResult(payload={"status": "completed"}),
            session_factory=maker,
        )

        second_claim = await claim_next_workspace_operation(session_factory=maker)
        assert second_claim is not None
        assert second_claim.operation_id in runner_operation_ids
        assert second_claim.operation_id != live_claim.operation_id
        expired = get_current_utc_datetime() - timedelta(seconds=10)
        async with maker() as session:
            await session.execute(
                update(WorkspaceOperation)
                .where(WorkspaceOperation.id == second_claim.operation_id)
                .values(lease_expires_at=expired, heartbeat_at=expired)
                .execution_options(skip_owner_scope=True)
            )
            await session.commit()
        assert (
            await recover_expired_workspace_operations(session_factory=maker) == 1
        )
        recovered = await _operation(maker, second_claim.operation_id)
        assert recovered.state == "queued"
        assert recovered.retry_count == 1
        assert recovered.attempt_token is None
        assert recovered.error_code == "operation_recovery_required"
        assert [
            event.event_type
            for event in await _operation_events(maker, second_claim.operation_id)
        ] == ["operation.progress", "operation.error"]

        sequence_presentation_id, sequence_conversation_id = (
            await _seed_presentation(maker, OWNER_A, conversation_next_position=2)
        )
        assert sequence_conversation_id is not None
        sequence_operation_id = await _seed_operation(
            maker,
            OWNER_A,
            presentation_id=sequence_presentation_id,
            conversation_id=sequence_conversation_id,
            anchor_position=1,
            key_seed=3,
            state="succeeded",
        )

        async def reserve_position() -> int:
            owner_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session, session.begin():
                    return (
                        await reserve_conversation_positions(
                            session, sequence_conversation_id, 1
                        )
                    ).start
            finally:
                reset_current_owner_id(owner_token)

        async def reserve_event_sequence() -> int:
            owner_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session, session.begin():
                    return await reserve_operation_event_sequence(
                        session, sequence_operation_id
                    )
            finally:
                reset_current_owner_id(owner_token)

        assert sorted(
            await asyncio.gather(reserve_position(), reserve_position())
        ) == [2, 3]
        assert sorted(
            await asyncio.gather(
                reserve_event_sequence(), reserve_event_sequence()
            )
        ) == [1, 2]

        publication_id, _ = await _seed_presentation(maker, OWNER_A)

        async def publish() -> str:
            owner_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session:
                    try:
                        revision = await publish_presentation_revision(
                            session,
                            presentation_id=publication_id,
                            expected_revision=0,
                        )
                        await session.commit()
                        return f"ok:{revision}"
                    except PresentationPublicationConflict:
                        await session.rollback()
                        return "stale"
            finally:
                reset_current_owner_id(owner_token)

        assert sorted(await asyncio.gather(publish(), publish())) == [
            "ok:1",
            "stale",
        ]
        async with maker() as session:
            stored_presentation = await session.get(
                PresentationModel, publication_id
            )
            assert stored_presentation is not None
            assert stored_presentation.revision == 1

        command_presentation_a, command_conversation_a = (
            await _seed_presentation(maker, OWNER_A, conversation_next_position=1)
        )
        command_presentation_b, _ = await _seed_presentation(
            maker, OWNER_B, conversation_next_position=1
        )
        assert command_conversation_a is not None
        command_request = WorkspaceCommandRequest(
            command="Change the title",
            command_type="presentation.edit",
            idempotency_key="postgres-concurrent-command",
            expected_revision=0,
        )

        async def issue_command(owner_id: uuid.UUID, presentation_id: uuid.UUID):
            owner_token = set_current_owner_id(owner_id)
            try:
                async with maker() as session:
                    return await create_workspace_command(
                        session,
                        presentation_id=presentation_id,
                        request=command_request,
                    )
            finally:
                reset_current_owner_id(owner_token)

        duplicate_results = await asyncio.gather(
            issue_command(OWNER_A, command_presentation_a),
            issue_command(OWNER_A, command_presentation_a),
        )
        assert duplicate_results[0][0].id == duplicate_results[1][0].id
        assert sorted(result[1] for result in duplicate_results) == [False, True]

        async with maker() as session:
            command_messages = list(
                (
                    await session.scalars(
                        select(WorkspaceMessage)
                        .where(
                            WorkspaceMessage.conversation_id
                            == command_conversation_a
                        )
                        .execution_options(skip_owner_scope=True)
                    )
                ).all()
            )
            command_operations = list(
                (
                    await session.scalars(
                        select(WorkspaceOperation)
                        .where(
                            WorkspaceOperation.presentation_id
                            == command_presentation_a,
                            WorkspaceOperation.kind == "presentation.edit",
                        )
                        .execution_options(skip_owner_scope=True)
                    )
                ).all()
            )
        assert len(command_messages) == 1
        assert len(command_operations) == 1

        # Exercise the dialect-neutral timeline projection against PostgreSQL.
        # The message and operation anchors are selected by one UNION statement,
        # while the event high-watermark remains an independent replay cursor.
        owner_token = set_current_owner_id(OWNER_A)
        try:
            async with maker() as session:
                timeline = await workspace_timeline(
                    session,
                    presentation_id=command_presentation_a,
                )
        finally:
            reset_current_owner_id(owner_token)
        assert [item["anchor_type"] for item in timeline["items"]] == [
            "message",
            "operation",
        ]
        assert timeline["items"][0]["id"] == str(command_messages[0].id)
        assert timeline["items"][1]["operation_id"] == str(
            command_operations[0].id
        )
        assert int(timeline["event_high_watermark"]) >= 1

        cancel_request = WorkspaceOperationActionRequest(
            action="cancel",
            action_id="postgres-concurrent-cancel",
            idempotency_key="postgres-concurrent-cancel",
            expected_operation_version=0,
            expected_revision=0,
        )

        async def issue_cancel():
            owner_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session:
                    return await apply_workspace_action(
                        session,
                        operation_id=command_operations[0].id,
                        request=cancel_request,
                    )
            finally:
                reset_current_owner_id(owner_token)

        cancel_results = await asyncio.gather(issue_cancel(), issue_cancel())
        assert cancel_results[0][0].id == cancel_results[1][0].id
        assert sorted(result[2] for result in cancel_results) == [False, True]
        async with maker() as session:
            receipts = list(
                (
                    await session.scalars(
                        select(WorkspaceActionReceipt)
                        .where(
                            WorkspaceActionReceipt.operation_id
                            == command_operations[0].id
                        )
                        .execution_options(skip_owner_scope=True)
                    )
                ).all()
            )
        assert len(receipts) == 1

        other_owner_operation, other_owner_duplicate = await issue_command(
            OWNER_B, command_presentation_b
        )
        assert not other_owner_duplicate
        assert other_owner_operation.id != duplicate_results[0][0].id
        assert other_owner_operation.owner_id == OWNER_B

        with pytest.raises(IntegrityError):
            owner_token = set_current_owner_id(OWNER_A)
            try:
                async with maker() as session, session.begin():
                    session.add(
                        WorkspaceAsset(
                            state="unsafe_unknown_state",
                            original_name="invalid.txt",
                            storage_key=f"workspace/{uuid.uuid4()}/invalid.txt",
                        )
                    )
            finally:
                reset_current_owner_id(owner_token)
    finally:
        await engine.dispose()


def test_postgresql_workspace_migrations_and_concurrency_contracts(
    postgres_workspace_database: tuple[Engine, Config, URL],
) -> None:
    engine, config, async_url = postgres_workspace_database

    command.upgrade(config, "head")
    _assert_workspace_schema(engine)

    command.downgrade(config, migrations.REVISION_AUTHORED_EXACT_PROVENANCE)
    downgraded_inspector = inspect(engine)
    assert WORKSPACE_TABLES.isdisjoint(downgraded_inspector.get_table_names())
    assert "revision" not in {
        column["name"]
        for column in downgraded_inspector.get_columns("presentations")
    }
    assert "workspace_operation_id" not in {
        column["name"]
        for column in downgraded_inspector.get_columns(
            "presentation_generation_jobs"
        )
    }

    command.upgrade(config, "head")
    _assert_workspace_schema(engine)
    asyncio.run(_exercise_postgresql_workspace(async_url))
