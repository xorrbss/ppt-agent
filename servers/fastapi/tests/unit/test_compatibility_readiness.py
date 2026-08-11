from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel, select

import migrations
from models.sql.image_asset import ImageAsset
from models.sql.presentation import PresentationModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.user import User
from models.sql.workspace import (
    WorkspaceAsset,
    WorkspaceCleanupJournal,
    WorkspaceConversation,
)
from api.v1.ppt.endpoints.images import delete_uploaded_image_by_id
from services.assets import (
    AssetGatewayError,
    backfill_legacy_image_assets,
    canonicalize_legacy_image_asset,
)
from services.compatibility_readiness import (
    backfill_presentation_profiles,
    compatibility_readiness,
)
from services.workspace_cleanup_journal_service import sweep_workspace_cleanup_journal
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_owner_is_admin,
    set_current_owner_id,
    set_current_owner_is_admin,
)


@contextmanager
def _owner(owner_id: uuid.UUID, *, is_admin: bool = False):
    token = set_current_owner_id(owner_id)
    admin_token = set_current_owner_is_admin(is_admin)
    try:
        yield
    finally:
        reset_current_owner_is_admin(admin_token)
        reset_current_owner_id(token)


async def _database(path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: SQLModel.metadata.create_all(
                sync,
                tables=[
                    User.__table__,
                    PresentationModel.__table__,
                    WorkspaceConversation.__table__,
                    WorkspaceAsset.__table__,
                    WorkspaceCleanupJournal.__table__,
                    ImageAsset.__table__,
                    PresentationGenerationJob.__table__,
                ],
            )
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_compatibility_revision_is_single_head_and_round_trips(tmp_path: Path):
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    database_url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", database_url)
    assert ScriptDirectory.from_config(config).get_heads() == [migrations.HEAD_REVISION]

    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE workspace_assets (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE imageasset ("
                "id CHAR(32) PRIMARY KEY, path VARCHAR NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE presentations (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
            connection.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": "f7c9e1a3b5d8"},
            )
        command.upgrade(config, migrations.REVISION_ASSET_PROFILE_COMPATIBILITY)
        assert "canonical_asset_id" in {
            column["name"] for column in sa.inspect(engine).get_columns("imageasset")
        }
        assert any(
            tuple(constraint.get("column_names") or ())
            == ("canonical_asset_id",)
            for constraint in sa.inspect(engine).get_unique_constraints("imageasset")
        )
        assert any(
            tuple(constraint.get("constrained_columns") or ())
            == ("canonical_asset_id",)
            and constraint.get("referred_table") == "workspace_assets"
            for constraint in sa.inspect(engine).get_foreign_keys("imageasset")
        )
        assert "profile_driver_id" in {
            column["name"]
            for column in sa.inspect(engine).get_columns("presentations")
        }
        command.downgrade(config, "f7c9e1a3b5d8")
        assert "canonical_asset_id" not in {
            column["name"] for column in sa.inspect(engine).get_columns("imageasset")
        }
        command.upgrade(config, migrations.REVISION_ASSET_PROFILE_COMPATIBILITY)
        assert "canonical_asset_id" in {
            column["name"] for column in sa.inspect(engine).get_columns("imageasset")
        }
    finally:
        engine.dispose()


def test_compatibility_revision_rejects_wrong_named_partial_constraint(
    tmp_path: Path,
):
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parents[2] / "alembic"),
    )
    database_url = f"sqlite:///{(tmp_path / 'bad-partial.db').as_posix()}"
    config.set_main_option("sqlalchemy.url", database_url)
    engine = sa.create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE workspace_assets (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE imageasset ("
                "id CHAR(32) PRIMARY KEY, path VARCHAR NOT NULL, "
                "canonical_asset_id CHAR(32), "
                "CONSTRAINT uq_imageasset_canonical_asset_id UNIQUE(path))"
            )
            connection.exec_driver_sql(
                "CREATE TABLE presentations (id CHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"
            )
            connection.execute(
                sa.text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": "f7c9e1a3b5d8"},
            )

        with pytest.raises(
            RuntimeError,
            match="canonical asset unique constraint is invalid",
        ):
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def test_image_backfill_is_owner_scoped_and_rejects_traversal(tmp_path: Path):
    async def scenario():
        engine, maker = await _database(tmp_path / "images.db")
        owner_a = uuid.uuid4()
        owner_b = uuid.uuid4()
        image_root = tmp_path / "images"
        image_root.mkdir()
        valid = image_root / "valid.png"
        valid.write_bytes(b"\x89PNG\r\n\x1a\n" + b"safe-image")
        escaped = tmp_path / "escaped.png"
        escaped.write_bytes(b"\x89PNG\r\n\x1a\n" + b"escaped")
        try:
            async with maker() as session:
                session.add_all(
                    [
                        User(id=owner_a, username="owner-a", hashed_password="x"),
                        User(id=owner_b, username="owner-b", hashed_password="x"),
                    ]
                )
                await session.commit()
            with _owner(owner_a):
                async with maker() as session:
                    own = ImageAsset(path=str(valid))
                    session.add(own)
                    await session.commit()
                    own_id = own.id
            with _owner(owner_b):
                async with maker() as session:
                    foreign = ImageAsset(path=str(valid))
                    session.add(foreign)
                    await session.commit()
                    foreign_id = foreign.id

            with _owner(owner_a):
                async with maker() as session:
                    binding = await canonicalize_legacy_image_asset(
                        session,
                        image=own_id,
                        image_root=image_root,
                        storage_root=tmp_path / "canonical",
                    )
                    assert binding.asset_id
                    with pytest.raises(AssetGatewayError) as hidden:
                        await canonicalize_legacy_image_asset(
                            session,
                            image=foreign_id,
                            image_root=image_root,
                            storage_root=tmp_path / "canonical",
                        )
                    assert hidden.value.code == "legacy_image_not_found"

                    traversal = ImageAsset(path=str(escaped))
                    session.add(traversal)
                    await session.commit()
                    with pytest.raises(AssetGatewayError) as forbidden:
                        await canonicalize_legacy_image_asset(
                            session,
                            image=traversal.id,
                            image_root=image_root,
                            storage_root=tmp_path / "canonical",
                        )
                    assert forbidden.value.code == "legacy_image_path_forbidden"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_admin_legacy_image_backfill_accepts_only_exact_shared_root_provenance(
    tmp_path: Path,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "admin-images.db")
        admin_id = uuid.uuid4()
        other_id = uuid.uuid4()
        shared_root = tmp_path / "app-data" / "images"
        admin_root = shared_root / "users" / str(admin_id)
        other_root = shared_root / "users" / str(other_id)
        admin_root.mkdir(parents=True)
        other_root.mkdir(parents=True)
        legacy = shared_root / "legacy.png"
        foreign = other_root / "foreign.png"
        legacy.write_bytes(b"\x89PNG\r\n\x1a\nlegacy")
        foreign.write_bytes(b"\x89PNG\r\n\x1a\nforeign")
        try:
            async with maker() as session:
                session.add_all(
                    [
                        User(
                            id=admin_id,
                            username="bootstrap-admin",
                            hashed_password="x",
                        ),
                        User(id=other_id, username="other", hashed_password="x"),
                    ]
                )
                await session.commit()
            with _owner(admin_id):
                async with maker() as session:
                    legacy_row = ImageAsset(path=str(legacy))
                    forged_foreign_row = ImageAsset(path=str(foreign))
                    session.add_all([legacy_row, forged_foreign_row])
                    await session.commit()
                    legacy_row_id = legacy_row.id
                    forged_foreign_row_id = forged_foreign_row.id
                    with pytest.raises(AssetGatewayError) as non_admin:
                        await canonicalize_legacy_image_asset(
                            session,
                            image=legacy_row_id,
                            image_root=admin_root,
                            admin_legacy_image_root=shared_root,
                            storage_root=tmp_path / "canonical",
                        )
                    assert non_admin.value.code == "admin_migration_required"

            with _owner(admin_id, is_admin=True):
                async with maker() as session:
                    binding = await canonicalize_legacy_image_asset(
                        session,
                        image=legacy_row_id,
                        image_root=admin_root,
                        admin_legacy_image_root=shared_root,
                        storage_root=tmp_path / "canonical",
                    )
                    assert binding.asset_id

                    with pytest.raises(AssetGatewayError) as denied:
                        await canonicalize_legacy_image_asset(
                            session,
                            image=forged_foreign_row_id,
                            image_root=admin_root,
                            admin_legacy_image_root=shared_root,
                            storage_root=tmp_path / "canonical",
                        )
                    assert denied.value.code == "legacy_image_path_forbidden"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_concurrent_legacy_image_canonicalization_converges_to_one_asset(
    tmp_path: Path,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "image-race.db")
        owner_id = uuid.uuid4()
        image_root = tmp_path / "race-images"
        image_root.mkdir()
        source = image_root / "race.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nconcurrent")
        storage_root = tmp_path / "race-canonical"
        try:
            with _owner(owner_id):
                async with maker() as session:
                    session.add(User(id=owner_id, username="race-owner", hashed_password="x"))
                    await session.commit()
                    image = ImageAsset(path=str(source))
                    session.add(image)
                    await session.commit()
                    image_id = image.id

                async def canonicalize():
                    async with maker() as session:
                        return await canonicalize_legacy_image_asset(
                            session,
                            image=image_id,
                            image_root=image_root,
                            storage_root=storage_root,
                        )

                first, second = await asyncio.gather(canonicalize(), canonicalize())
                assert first.asset_id == second.asset_id
                async with maker() as session:
                    assert len(list(await session.scalars(select(WorkspaceAsset)))) == 1
                    row = await session.get(ImageAsset, image_id)
                    assert row is not None
                    assert row.canonical_asset_id == first.asset_id
            assert len([path for path in storage_root.rglob("*") if path.is_file()]) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_image_canonicalization_commit_failure_compensates_db_and_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "image-commit.db")
        owner_id = uuid.uuid4()
        image_root = tmp_path / "commit-images"
        image_root.mkdir()
        source = image_root / "commit.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\ncommit")
        storage_root = tmp_path / "commit-canonical"
        try:
            with _owner(owner_id):
                async with maker() as session:
                    session.add(User(id=owner_id, username="commit-owner", hashed_password="x"))
                    await session.commit()
                    image = ImageAsset(path=str(source))
                    session.add(image)
                    await session.commit()
                    image_id = image.id

                async with maker() as session:
                    original_commit = session.commit
                    commit_calls = 0

                    async def fail_canonical_commit():
                        nonlocal commit_calls
                        commit_calls += 1
                        if commit_calls == 1:
                            raise RuntimeError("injected commit failure")
                        await original_commit()

                    monkeypatch.setattr(session, "commit", fail_canonical_commit)
                    with pytest.raises(AssetGatewayError) as raised:
                        await canonicalize_legacy_image_asset(
                            session,
                            image=image_id,
                            image_root=image_root,
                            storage_root=storage_root,
                        )
                    assert raised.value.code == "legacy_image_canonicalization_failed"
                    row = await session.get(ImageAsset, image_id)
                    assert row is not None
                    assert row.canonical_asset_id is None
                    assert row.canonicalization_error_code == raised.value.code
                    assert list(await session.scalars(select(WorkspaceAsset))) == []
            assert not storage_root.exists() or not any(
                path.is_file() for path in storage_root.rglob("*")
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_image_canonicalization_recovers_ambiguous_successful_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "image-ambiguous.db")
        owner_id = uuid.uuid4()
        image_root = tmp_path / "ambiguous-images"
        image_root.mkdir()
        source = image_root / "ambiguous.png"
        source.write_bytes(b"\x89PNG\r\n\x1a\nambiguous")
        storage_root = tmp_path / "ambiguous-canonical"
        try:
            with _owner(owner_id):
                async with maker() as session:
                    session.add(User(id=owner_id, username="ambiguous-owner", hashed_password="x"))
                    await session.commit()
                    image = ImageAsset(path=str(source))
                    session.add(image)
                    await session.commit()
                    image_id = image.id
                async with maker() as session:
                    original_commit = session.commit
                    raised = False

                    async def commit_then_lose_ack():
                        nonlocal raised
                        await original_commit()
                        if not raised:
                            raised = True
                            raise RuntimeError("lost commit acknowledgement")

                    monkeypatch.setattr(session, "commit", commit_then_lose_ack)
                    binding = await canonicalize_legacy_image_asset(
                        session,
                        image=image_id,
                        image_root=image_root,
                        storage_root=storage_root,
                    )
                    row = await session.get(ImageAsset, image_id)
                    assert row is not None
                    assert row.canonical_asset_id == binding.asset_id
                    assert await session.get(WorkspaceAsset, binding.asset_id) is not None
            assert len([path for path in storage_root.rglob("*") if path.is_file()]) == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_admin_aggregate_backfills_all_owners_and_anonymous_preserving_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "aggregate-backfill.db")
        admin_id = uuid.uuid4()
        other_id = uuid.uuid4()
        shared_root = tmp_path / "aggregate-app-data" / "images"
        admin_root = shared_root / "users" / str(admin_id)
        other_root = shared_root / "users" / str(other_id)
        admin_root.mkdir(parents=True)
        other_root.mkdir(parents=True)
        paths = {
            admin_id: admin_root / "admin.png",
            other_id: other_root / "other.png",
            None: shared_root / "anonymous.png",
        }
        for path in paths.values():
            path.write_bytes(b"\x89PNG\r\n\x1a\n" + path.name.encode())
        image_ids: dict[uuid.UUID | None, uuid.UUID] = {}
        presentation_ids: list[uuid.UUID] = []
        try:
            async with maker() as session:
                session.add_all(
                    [
                        User(id=admin_id, username="aggregate-admin", hashed_password="x"),
                        User(id=other_id, username="aggregate-other", hashed_password="x"),
                    ]
                )
                await session.commit()
            for owner_id in (admin_id, other_id, None):
                if owner_id is None:
                    owner_token = set_current_owner_id(None)
                    admin_token = set_current_owner_is_admin(False)
                    owner_context = None
                else:
                    owner_context = _owner(owner_id)
                try:
                    if owner_context is not None:
                        owner_context.__enter__()
                    async with maker() as session:
                        image = ImageAsset(path=str(paths[owner_id]))
                        presentation = PresentationModel(
                            content="legacy",
                            n_slides=1,
                            language="en",
                            layout={"name": "legacy"},
                            generation_mode="standard",
                        )
                        session.add_all([image, presentation])
                        await session.commit()
                        image_ids[owner_id] = image.id
                        presentation_ids.append(presentation.id)
                        await session.execute(
                            sa.update(PresentationModel)
                            .where(PresentationModel.id == presentation.id)
                            .values(
                                profile_driver_id=None,
                                profile_schema_version=None,
                                profile_backfilled_at=None,
                            )
                        )
                        await session.commit()
                finally:
                    if owner_context is not None:
                        owner_context.__exit__(None, None, None)
                    else:
                        reset_current_owner_is_admin(admin_token)
                        reset_current_owner_id(owner_token)

            with _owner(admin_id):
                async with maker() as session:
                    with pytest.raises(PermissionError, match="admin_aggregate_required"):
                        await backfill_legacy_image_assets(
                            session,
                            image_root=admin_root,
                            admin_legacy_image_root=shared_root,
                            aggregate_all_owners=True,
                        )
                    with pytest.raises(PermissionError, match="admin_aggregate_required"):
                        await backfill_presentation_profiles(
                            session,
                            aggregate_all_owners=True,
                        )

            with _owner(admin_id, is_admin=True):
                async with maker() as session:
                    image_result = await backfill_legacy_image_assets(
                        session,
                        image_root=admin_root,
                        storage_root=tmp_path / "aggregate-canonical",
                        admin_legacy_image_root=shared_root,
                        aggregate_all_owners=True,
                    )
                    profile_result = await backfill_presentation_profiles(
                        session,
                        aggregate_all_owners=True,
                    )
                    assert image_result.backfilled == 3
                    assert image_result.failed == 0
                    assert profile_result.backfilled == 3
                    images = list(
                        await session.scalars(
                            select(ImageAsset).execution_options(skip_owner_scope=True)
                        )
                    )
                    assets = {
                        asset.id: asset
                        for asset in await session.scalars(
                            select(WorkspaceAsset).execution_options(skip_owner_scope=True)
                        )
                    }
                    assert {image.id for image in images} == set(image_ids.values())
                    assert all(image.canonical_asset_id is not None for image in images)
                    assert all(
                        assets[image.canonical_asset_id].owner_id == image.owner_id
                        for image in images
                        if image.canonical_asset_id is not None
                    )
                    presentations = list(
                        await session.scalars(
                            select(PresentationModel).execution_options(skip_owner_scope=True)
                        )
                    )
                    assert {row.id for row in presentations} == set(presentation_ids)
                    assert all(row.profile_driver_id == "standard" for row in presentations)
                    report = await compatibility_readiness(
                        session,
                        aggregate_all_owners=True,
                    )
                    assert report.image_path_api_removal_ready is True
                    assert report.generation_mode_removal_ready is True
        finally:
            await engine.dispose()

    monkeypatch.setenv("LEGACY_IMAGE_PATH_CALLERS_DISABLED", "true")
    monkeypatch.setenv("LEGACY_GENERATION_MODE_READERS_DISABLED", "true")
    monkeypatch.setenv("LEGACY_RAW_PATH_CALLERS_DISABLED", "true")
    asyncio.run(scenario())


