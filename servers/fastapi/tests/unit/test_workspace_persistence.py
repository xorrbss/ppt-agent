from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import migrations
from models.sql.presentation import PresentationModel
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
    WorkspaceOutbox,
)
from services.presentation_publication_service import (
    PresentationPublicationConflict,
    publish_presentation_revision,
)
from services.workspace_db_adapter import (
    bootstrap_workspace_turn,
    reserve_conversation_positions,
    reserve_operation_event_sequence,
)
from services.workspace_runtime_profile import (
    UnsupportedWorkspaceRuntimeError,
    WorkspaceRuntimeKind,
    require_supported_workspace_runtime,
    resolve_workspace_runtime_profile,
)


WORKSPACE_TABLES = [
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
    WorkspaceArtifact.__table__,
    WorkspaceCleanupJournal.__table__,
]


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection, tables=WORKSPACE_TABLES
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _presentation() -> PresentationModel:
    return PresentationModel(
        content="bootstrap",
        n_slides=1,
        language="en",
        layout={"name": "test"},
    )


def test_anonymous_bootstrap_is_atomic_and_idempotent(tmp_path: Path):
    asyncio.run(_anonymous_bootstrap_is_atomic_and_idempotent(tmp_path))


async def _anonymous_bootstrap_is_atomic_and_idempotent(tmp_path: Path):
    engine, maker = await _database(tmp_path / "bootstrap.db")
    try:
        async with maker() as session, session.begin():
            presentation = _presentation()
            session.add(presentation)
            await session.flush()
            conversation = WorkspaceConversation(presentation_id=presentation.id)
            session.add(conversation)
            await session.flush()
            message, operation = await bootstrap_workspace_turn(
                session,
                conversation_id=conversation.id,
                message={"content": "make a blue deck", "message_metadata": {"source": "initial_generation"}},
                operation={
                    "presentation_id": presentation.id,
                    "kind": "deck.generate",
                    "idempotency_key_hash": "a" * 64,
                    "request_hash": "b" * 64,
                    "request_payload": {"tone": "business"},
                },
            )
            assert (message.position, operation.anchor_position) == (1, 2)
            assert operation.owner_id is None
            assert operation.owner_scope == "anonymous"

        with pytest.raises(IntegrityError):
            async with maker() as session:
                async with session.begin():
                    conversation = (
                        await session.execute(select(WorkspaceConversation))
                    ).scalar_one()
                    presentation = (
                        await session.execute(select(PresentationModel))
                    ).scalar_one()
                    await bootstrap_workspace_turn(
                        session,
                        conversation_id=conversation.id,
                        message={"content": "duplicate"},
                        operation={
                            "presentation_id": presentation.id,
                            "kind": "deck.generate",
                            "idempotency_key_hash": "a" * 64,
                            "request_hash": "b" * 64,
                            "request_payload": {},
                        },
                    )

        async with maker() as session:
            assert len((await session.execute(select(WorkspaceMessage))).scalars().all()) == 1
            assert len((await session.execute(select(WorkspaceOperation))).scalars().all()) == 1
            conversation = (await session.execute(select(WorkspaceConversation))).scalar_one()
            assert conversation.next_position == 3
    finally:
        await engine.dispose()


def test_positions_and_event_sequences_are_concurrently_reserved(tmp_path: Path):
    asyncio.run(_positions_and_event_sequences_are_concurrently_reserved(tmp_path))


async def _positions_and_event_sequences_are_concurrently_reserved(tmp_path: Path):
    engine, maker = await _database(tmp_path / "positions.db")
    try:
        async with maker() as session, session.begin():
            presentation = _presentation()
            session.add(presentation)
            await session.flush()
            conversation = WorkspaceConversation(presentation_id=presentation.id)
            session.add(conversation)
            await session.flush()
            operation = WorkspaceOperation(
                conversation_id=conversation.id,
                presentation_id=presentation.id,
                anchor_position=99,
                kind="test",
                idempotency_key_hash="c" * 64,
                request_hash="d" * 64,
                request_payload={},
            )
            session.add(operation)
            await session.flush()
            conversation_id, operation_id = conversation.id, operation.id

        async def reserve_position() -> int:
            async with maker() as session, session.begin():
                return (await reserve_conversation_positions(session, conversation_id, 1)).start

        async def reserve_event() -> int:
            async with maker() as session, session.begin():
                return await reserve_operation_event_sequence(session, operation_id)

        assert sorted(await asyncio.gather(reserve_position(), reserve_position())) == [1, 2]
        assert sorted(await asyncio.gather(reserve_event(), reserve_event())) == [1, 2]
    finally:
        await engine.dispose()


