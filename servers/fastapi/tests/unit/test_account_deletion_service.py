from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.v1.admin.router as admin_router
from models.sql.key_value import KeyValueSqlModel
from models.sql.user import User
from models.sql.workspace import WorkspaceCleanupJournal
from services.account_deletion_service import (
    ACCOUNT_DELETION_ACTIVE,
    ACCOUNT_DELETION_DELETING,
    AccountDeletionFenceConflict,
    ProtectedAccountError,
    mark_account_deleting,
    require_active_account_generation,
)
from services.workspace_cleanup_journal_service import (
    account_cleanup_tombstone_exists,
    account_owned_tree_cleanup_target,
    enqueue_workspace_cleanup_targets,
)


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_safety(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")

    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
        await connection.run_sync(KeyValueSqlModel.__table__.create)
        await connection.run_sync(WorkspaceCleanupJournal.__table__.create)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(
    username: str,
    *,
    user_id: uuid.UUID | None = None,
    superuser: bool = False,
    auth_version: int = 1,
) -> User:
    return User(
        id=user_id or uuid.uuid4(),
        username=username,
        hashed_password="test-only",
        is_active=True,
        is_superuser=superuser,
        is_verified=True,
        admin_slot="primary" if superuser else None,
        auth_version=auth_version,
    )


async def _async_zero(owner_id: uuid.UUID, expected_owner_id: uuid.UUID) -> int:
    assert owner_id == expected_owner_id
    return 0


def test_duplicate_workers_share_one_durable_deletion_generation(tmp_path: Path):
    asyncio.run(_duplicate_workers_share_one_durable_deletion_generation(tmp_path))


async def _duplicate_workers_share_one_durable_deletion_generation(tmp_path: Path):
    engine, maker = await _database(tmp_path / "account-deletion-race.db")
    target = _user("race-target", auth_version=7)
    try:
        async with maker() as session:
            session.add(target)
            await session.commit()
            assert (
                await require_active_account_generation(
                    session, user_id=target.id
                )
                == 0
            )

        async def mark_from_worker():
            async with maker() as session:
                return await mark_account_deleting(session, user_id=target.id)

        first, second = await asyncio.gather(mark_from_worker(), mark_from_worker())
        assert first.user_id == second.user_id == target.id
        assert first.generation == second.generation == 1
        assert first.requested_at == second.requested_at

        async with maker() as session:
            persisted = await session.get(User, target.id)
            assert persisted is not None
            assert persisted.deletion_state == ACCOUNT_DELETION_DELETING
            assert persisted.deletion_generation == 1
            assert persisted.deletion_requested_at == first.requested_at
            assert persisted.auth_version == 8
            assert persisted.is_active is False
            with pytest.raises(
                AccountDeletionFenceConflict,
                match="deletion is in progress",
            ):
                await require_active_account_generation(
                    session,
                    user_id=target.id,
                    expected_generation=0,
                )
    finally:
        await engine.dispose()


def test_mutation_fence_serializes_marker_and_rollback_releases_it(tmp_path: Path):
    asyncio.run(_mutation_fence_serializes_marker_and_rollback_releases_it(tmp_path))


async def _mutation_fence_serializes_marker_and_rollback_releases_it(
    tmp_path: Path,
):
    engine, maker = await _database(tmp_path / "account-admission-fence.db")
    target = _user("fenced-target")
    guard = maker()
    marker_task: asyncio.Task | None = None
    try:
        async with maker() as session:
            session.add(target)
            await session.commit()

        # The no-op update is intentionally kept uncommitted: it protects all
        # storage/DB publication performed by the admitted caller.
        assert (
            await require_active_account_generation(
                guard,
                user_id=target.id,
                expected_generation=0,
            )
            == 0
        )

        async def mark_from_admin():
            async with maker() as session:
                return await mark_account_deleting(session, user_id=target.id)

        marker_task = asyncio.create_task(mark_from_admin())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(marker_task), timeout=0.05)

        # Rollback is a supported request-failure path and must release the
        # serialization point without changing the captured generation.
        await guard.rollback()
        marker = await asyncio.wait_for(marker_task, timeout=2)
        assert marker.generation == 1

        async with maker() as session:
            with pytest.raises(AccountDeletionFenceConflict):
                await require_active_account_generation(
                    session,
                    user_id=target.id,
                    expected_generation=0,
                )
    finally:
        if marker_task is not None and not marker_task.done():
            marker_task.cancel()
            await asyncio.gather(marker_task, return_exceptions=True)
        await guard.close()
        await engine.dispose()