def test_profile_backfill_and_removal_gate_fail_closed_without_deployment_evidence(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "profiles.db")
        owner = uuid.uuid4()
        try:
            with _owner(owner):
                async with maker() as session:
                    session.add(User(id=owner, username="profile-owner", hashed_password="x"))
                    presentation = PresentationModel(
                            content="smart",
                            n_slides=1,
                            language="en",
                            layout={"name": "legacy"},
                            generation_mode="smart",
                        )
                    session.add(presentation)
                    await session.commit()
                    # The write boundary canonicalizes new rows. Simulate one
                    # pre-migration row so this test exercises the restartable
                    # compatibility backfill rather than ordinary persistence.
                    await session.execute(
                        sa.update(PresentationModel)
                        .where(PresentationModel.id == presentation.id)
                        .values(
                            profile_driver_id=None,
                            profile_schema_version=None,
                            profile_backfilled_at=None,
                        )
                    )
                    await session.commit()
                    result = await backfill_presentation_profiles(session)
                    assert result.backfilled == 1
                    row = (await session.execute(select(PresentationModel))).scalar_one()
                    assert row.profile_driver_id == "smart"
                    report = await compatibility_readiness(session)
                    assert report.presentation_rows_without_canonical_profile == 0
                    assert report.generation_mode_removal_ready is False
        finally:
            await engine.dispose()

    monkeypatch.delenv("LEGACY_GENERATION_MODE_READERS_DISABLED", raising=False)
    asyncio.run(scenario())


