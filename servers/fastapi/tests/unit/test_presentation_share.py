"""Read-only public sharing: token lifecycle + that the public view is reachable
only by a live token and never leaks the token or other decks."""
import asyncio
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from api.v1.ppt.endpoints import presentation_share as share
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel  # noqa: F401 (metadata)


def _run(coro):
    return asyncio.run(coro)


async def _make_engine(db_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


async def _seed_deck(session) -> uuid.UUID:
    pid = uuid.uuid4()
    session.add(
        PresentationModel(id=pid, content="deck", n_slides=1, language="ko")
    )
    session.add(
        SlideModel(
            presentation=pid, layout_group="korean-biz",
            layout="korean-biz:bullets", index=0, content={"title": "Hi"},
        )
    )
    await session.commit()
    return pid


def test_enable_then_public_get_returns_deck_without_leaking_token(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "t.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(session)

            info = await share.enable_share(id=pid, sql_session=session)
            assert info.shared is True
            assert info.share_token and len(info.share_token) >= share._MIN_TOKEN_LEN

            status = await share.get_share_status(id=pid, sql_session=session)
            assert status.shared is True and status.share_token == info.share_token

            deck = await share.get_shared_presentation(
                share_token=info.share_token, sql_session=session
            )
            assert deck.id == pid
            assert [s.content["title"] for s in deck.slides] == ["Hi"]
            # The token must never come back on the public response model.
            assert "share_token" not in deck.model_dump()
        await engine.dispose()

    _run(runner())


def test_public_get_rejects_unknown_and_short_tokens(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "t.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _seed_deck(session)
            for bad in ["", "short", "x" * 40]:  # blank, too short, valid-length miss
                with pytest.raises(HTTPException) as exc:
                    await share.get_shared_presentation(
                        share_token=bad, sql_session=session
                    )
                assert exc.value.status_code == 404
        await engine.dispose()

    _run(runner())


def test_disable_share_makes_public_view_404(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "t.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(session)
            info = await share.enable_share(id=pid, sql_session=session)
            token = info.share_token

            off = await share.disable_share(id=pid, sql_session=session)
            assert off.shared is False and off.share_token is None

            with pytest.raises(HTTPException) as exc:
                await share.get_shared_presentation(
                    share_token=token, sql_session=session
                )
            assert exc.value.status_code == 404
        await engine.dispose()

    _run(runner())


def test_regenerate_rotates_and_voids_old_link(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "t.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(session)
            first = (await share.enable_share(id=pid, sql_session=session)).share_token
            second = (
                await share.enable_share(
                    id=pid, regenerate=True, sql_session=session
                )
            ).share_token
            assert first != second

            # Old link is dead; new link works.
            with pytest.raises(HTTPException):
                await share.get_shared_presentation(
                    share_token=first, sql_session=session
                )
            deck = await share.get_shared_presentation(
                share_token=second, sql_session=session
            )
            assert deck.id == pid
        await engine.dispose()

    _run(runner())