def test_protected_account_is_never_marked(tmp_path: Path):
    asyncio.run(_protected_account_is_never_marked(tmp_path))


async def _protected_account_is_never_marked(tmp_path: Path):
    engine, maker = await _database(tmp_path / "protected-account.db")
    administrator = _user("primary-admin", superuser=True, auth_version=11)
    try:
        async with maker() as session:
            session.add(administrator)
            await session.commit()
        async with maker() as session:
            with pytest.raises(ProtectedAccountError):
                await mark_account_deleting(session, user_id=administrator.id)
        async with maker() as session:
            persisted = await session.get(User, administrator.id)
            assert persisted is not None
            assert persisted.deletion_state == ACCOUNT_DELETION_ACTIVE
            assert persisted.deletion_generation == 0
            assert persisted.deletion_requested_at is None
            assert persisted.auth_version == 11
            assert persisted.is_active is True
    finally:
        await engine.dispose()


def test_admin_delete_commits_marker_before_rescan_and_cascade(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _admin_delete_commits_marker_before_rescan_and_cascade(tmp_path, monkeypatch)
    )


async def _admin_delete_commits_marker_before_rescan_and_cascade(
    tmp_path: Path, monkeypatch
):
    engine, maker = await _database(tmp_path / "admin-delete.db")
    administrator = _user("delete-admin", superuser=True)
    target = _user("delete-target", auth_version=3)
    theme_key = f"presentation_custom_themes:{target.id}"
    observed: list[tuple[str, int, int, bool]] = []
    try:
        async with maker() as session:
            session.add_all(
                [
                    administrator,
                    target,
                    KeyValueSqlModel(key=theme_key, value={"theme": "private"}),
                ]
            )
            await session.commit()

        async def snapshot_after_committed_marker(_session, *, owner_id):
            assert owner_id == target.id
            # An independent transaction must see phase one before either
            # snapshot pass starts.
            async with maker() as observer:
                persisted = await observer.get(User, owner_id)
                assert persisted is not None
                observed.append(
                    (
                        persisted.deletion_state,
                        persisted.deletion_generation,
                        persisted.auth_version,
                        persisted.is_active,
                    )
                )
            return 0

        monkeypatch.setattr(
            admin_router,
            "snapshot_account_workspace_cleanup_targets",
            snapshot_after_committed_marker,
        )
        monkeypatch.setattr(
            admin_router,
            "delete_account_restricted_dependents",
            lambda _session, *, owner_id: _async_zero(owner_id, target.id),
        )
        async with maker() as session:
            await admin_router.delete_user(
                target.id,
                admin=administrator,
                session=session,
            )

        assert observed == [
            (ACCOUNT_DELETION_DELETING, 1, 4, False),
            (ACCOUNT_DELETION_DELETING, 1, 4, False),
        ]
        async with maker() as session:
            assert await session.get(User, target.id) is None
            assert (
                await session.scalar(
                    select(KeyValueSqlModel.id).where(
                        KeyValueSqlModel.key == theme_key
                    )
                )
                is None
            )
    finally:
        await engine.dispose()


def test_admin_delete_resumes_after_snapshot_failure_without_generation_bump(
    tmp_path: Path, monkeypatch
):
    asyncio.run(
        _admin_delete_resumes_after_snapshot_failure_without_generation_bump(
            tmp_path, monkeypatch
        )
    )


