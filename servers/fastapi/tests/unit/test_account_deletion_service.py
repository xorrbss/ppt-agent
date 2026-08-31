from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import api.v1.admin.router as admin_router
import services.account_deletion_controller as deletion_controller
from models.sql.key_value import KeyValueSqlModel
from models.sql.account_deletion_request import AccountDeletionRequest
from models.sql.public_artifact import (
    PublicArtifact,
    PublicArtifactPublication,
    PublicExportIdentity,
    PublicSlideCheckpoint,
    artifact_storage_key,
)
from models.sql.user import User
from models.sql.workspace import WorkspaceCleanupJournal
from services.account_deletion_service import (
    ACCOUNT_DELETION_ACTIVE,
    ACCOUNT_DELETION_DELETING,
    AccountDeletionFenceConflict,
    ProtectedAccountError,
    mark_account_deleting,
    retire_public_artifacts_for_account,
    require_active_account_generation,
)
from services.account_deletion_controller import (
    claim_account_deletion_request,
    finalize_account_deletion_claim,
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
        await connection.run_sync(AccountDeletionRequest.__table__.create)
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


def test_api_outbox_and_controller_claim_finalize_account_deletion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    asyncio.run(_api_outbox_and_controller_finalize(tmp_path, monkeypatch))


async def _api_outbox_and_controller_finalize(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, maker = await _database(tmp_path / "account-controller-finalize.db")
    administrator = _user("controller-admin", superuser=True)
    target = _user("controller-target", auth_version=4)
    digest = hashlib.sha256(b"controller-owned-artifact").hexdigest()
    artifact = PublicArtifact(
        id=uuid.uuid4(),
        owner_id=target.id,
        tenant_id="tenant-controller-delete",
        kind="checkpoint",
        sha256=digest,
        size_bytes=25,
        media_type="application/json",
        storage_key=artifact_storage_key(
            owner_id=target.id,
            tenant_id="tenant-controller-delete",
            digest=digest,
        ),
        created_by_operation_id=uuid.uuid4(),
        retention_class="checkpoint",
        lifecycle_state="committed",
        refcount=0,
    )

    async def noop(_session, *, owner_id):
        assert owner_id == target.id
        return 0

    monkeypatch.setattr(
        deletion_controller,
        "snapshot_account_workspace_cleanup_targets",
        noop,
    )
    monkeypatch.setattr(
        deletion_controller,
        "cancel_authored_vnext_generation_operations_for_account",
        noop,
    )
    monkeypatch.setattr(
        deletion_controller,
        "cancel_authored_vnext_exports_for_account",
        noop,
    )
    monkeypatch.setattr(
        deletion_controller,
        "revoke_account_provider_credentials",
        noop,
    )
    monkeypatch.setattr(
        deletion_controller,
        "delete_account_restricted_dependents",
        noop,
    )

    try:
        async with engine.begin() as connection:
            await connection.run_sync(PublicArtifact.__table__.create)
            await connection.run_sync(PublicExportIdentity.__table__.create)
            await connection.exec_driver_sql(
                "CREATE TABLE workspace_operations (id CHAR(32) PRIMARY KEY)"
            )
            await connection.run_sync(PublicArtifactPublication.__table__.create)
            await connection.run_sync(PublicSlideCheckpoint.__table__.create)
        async with maker() as session:
            session.add_all([administrator, target, artifact])
            await session.commit()

        async with maker() as session:
            accepted = await admin_router.delete_user(
                target.id,
                admin=administrator,
                session=session,
            )
        assert accepted == {
            "status": "accepted",
            "user_id": str(target.id),
            "deletion_generation": 1,
        }

        async with maker() as session:
            persisted = await session.get(User, target.id)
            request = await session.get(AccountDeletionRequest, target.id)
            assert persisted is not None and persisted.is_active is False
            assert persisted.auth_version == 5
            assert persisted.deletion_state == ACCOUNT_DELETION_DELETING
            assert request is not None and request.state == "pending"
            assert request.generation == persisted.deletion_generation == 1

        async with maker() as session:
            claim = await claim_account_deletion_request(
                session,
                worker_id="controller-test",
            )
        assert claim is not None and claim.claim_fence == 1
        async with maker() as session:
            assert (
                await claim_account_deletion_request(
                    session,
                    worker_id="controller-other",
                )
                is None
            )

        async with maker() as session:
            assert await finalize_account_deletion_claim(session, claim=claim) is True

        async with maker() as session:
            assert await session.get(User, target.id) is None
            assert await session.get(AccountDeletionRequest, target.id) is None
            retired = await session.get(PublicArtifact, artifact.id)
            assert retired is not None
            assert retired.lifecycle_state == "tombstoned"
            assert retired.refcount == 0
    finally:
        await engine.dispose()


def test_account_deletion_fences_export_reuse_and_tombstones_public_artifacts(
    tmp_path: Path,
):
    asyncio.run(_account_deletion_retires_public_artifacts(tmp_path))


async def _account_deletion_retires_public_artifacts(tmp_path: Path) -> None:
    engine, maker = await _database(tmp_path / "account-public-artifacts.db")
    owner_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    digest = hashlib.sha256(b"account-owned-artifact").hexdigest()
    artifact = PublicArtifact(
        id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-account-delete",
        kind="checkpoint",
        sha256=digest,
        size_bytes=22,
        media_type="application/json",
        storage_key=artifact_storage_key(
            owner_id=owner_id,
            tenant_id="tenant-account-delete",
            digest=digest,
        ),
        created_by_operation_id=operation_id,
        retention_class="checkpoint",
        lifecycle_state="committed",
        refcount=1,
    )
    now = datetime.now(timezone.utc)
    identity = PublicExportIdentity(
        id=uuid.uuid4(),
        owner_id=owner_id,
        tenant_id="tenant-account-delete",
        presentation_id=uuid.uuid4(),
        revision=1,
        canonical_digest="1" * 64,
        scene_digest="2" * 64,
        semantic_digest="3" * 64,
        quality_digest="4" * 64,
        exporter_digest="5" * 64,
        export_mode="pptx",
        output_mode="fidelity-raster",
        font_manifest_digest="6" * 64,
        exporter_version="test",
        render_adapter="test",
        source_artifact_id=artifact.id,
        state="running",
        claimed_by_operation_id=operation_id,
        worker_id="worker-test",
        attempt_token_hash="sha256:" + "7" * 64,
        fencing_token=7,
        attempt_count=1,
        lease_expires_at=now + timedelta(minutes=5),
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(PublicArtifact.__table__.create)
            await connection.run_sync(PublicExportIdentity.__table__.create)
            await connection.exec_driver_sql(
                "CREATE TABLE workspace_operations (id CHAR(32) PRIMARY KEY)"
            )
            await connection.run_sync(PublicArtifactPublication.__table__.create)
            await connection.run_sync(PublicSlideCheckpoint.__table__.create)
        async with maker() as session:
            session.add_all([artifact, identity])
            await session.commit()
            assert await retire_public_artifacts_for_account(
                session,
                owner_id=owner_id,
            ) == 1
            await session.commit()

        async with maker() as session:
            retired = await session.get(PublicArtifact, artifact.id)
            fenced = await session.get(PublicExportIdentity, identity.id)
            assert retired is not None
            assert retired.lifecycle_state == "tombstoned"
            assert retired.refcount == 0
            assert retired.lifecycle_revision == 2
            assert retired.expires_at is not None
            assert fenced is not None
            assert fenced.state == "failed"
            assert fenced.fencing_token == 8
            assert fenced.error_code == "account_deletion_in_progress"
    finally:
        await engine.dispose()


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


def test_account_deletion_controller_bounds_external_io_and_shutdown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    asyncio.run(_account_deletion_controller_bounds_external_io_and_shutdown(
        tmp_path,
        monkeypatch,
    ))


def test_account_deletion_controller_interval_defaults_and_clamps(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ACCOUNT_DELETION_CONTROLLER_INTERVAL_SECONDS",
        "invalid",
    )
    assert deletion_controller._account_deletion_controller_interval_seconds() == 5

    monkeypatch.setenv("ACCOUNT_DELETION_CONTROLLER_INTERVAL_SECONDS", "0")
    assert deletion_controller._account_deletion_controller_interval_seconds() == 1


async def _account_deletion_controller_bounds_external_io_and_shutdown(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def stalled_to_thread(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(
        deletion_controller,
        "get_app_data_directory_env",
        lambda: str(tmp_path),
    )
    monkeypatch.setattr(deletion_controller.asyncio, "to_thread", stalled_to_thread)
    monkeypatch.setattr(deletion_controller, "_EXTERNAL_IO_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(
            deletion_controller._run_external_account_deletion_io(
                object(),
                owner_id=uuid.uuid4(),
                generation=1,
            ),
            timeout=0.5,
        )

    runtime = deletion_controller.AccountDeletionControllerRuntime(
        worker_id="bounded-shutdown-test"
    )
    task = asyncio.create_task(asyncio.Event().wait())
    runtime._task = task
    monkeypatch.setattr(deletion_controller, "_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    await asyncio.wait_for(runtime.close(), timeout=0.5)
    await asyncio.sleep(0)
    assert task.cancelled()


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