def test_publication_revision_compare_and_swap(tmp_path: Path):
    asyncio.run(_publication_revision_compare_and_swap(tmp_path))


async def _publication_revision_compare_and_swap(tmp_path: Path):
    engine, maker = await _database(tmp_path / "revision.db")
    try:
        async with maker() as session, session.begin():
            presentation = _presentation()
            session.add(presentation)
            await session.flush()
            presentation_id = presentation.id

        async def publish() -> str:
            async with maker() as session:
                try:
                    revision = await publish_presentation_revision(
                        session,
                        presentation_id=presentation_id,
                        expected_revision=0,
                    )
                    await session.commit()
                    return f"ok:{revision}"
                except PresentationPublicationConflict:
                    await session.rollback()
                    return "stale"

        assert sorted(await asyncio.gather(publish(), publish())) == ["ok:1", "stale"]
        async with maker() as session:
            stored = await session.get(PresentationModel, presentation_id)
            assert stored is not None and stored.revision == 1
    finally:
        await engine.dispose()


def test_runtime_profile_recognises_supported_dialects():
    assert (
        resolve_workspace_runtime_profile("sqlite+aiosqlite:///local.db").kind
        is WorkspaceRuntimeKind.SQLITE_LOCAL
    )
    assert (
        resolve_workspace_runtime_profile("postgresql+asyncpg://db/app").kind
        is WorkspaceRuntimeKind.POSTGRESQL_CLUSTER
    )
    assert not resolve_workspace_runtime_profile("mysql+aiomysql://db/app").supported


def test_unsupported_dialect_fails_closed():
    sqlite = require_supported_workspace_runtime("sqlite+aiosqlite:///local.db")
    postgres = require_supported_workspace_runtime("postgresql+asyncpg://db/app")
    assert sqlite.kind is WorkspaceRuntimeKind.SQLITE_LOCAL
    assert postgres.kind is WorkspaceRuntimeKind.POSTGRESQL_CLUSTER

    with pytest.raises(UnsupportedWorkspaceRuntimeError) as exc_info:
        require_supported_workspace_runtime("mysql+aiomysql://db/app")
    assert exc_info.value.code == "chat_first_runtime_unsupported"


def test_workspace_asset_state_constraint_is_enforced(tmp_path: Path):
    asyncio.run(_workspace_asset_state_constraint_is_enforced(tmp_path))


async def _workspace_asset_state_constraint_is_enforced(tmp_path: Path):
    engine, maker = await _database(tmp_path / "asset-state.db")
    try:
        with pytest.raises(IntegrityError):
            async with maker() as session, session.begin():
                session.add(
                    WorkspaceAsset(
                        state="unsafe_unknown_state",
                        original_name="example.txt",
                        storage_key="workspace/example.txt",
                    )
                )
    finally:
        await engine.dispose()


def test_workspace_event_retention_floor_constraint_is_enforced(tmp_path: Path):
    asyncio.run(_workspace_event_retention_floor_constraint_is_enforced(tmp_path))


async def _workspace_event_retention_floor_constraint_is_enforced(tmp_path: Path):
    engine, maker = await _database(tmp_path / "event-retention-floor.db")
    try:
        with pytest.raises(IntegrityError):
            async with maker() as session, session.begin():
                presentation = _presentation()
                session.add(presentation)
                await session.flush()
                session.add(
                    WorkspaceConversation(
                        presentation_id=presentation.id,
                        event_retention_floor_cursor=-1,
                    )
                )
    finally:
        await engine.dispose()


