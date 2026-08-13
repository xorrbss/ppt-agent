"""Persistence boundary for external provider credential references."""

from __future__ import annotations

import uuid
import asyncio

from sqlalchemy import delete, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models.sql.provider_credential_reference import ProviderCredentialReference
from services.provider_credential_vault import (
    CredentialRef,
    CredentialScope,
    CredentialVaultError,
    ProviderCredentialVault,
    validate_persisted_credential_ref,
)


async def save_provider_credential_reference(
    session: AsyncSession,
    *,
    scope: CredentialScope,
    provider: str,
    credential_ref: str,
) -> ProviderCredentialReference:
    """Upsert scoped reference metadata while rejecting secret material."""

    parsed = validate_persisted_credential_ref(credential_ref)
    if parsed.backend == "memory":
        raise CredentialVaultError("credential_test_backend_not_persistable")
    if (
        parsed.scope != scope
        or parsed.provider_id != provider
        or parsed.user_id != str(scope.user_id)
    ):
        raise CredentialVaultError("credential_scope_mismatch")
    try:
        user_id = uuid.UUID(str(scope.user_id))
    except ValueError as error:
        raise CredentialVaultError("credential_user_id_invalid") from error

    existing = await session.scalar(
        select(ProviderCredentialReference).where(
            ProviderCredentialReference.tenant_id == str(scope.tenant_id),
            ProviderCredentialReference.user_id == user_id,
            ProviderCredentialReference.provider == provider,
        )
    )
    if existing is None:
        existing = ProviderCredentialReference(
            tenant_id=str(scope.tenant_id),
            user_id=user_id,
            provider=provider,
            backend=parsed.backend,
            credential_ref=parsed.storage_value,
        )
        session.add(existing)
    else:
        existing.backend = parsed.backend
        existing.credential_ref = parsed.storage_value
    await session.flush()
    return existing


async def get_provider_credential_reference(
    session: AsyncSession,
    *,
    scope: CredentialScope,
    provider: str,
) -> CredentialRef | None:
    try:
        user_id = uuid.UUID(str(scope.user_id))
    except ValueError as error:
        raise CredentialVaultError("credential_user_id_invalid") from error
    stored = await session.scalar(
        select(ProviderCredentialReference).where(
            ProviderCredentialReference.tenant_id == str(scope.tenant_id),
            ProviderCredentialReference.user_id == user_id,
            ProviderCredentialReference.provider == provider,
        )
    )
    if stored is None:
        return None
    parsed = validate_persisted_credential_ref(stored.credential_ref)
    if parsed.scope != scope or parsed.provider_id != provider:
        raise CredentialVaultError("credential_scope_mismatch")
    return parsed


async def rotate_provider_credential(
    session: AsyncSession,
    *,
    vault: ProviderCredentialVault,
    scope: CredentialScope,
    provider: str,
    secret: str,
) -> CredentialRef:
    """Atomically select a new ref, then revoke the superseded vault value."""

    previous = await get_provider_credential_reference(
        session, scope=scope, provider=provider
    )
    created = await asyncio.to_thread(
        vault.store,
        scope=scope,
        provider_id=provider,
        secret=secret,
    )
    try:
        await save_provider_credential_reference(
            session,
            scope=scope,
            provider=provider,
            credential_ref=created.storage_value,
        )
        await session.commit()
    except BaseException:
        await session.rollback()
        await asyncio.to_thread(vault.delete, created, scope=scope)
        raise
    if previous is not None and previous != created:
        await asyncio.to_thread(vault.delete, previous, scope=scope)
    return created


async def revoke_provider_credential(
    session: AsyncSession,
    *,
    vault: ProviderCredentialVault,
    scope: CredentialScope,
    provider: str,
) -> bool:
    reference = await get_provider_credential_reference(
        session, scope=scope, provider=provider
    )
    if reference is None:
        return False
    # Revoke external material before deleting its only durable reference. A
    # backend outage therefore leaves a retryable ref and never an orphan secret.
    await asyncio.to_thread(vault.delete, reference, scope=scope)
    await session.execute(
        delete(ProviderCredentialReference).where(
            ProviderCredentialReference.tenant_id == str(scope.tenant_id),
            ProviderCredentialReference.user_id == uuid.UUID(str(scope.user_id)),
            ProviderCredentialReference.provider == provider,
        )
    )
    await session.commit()
    return True


async def revoke_account_provider_credentials(
    session: AsyncSession,
    *,
    owner_id: uuid.UUID,
    vault: ProviderCredentialVault | None = None,
) -> int:
    # Account deletion must remain rolling-deploy safe while the additive
    # credential-reference migration is still being applied to other nodes.
    table_exists = await session.run_sync(
        lambda sync_session: inspect(sync_session.connection()).has_table(
            ProviderCredentialReference.__tablename__
        )
    )
    if not table_exists:
        return 0
    rows = list(
        (
            await session.scalars(
                select(ProviderCredentialReference).where(
                    ProviderCredentialReference.user_id == owner_id
                )
            )
        ).all()
    )
    if not rows:
        return 0
    if vault is None:
        from services.provider_credential_vault import (
            provider_credential_vault_from_environment,
        )

        vault = provider_credential_vault_from_environment()
    for row in rows:
        reference = validate_persisted_credential_ref(row.credential_ref)
        await asyncio.to_thread(
            vault.delete,
            reference,
            scope=CredentialScope(row.tenant_id, owner_id),
        )
    await session.execute(
        delete(ProviderCredentialReference).where(
            ProviderCredentialReference.user_id == owner_id
        )
    )
    await session.flush()
    return len(rows)


__all__ = [
    "get_provider_credential_reference",
    "revoke_account_provider_credentials",
    "revoke_provider_credential",
    "rotate_provider_credential",
    "save_provider_credential_reference",
]
