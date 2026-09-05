"""Fail-closed owner context: requests without an identity must not run unscoped."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import pytest
from sqlalchemy import func, select
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


def test_require_owner_context_raises_inside_request_without_identity() -> None:
    with _request_scope():
        with pytest.raises(OwnerContextRequiredError, match="owner_context_required"):
            require_owner_context()


def test_require_owner_context_accepts_owner_or_tenant_identity() -> None:
    with _request_scope():
        with _owner(uuid.uuid4()):
            require_owner_context()
        token = set_current_tenant_id("tenant-a")
        try:
            require_owner_context()
        finally:
            reset_current_tenant_id(token)


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
