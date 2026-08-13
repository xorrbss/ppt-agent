from __future__ import annotations

import asyncio
from pathlib import Path
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.sql.provider_credential_reference import ProviderCredentialReference
from models.sql.user import User
from services.provider_credential_reference_service import (
    get_provider_credential_reference,
    save_provider_credential_reference,
)
from services.provider_credential_vault import (
    CredentialRef,
    CredentialScope,
    CredentialVaultError,
)


def test_reference_service_persists_only_scoped_ref(tmp_path: Path) -> None:
    async def scenario() -> None:
        assert User.__tablename__ == "user"
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'refs.db'}")
        async with engine.begin() as connection:
            await connection.run_sync(ProviderCredentialReference.__table__.create)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        user_id = uuid.uuid4()
        scope = CredentialScope("tenant-a", user_id)
        reference = CredentialRef(
            backend="server_kms",
            tenant_id="tenant-a",
            user_id=str(user_id),
            provider_id="openai",
            key_id="a" * 32,
        )
        try:
            async with sessions() as session:
                stored = await save_provider_credential_reference(
                    session, scope=scope, provider="openai",
                    credential_ref=reference.storage_value,
                )
                await session.commit()
                assert stored.credential_ref == reference.storage_value
                assert "secret" not in stored.__table__.columns
            async with sessions() as session:
                assert await get_provider_credential_reference(
                    session, scope=scope, provider="openai"
                ) == reference
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", ["sk-not-a-reference", "raw-secret", "{}"])
def test_reference_service_rejects_secret_like_values(tmp_path: Path, value: str) -> None:
    async def scenario() -> None:
        assert User.__tablename__ == "user"
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / (uuid.uuid4().hex + '.db')}")
        async with engine.begin() as connection:
            await connection.run_sync(ProviderCredentialReference.__table__.create)
        try:
            async with async_sessionmaker(engine)() as session:
                with pytest.raises(CredentialVaultError):
                    await save_provider_credential_reference(
                        session,
                        scope=CredentialScope("tenant-a", uuid.uuid4()),
                        provider="openai",
                        credential_ref=value,
                    )
        finally:
            await engine.dispose()

    asyncio.run(scenario())
