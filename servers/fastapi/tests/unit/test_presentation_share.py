"""Read-only public sharing: token lifecycle + that the public view is reachable
only by a live token and never leaks the token or other decks."""
import asyncio
from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from api.v1.ppt.endpoints import presentation_share as share
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel  # noqa: F401 (metadata)
from utils.owner_context import reset_current_owner_id, set_current_owner_id


def _run(coro):
    return asyncio.run(coro)


async def _make_engine(db_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}", connect_args={"timeout": 30}
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.fetchone()
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


async def _seed_deck(
    session, *, content=None, html_content=None, owner_id=None
) -> uuid.UUID:
    pid = uuid.uuid4()
    session.add(
        PresentationModel(
            id=pid,
            owner_id=owner_id,
            content="deck",
            n_slides=1,
            language="ko",
        )
    )
    session.add(
        SlideModel(
            presentation=pid, layout_group="korean-biz",
            layout="korean-biz:bullets", index=0,
            content=content or {"title": "Hi"},
            html_content=html_content,
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
            assert status.expires_at is None
            assert status.view_count == 0

            deck = await share.get_shared_presentation(
                share_token=info.share_token, sql_session=session
            )
            assert deck.id == pid
            assert [s.content["title"] for s in deck.slides] == ["Hi"]
            # The token must never come back on the public response model.
            assert "share_token" not in deck.model_dump()
            counted = await share.get_share_status(id=pid, sql_session=session)
            assert counted.view_count == 1
        await engine.dispose()

    _run(runner())


def test_share_expiry_is_utc_safe_and_indistinguishable_from_unknown(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "expiry.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(session)
            offset_expiry = datetime.now(timezone(timedelta(hours=9))) + timedelta(
                hours=2
            )
            info = await share.enable_share(
                id=pid,
                settings=share.ShareSettings(expires_at=offset_expiry),
                sql_session=session,
            )
            assert info.expires_at is not None
            assert info.expires_at.tzinfo == timezone.utc
            assert info.expires_at == offset_expiry.astimezone(timezone.utc)

            presentation = await session.get(PresentationModel, pid)
            presentation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()

            details = []
            for token in (info.share_token, "x" * 40):
                with pytest.raises(HTTPException) as exc:
                    await share.get_shared_presentation(
                        share_token=token, sql_session=session
                    )
                assert exc.value.status_code == 404
                details.append(exc.value.detail)
            assert details == [
                "Shared presentation not found",
                "Shared presentation not found",
            ]

            with pytest.raises(HTTPException) as asset_exc:
                await share.get_shared_presentation_asset(
                    share_token=info.share_token,
                    asset_id="0" * 64,
                    sql_session=session,
                )
            assert asset_exc.value.status_code == 404
            assert asset_exc.value.detail == "Shared asset not found"

        with pytest.raises(ValidationError, match="must include a timezone"):
            share.ShareSettings(expires_at=datetime(2026, 8, 10, 12, 0, 0))
        await engine.dispose()

    _run(runner())


def test_public_views_increment_atomically_without_touching_deck_revision(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "concurrent-views.db")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            pid = await _seed_deck(session)
            token = (await share.enable_share(id=pid, sql_session=session)).share_token

        async with maker() as session:
            baseline = await session.get(PresentationModel, pid)
            baseline_updated_at = baseline.updated_at
            baseline_revision = baseline.revision

        async def view_once():
            async with maker() as session:
                deck = await share.get_shared_presentation(
                    share_token=token,
                    sql_session=session,
                )
                assert deck.id == pid

        await asyncio.gather(*(view_once() for _ in range(16)))

        async with maker() as session:
            counted = await session.get(PresentationModel, pid)
            assert counted.view_count == 16
            assert counted.updated_at == baseline_updated_at
            assert counted.revision == baseline_revision
        await engine.dispose()

    _run(runner())


def test_share_management_is_owner_scoped(tmp_path):
    async def runner():
        engine = await _make_engine(tmp_path / "owner-scope.db")
        owner_id = uuid.uuid4()
        other_owner_id = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(session, owner_id=owner_id)

        owner_token = set_current_owner_id(other_owner_id)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                for operation in (
                    lambda: share.get_share_status(id=pid, sql_session=session),
                    lambda: share.enable_share(id=pid, sql_session=session),
                    lambda: share.disable_share(id=pid, sql_session=session),
                ):
                    with pytest.raises(HTTPException) as exc:
                        await operation()
                    assert exc.value.status_code == 404
        finally:
            reset_current_owner_id(owner_token)
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


def test_public_projection_rewrites_only_referenced_images_to_token_route(
    tmp_path, monkeypatch
):
    async def runner():
        app_data = tmp_path / "app_data"
        owner_id = uuid.uuid4()
        other_id = uuid.uuid4()
        referenced = app_data / "images" / "users" / str(owner_id) / "slide.png"
        unreferenced = app_data / "images" / "users" / str(other_id) / "secret.png"
        referenced.parent.mkdir(parents=True)
        unreferenced.parent.mkdir(parents=True)
        referenced.write_bytes(b"safe image")
        unreferenced.write_bytes(b"private image")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

        engine = await _make_engine(tmp_path / "asset.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(
                session,
                content={
                    "__authored__": True,
                    "image": f"users/{owner_id}/slide.png",
                    "nested": {
                        "src": f"/app_data/images/users/{owner_id}/slide.png",
                        "otherImage": {
                            "src": f"/app_data/images/users/{other_id}/secret.png"
                        },
                        "requestPayload": {
                            "src": f"/app_data/images/users/{other_id}/secret.png"
                        },
                    }
                },
                html_content=(
                    '<img src="http://localhost:8000/app_data/images/'
                    f'users/{owner_id}/slide.png">'
                ),
                owner_id=owner_id,
            )
            token = (await share.enable_share(id=pid, sql_session=session)).share_token
            deck = await share.get_shared_presentation(
                share_token=token, sql_session=session
            )
            serialized = deck.model_dump(mode="json")
            public_url = serialized["slides"][0]["content"]["nested"]["src"]
            assert public_url.startswith(
                f"/api/v1/ppt/presentation/public/{token}/assets/"
            )
            assert str(owner_id) not in public_url
            assert serialized["slides"][0]["content"]["image"] == public_url
            assert public_url in serialized["slides"][0]["html_content"]
            assert "requestPayload" not in str(serialized)
            assert str(other_id) not in str(serialized)
            other_public_url = serialized["slides"][0]["content"]["nested"][
                "otherImage"
            ]["src"]

            response = await share.get_shared_presentation_asset(
                share_token=token,
                asset_id=public_url.rsplit("/", 1)[-1],
                sql_session=session,
            )
            assert response.path == referenced.resolve()
            assert response.headers["cache-control"] == "no-store"
            stored = await session.get(PresentationModel, pid)
            assert stored.view_count == 1, "asset reads must not count as deck views"

            for denied_asset_id in (
                other_public_url.rsplit("/", 1)[-1],
                "../userConfig.json",
                "%252e%252e/userConfig.json",
            ):
                with pytest.raises(HTTPException) as exc:
                    await share.get_shared_presentation_asset(
                        share_token=token,
                        asset_id=denied_asset_id,
                        sql_session=session,
                    )
                assert exc.value.status_code == 404
        await engine.dispose()

    _run(runner())


def test_public_endpoint_removes_authored_html_prompts_before_rewriting_images(
    tmp_path, monkeypatch
):
    async def runner():
        app_data = tmp_path / "app_data"
        image = app_data / "images" / "authored" / "deck" / "slide.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"safe image")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

        engine = await _make_engine(tmp_path / "html-privacy.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(
                session,
                html_content=(
                    "<!-- private illustration prompt: board acquisition plan -->"
                    '<img data-illust-prompt="private image prompt" '
                    'data-qid="editor-only" '
                    'src="/app_data/images/authored/deck/slide.png">'
                ),
            )
            token = (await share.enable_share(id=pid, sql_session=session)).share_token
            deck = await share.get_shared_presentation(
                share_token=token, sql_session=session
            )
            html = deck.model_dump(mode="json")["slides"][0]["html_content"]

            assert "board acquisition plan" not in html
            assert "private image prompt" not in html
            assert "data-illust-prompt" not in html
            assert "data-qid" not in html
            assert f'/api/v1/ppt/presentation/public/{token}/assets/' in html
        await engine.dispose()

    _run(runner())


def test_disabling_share_revokes_public_asset_route(tmp_path, monkeypatch):
    async def runner():
        app_data = tmp_path / "app_data"
        image = app_data / "images" / "authored" / "deck" / "slide.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"image")
        monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

        engine = await _make_engine(tmp_path / "revoked-asset.db")
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pid = await _seed_deck(
                session,
                content={"src": "/app_data/images/authored/deck/slide.png"},
            )
            token = (await share.enable_share(id=pid, sql_session=session)).share_token
            await share.disable_share(id=pid, sql_session=session)

            with pytest.raises(HTTPException) as exc:
                await share.get_shared_presentation_asset(
                    share_token=token,
                    asset_id="0" * 64,
                    sql_session=session,
                )
            assert exc.value.status_code == 404
        await engine.dispose()

    _run(runner())
