"""The chat slide tools must not destroy what they cannot rebuild.

An authored slide is model-written HTML with no layout schema behind it.
``delete_slide`` works on any slide row, but ``save_slide`` needs a layout to
author against - so on an authored deck the destructive half of the pair
succeeds while the constructive half always fails.  A "redo this page" turn
therefore deletes the page and leaves a blank in its place.

These tests pin the gate that closes that path, and the control case that keeps
it from over-blocking ordinary layout decks.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

from models.sql.presentation import PresentationModel
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from services.chat.memory_layer import PresentationChatMemoryLayer
from services.chat.outline_context import OutlineTemplateContext


TABLES = [
    PresentationModel.__table__,
    SlideModel.__table__,
    PresentationVersionModel.__table__,
]

AUTHORED_HTML = "<!DOCTYPE html><html><body><h1>제안 아키텍처</h1></body></html>"


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


async def _seed(maker, *, authored: bool) -> uuid.UUID:
    presentation_id = uuid.uuid4()
    async with maker() as session, session.begin():
        session.add(
            PresentationModel(
                id=presentation_id,
                content="gate fixture",
                n_slides=3,
                language="ko",
                title="gate fixture",
                # An authored deck carries no layout catalogue at all, which is
                # exactly why save_slide cannot rebuild one of its pages.
                layout=None if authored else {"name": "deck", "slides": []},
            )
        )
        for index in range(3):
            session.add(
                SlideModel(
                    presentation=presentation_id,
                    layout_group="authored" if authored else "deck",
                    layout="authored:ARCHITECTURE" if authored else "title",
                    index=index,
                    content={} if authored else {"title": f"slide {index}"},
                    html_content=AUTHORED_HTML if authored else None,
                    ui=None,
                    properties=None,
                )
            )
    return presentation_id


async def _slide_rows(maker, presentation_id: uuid.UUID) -> list[SlideModel]:
    async with maker() as session:
        return list(
            (
                await session.execute(
                    select(SlideModel)
                    .where(SlideModel.presentation == presentation_id)
                    .order_by(SlideModel.index)
                )
            ).scalars()
        )


def test_page_remake_owns_slide_regenerate_regardless_of_registration_order():
    """Ownership of the remake kind must not depend on which line runs first.

    Both handlers used to claim ``slide.regenerate`` and the remake handler won
    only because it registered second.  Swapping those two lines would have
    handed authored page regeneration to the chat tools - the exact path that
    deletes a page it cannot rebuild.
    """

    from services.workspace_chat_operation_handler import (
        register_presentation_chat_operation_handlers,
    )
    from services.workspace_operation_handler_registry import (
        WorkspaceOperationHandlerRegistry,
    )
    from services.workspace_page_remake_operation_handler import (
        WorkspacePageRemakeOperationHandler,
        register_workspace_page_remake_operation_handler,
    )

    for chat_first in (True, False):
        registry = WorkspaceOperationHandlerRegistry()
        order = (
            register_presentation_chat_operation_handlers,
            register_workspace_page_remake_operation_handler,
        )
        for register in order if chat_first else reversed(order):
            register(registry=registry, replace=True)

        assert isinstance(
            registry.resolve("slide.regenerate"),
            WorkspacePageRemakeOperationHandler,
        ), f"chat_first={chat_first}"


def test_delete_slide_refuses_an_authored_page(tmp_path: Path):
    asyncio.run(_delete_slide_refuses_an_authored_page(tmp_path))


async def _delete_slide_refuses_an_authored_page(tmp_path: Path):
    engine, maker = await _database(tmp_path / "authored-delete.db")
    try:
        presentation_id = await _seed(maker, authored=True)
        async with maker() as session:
            result = await PresentationChatMemoryLayer(
                session, presentation_id
            ).delete_slide(index=1)
            await session.commit()

        assert result["deleted"] is False, result
        assert "페이지 다시 만들기" in result["message"]

        rows = await _slide_rows(maker, presentation_id)
        assert [row.index for row in rows] == [0, 1, 2]
        assert all(row.html_content == AUTHORED_HTML for row in rows)
    finally:
        await engine.dispose()


def test_save_slide_refuses_to_replace_an_authored_page(tmp_path: Path):
    asyncio.run(_save_slide_refuses_to_replace_an_authored_page(tmp_path))


async def _save_slide_refuses_to_replace_an_authored_page(tmp_path: Path):
    engine, maker = await _database(tmp_path / "authored-replace.db")
    try:
        presentation_id = await _seed(maker, authored=True)
        async with maker() as session:
            result = await PresentationChatMemoryLayer(
                session, presentation_id
            ).save_slide(
                content={"title": "replacement"},
                layout_id="title",
                index=1,
                replace_old_slide_at_index=True,
            )
            await session.commit()

        assert result["saved"] is False, result
        assert result["validation_errors"] == ["authored_slide_requires_page_remake"]

        rows = await _slide_rows(maker, presentation_id)
        assert [row.layout for row in rows] == ["authored:ARCHITECTURE"] * 3
        assert all(row.html_content == AUTHORED_HTML for row in rows)
    finally:
        await engine.dispose()


def test_add_new_slide_refuses_to_insert_an_unfillable_blank(tmp_path: Path):
    asyncio.run(_add_new_slide_refuses_to_insert_an_unfillable_blank(tmp_path))


async def _add_new_slide_refuses_to_insert_an_unfillable_blank(tmp_path: Path):
    engine, maker = await _database(tmp_path / "authored-insert.db")
    try:
        presentation_id = await _seed(maker, authored=True)
        async with maker() as session:
            context = OutlineTemplateContext(
                sql_session=session,
                presentation_id=presentation_id,
                memory=PresentationChatMemoryLayer(session, presentation_id),
            )
            result = await context.add_new_slide(index=1)
            await session.commit()

        assert result["error"] == "authored_slide_requires_page_remake", result

        rows = await _slide_rows(maker, presentation_id)
        assert len(rows) == 3
        assert all(row.layout == "authored:ARCHITECTURE" for row in rows)
    finally:
        await engine.dispose()


def test_an_ordinary_layout_deck_is_still_editable(tmp_path: Path):
    asyncio.run(_an_ordinary_layout_deck_is_still_editable(tmp_path))


async def _an_ordinary_layout_deck_is_still_editable(tmp_path: Path):
    """The gate must close only the authored path, not slide editing at large."""

    engine, maker = await _database(tmp_path / "layout-deck.db")
    try:
        presentation_id = await _seed(maker, authored=False)
        async with maker() as session:
            result = await PresentationChatMemoryLayer(
                session, presentation_id
            ).delete_slide(index=1)
            await session.commit()

        assert result["deleted"] is True, result
        assert result["shifted_slide_count"] == 1

        rows = await _slide_rows(maker, presentation_id)
        assert [row.index for row in rows] == [0, 1]
        assert [row.content["title"] for row in rows] == ["slide 0", "slide 2"]
    finally:
        await engine.dispose()