def test_workspace_migration_upgrades_sqlite(tmp_path: Path):
    database_path = tmp_path / "migration.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "head")

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert set(table.__tablename__ for table in (
                WorkspaceConversation,
                WorkspaceMessage,
                WorkspaceAsset,
                WorkspaceMessageAttachment,
                WorkspaceOperation,
                WorkspaceOperationEvent,
                WorkspaceOutbox,
                WorkspaceActionReceipt,
                WorkspaceArtifact,
                WorkspaceCleanupJournal,
            )).issubset(inspector.get_table_names())
            presentation_columns = {
                column["name"]: column
                for column in inspector.get_columns("presentations")
            }
            assert {
                "revision",
                "deleted_at",
                "deletion_operation_id",
            }.issubset(presentation_columns)
            assert "workspace_operation_id" in {column["name"] for column in inspector.get_columns("presentation_generation_jobs")}
            attachment_columns = {
                column["name"]: column
                for column in inspector.get_columns(
                    "workspace_message_attachments"
                )
            }
            assert {
                "asset_id_snapshot",
                "asset_sha256",
                "asset_failure_code",
                "asset_failure_detail",
            }.issubset(attachment_columns)
            assert attachment_columns["asset_id"]["nullable"] is True
            assert attachment_columns["asset_id_snapshot"]["nullable"] is False
            attachment_asset_fk = next(
                foreign_key
                for foreign_key in inspector.get_foreign_keys(
                    "workspace_message_attachments"
                )
                if foreign_key["name"]
                == "fk_workspace_message_attachments_asset_id"
            )
            assert attachment_asset_fk["constrained_columns"] == ["asset_id"]
            assert attachment_asset_fk["referred_table"] == "workspace_assets"
            assert (
                attachment_asset_fk.get("options", {}).get("ondelete", "").upper()
                == "SET NULL"
            )
            attachment_asset_unique = next(
                constraint
                for constraint in inspector.get_unique_constraints(
                    "workspace_message_attachments"
                )
                if constraint["name"]
                == "uq_workspace_message_attachments_asset"
            )
            assert attachment_asset_unique["column_names"] == [
                "message_id",
                "asset_id_snapshot",
            ]
            assert inspector.get_foreign_keys("workspace_cleanup_journal") == []
            conversation_columns = {
                column["name"]: column
                for column in inspector.get_columns("workspace_conversations")
            }
            retention_floor = conversation_columns[
                "event_retention_floor_cursor"
            ]
            assert retention_floor["nullable"] is False
            assert str(retention_floor["type"]).upper() == "INTEGER"
            assert str(retention_floor["default"]) in {"0", "'0'"}
            assert "ck_workspace_conversations_event_retention_floor_cursor" in {
                constraint["name"]
                for constraint in inspector.get_check_constraints(
                    "workspace_conversations"
                )
            }
            assert (
                migrations._infer_revision_from_schema(
                    inspector,
                    set(inspector.get_table_names()),
                    migrations.REVISION_ACCOUNT_DELETION_FENCE,
                )
                    == migrations.HEAD_REVISION
            )
    finally:
        engine.dispose()


def test_workspace_schema_inference_distinguishes_f8_from_partial_fa1(
    tmp_path: Path,
):
    database_path = tmp_path / "migration-inference.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, migrations.REVISION_DURABLE_DELETION)

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            assert migrations._infer_revision_from_schema(
                inspector,
                tables,
                migrations.REVISION_ACCOUNT_DELETION_FENCE,
            ) == migrations.REVISION_DURABLE_DELETION

        with engine.begin() as connection:
            connection.execute(
                text(
                    'ALTER TABLE "user" ADD COLUMN deletion_state '
                    "VARCHAR(24) NOT NULL DEFAULT 'active'"
                )
            )

        with engine.connect() as connection:
            inspector = inspect(connection)
            with pytest.raises(
                RuntimeError,
                match="Account deletion fence columns are only partially applied",
            ):
                migrations._infer_revision_from_schema(
                    inspector,
                    set(inspector.get_table_names()),
                    migrations.REVISION_ACCOUNT_DELETION_FENCE,
                )
    finally:
        engine.dispose()


def test_workspace_migration_downgrade_upgrade_cycle(tmp_path: Path):
    database_path = tmp_path / "migration-cycle.db"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[2] / "alembic")
    )
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database_path.as_posix()}")
    command.upgrade(config, "head")
    command.downgrade(config, migrations.REVISION_AUTHORED_EXACT_PROVENANCE)

    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            assert "workspace_conversations" not in inspector.get_table_names()
            assert "workspace_cleanup_journal" not in inspector.get_table_names()
            assert "revision" not in {
                column["name"]
                for column in inspector.get_columns("presentations")
            }
            assert {
                "deleted_at",
                "deletion_operation_id",
            }.isdisjoint(
                {
                    column["name"]
                    for column in inspector.get_columns("presentations")
                }
            )
            assert "workspace_operation_id" not in {
                column["name"]
                for column in inspector.get_columns(
                    "presentation_generation_jobs"
                )
            }
    finally:
        engine.dispose()

    command.upgrade(config, "head")