def test_only_admin_all_owner_aggregate_can_claim_global_removal_readiness(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "global-readiness.db")
        admin_id = uuid.uuid4()
        other_id = uuid.uuid4()
        try:
            async with maker() as session:
                session.add_all(
                    [
                        User(id=admin_id, username="admin-scope", hashed_password="x"),
                        User(id=other_id, username="other-scope", hashed_password="x"),
                    ]
                )
                await session.commit()
            with _owner(other_id):
                async with maker() as session:
                    session.add(ImageAsset(path=str(tmp_path / "missing.png")))
                    await session.commit()

            with _owner(admin_id):
                async with maker() as session:
                    owner_report = await compatibility_readiness(session)
                    assert owner_report.image_rows == 0
                    assert owner_report.global_removal_evidence is False
                    assert owner_report.image_path_api_removal_ready is False
                    with pytest.raises(PermissionError, match="admin_aggregate_required"):
                        await compatibility_readiness(
                            session,
                            aggregate_all_owners=True,
                        )

            with _owner(admin_id, is_admin=True):
                async with maker() as session:
                    global_report = await compatibility_readiness(
                        session,
                        aggregate_all_owners=True,
                    )
                    assert global_report.evidence_scope == "global"
                    assert global_report.global_removal_evidence is True
                    assert global_report.image_rows == 1
                    assert global_report.image_rows_without_canonical_asset == 1
                    assert global_report.image_path_api_removal_ready is False
        finally:
            await engine.dispose()

    monkeypatch.setenv("LEGACY_IMAGE_PATH_CALLERS_DISABLED", "true")
    monkeypatch.setenv("LEGACY_GENERATION_MODE_READERS_DISABLED", "true")
    monkeypatch.setenv("LEGACY_RAW_PATH_CALLERS_DISABLED", "true")
    asyncio.run(scenario())


