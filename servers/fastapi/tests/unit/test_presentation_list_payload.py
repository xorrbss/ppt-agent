"""The deck list must not carry authored slide HTML; the editor read still must.

``GET /presentation/all`` joined the whole first-slide row, so every authored
deck shipped a full HTML document per dashboard card that no dashboard code
reads.  These tests pin both halves of the fix: the list withholds the HTML and
never SELECTs the column, while ``GET /presentation/{id}`` — the read that feeds
the editor — still serves it in full.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
import uuid

from fastapi.encoders import jsonable_encoder
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from api.v1.ppt.endpoints import presentation_crud
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.slide import SlideModel

TABLES = [
    PresentationModel.__table__,
    SlideModel.__table__,
]

# Long enough that a regression shows up as payload weight, not just a flag.
AUTHORED_COVER_HTML = (
    "<section data-slide='cover'><h1>Authored cover</h1>"
    + ("<p>filler</p>" * 512)
    + "</section>"
)
AUTHORED_SECOND_HTML = "<section data-slide='body'><h2>Second</h2></section>"


def _native_ui() -> dict[str, Any]:
    return {
        "id": "title",
        "description": "Native editable title slide",
        "components": [
            {
                "id": "hero",
                "description": "Editable hero title component",
                "position": {"x": 0, "y": 0},
                "elements": [
                    {
                        "type": "text",
                        "position": {"x": 1, "y": 1},
                        "size": {"width": 8, "height": 1},
                        "runs": [{"text": "Native", "font": {"size": 18, "bold": True}}],
                        "decorative": False,
                        "name": "title",
                        "min_length": 1,
                        "max_length": 80,
                    }
                ],
            }
        ],
    }


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: SQLModel.metadata.create_all(
                sync_connection,
                tables=TABLES,
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(maker) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """One authored deck, one native deck, one staging deck that stays hidden."""

    authored_id = uuid.uuid4()
    native_id = uuid.uuid4()
    staging_id = uuid.uuid4()
    async with maker() as session, session.begin():
        session.add_all(
            [
                PresentationModel(
                    id=authored_id,
                    content="authored prompt",
                    n_slides=2,
                    language="ko",
                    title="Authored deck",
                    mode="authored",
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                ),
                PresentationModel(
                    id=native_id,
                    content="native prompt",
                    n_slides=1,
                    language="en",
                    title="Native deck",
                    mode="template",
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                ),
                PresentationModel(
                    id=staging_id,
                    content="staging prompt",
                    n_slides=1,
                    language="en",
                    title="Staging deck",
                    lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
                ),
                SlideModel(
                    presentation=authored_id,
                    layout_group="authored",
                    layout="authored:cover",
                    index=0,
                    content={"image": "/static/cover.png", "__authored__": True},
                    html_content=AUTHORED_COVER_HTML,
                    speaker_note="cover note",
                    properties={"source": "authored"},
                ),
                SlideModel(
                    presentation=authored_id,
                    layout_group="authored",
                    layout="authored:body",
                    index=1,
                    content={"image": "/static/body.png", "__authored__": True},
                    html_content=AUTHORED_SECOND_HTML,
                    properties=None,
                ),
                SlideModel(
                    presentation=native_id,
                    layout_group="native",
                    layout="title",
                    index=0,
                    content={"title": "Native cover"},
                    ui=_native_ui(),
                    speaker_note="native note",
                    properties={"source": "template-v2"},
                ),
                SlideModel(
                    presentation=staging_id,
                    layout_group="native",
                    layout="title",
                    index=0,
                    content={"title": "Staging cover"},
                ),
            ]
        )
    return authored_id, native_id, staging_id


def test_presentation_list_withholds_slide_html_and_never_selects_it(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "list-payload.db")
        try:
            authored_id, native_id, staging_id = await _seed(maker)

            statements: list[str] = []

            @event.listens_for(engine.sync_engine, "before_cursor_execute")
            def _record(_conn, _cursor, statement, _params, _context, _many):
                statements.append(statement)

            async with maker() as session:
                decks = await presentation_crud.get_all_presentations(session)
                dirty_after_call = list(session.dirty)

            event.remove(engine.sync_engine, "before_cursor_execute", _record)

            by_id = {deck.id: deck for deck in decks}
            assert set(by_id) == {authored_id, native_id}, "staging deck leaked"
            assert staging_id not in by_id

            # The list withholds authored HTML for every deck, authored or not.
            for deck in decks:
                assert len(deck.slides) == 1
                assert deck.slides[0].html_content is None

            # The column is never read from the database at all: neither in the
            # SELECT list nor via a lazy load behind the deferred attribute.
            assert statements, "no SQL captured; the listener never fired"
            assert not any("html_content" in statement for statement in statements), (
                "the list query still touches slides.html_content: "
                f"{[s for s in statements if 'html_content' in s]}"
            )

            # A read endpoint must not write. Blanking the loaded row instead of
            # copying it marks it dirty, and the next query autoflushes
            # `UPDATE slides SET html_content=NULL`.
            assert dirty_after_call == []
            assert not any(
                statement.strip().upper().startswith("UPDATE")
                for statement in statements
            )

            # Everything the cards actually render survives untouched.
            authored_slide = by_id[authored_id].slides[0]
            assert authored_slide.content == {
                "image": "/static/cover.png",
                "__authored__": True,
            }
            assert authored_slide.layout_group == "authored"
            assert authored_slide.layout == "authored:cover"
            assert authored_slide.index == 0
            assert authored_slide.speaker_note == "cover note"
            assert authored_slide.properties == {"source": "authored"}
            assert by_id[authored_id].title == "Authored deck"
            assert by_id[authored_id].mode == "authored"

            native_slide = by_id[native_id].slides[0]
            assert native_slide.content == {"title": "Native cover"}
            assert native_slide.ui is not None
            assert native_slide.ui["components"][0]["id"] == "hero"
            assert native_slide.properties == {"source": "template-v2"}

            # `html_content` stays in the response shape as an explicit null
            # rather than disappearing, so existing clients keep their key.
            encoded = jsonable_encoder(decks)
            for deck in encoded:
                for slide in deck["slides"]:
                    assert "html_content" in slide
                    assert slide["html_content"] is None

            # Nothing was blanked in storage.
            async with maker() as session:
                stored = (
                    (
                        await session.execute(
                            select(SlideModel)
                            .where(SlideModel.presentation == authored_id)
                            .order_by(SlideModel.index)
                        )
                    )
                    .scalars()
                    .all()
                )
            assert [slide.html_content for slide in stored] == [
                AUTHORED_COVER_HTML,
                AUTHORED_SECOND_HTML,
            ]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_single_presentation_read_still_serves_authored_html(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "single-payload.db")
        try:
            authored_id, _native_id, _staging_id = await _seed(maker)

            async with maker() as session:
                deck = await presentation_crud.get_presentation(authored_id, session)

            assert [slide.html_content for slide in deck.slides] == [
                AUTHORED_COVER_HTML,
                AUTHORED_SECOND_HTML,
            ]
            assert jsonable_encoder(deck)["slides"][0]["html_content"] == (
                AUTHORED_COVER_HTML
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
