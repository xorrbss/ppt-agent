"""Durable slide version history: snapshot throttling/cap + restore semantics.

Uses a real async SQLite session (the service issues ordered SELECT / COUNT /
DELETE queries the FakeAsyncSession stub can't model)."""
import asyncio
import uuid

from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from models.sql.presentation import PresentationModel  # noqa: F401 (metadata)
from models.sql.presentation_version import PresentationVersionModel
from models.sql.slide import SlideModel
from services import presentation_version_service as vs


def _run(coro):
    return asyncio.run(coro)


async def _make_session(db_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    return engine


def _slide_dump(index: int, title: str) -> dict:
    return SlideModel(
        presentation=uuid.uuid4(),
        layout_group="korean-biz",
        layout="korean-biz:bullets",
        index=index,
        content={"title": title},
    ).model_dump(mode="json")


def test_snapshot_creates_then_throttles(tmp_path):
    async def runner():
        engine = await _make_session(tmp_path / "t.db")
        pid = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            first = await vs.snapshot_slides(session, pid, [_slide_dump(0, "A")])
            await session.commit()
            assert first is not None
            # A second save moments later is throttled — no new restore point.
            second = await vs.snapshot_slides(session, pid, [_slide_dump(0, "A2")])
            await session.commit()
            assert second is None
            assert len(await vs.list_versions(session, pid)) == 1
        await engine.dispose()

    _run(runner())


def test_snapshot_ignores_empty_slides(tmp_path):
    async def runner():
        engine = await _make_session(tmp_path / "t.db")
        pid = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            assert await vs.snapshot_slides(session, pid, []) is None
            assert len(await vs.list_versions(session, pid)) == 0
        await engine.dispose()

    _run(runner())


def test_snapshot_caps_retention(tmp_path):
    async def runner():
        engine = await _make_session(tmp_path / "t.db")
        pid = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            # force=True bypasses the throttle; retention still caps the count.
            for i in range(vs.MAX_VERSIONS_PER_PRESENTATION + 5):
                await vs.snapshot_slides(
                    session, pid, [_slide_dump(0, f"v{i}")], force=True
                )
                await session.commit()
            assert (
                len(await vs.list_versions(session, pid))
                == vs.MAX_VERSIONS_PER_PRESENTATION
            )
        await engine.dispose()

    _run(runner())


def test_restore_replaces_slides_and_checkpoints_current(tmp_path):
    async def runner():
        engine = await _make_session(tmp_path / "t.db")
        pid = uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            # A version holding a 3-slide snapshot.
            version = await vs.snapshot_slides(
                session,
                pid,
                [_slide_dump(0, "old0"), _slide_dump(1, "old1"), _slide_dump(2, "old2")],
                force=True,
            )
            await session.commit()

            # Current live deck = 2 different slides.
            session.add_all(
                [
                    SlideModel(
                        presentation=pid, layout_group="g", layout="g:l",
                        index=0, content={"title": "cur0"},
                    ),
                    SlideModel(
                        presentation=pid, layout_group="g", layout="g:l",
                        index=1, content={"title": "cur1"},
                    ),
                ]
            )
            await session.commit()

            restored = await vs.restore_version(session, pid, version.id)
            await session.commit()
            assert restored is not None
            assert [s.content["title"] for s in restored] == ["old0", "old1", "old2"]

            # DB now holds exactly the restored slides.
            live = list(
                await session.exec(
                    select(SlideModel)
                    .where(SlideModel.presentation == pid)
                    .order_by(SlideModel.index)
                )
            )
            assert [s.content["title"] for s in live] == ["old0", "old1", "old2"]

            # The pre-restore state was checkpointed into history.
            labels = [v.label for v in await vs.list_versions(session, pid)]
            assert "복원 전 자동 저장" in labels
        await engine.dispose()

    _run(runner())


def test_restore_rejects_version_from_other_presentation(tmp_path):
    async def runner():
        engine = await _make_session(tmp_path / "t.db")
        pid_a, pid_b = uuid.uuid4(), uuid.uuid4()
        async with AsyncSession(engine, expire_on_commit=False) as session:
            version_a = await vs.snapshot_slides(
                session, pid_a, [_slide_dump(0, "A")], force=True
            )
            await session.commit()
            # Restoring A's version under B must be rejected.
            assert await vs.restore_version(session, pid_b, version_a.id) is None
        await engine.dispose()

    _run(runner())
