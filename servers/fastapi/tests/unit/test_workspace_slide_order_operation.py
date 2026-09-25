"""End-to-end coverage for the slide-order command: the editor's drag reorder
had no backing endpoint at all (PATCH /presentation/update, no FastAPI route).
Exercises the real create_workspace_command -> WorkspaceOperationRunner ->
WorkspaceSlideOrderOperationHandler -> publish_presentation_revision path
against a real (SQLite) database, the same path every other mutation uses.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import services.database  # noqa: F401 - registers the full model graph
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.user import User
from models.workspace import WorkspaceCommandRequest
from services.workspace_operation_handler_registry import WorkspaceOperationHandlerRegistry
from services.workspace_operation_lease import TERMINAL_OPERATION_STATES
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_service import WorkspaceServiceError, create_workspace_command
from services.workspace_slide_order_operation_handler import (
    OPERATION_KIND,
    register_workspace_slide_order_operation_handler,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _drain_once(runner: WorkspaceOperationRunner) -> int:
    dispatched = await runner.dispatch_once()
    tasks = tuple(runner._active.values())
    if tasks:
        await asyncio.gather(*tasks)
        await asyncio.sleep(0)
    return dispatched


async def _seed_presentation(maker, *, n_slides: int, mode: str = "authored", theme: dict | None = None):
    async with maker() as session, session.begin():
        presentation = PresentationModel(
            content="source", n_slides=n_slides, language="en", title="title",
            mode=mode, version="v1-standard", lifecycle_status="published",
            revision=1, theme=theme or {"mode": mode},
        )
        session.add(presentation)
        await session.flush()
        slide_ids = []
        for index in range(n_slides):
            slide = SlideModel(presentation=presentation.id, layout_group="custom",
                               layout="content", index=index, content={"title": f"slide-{index}"})
            session.add(slide)
            await session.flush()
            slide_ids.append(slide.id)
        presentation_id = presentation.id
    return presentation_id, slide_ids


async def _submit_and_run(maker, registry, runner, *, presentation_id, slide_ids, expected_revision, key):
    command = WorkspaceCommandRequest(
        command="Reorder slides", command_type=OPERATION_KIND,
        idempotency_key=key, expected_revision=expected_revision,
        context={"slide_ids": [str(x) for x in slide_ids]},
    )
    async with maker() as session:
        operation, _replayed = await create_workspace_command(
            session, presentation_id=presentation_id, request=command)
        operation_id = operation.id
    # The first dispatch always claims this operation immediately -- nothing
    # else contends for this isolated, single-row database. One dispatch
    # does not, however, guarantee a *terminal* state: WorkspaceOperationRunner
    # requeues (state -> "queued", with a future next_attempt_at) on any
    # unexpected exception during execution, exactly as it would in
    # production (see the generic `except Exception` branch in
    # WorkspaceOperationRunner._execute, services/workspace_operation_runner.py).
    # Drain until the operation reaches a terminal state or a bounded number
    # of attempts is exhausted, mirroring how the real HTTP endpoint
    # (presentation_slide_order.py) polls rather than assuming one dispatch
    # always finishes.
    assert await _drain_once(runner) == 1
    for _ in range(5):
        async with maker() as session:
            row = await session.get(type(operation), operation_id, populate_existing=True)
        if row.state in TERMINAL_OPERATION_STATES:
            return row
        wait_seconds = 0.05
        next_attempt_at = row.next_attempt_at
        if next_attempt_at is not None:
            if next_attempt_at.tzinfo is None:
                next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
            wait_seconds = max(wait_seconds, (next_attempt_at - datetime.now(timezone.utc)).total_seconds() + 0.05)
        await asyncio.sleep(wait_seconds)
        if row.state == "queued":
            await _drain_once(runner)
    async with maker() as session:
        return await session.get(type(operation), operation_id, populate_existing=True)


@pytest.fixture
async def harness(tmp_path):
    engine, maker = await _database(tmp_path / "slide-order.db")
    async with maker() as session, session.begin():
        session.add(User(id=OWNER_ID, username="reorder-owner", hashed_password="test-only"))
    registry = WorkspaceOperationHandlerRegistry()
    register_workspace_slide_order_operation_handler(registry=registry, session_factory=maker, replace=True)
    runner = WorkspaceOperationRunner(
        session_factory=maker, registry=registry, max_concurrency=1,
        heartbeat_seconds=60, poll_seconds=0.01,
    )
    owner_token = set_current_owner_id(OWNER_ID)
    try:
        yield maker, registry, runner
    finally:
        reset_current_owner_id(owner_token)
        await engine.dispose()


@pytest.mark.anyio
async def test_reorder_persists_new_indices_and_bumps_revision(harness):
    maker, registry, runner = harness
    presentation_id, slide_ids = await _seed_presentation(maker, n_slides=3)
    new_order = [slide_ids[2], slide_ids[0], slide_ids[1]]

    operation = await _submit_and_run(
        maker, registry, runner, presentation_id=presentation_id, slide_ids=new_order,
        expected_revision=1, key="reorder-1")

    assert operation.state == "succeeded"
    assert operation.result_revision == 2
    async with maker() as session:
        presentation = await session.get(PresentationModel, presentation_id)
        assert presentation.revision == 2
        slides = (await session.scalars(
            select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
        )).all()
        assert [slide.id for slide in slides] == new_order


@pytest.mark.anyio
async def test_reorder_same_order_is_idempotent_and_does_not_bump_revision(harness):
    maker, registry, runner = harness
    presentation_id, slide_ids = await _seed_presentation(maker, n_slides=2)

    operation = await _submit_and_run(
        maker, registry, runner, presentation_id=presentation_id, slide_ids=slide_ids,
        expected_revision=1, key="reorder-noop")

    assert operation.state == "succeeded"
    assert operation.result_revision == 1
    async with maker() as session:
        presentation = await session.get(PresentationModel, presentation_id)
        assert presentation.revision == 1


@pytest.mark.anyio
async def test_reorder_rejects_id_set_mismatch(harness):
    maker, registry, runner = harness
    presentation_id, slide_ids = await _seed_presentation(maker, n_slides=2)
    bogus_order = [slide_ids[0], uuid.uuid4()]

    operation = await _submit_and_run(
        maker, registry, runner, presentation_id=presentation_id, slide_ids=bogus_order,
        expected_revision=1, key="reorder-mismatch")

    assert operation.state == "failed"
    assert operation.error_code == "slide_order_id_set_mismatch"
    async with maker() as session:
        presentation = await session.get(PresentationModel, presentation_id)
        assert presentation.revision == 1


@pytest.mark.anyio
async def test_reorder_rejects_stale_expected_revision(harness):
    """create_workspace_command's own admission fence (services/workspace_service.py)
    catches a stale expected_revision before an operation is even created --
    the same 409 envelope (WorkspaceServiceError -> WorkspaceAPIException) the
    HTTP endpoint surfaces to the caller."""
    maker, registry, runner = harness
    presentation_id, slide_ids = await _seed_presentation(maker, n_slides=2)
    command = WorkspaceCommandRequest(
        command="Reorder slides", command_type=OPERATION_KIND,
        idempotency_key="reorder-stale", expected_revision=0,
        context={"slide_ids": [str(x) for x in reversed(slide_ids)]},
    )
    async with maker() as session:
        with pytest.raises(WorkspaceServiceError) as excinfo:
            await create_workspace_command(session, presentation_id=presentation_id, request=command)
    assert excinfo.value.code == "revision_conflict"
    assert excinfo.value.status_code == 409


@pytest.mark.anyio
async def test_reorder_of_an_svg_deck_renumbers_page_footers(harness):
    """Confirms the endpoint's integration point (publish_presentation_revision)
    still triggers refresh_svg_page_numbers for svg-mode decks -- the same path
    B's raster-redesign renumbering (services/svg_raster_renumber.py) hangs off
    of. A full raster fixture is covered separately in
    tests/unit/test_svg_raster_redesign_publication.py; this checks the
    generic (non-raster) svg page-number stamp updates through this endpoint's
    own call path."""
    maker, registry, runner = harness
    style_snapshot = {"tokens": {"body_font": "Noto Sans KR", "primary": "#2563EB"}}
    presentation_id, slide_ids = await _seed_presentation(
        maker, n_slides=2, mode="svg",
        theme={"mode": "svg", "style": "test", "svg": {"styleSnapshot": style_snapshot}},
    )
    canonical = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
        '<text x="10" y="20" font-family="Noto Sans KR" font-size="16" fill="#000000">body</text></svg>'
    )
    async with maker() as session, session.begin():
        slides = (await session.scalars(
            select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
        )).all()
        for slide in slides:
            slide.svg_content = canonical
            slide.source_format = "svg"
            session.add(slide)

    operation = await _submit_and_run(
        maker, registry, runner, presentation_id=presentation_id, slide_ids=list(reversed(slide_ids)),
        expected_revision=1, key="reorder-svg")

    assert operation.state == "succeeded"
    async with maker() as session:
        slides = (await session.scalars(
            select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
        )).all()
        # finalize_svg stamps a 1-based, zero-padded page number per slide.index.
        assert '>01<' in slides[0].svg_content
        assert '>02<' in slides[1].svg_content
