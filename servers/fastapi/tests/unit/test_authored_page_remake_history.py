"""Remake history must survive a reload and report only what is still usable."""

import asyncio
import uuid
from functools import wraps

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.authored_page_remake_history import (
    list_authored_page_remake_previews,
)
from services.authored_page_remake_service import (
    PREVIEW_KIND,
    authored_page_source_digest,
)

SOURCE_HTML = "<section id='s'><h1>원본</h1></section>"
OWNER = uuid.uuid4()


def async_test(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


async def _engine(path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine


async def _seed(session: AsyncSession):
    presentation = PresentationModel(
        id=uuid.uuid4(), owner_id=OWNER, content="deck", n_slides=1, language="Korean"
    )
    session.add(presentation)
    await session.flush()
    slide = SlideModel(
        presentation=presentation.id,
        index=0,
        layout_group="authored",
        layout="authored:content",
        content={"__authored__": True, "image": "old.png"},
        html_content=SOURCE_HTML,
        properties={"image": "old.png"},
    )
    session.add(slide)
    await session.commit()
    return presentation, slide


def _preview_task(presentation_id, slide, *, digest, status="completed",
                  phase="preview_ready"):
    return AsyncPresentationGenerationTaskModel(
        owner_id=OWNER,
        status=status,
        data={
            "kind": PREVIEW_KIND,
            "phase": phase,
            "presentation_id": str(presentation_id),
            "slide_index": slide.index,
            "source_digest": digest,
            "candidate_html": "<section>후보</section>",
            "candidate_image": "users/o/authored/d/slide_0_abc.png",
            "candidate_issues": [{"category": "contrast", "severity": "medium"}],
            "validation": {"candidate_dom": "warning"},
        },
    )


@async_test
async def test_history_restores_a_pending_preview(tmp_path):
    engine = await _engine(tmp_path / "history.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slide = await _seed(session)
        session.add(
            _preview_task(
                presentation.id, slide, digest=authored_page_source_digest(slide)
            )
        )
        await session.commit()

        history = await list_authored_page_remake_previews(session, presentation.id)

        assert len(history) == 1
        entry = history[0]
        assert entry["slide_index"] == 0
        assert entry["restorable"] is True
        assert entry["candidate_image"]
        assert entry["validation"] == {"candidate_dom": "warning"}
        assert entry["candidate_issues"]
        # The stored HTML must not travel to the timeline.
        assert "candidate_html" not in entry
    await engine.dispose()


@async_test
async def test_history_marks_a_stale_preview_unrestorable(tmp_path):
    engine = await _engine(tmp_path / "stale.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slide = await _seed(session)
        session.add(
            _preview_task(presentation.id, slide, digest="sha256:something-else")
        )
        await session.commit()

        history = await list_authored_page_remake_previews(session, presentation.id)

        assert len(history) == 1
        assert history[0]["restorable"] is False
    await engine.dispose()


@async_test
async def test_history_keeps_published_and_failed_entries_unrestorable(tmp_path):
    engine = await _engine(tmp_path / "terminal.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slide = await _seed(session)
        digest = authored_page_source_digest(slide)
        session.add(
            _preview_task(presentation.id, slide, digest=digest, phase="published")
        )
        failed = AsyncPresentationGenerationTaskModel(
            owner_id=OWNER,
            status="error",
            error={"detail": "authored_page_remake_generation_failed"},
            data={
                "kind": PREVIEW_KIND,
                "phase": "failed",
                "presentation_id": str(presentation.id),
                "slide_index": 0,
            },
        )
        session.add(failed)
        await session.commit()

        history = await list_authored_page_remake_previews(session, presentation.id)

        assert len(history) == 2
        assert all(entry["restorable"] is False for entry in history)
        codes = {entry["error_code"] for entry in history}
        assert "authored_page_remake_generation_failed" in codes
    await engine.dispose()


@async_test
async def test_history_is_scoped_to_the_presentation_and_owner(tmp_path):
    engine = await _engine(tmp_path / "scope.sqlite")
    async with AsyncSession(engine, expire_on_commit=False) as session:
        presentation, slide = await _seed(session)
        digest = authored_page_source_digest(slide)

        other_deck = _preview_task(uuid.uuid4(), slide, digest=digest)
        other_owner = _preview_task(presentation.id, slide, digest=digest)
        other_owner.owner_id = uuid.uuid4()
        unrelated = AsyncPresentationGenerationTaskModel(
            owner_id=OWNER, status="completed", data={"kind": "something_else"}
        )
        session.add_all((other_deck, other_owner, unrelated))
        await session.commit()

        history = await list_authored_page_remake_previews(session, presentation.id)

        assert history == []
    await engine.dispose()
