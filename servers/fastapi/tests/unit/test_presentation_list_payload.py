"""The deck list must not carry authored slide HTML; the editor read still must.

``GET /presentation/all`` joined the whole first-slide row, so every authored
deck shipped a full HTML document per dashboard card that no dashboard code
reads.  These tests pin both halves of the fix: the list withholds the HTML and
never SELECTs the column, while ``GET /presentation/{id}`` — the read that feeds
the editor — still serves it in full.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import uuid

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
import pytest
from sqlalchemy import event, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

from api.v1.ppt.endpoints import presentation_crud
from api.v1.ppt.endpoints.presentation_list_projection import (
    UnsupportedPresentationListItem,
)
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.slide import SlideModel
from services.authored_canvas import (
    canvas_metadata_for_aspect_ratio,
    merge_presentation_canvas,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id

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
SVG_DETAIL = {"diagnostic": "large source geometry " * 2048}
LIST_THEME = merge_presentation_canvas({
    "data": {"colors": {"primary": "#123456"}},
    "svg": {"qualityStatus": "passed", "reviewPages": []},
}, canvas_metadata_for_aspect_ratio("16:9").model_dump(mode="json", by_alias=True))
STORED_THEME = {
    **LIST_THEME,
    "authoredQualityGate": {"localFit": SVG_DETAIL},
    "authored_vnext": {**LIST_THEME["authored_vnext"], "scene": SVG_DETAIL},
    "svg": {
        **LIST_THEME["svg"],
        "quality": [SVG_DETAIL],
        "initialQuality": [SVG_DETAIL],
        "styleSnapshot": SVG_DETAIL,
    },
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
    """One authored deck, one svg deck, one staging deck that stays hidden."""

    authored_id = uuid.uuid4()
    svg_id = uuid.uuid4()
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
                    theme=STORED_THEME,
                    mode="authored",
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                ),
                PresentationModel(
                    id=svg_id,
                    content="svg prompt",
                    n_slides=1,
                    language="en",
                    title="SVG deck",
                    # svg-v1 is the only currently-registered non-authored
                    # driver; template-v2 was removed from the registry, so a
                    # row with that identity no longer resolves a profile.
                    mode="svg",
                    theme={"mode": "svg"},
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                ),
                PresentationModel(
                    id=staging_id,
                    content="staging prompt",
                    n_slides=1,
                    language="en",
                    title="Staging deck",
                    # A staging shell is stamped authored the moment generation
                    # creates it, so it carries that identity here too.
                    mode="authored",
                    theme={"mode": "authored"},
                    lifecycle_status=PRESENTATION_LIFECYCLE_STAGING,
                ),
                SlideModel(
                    presentation=authored_id,
                    layout_group="authored",
                    layout="authored:cover",
                    index=0,
                    content={"image": "/static/cover.png", "__authored__": True,
                             "svgReceipt": SVG_DETAIL},
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
                    presentation=svg_id,
                    layout_group="svg",
                    layout="svg",
                    index=0,
                    content={"title": "SVG cover"},
                    svg_content="<svg><text>SVG cover</text></svg>",
                    speaker_note="svg note",
                    properties={"source": "svg-v1"},
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
    return authored_id, svg_id, staging_id


def test_presentation_list_withholds_slide_html_and_never_selects_it(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "list-payload.db")
        try:
            authored_id, svg_id, staging_id = await _seed(maker)

            statements: list[str] = []

            @event.listens_for(engine.sync_engine, "before_cursor_execute")
            def _record(_conn, _cursor, statement, _params, _context, _many):
                statements.append(statement)

            async with maker() as session:
                decks = await presentation_crud.get_all_presentations(session)
                dirty_after_call = list(session.dirty)

            event.remove(engine.sync_engine, "before_cursor_execute", _record)

            by_id = {deck.id: deck for deck in decks}
            assert set(by_id) == {authored_id, svg_id}, "staging deck leaked"
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
            assert by_id[authored_id].theme == LIST_THEME
            assert by_id[svg_id].theme == {"mode": "svg"}
            # Both large JSON columns are reduced inside SQL, not after fetch.
            assert any("json_remove(presentations.theme" in sql for sql in statements)
            assert any("json_remove(slides.content" in sql for sql in statements)

            svg_slide = by_id[svg_id].slides[0]
            assert svg_slide.content == {"title": "SVG cover"}
            assert svg_slide.svg_content == "<svg><text>SVG cover</text></svg>"
            assert svg_slide.properties == {"source": "svg-v1"}

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
            assert stored[0].content["svgReceipt"] == SVG_DETAIL
            async with maker() as session:
                detail = await presentation_crud._get_presentation_with_slides(
                    authored_id, session
                )
                assert detail.theme == STORED_THEME
                assert detail.slides[0].content["svgReceipt"] == SVG_DETAIL
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_server_search_pagination_total_and_owner_scope_match(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "server-search.db")
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        base_time = datetime(2026, 8, 10, tzinfo=timezone.utc)
        deck_specs = [
            ("Quarter Plan", "alpha", owner_id, base_time, True, True),
            (
                "Budget 100%_Plan",
                "beta",
                owner_id,
                base_time + timedelta(minutes=1),
                True,
                True,
            ),
            (
                "Other",
                "quarter plan notes",
                owner_id,
                base_time + timedelta(minutes=2),
                True,
                False,
            ),
            (
                "Budget 100XXOther",
                "gamma",
                owner_id,
                base_time + timedelta(minutes=3),
                True,
                True,
            ),
            (
                "Quarter Plan draft",
                "hidden",
                owner_id,
                base_time + timedelta(minutes=4),
                False,
                True,
            ),
            (
                "Quarter Plan other owner",
                "hidden",
                other_owner_id,
                base_time + timedelta(minutes=5),
                True,
                True,
            ),
        ]
        async with maker() as session, session.begin():
            for title, content, deck_owner, updated_at, published, has_cover in deck_specs:
                presentation = PresentationModel(
                    owner_id=deck_owner,
                    content=content,
                    n_slides=1,
                    language="ko",
                    title=title,
                    # Every deck this fork can create is authored; a row with
                    # no identity describes one of the retired React-template
                    # formats, which no longer resolves a profile.
                    mode="authored",
                    theme={"mode": "authored"},
                    updated_at=updated_at,
                    lifecycle_status=(
                        PRESENTATION_LIFECYCLE_PUBLISHED
                        if published
                        else PRESENTATION_LIFECYCLE_STAGING
                    ),
                )
                session.add(presentation)
                if has_cover:
                    session.add(
                        SlideModel(
                            owner_id=deck_owner,
                            presentation=presentation.id,
                            layout_group="native",
                            layout="title",
                            index=0,
                            content={"title": title},
                        )
                    )

        owner_token = set_current_owner_id(owner_id)
        try:
            async with maker() as session:
                first_page = await presentation_crud.get_all_presentations(
                    session,
                    q="  PLAN  ",
                    page=1,
                    page_size=2,
                    sort="asc",
                )
                second_page = await presentation_crud.get_all_presentations(
                    session,
                    q="plan",
                    page=2,
                    page_size=2,
                    sort="asc",
                )
                literal_wildcards = await presentation_crud.get_all_presentations(
                    session,
                    q="%_",
                    page=1,
                    page_size=10,
                    sort="desc",
                )
        finally:
            reset_current_owner_id(owner_token)
            await engine.dispose()

        assert isinstance(first_page, presentation_crud.PresentationListPage)
        assert first_page.total == 3
        assert [item.title for item in first_page.items] == [
            "Quarter Plan",
            "Budget 100%_Plan",
        ]
        assert second_page.total == 3
        assert [item.title for item in second_page.items] == ["Other"]
        assert second_page.items[0].slides == []
        assert literal_wildcards.total == 1
        assert [item.title for item in literal_wildcards.items] == [
            "Budget 100%_Plan"
        ]

    asyncio.run(scenario())


async def _seed_unsupported_legacy_row(maker) -> tuple[uuid.UUID, uuid.UUID]:
    """One resolvable authored deck plus one pre-removal Template V2 row.

    ``mode="template"`` / ``version="v2-standard"`` is exactly the persisted
    identity a deck generated by the removed Template V2 driver still carries:
    the identity fields remain "known" for compatibility, but no driver in the
    registry claims them, so ``resolve_presentation_driver`` raises
    ``unsupported_presentation_driver``.

    A row this shape can only exist today as a pre-existing row written
    before Template V2 was removed from the driver registry: every new
    ``PresentationModel`` insert is stamped with a resolvable profile at
    write time (``services.database_owner_scope._stamp_new_owned_rows``), so
    the legacy row is inserted with a Core statement to bypass that ORM
    insert hook, the same way an old row already sitting in the database
    would.
    """

    authored_id = uuid.uuid4()
    legacy_id = uuid.uuid4()
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with maker() as session, session.begin():
        session.add_all(
            [
                PresentationModel(
                    id=authored_id,
                    content="authored prompt",
                    n_slides=1,
                    language="ko",
                    title="Authored deck",
                    mode="authored",
                    theme={"mode": "authored"},
                    lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                ),
                SlideModel(
                    presentation=authored_id,
                    layout_group="authored",
                    layout="authored:cover",
                    index=0,
                    content={"title": "Authored cover"},
                    html_content="<section>Authored cover</section>",
                ),
            ]
        )
        await session.execute(
            insert(PresentationModel.__table__).values(
                id=legacy_id,
                content="legacy template v2 prompt",
                n_slides=1,
                language="en",
                title="Legacy Template V2 deck",
                mode="template",
                version="v2-standard",
                theme={"mode": "template"},
                generation_mode="standard",
                lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            insert(SlideModel.__table__).values(
                id=uuid.uuid4(),
                presentation=legacy_id,
                layout_group="template",
                layout="template:cover",
                index=0,
                content={"title": "Legacy cover"},
            )
        )
    return authored_id, legacy_id


def test_presentation_all_marks_unresolvable_legacy_row_instead_of_500(
    tmp_path: Path,
):
    """One legacy row must not 500 the whole dashboard list (regression)."""

    async def scenario():
        engine, maker = await _database(tmp_path / "legacy-row-list.db")
        try:
            authored_id, legacy_id = await _seed_unsupported_legacy_row(maker)

            async with maker() as session:
                decks = await presentation_crud.get_all_presentations(session)

            by_id = {deck.id: deck for deck in decks}
            assert set(by_id) == {authored_id, legacy_id}

            authored_deck = by_id[authored_id]
            assert isinstance(authored_deck, presentation_crud.PresentationWithSlides)
            assert authored_deck.profile.driver_id == "authored"

            legacy_deck = by_id[legacy_id]
            assert isinstance(legacy_deck, UnsupportedPresentationListItem)
            assert legacy_deck.unsupported_template_format is True
            assert legacy_deck.title == "Legacy Template V2 deck"

            # The paginated envelope path hits the same per-row branch.
            async with maker() as session:
                page = await presentation_crud.get_all_presentations(
                    session, page=1, page_size=10
                )
            page_by_id = {item.id: item for item in page.items}
            assert page.total == 2
            assert isinstance(
                page_by_id[legacy_id], UnsupportedPresentationListItem
            )
            assert isinstance(
                page_by_id[authored_id], presentation_crud.PresentationWithSlides
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_get_presentation_unresolvable_driver_returns_422_not_500(tmp_path: Path):
    """Single-deck open must fail closed with a 4xx, not an uncaught 500."""

    async def scenario():
        engine, maker = await _database(tmp_path / "legacy-row-single.db")
        try:
            _authored_id, legacy_id = await _seed_unsupported_legacy_row(maker)

            async with maker() as session:
                with pytest.raises(HTTPException) as excinfo:
                    await presentation_crud.get_presentation(legacy_id, session)

            assert excinfo.value.status_code == 422
            assert excinfo.value.detail == "unsupported_presentation_driver"
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
