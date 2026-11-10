"""The authored-slide editor event table, kept until a migration drops it."""

from __future__ import annotations

from tests.support.existing_authored_rows import add_existing_authored_presentation
import asyncio
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import migrations
from models.sql.authored_slide_editor_event import AuthoredSlideEditorEvent
from models.sql.presentation import PresentationModel
from models.sql.user import User

# Importing the database module installs the central ``do_orm_execute`` owner
# scoping and the ``before_flush`` owner stamper on SQLAlchemy's Session; the
# running app always has it imported through ``get_async_session``.
import services.database  # noqa: F401
from utils.owner_context import reset_current_owner_id, set_current_owner_id

FASTAPI_ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def _owner(owner_id: uuid.UUID):
    token = set_current_owner_id(owner_id)
    try:
        yield
    finally:
        reset_current_owner_id(token)


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _configure(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=[
                    User.__table__,
                    PresentationModel.__table__,
                    AuthoredSlideEditorEvent.__table__,
                ],
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _deck(session_maker, owner_id: uuid.UUID) -> uuid.UUID:
    presentation_id = uuid.uuid4()
    async with session_maker() as session:
        with _owner(owner_id):
            await add_existing_authored_presentation(session, 
                PresentationModel(
                    id=presentation_id,
                    owner_id=owner_id,
                    content="",
                    n_slides=1,
                    language="ko",
                    title="benchmark deck",
                    mode="authored",
                )
            )
            await session.commit()
    return presentation_id


async def _test_the_table_refuses_a_slide_event_without_a_slide(tmp_path):
    engine, session_maker = await _database(tmp_path / "events.db")
    owner_id = uuid.uuid4()
    presentation_id = await _deck(session_maker, owner_id)
    try:
        async with session_maker() as session:
            with _owner(owner_id):
                session.add(
                    AuthoredSlideEditorEvent(
                        presentation_id=presentation_id,
                        slide_index=None,
                        event_type="slide_regenerated",
                        event_key_hash="0" * 64,
                    )
                )
                with pytest.raises(IntegrityError):
                    await session.commit()
    finally:
        await engine.dispose()


def test_the_migration_creates_the_table_and_is_a_no_op_on_rerun(tmp_path):
    database = tmp_path / "migrated.db"
    config = Config()
    config.set_main_option("script_location", str(FASTAPI_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")

    command.upgrade(config, "head")
    command.upgrade(config, "head")

    engine = sa.create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar()
                == migrations.HEAD_REVISION
            )
            inspector = sa.inspect(connection)
            assert "authored_slide_editor_events" in inspector.get_table_names()
            columns = {
                column["name"]
                for column in inspector.get_columns("authored_slide_editor_events")
            }
        assert {
            "owner_id",
            "presentation_id",
            "slide_index",
            "event_type",
            "archetype",
            "previous_archetype",
            "changed_characters",
            "event_key_hash",
            "created_at",
        }.issubset(columns)
    finally:
        engine.dispose()


def test_the_table_refuses_a_slide_event_without_a_slide(tmp_path):
    asyncio.run(_test_the_table_refuses_a_slide_event_without_a_slide(tmp_path))
