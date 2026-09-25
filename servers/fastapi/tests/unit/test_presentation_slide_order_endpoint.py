"""Endpoint-level coverage for the slide-order PATCH route.

D found a real bug in production use: the idempotency key was built as
``"slide-order:" + "-".join(...)[:255]``. Pydantic's ``idempotency_key``
field on ``WorkspaceCommandRequest`` has a 255-character max_length, and for
any deck with roughly 7+ slides the joined string already exceeds that even
after slicing is applied to the *wrong* thing -- the slice happened on the
raw string, not the already-short "slide-order:" prefix, so a >255-char
value could still validate through and, worse, two different orders that
happened to share the identical first 255 characters would collide onto the
*same* key. The uncaught ``pydantic.ValidationError`` inside the route body
surfaced to the caller as a bare 500. Fixed in
api/v1/ppt/endpoints/presentation_slide_order.py by hashing the joined
string (sha256 hex digest, always 64 characters) instead of slicing it.

test_workspace_slide_order_operation.py already covers the operation handler
directly; these tests exercise the real ASGI route end to end (request
validation, idempotency-key construction, and the response the caller
actually sees), with a background drain loop standing in for the worker
process that normally claims and executes the operation out-of-request.
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel

import services.database  # noqa: F401 - registers the full model graph
from api.v1.ppt.endpoints.presentation_slide_order import PRESENTATION_SLIDE_ORDER_ROUTER
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.user import User
from models.sql.workspace import WorkspaceOperation
from services.database import get_async_session
from services.workspace_operation_handler_registry import (
    NonRetryableWorkspaceOperationError,
    WorkspaceOperationHandlerRegistry,
)
from services.workspace_operation_runner import WorkspaceOperationRunner
from services.workspace_slide_order_operation_handler import (
    OPERATION_KIND,
    WorkspaceSlideOrderOperationHandler,
    register_workspace_slide_order_operation_handler,
)
from utils.owner_context import reset_current_owner_id, set_current_owner_id

OWNER_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
BASE_URL = "http://slide-order.test"


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")

    async with engine.begin() as connection:
        await connection.run_sync(SQLModel.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _app(session_factory):
    app = FastAPI()
    app.include_router(PRESENTATION_SLIDE_ORDER_ROUTER, prefix="/api/v1/ppt")

    async def session_dependency():
        token = set_current_owner_id(OWNER_ID)
        try:
            async with session_factory() as session:
                yield session
        finally:
            reset_current_owner_id(token)

    app.dependency_overrides[get_async_session] = session_dependency
    return app


async def _seed_presentation(maker, *, n_slides: int):
    # Owner scoping (services/database.py's do_orm_execute hook) is applied
    # on read based on the current owner contextvar. The endpoint always
    # reads under OWNER_ID (set in the request-scoped session_dependency
    # above), so the presentation must be created under that same identity or
    # the endpoint's own lookup sees it as absent.
    owner_token = set_current_owner_id(OWNER_ID)
    try:
        async with maker() as session, session.begin():
            session.add(User(id=OWNER_ID, username="slide-order-endpoint", hashed_password="test-only"))
            presentation = PresentationModel(
                content="source", n_slides=n_slides, language="en", title="title",
                mode="authored", version="v1-standard", lifecycle_status="published",
                revision=1, theme={"mode": "authored"}, owner_id=OWNER_ID,
            )
            session.add(presentation)
            await session.flush()
            slide_ids: list[uuid.UUID] = []
            for index in range(n_slides):
                slide = SlideModel(presentation=presentation.id, layout_group="custom",
                                   layout="content", index=index, content={"title": f"slide-{index}"})
                session.add(slide)
                await session.flush()
                slide_ids.append(slide.id)
            presentation_id = presentation.id
    finally:
        reset_current_owner_id(owner_token)
    return presentation_id, slide_ids


async def _drain_forever(runner: WorkspaceOperationRunner, stop: asyncio.Event) -> None:
    """Stand-in for the real worker process: claim+run anything queued while
    the endpoint's own request handler is polling for a terminal state."""
    while not stop.is_set():
        dispatched = await runner.dispatch_once()
        if dispatched == 0:
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.05)
            except asyncio.TimeoutError:
                pass


@pytest.fixture
async def harness(tmp_path):
    engine, maker = await _database(tmp_path / "slide-order-endpoint.db")
    registry = WorkspaceOperationHandlerRegistry()
    register_workspace_slide_order_operation_handler(registry=registry, session_factory=maker, replace=True)
    runner = WorkspaceOperationRunner(
        session_factory=maker, registry=registry, max_concurrency=4,
        heartbeat_seconds=60, poll_seconds=0.01,
    )
    app = _app(maker)
    stop = asyncio.Event()
    drain_task = asyncio.create_task(_drain_forever(runner, stop))
    try:
        yield maker, app
    finally:
        stop.set()
        await drain_task
        await engine.dispose()


