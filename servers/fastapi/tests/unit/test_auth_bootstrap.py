import asyncio
import json
import os
import stat
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.v1.auth import bootstrap, secret_store
from api.v1.auth.users import PASSWORD_HELPER
from models.sql.access_token import AccessToken, hash_access_token
from models.sql.user import User
from models.sql.template_v2 import TemplateV2
from utils import user_config_store


def assert_private_config_file(config_path):
    # Windows represents the owner-only contract as an ACL; stat mode bits are
    # not a reliable indicator after ``icacls`` has secured the file.
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


async def _create_auth_database(database_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(User.__table__.create)
        await connection.run_sync(AccessToken.__table__.create)
    return engine, session_maker


def test_legacy_backfill_excludes_all_catalog_template_ids():
    class RecordingSession:
        def __init__(self):
            self.statements = []
            self.commit_count = 0

        async def execute(self, statement):
            self.statements.append(statement)

        async def commit(self):
            self.commit_count += 1

    session = RecordingSession()
    admin = User(
        id=uuid.uuid4(),
        username="admin",
        hashed_password="unused",
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )

    asyncio.run(bootstrap._backfill_legacy_ownership(session, admin))

    template_update = next(
        statement
        for statement in session.statements
        if statement.table.name == TemplateV2.__tablename__
    )
    compiled = template_update.compile()
    catalog_ids = {
        entry.id
        for entry in bootstrap.list_template_v2_catalog_entries(
            include_unpublished=True
        )
    }

    assert "NOT IN" in str(template_update)
    assert any(
        isinstance(value, (tuple, list)) and set(value) == catalog_ids
        for value in compiled.params.values()
    )
    assert session.commit_count == 1


def test_reset_auth_recovers_admin_without_replacing_account(
    monkeypatch, tmp_path
):
    config_path = tmp_path / "userConfig.json"
    config_path.write_text(
        json.dumps(
            {
                "AUTH_USERNAME": "old-admin",
                "AUTH_PASSWORD_HASH": "old-hash",
                "AUTH_SECRET_KEY": "old-secret",
                "LLM_PROVIDER": "openai",
            }
        )
    )
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("RESET_AUTH", "true")
    monkeypatch.setenv("AUTH_USERNAME", "recovered-admin")
    monkeypatch.setenv("AUTH_PASSWORD", "new-secret-123")
    monkeypatch.delenv("AUTH_OVERRIDE_FROM_ENV", raising=False)

    async def runner():
        engine, session_maker = await _create_auth_database(tmp_path / "auth.db")
        original_id = None
        try:
            async with session_maker() as session:
                admin = User(
                    username="old-admin",
                    hashed_password=PASSWORD_HELPER.hash("old-secret-123"),
                    is_active=True,
                    is_verified=True,
                    is_superuser=True,
                    auth_version=4,
                )
                session.add(admin)
                await session.flush()
                original_id = admin.id
                session.add(
                    AccessToken(
                        token_hash=hash_access_token("sk-presenton-old"),
                        user_id=admin.id,
                    )
                )
                await session.commit()

            async def skip_ownership_backfill(_session, _admin):
                return None

            monkeypatch.setattr(bootstrap, "async_session_maker", session_maker)
            monkeypatch.setattr(
                bootstrap,
                "_backfill_legacy_ownership",
                skip_ownership_backfill,
            )
            await bootstrap.bootstrap_database_admin()

            async with session_maker() as session:
                recovered = await session.scalar(select(User))
                tokens = list(await session.scalars(select(AccessToken)))

            assert recovered is not None
            assert recovered.id == original_id
            assert recovered.username == "recovered-admin"
            assert recovered.admin_slot == "primary"
            assert recovered.auth_version == 5
            verified, _ = PASSWORD_HELPER.verify_and_update(
                "new-secret-123",
                recovered.hashed_password,
            )
            assert verified is True
            assert tokens == []
        finally:
            await engine.dispose()

    asyncio.run(runner())

    config = json.loads(config_path.read_text())
    assert config["AUTH_USERNAME"] == "recovered-admin"
    assert config["AUTH_PASSWORD_HASH"] != "old-hash"
    # Rotation writes the new secret to the vault and drops the plaintext copy.
    assert "AUTH_SECRET_KEY" not in config
    assert secret_store.peek_auth_secret() not in (None, "old-secret")
    assert config["LLM_PROVIDER"] == "openai"
    assert_private_config_file(config_path)
    assert_private_config_file(tmp_path / "userConfig.json.bak")


def test_bootstrap_prefers_existing_primary_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("USER_CONFIG_PATH", str(tmp_path / "userConfig.json"))
    monkeypatch.delenv("RESET_AUTH", raising=False)
    monkeypatch.delenv("AUTH_OVERRIDE_FROM_ENV", raising=False)

    async def runner():
        engine, session_maker = await _create_auth_database(
            tmp_path / "primary-admin.db"
        )
        selected_admin_ids = []
        try:
            async with session_maker() as session:
                legacy_admin = User(
                    username="legacy-admin",
                    hashed_password="legacy-hash",
                    is_active=True,
                    is_verified=True,
                    is_superuser=True,
                )
                primary_admin = User(
                    username="primary-admin",
                    hashed_password="primary-hash",
                    is_active=True,
                    is_verified=True,
                    is_superuser=True,
                    admin_slot="primary",
                )
                session.add_all([legacy_admin, primary_admin])
                await session.commit()
                primary_admin_id = primary_admin.id

            async def record_ownership_backfill(_session, admin):
                selected_admin_ids.append(admin.id)

            monkeypatch.setattr(bootstrap, "async_session_maker", session_maker)
            monkeypatch.setattr(
                bootstrap,
                "_backfill_legacy_ownership",
                record_ownership_backfill,
            )
            await bootstrap.bootstrap_database_admin()

            assert selected_admin_ids == [primary_admin_id]
        finally:
            await engine.dispose()

    asyncio.run(runner())


def test_reset_auth_without_password_refuses_to_delete_or_replace_admin(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("USER_CONFIG_PATH", str(tmp_path / "userConfig.json"))
    monkeypatch.setenv("RESET_AUTH", "true")
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    monkeypatch.delenv("AUTH_OVERRIDE_FROM_ENV", raising=False)

    async def runner():
        engine, session_maker = await _create_auth_database(
            tmp_path / "missing-password.db"
        )
        try:
            async with session_maker() as session:
                admin = User(
                    username="admin",
                    hashed_password=PASSWORD_HELPER.hash("old-secret-123"),
                    is_active=True,
                    is_verified=True,
                    is_superuser=True,
                    auth_version=1,
                )
                session.add(admin)
                await session.commit()
                original_id = admin.id

            monkeypatch.setattr(bootstrap, "async_session_maker", session_maker)
            with pytest.raises(RuntimeError, match="require AUTH_PASSWORD"):
                await bootstrap.bootstrap_database_admin()

            async with session_maker() as session:
                users = list(await session.scalars(select(User)))
            assert len(users) == 1
            assert users[0].id == original_id
            assert users[0].username == "admin"
        finally:
            await engine.dispose()

    asyncio.run(runner())
