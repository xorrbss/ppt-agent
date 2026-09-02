"""The authored-slide editor event ledger and its write path."""

from __future__ import annotations

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
from models.authored_slide_editor_event import AuthoredSlideEditorEventRequest
from models.sql.authored_slide_editor_event import AuthoredSlideEditorEvent
from models.sql.presentation import PresentationModel
from models.sql.user import User

# Importing the database module installs the central ``do_orm_execute`` owner
# scoping and the ``before_flush`` owner stamper on SQLAlchemy's Session; the
# running app always has it imported through ``get_async_session``.
import services.database  # noqa: F401
from services.authored_slide_editor_event_service import (
    count_editor_events_by_archetype,
    editor_event_key_hash,
    record_authored_slide_editor_event,
)
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
            session.add(
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


def _request(**overrides) -> AuthoredSlideEditorEventRequest:
    payload = {
        "event_type": "slide_regenerated",
        "slide_index": 3,
        "archetype": "process",
        "event_key": "click-1",
    }
    payload.update(overrides)
    return AuthoredSlideEditorEventRequest(**payload)


async def _test_an_event_is_recorded_and_owner_stamped(tmp_path):
    engine, session_maker = await _database(tmp_path / "events.db")
    owner_id = uuid.uuid4()
    presentation_id = await _deck(session_maker, owner_id)
    try:
        async with session_maker() as session:
            with _owner(owner_id):
                response = await record_authored_slide_editor_event(
                    session, presentation_id, _request()
                )
        assert response.deduplicated is False
        assert response.slide_index == 3

        async with session_maker() as session:
            with _owner(owner_id):
                rows = (
                    (await session.execute(sa.select(AuthoredSlideEditorEvent)))
                    .scalars()
                    .all()
                )
        assert [row.owner_id for row in rows] == [owner_id]
    finally:
        await engine.dispose()


async def _test_a_repeated_event_key_records_the_action_once(tmp_path):
    engine, session_maker = await _database(tmp_path / "events.db")
    owner_id = uuid.uuid4()
    presentation_id = await _deck(session_maker, owner_id)
    try:
        for _ in range(3):
            async with session_maker() as session:
                with _owner(owner_id):
                    response = await record_authored_slide_editor_event(
                        session, presentation_id, _request()
                    )
        assert response.deduplicated is True

        async with session_maker() as session:
            with _owner(owner_id):
                count = await session.scalar(
                    sa.select(sa.func.count()).select_from(AuthoredSlideEditorEvent)
                )
        assert count == 1
    finally:
        await engine.dispose()


async def _test_another_owner_cannot_see_the_events(tmp_path):
    engine, session_maker = await _database(tmp_path / "events.db")
    owner_id = uuid.uuid4()
    intruder_id = uuid.uuid4()
    presentation_id = await _deck(session_maker, owner_id)
    try:
        async with session_maker() as session:
            with _owner(owner_id):
                await record_authored_slide_editor_event(
                    session, presentation_id, _request()
                )

        async with session_maker() as session:
            with _owner(intruder_id):
                visible = (
                    (await session.execute(sa.select(AuthoredSlideEditorEvent)))
                    .scalars()
                    .all()
                )
        assert visible == []
    finally:
        await engine.dispose()


async def _test_events_aggregate_per_archetype_for_the_weekly_report(tmp_path):
    engine, session_maker = await _database(tmp_path / "events.db")
    owner_id = uuid.uuid4()
    presentation_id = await _deck(session_maker, owner_id)
    try:
        async with session_maker() as session:
            with _owner(owner_id):
                await record_authored_slide_editor_event(
                    session, presentation_id, _request(event_key="a")
                )
                await record_authored_slide_editor_event(
                    session, presentation_id, _request(event_key="b", slide_index=4)
                )
                await record_authored_slide_editor_event(
                    session,
                    presentation_id,
                    _request(
                        event_type="archetype_switched",
                        event_key="c",
                        archetype="cycle",
                        previous_archetype="process",
                    ),
                )
                aggregated = await count_editor_events_by_archetype(session)
        assert sorted(aggregated) == [
            ("cycle", "archetype_switched", 1),
            ("process", "slide_regenerated", 2),
        ]
    finally:
        await engine.dispose()


async def _test_a_deck_scoped_export_event_needs_no_slide_index(tmp_path):
    engine, session_maker = await _database(tmp_path / "events.db")
    owner_id = uuid.uuid4()
    presentation_id = await _deck(session_maker, owner_id)
    try:
        async with session_maker() as session:
            with _owner(owner_id):
                response = await record_authored_slide_editor_event(
                    session,
                    presentation_id,
                    AuthoredSlideEditorEventRequest(
                        event_type="export_completed", event_key="export-1"
                    ),
                )
        assert response.slide_index is None
    finally:
        await engine.dispose()


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


def test_the_request_model_refuses_a_slide_event_without_a_slide():
    with pytest.raises(ValueError):
        AuthoredSlideEditorEventRequest(
            event_type="slide_regenerated", event_key="k"
        )
    with pytest.raises(ValueError):
        AuthoredSlideEditorEventRequest(
            event_type="archetype_switched",
            slide_index=1,
            archetype="cycle",
            event_key="k",
        )


def test_the_raw_event_key_never_reaches_the_database():
    presentation_id = uuid.uuid4()
    digest = editor_event_key_hash(presentation_id, _request(event_key="secret-key"))

    assert len(digest) == 64
    assert "secret-key" not in digest
    assert digest != editor_event_key_hash(uuid.uuid4(), _request(event_key="secret-key"))


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


def test_an_event_is_recorded_and_owner_stamped(tmp_path):
    asyncio.run(_test_an_event_is_recorded_and_owner_stamped(tmp_path))


def test_a_repeated_event_key_records_the_action_once(tmp_path):
    asyncio.run(_test_a_repeated_event_key_records_the_action_once(tmp_path))


def test_another_owner_cannot_see_the_events(tmp_path):
    asyncio.run(_test_another_owner_cannot_see_the_events(tmp_path))


def test_events_aggregate_per_archetype_for_the_weekly_report(tmp_path):
    asyncio.run(_test_events_aggregate_per_archetype_for_the_weekly_report(tmp_path))


def test_a_deck_scoped_export_event_needs_no_slide_index(tmp_path):
    asyncio.run(_test_a_deck_scoped_export_event_needs_no_slide_index(tmp_path))


def test_the_table_refuses_a_slide_event_without_a_slide(tmp_path):
    asyncio.run(_test_the_table_refuses_a_slide_event_without_a_slide(tmp_path))