async def _admin_delete_resumes_after_snapshot_failure_without_generation_bump(
    tmp_path: Path, monkeypatch
):
    engine, maker = await _database(tmp_path / "admin-delete-resume.db")
    administrator = _user("resume-admin", superuser=True)
    target = _user("resume-target", auth_version=5)
    try:
        async with maker() as session:
            session.add_all([administrator, target])
            await session.commit()

        async def fail_snapshot(_session, *, owner_id):
            assert owner_id == target.id
            raise OSError("simulated cleanup snapshot failure")

        monkeypatch.setattr(
            admin_router,
            "snapshot_account_workspace_cleanup_targets",
            fail_snapshot,
        )
        async with maker() as session:
            with pytest.raises(OSError, match="simulated cleanup snapshot failure"):
                await admin_router.delete_user(
                    target.id,
                    admin=administrator,
                    session=session,
                )

        async with maker() as session:
            persisted = await session.get(User, target.id)
            assert persisted is not None
            requested_at = persisted.deletion_requested_at
            assert requested_at is not None
            assert persisted.deletion_state == ACCOUNT_DELETION_DELETING
            assert persisted.deletion_generation == 1
            assert persisted.auth_version == 6
            assert persisted.is_active is False

        resumed: list[tuple[int, int, object]] = []

        async def successful_rescan(_session, *, owner_id):
            async with maker() as observer:
                persisted = await observer.get(User, owner_id)
                assert persisted is not None
                resumed.append(
                    (
                        persisted.deletion_generation,
                        persisted.auth_version,
                        persisted.deletion_requested_at,
                    )
                )
            return 0

        monkeypatch.setattr(
            admin_router,
            "snapshot_account_workspace_cleanup_targets",
            successful_rescan,
        )
        monkeypatch.setattr(
            admin_router,
            "delete_account_restricted_dependents",
            lambda _session, *, owner_id: _async_zero(owner_id, target.id),
        )
        async with maker() as session:
            await admin_router.delete_user(
                target.id,
                admin=administrator,
                session=session,
            )

        assert resumed == [(1, 6, requested_at), (1, 6, requested_at)]
        async with maker() as session:
            assert await session.get(User, target.id) is None
    finally:
        await engine.dispose()


def test_cleanup_redrive_route_requires_admin_dependency():
    route = next(
        route
        for route in admin_router.API_V1_ADMIN_ROUTER.routes
        if route.path
        == "/api/v1/admin/workspace-cleanup/dead-letters/{journal_id}/redrive"
    )

    assert route.methods == {"POST"}
    assert admin_router.get_current_admin in {
        dependency.call for dependency in route.dependant.dependencies
    }


def test_admin_delete_replays_success_after_lost_response(tmp_path: Path, monkeypatch):
    asyncio.run(
        _admin_delete_replays_success_after_lost_response(tmp_path, monkeypatch)
    )


async def _admin_delete_replays_success_after_lost_response(
    tmp_path: Path,
    monkeypatch,
):
    engine, maker = await _database(tmp_path / "admin-delete-replay.db")
    administrator = _user("replay-admin", superuser=True)
    target = _user("replay-target")
    cleanup_target = account_owned_tree_cleanup_target(
        owner_id=target.id,
        root_kind="temp",
        root=tmp_path / "account-tree",
    )
    try:
        async with maker() as session:
            session.add_all([administrator, target])
            await session.commit()

        async def durable_snapshot(session, *, owner_id):
            assert owner_id == target.id
            return await enqueue_workspace_cleanup_targets(
                session,
                targets=[cleanup_target],
            )

        monkeypatch.setattr(
            admin_router,
            "snapshot_account_workspace_cleanup_targets",
            durable_snapshot,
        )
        monkeypatch.setattr(
            admin_router,
            "delete_account_restricted_dependents",
            lambda _session, *, owner_id: _async_zero(owner_id, target.id),
        )

        # The first request commits successfully; model a lost HTTP response by
        # discarding its return value and issuing the exact same DELETE again.
        async with maker() as session:
            await admin_router.delete_user(
                target.id,
                admin=administrator,
                session=session,
            )
        async with maker() as session:
            assert await session.get(User, target.id) is None
            assert await account_cleanup_tombstone_exists(
                session,
                owner_id=target.id,
            )

        async with maker() as session:
            assert (
                await admin_router.delete_user(
                    target.id,
                    admin=administrator,
                    session=session,
                )
                is None
            )

        # An arbitrary UUID remains distinguishable from a completed deletion.
        async with maker() as session:
            with pytest.raises(HTTPException) as raised:
                await admin_router.delete_user(
                    uuid.uuid4(),
                    admin=administrator,
                    session=session,
                )
            assert raised.value.status_code == 404
    finally:
        await engine.dispose()