def test_image_delete_journals_canonical_and_legacy_bytes_before_db_removal(
    tmp_path: Path,
    monkeypatch,
):
    async def scenario():
        engine, maker = await _database(tmp_path / "image-delete.db")
        owner_id = uuid.uuid4()
        app_data = tmp_path / "app-data"
        image_root = app_data / "images" / "users" / str(owner_id)
        canonical_root = app_data / "workspace_assets"
        image_root.mkdir(parents=True)
        legacy_file = image_root / "gallery.png"
        legacy_file.write_bytes(b"\x89PNG\r\n\x1a\ncleanup")
        try:
            async with maker() as session:
                session.add(User(id=owner_id, username="image-owner", hashed_password="x"))
                await session.commit()
            with _owner(owner_id):
                async with maker() as session:
                    image = ImageAsset(path=str(legacy_file), is_uploaded=True)
                    session.add(image)
                    await session.commit()
                    image_id = image.id
                    binding = await canonicalize_legacy_image_asset(
                        session,
                        image=image_id,
                        image_root=image_root,
                        storage_root=canonical_root,
                    )
                    canonical = await session.get(WorkspaceAsset, binding.asset_id)
                    assert canonical is not None
                    canonical_file = canonical_root.joinpath(
                        *canonical.storage_key.split("/")
                    )
                    assert canonical_file.is_file()

                    await delete_uploaded_image_by_id(image_id, session)

                    assert await session.get(ImageAsset, image_id) is None
                    deleted_asset = await session.get(WorkspaceAsset, binding.asset_id)
                    assert deleted_asset is not None
                    assert deleted_asset.state == "deleted"
                    journals = list(
                        await session.scalars(
                            select(WorkspaceCleanupJournal).order_by(
                                WorkspaceCleanupJournal.storage_namespace
                            )
                        )
                    )
                    assert {row.storage_namespace for row in journals} == {
                        "legacy_gallery_image",
                        "workspace_asset",
                    }
                    assert all(row.status == "pending" for row in journals)
                    assert legacy_file.is_file()
                    assert canonical_file.is_file()

                swept = await sweep_workspace_cleanup_journal(
                    session_factory=maker,
                    limit=2,
                )
                assert swept["deleted"] == 2
                assert not legacy_file.exists()
                assert not canonical_file.exists()
                async with maker() as session:
                    statuses = set(
                        await session.scalars(
                            select(WorkspaceCleanupJournal.status)
                        )
                    )
                    assert statuses == {"succeeded"}
        finally:
            await engine.dispose()

    monkeypatch.setenv("APP_DATA_DIRECTORY", str(tmp_path / "app-data"))
    asyncio.run(scenario())
