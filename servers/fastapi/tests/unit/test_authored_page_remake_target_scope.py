"""One unusable page must not disable remake for the rest of the deck.

``_load_deck`` used to require that *every* slide be authored, so a single
blank placeholder - the kind the editor inserts, and the kind a chat turn left
behind - made "페이지 다시 만들기" fail with
``authored_page_remake_unsupported_presentation`` on all pages, including the
fourteen healthy ones.

The requirement belongs to the page being remade.  These tests pin both halves:
an authored page stays remakeable next to a blank sibling, and the blank page
itself is still refused.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.presentation_from_template import AuthoredPageRemakePreviewRequest
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_page_remake_service import (
    AuthoredPageRemakeError,
    queue_authored_page_remake_preview,
)


TABLES = [
    PresentationModel.__table__,
    SlideModel.__table__,
    AsyncPresentationGenerationTaskModel.__table__,
]

AUTHORED_HTML = "<!DOCTYPE html><html><body><h1>제안 아키텍처</h1></body></html>"
BLANK_SLIDE_INDEX = 4
DECK_SIZE = 15


async def _database(path: Path):
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


async def _seed_deck_with_one_blank(maker) -> uuid.UUID:
    """Reproduce the shape of a real authored deck carrying one blank page."""

    presentation_id = uuid.uuid4()
    async with maker() as session, session.begin():
        session.add(
            PresentationModel(
                id=presentation_id,
                content="remake scope fixture",
                n_slides=DECK_SIZE,
                language="ko",
                title="remake scope fixture",
                # An authored deck has no layout catalogue of its own.
                layout=None,
                theme={"mode": "authored", "style": "visual-discovery"},
            )
        )
        for index in range(DECK_SIZE):
            blank = index == BLANK_SLIDE_INDEX
            session.add(
                SlideModel(
                    presentation=presentation_id,
                    layout_group="presentation" if blank else "authored",
                    layout="__blank_slide__" if blank else "authored:ARCHITECTURE",
                    index=index,
                    content={} if blank else {"__authored__": True},
                    html_content=None if blank else AUTHORED_HTML,
                    ui=None,
                    properties=None,
                )
            )
    return presentation_id


def test_an_authored_page_is_remakeable_beside_a_blank_sibling(tmp_path: Path):
    asyncio.run(_an_authored_page_is_remakeable_beside_a_blank_sibling(tmp_path))


async def _an_authored_page_is_remakeable_beside_a_blank_sibling(tmp_path: Path):
    engine, maker = await _database(tmp_path / "remake-scope.db")
    try:
        presentation_id = await _seed_deck_with_one_blank(maker)
        async with maker() as session:
            background_tasks = BackgroundTasks()
            task = await queue_authored_page_remake_preview(
                presentation_id,
                0,
                AuthoredPageRemakePreviewRequest(),
                background_tasks,
                session,
            )

        assert task.status == "pending"
        assert len(background_tasks.tasks) == 1
    finally:
        await engine.dispose()


def test_the_blank_page_itself_is_still_refused(tmp_path: Path):
    asyncio.run(_the_blank_page_itself_is_still_refused(tmp_path))


async def _the_blank_page_itself_is_still_refused(tmp_path: Path):
    """Remake re-authors an existing page; a blank one has no source to use."""

    engine, maker = await _database(tmp_path / "remake-blank.db")
    try:
        presentation_id = await _seed_deck_with_one_blank(maker)
        async with maker() as session:
            with pytest.raises(AuthoredPageRemakeError) as raised:
                await queue_authored_page_remake_preview(
                    presentation_id,
                    BLANK_SLIDE_INDEX,
                    AuthoredPageRemakePreviewRequest(),
                    BackgroundTasks(),
                    session,
                )

        assert raised.value.code == "authored_page_remake_unsupported_presentation"
        assert raised.value.stage == "source_validation"
    finally:
        await engine.dispose()


def test_a_deck_that_is_not_authored_at_all_is_still_refused(tmp_path: Path):
    asyncio.run(_a_deck_that_is_not_authored_at_all_is_still_refused(tmp_path))


async def _a_deck_that_is_not_authored_at_all_is_still_refused(tmp_path: Path):
    """The deck-level gate must survive the narrowing of the per-slide one."""

    engine, maker = await _database(tmp_path / "remake-layout-deck.db")
    try:
        presentation_id = uuid.uuid4()
        async with maker() as session, session.begin():
            session.add(
                PresentationModel(
                    id=presentation_id,
                    content="layout deck",
                    n_slides=1,
                    language="ko",
                    title="layout deck",
                    layout={"name": "deck", "slides": []},
                )
            )
            session.add(
                SlideModel(
                    presentation=presentation_id,
                    layout_group="deck",
                    layout="title",
                    index=0,
                    content={"title": "slide 0"},
                    html_content=None,
                    ui=None,
                    properties=None,
                )
            )

        async with maker() as session:
            with pytest.raises(AuthoredPageRemakeError) as raised:
                await queue_authored_page_remake_preview(
                    presentation_id,
                    0,
                    AuthoredPageRemakePreviewRequest(),
                    BackgroundTasks(),
                    session,
                )

        assert raised.value.code == "authored_page_remake_unsupported_presentation"
    finally:
        await engine.dispose()