@pytest.mark.anyio
async def test_endpoint_accepts_a_thirty_slide_deck_without_500(harness):
    maker, app = harness
    presentation_id, slide_ids = await _seed_presentation(maker, n_slides=30)
    new_order = list(reversed(slide_ids))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.patch(
            f"/api/v1/ppt/presentation/{presentation_id}/slides/order",
            json={"slide_ids": [str(s) for s in new_order], "expected_revision": 1},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision"] == 2
    assert body["order"] == [str(s) for s in new_order]

    # The 200 above already proves the route body ran to completion instead
    # of raising the uncaught pydantic.ValidationError D found (the
    # WorkspaceCommandRequest(idempotency_key=...) construction is what used
    # to blow up for any deck with >= ~7 slides). Confirm exactly one
    # operation was durably recorded for it too.
    async with maker() as session:
        operations = (await session.scalars(
            select(WorkspaceOperation).where(WorkspaceOperation.presentation_id == presentation_id)
        )).all()
    assert len(operations) == 1


@pytest.mark.anyio
async def test_two_orders_sharing_a_long_prefix_get_different_idempotency_keys(harness):
    maker, app = harness
    presentation_id, slide_ids = await _seed_presentation(maker, n_slides=30)
    # Identical for the first 10 slide ids, diverging only after that. The
    # old scheme sliced the *joined* string to 255 characters before hashing;
    # for a 30-slide deck that boundary falls well before slide 10, so these
    # two orders collided onto the same idempotency key under the old code
    # and would again if the sha256 fix regresses.
    order_a = list(slide_ids)
    order_b = slide_ids[:10] + list(reversed(slide_ids[10:]))
    assert order_a != order_b
    assert order_a[:10] == order_b[:10]

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        first = await client.patch(
            f"/api/v1/ppt/presentation/{presentation_id}/slides/order",
            json={"slide_ids": [str(s) for s in order_a], "expected_revision": 1},
        )
        assert first.status_code == 200, first.text
        second = await client.patch(
            f"/api/v1/ppt/presentation/{presentation_id}/slides/order",
            json={"slide_ids": [str(s) for s in order_b], "expected_revision": first.json()["revision"]},
        )
        assert second.status_code == 200, second.text

    # The second request must not have been silently deduped onto the first
    # operation: the persisted order must reflect order_b, and the two
    # commands must be two distinct, differently-keyed operations.
    assert second.json()["order"] == [str(s) for s in order_b]
    async with maker() as session:
        operations = (await session.scalars(
            select(WorkspaceOperation)
            .where(WorkspaceOperation.presentation_id == presentation_id)
            .order_by(WorkspaceOperation.created_at)
        )).all()
        slides = (await session.scalars(
            select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
        )).all()
    assert len(operations) == 2
    assert operations[0].idempotency_key_hash != operations[1].idempotency_key_hash
    assert [slide.id for slide in slides] == order_b


@pytest.mark.anyio
async def test_invalid_body_is_rejected_with_422_not_500(harness):
    maker, app = harness
    presentation_id, _slide_ids = await _seed_presentation(maker, n_slides=3)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        empty_slide_ids = await client.patch(
            f"/api/v1/ppt/presentation/{presentation_id}/slides/order",
            json={"slide_ids": [], "expected_revision": 1},
        )
        missing_revision = await client.patch(
            f"/api/v1/ppt/presentation/{presentation_id}/slides/order",
            json={"slide_ids": [str(uuid.uuid4())]},
        )
        unknown_field = await client.patch(
            f"/api/v1/ppt/presentation/{presentation_id}/slides/order",
            json={"slide_ids": [str(uuid.uuid4())], "expected_revision": 1, "unexpected": "nope"},
        )
        not_a_uuid = await client.patch(
            f"/api/v1/ppt/presentation/{presentation_id}/slides/order",
            json={"slide_ids": ["not-a-uuid"], "expected_revision": 1},
        )

    assert empty_slide_ids.status_code == 422, empty_slide_ids.text
    assert missing_revision.status_code == 422, missing_revision.text
    assert unknown_field.status_code == 422, unknown_field.text
    assert not_a_uuid.status_code == 422, not_a_uuid.text


class _FailFirstAttemptHandler:
    """Wraps the real handler so the first operation submitted for a given
    presentation fails terminally (NonRetryableWorkspaceOperationError) and
    every operation after that runs for real. Models a transient failure
    (e.g. the publish conflict path WorkspaceSlideOrderOperationHandler's own
    ``except Exception`` branch reports as ``slide_order_publish_failed``)
    that has cleared by the time the editor resubmits, without depending on
    the runner's own internal attempt/backoff machinery to reach a terminal
    state deterministically in a test."""

    def __init__(self, real: WorkspaceSlideOrderOperationHandler) -> None:
        self._real = real
        self._failed_presentations: set[uuid.UUID] = set()

    async def execute(self, context):
        presentation_id = context.claim.presentation_id
        if presentation_id not in self._failed_presentations:
            self._failed_presentations.add(presentation_id)
            raise NonRetryableWorkspaceOperationError("slide_order_publish_failed")
        return await self._real.execute(context)


@pytest.mark.anyio
async def test_retry_with_same_payload_after_terminal_failure_succeeds(tmp_path):
    """D: a failed slide-order op used to block a retry with the same key at
    the same revision (idempotency_key_payload_conflict / stale error
    replay) -- create_workspace_command's idempotency lookup returns the
    same already-terminal operation for any resubmit that hashes the same,
    and that is intentional, generic workspace-command-layer behavior (the
    bootstrap admission path for presentation.generate has the identical
    rule). So the fix lives here, at the caller: when the base key's
    operation is a fresh terminal failure, escalate to a key derived from
    that failed operation's id and submit again, producing a genuinely new
    operation that actually re-executes the command."""
    engine, maker = await _database(tmp_path / "slide-order-retry.db")
    registry = WorkspaceOperationHandlerRegistry()
    flaky = _FailFirstAttemptHandler(WorkspaceSlideOrderOperationHandler(session_factory=maker))
    registry.register(OPERATION_KIND, flaky, recovery_policy="automatic")
    runner = WorkspaceOperationRunner(
        session_factory=maker, registry=registry, max_concurrency=4,
        heartbeat_seconds=60, poll_seconds=0.01,
    )
    app = _app(maker)
    stop = asyncio.Event()
    drain_task = asyncio.create_task(_drain_forever(runner, stop))
    try:
        presentation_id, slide_ids = await _seed_presentation(maker, n_slides=3)
        new_order = list(reversed(slide_ids))
        body = {"slide_ids": [str(s) for s in new_order], "expected_revision": 1}

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
            first = await client.patch(
                f"/api/v1/ppt/presentation/{presentation_id}/slides/order", json=body,
            )
            # Same idempotency inputs as `first`: same presentation, same
            # expected_revision, same requested order. Before the fix this
            # kept returning `first`'s own stale failure forever instead of
            # ever running the command again.
            second = await client.patch(
                f"/api/v1/ppt/presentation/{presentation_id}/slides/order", json=body,
            )

        assert first.status_code == 422, first.text
        assert second.status_code == 200, second.text
        assert second.json()["order"] == [str(s) for s in new_order]

        async with maker() as session:
            operations = (await session.scalars(
                select(WorkspaceOperation)
                .where(WorkspaceOperation.presentation_id == presentation_id)
                .order_by(WorkspaceOperation.created_at)
            )).all()
            slides = (await session.scalars(
                select(SlideModel).where(SlideModel.presentation == presentation_id).order_by(SlideModel.index)
            )).all()
        assert len(operations) == 2
        assert operations[0].state == "failed"
        assert operations[1].state == "succeeded"
        assert operations[0].idempotency_key_hash != operations[1].idempotency_key_hash
        assert [slide.id for slide in slides] == new_order
    finally:
        stop.set()
        await drain_task
        await engine.dispose()


@pytest.mark.anyio
async def test_concurrent_duplicate_submits_still_dedupe_onto_one_operation(harness):
    """The retry-key escalation only fires once the base key's operation is
    already a terminal failure. Two genuinely concurrent submits of the
    identical order/revision (no prior failure yet) must still collapse onto
    a single operation, exactly as before this change."""
    maker, app = harness
    presentation_id, slide_ids = await _seed_presentation(maker, n_slides=3)
    new_order = list(reversed(slide_ids))
    body = {"slide_ids": [str(s) for s in new_order], "expected_revision": 1}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        first, second = await asyncio.gather(
            client.patch(f"/api/v1/ppt/presentation/{presentation_id}/slides/order", json=body),
            client.patch(f"/api/v1/ppt/presentation/{presentation_id}/slides/order", json=body),
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["operation_id"] == second.json()["operation_id"]
    assert first.json()["order"] == [str(s) for s in new_order]
    assert second.json()["order"] == [str(s) for s in new_order]

    async with maker() as session:
        operations = (await session.scalars(
            select(WorkspaceOperation).where(WorkspaceOperation.presentation_id == presentation_id)
        )).all()
    assert len(operations) == 1
