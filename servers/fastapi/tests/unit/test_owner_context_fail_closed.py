"""Fail-closed owner context: requests without an identity must not run unscoped."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from models.sql.user import User
from services import database as _database_events  # noqa: F401
from utils import owner_context
from utils.owner_context import (
    OwnerContextRequiredError,
    require_owner_context,
    reset_current_owner_id,
    reset_current_tenant_id,
    reset_request_scope_active,
    set_current_owner_id,
    set_current_tenant_id,
    set_request_scope_active,
)


@pytest.fixture(autouse=True)
def _auth_enabled(monkeypatch):
    monkeypatch.delenv("DISABLE_AUTH", raising=False)


@contextlib.contextmanager
def _request_scope(active: bool = True):
    token = set_request_scope_active(active)
    try:
        yield
    finally:
        reset_request_scope_active(token)


@contextlib.contextmanager
def _owner(owner_id):
    token = set_current_owner_id(owner_id)
    try:
        yield
    finally:
        reset_current_owner_id(token)


@contextlib.contextmanager
def _tenant(tenant_id):
    token = set_current_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant_id(token)


@contextlib.contextmanager
def _middleware_request_without_owner():
    """The exact shape api.middlewares produces for an ownerless request.

    ``PrincipalEnvelope.tenant_id`` is a non-optional string that falls back to
    ``"default"``, so the tenant is always set on an authenticated request.
    A guard that accepted the tenant as an identity could therefore never fire.
    """
    with _request_scope(), _tenant("default"), _owner(None):
        yield


def test_require_owner_context_raises_inside_request_without_identity() -> None:
    with _request_scope():
        with pytest.raises(OwnerContextRequiredError, match="owner_context_required"):
            require_owner_context()


def test_require_owner_context_raises_when_only_the_tenant_is_set() -> None:
    with _middleware_request_without_owner():
        with pytest.raises(OwnerContextRequiredError, match="owner_context_required"):
            require_owner_context()


def test_require_owner_context_accepts_an_owner_identity() -> None:
    with _request_scope(), _tenant("default"):
        with _owner(uuid.uuid4()):
            require_owner_context()


def test_require_owner_context_is_silent_for_worker_context() -> None:
    # No middleware ran: workers, maintenance loops, startup bootstrap.
    assert owner_context.get_request_scope_active() is False
    require_owner_context()
    with _owner(None):
        require_owner_context()


def test_require_owner_context_is_silent_when_auth_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("DISABLE_AUTH", "true")
    with _request_scope():
        assert owner_context.owner_context_enforced() is False
        require_owner_context()


def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _create_tables(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
        await connection.run_sync(PresentationModel.__table__.create)
        await connection.run_sync(SlideModel.__table__.create)


def test_owned_select_inside_request_without_identity_raises() -> None:
    async def run():
        engine, session_maker = _session_factory()
        await _create_tables(engine)
        try:
            with _request_scope():
                async with session_maker() as session:
                    with pytest.raises(OwnerContextRequiredError):
                        await session.execute(select(PresentationModel))
                async with session_maker() as session:
                    with pytest.raises(OwnerContextRequiredError):
                        await session.get(SlideModel, uuid.uuid4())
                async with session_maker() as session:
                    # Non-owned tables and explicit opt-outs keep working.
                    await session.execute(select(func.count()).select_from(User))
                    await session.execute(
                        select(PresentationModel).execution_options(
                            skip_owner_scope=True
                        )
                    )
                with _owner(uuid.uuid4()):
                    async with session_maker() as session:
                        await session.execute(select(PresentationModel))
            # Worker context: same statement, no request flag, no error.
            async with session_maker() as session:
                await session.execute(select(PresentationModel))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_owned_select_with_a_tenant_but_no_owner_reads_nobody() -> None:
    """The reproduction: two owners' decks were visible to an ownerless request."""

    async def run():
        engine, session_maker = _session_factory()
        await _create_tables(engine)
        first, second = uuid.uuid4(), uuid.uuid4()
        try:
            async with session_maker() as session:
                session.add_all(
                    [
                        PresentationModel(
                            id=uuid.uuid4(),
                            owner_id=owner,
                            mode="svg",
                            content=content,
                            n_slides=1,
                            language="en",
                        )
                        for owner, content in ((first, "first"), (second, "second"))
                    ]
                )
                await session.commit()
            with _middleware_request_without_owner():
                async with session_maker() as session:
                    with pytest.raises(OwnerContextRequiredError):
                        await session.execute(select(PresentationModel))
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_owned_update_and_delete_inside_request_without_identity_raise() -> None:
    """SQLite has no row-level security behind the guard, so writes count."""

    async def run():
        engine, session_maker = _session_factory()
        await _create_tables(engine)
        owner_id = uuid.uuid4()

        async def _seed() -> None:
            async with session_maker() as session:
                session.add(
                    PresentationModel(
                        id=uuid.uuid4(),
                        owner_id=owner_id,
                        mode="svg",
                        content="owned",
                        n_slides=1,
                        language="en",
                    )
                )
                await session.commit()

        try:
            await _seed()
            with _middleware_request_without_owner():
                async with session_maker() as session:
                    with pytest.raises(OwnerContextRequiredError):
                        await session.execute(
                            update(PresentationModel).values(content="hijacked")
                        )
                async with session_maker() as session:
                    with pytest.raises(OwnerContextRequiredError):
                        await session.execute(delete(PresentationModel))
                # session.dirty
                async with session_maker() as session:
                    with _owner(owner_id):
                        row = (await session.scalars(select(PresentationModel))).one()
                    row.content = "hijacked"
                    with pytest.raises(OwnerContextRequiredError):
                        await session.flush()
                # session.deleted
                async with session_maker() as session:
                    with _owner(owner_id):
                        row = (await session.scalars(select(PresentationModel))).one()
                        await session.delete(row)
                    with pytest.raises(OwnerContextRequiredError):
                        await session.flush()
            async with session_maker() as session:
                survivors = (await session.scalars(select(PresentationModel))).all()
            assert [row.content for row in survivors] == ["owned"]
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_owned_writes_stay_open_for_worker_context() -> None:
    """The same statements outside a request keep their cross-owner authority."""

    async def run():
        engine, session_maker = _session_factory()
        await _create_tables(engine)
        try:
            async with session_maker() as session:
                await session.execute(update(PresentationModel).values(content="swept"))
                await session.execute(delete(PresentationModel))
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_owned_insert_inside_request_without_identity_raises() -> None:
    async def run():
        engine, session_maker = _session_factory()
        await _create_tables(engine)
        try:
            with _request_scope():
                async with session_maker() as session:
                    session.add(
                        PresentationModel(
                            id=uuid.uuid4(),
                            content="orphan",
                            n_slides=1,
                            language="en",
                        )
                    )
                    with pytest.raises(OwnerContextRequiredError):
                        await session.flush()
        finally:
            await engine.dispose()

    asyncio.run(run())
