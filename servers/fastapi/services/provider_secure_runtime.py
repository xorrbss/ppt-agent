"""Resolve user-scoped vault credentials at the provider SDK boundary."""

from __future__ import annotations

import os
from collections.abc import Mapping

from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from models.sql.provider_credential_reference import ProviderCredentialReference
from services.provider_settings_values import SECRET_SETTING_KEYS
from services.provider_credential_vault import (
    CredentialRef,
    CredentialScope,
    CredentialVaultError,
    provider_credential_vault_from_environment,
    validate_persisted_credential_ref,
)
from utils.db_utils import get_database_url_and_connect_args, to_sync_sqlalchemy_url
from utils.owner_context import get_current_owner_id


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_MANAGED_TIERS = frozenset({"staging", "production"})


def secure_credentials_required(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return the fail-closed plaintext policy for the active deployment.

    Managed deployments and an explicitly selected self-host secure profile
    always require vault references.  A false compatibility override remains
    available only for local/development/test profiles.  Unknown boolean text
    is treated as enabled so a typo cannot reopen plaintext persistence.
    """

    values = os.environ if environ is None else environ
    tier = values.get("TEMPLATE_V2_DEPLOYMENT_TIER", "local").strip().lower()
    secure_mode = values.get("PROVIDER_SECURE_MODE", "").strip().lower()
    if tier in _MANAGED_TIERS or secure_mode in _TRUE_VALUES:
        return True
    configured = values.get("PROVIDER_SECURE_CREDENTIALS_REQUIRED")
    if configured is None:
        return False
    normalized = configured.strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    # True and malformed values both fail closed.
    return normalized in _TRUE_VALUES or normalized not in _FALSE_VALUES


def _stored_reference(provider: str) -> CredentialRef | None:
    owner_id = get_current_owner_id()
    if owner_id is None:
        return None
    url, connect_args = get_database_url_and_connect_args()
    sync_url = to_sync_sqlalchemy_url(url)
    if "sqlite" in sync_url:
        connect_args = {"check_same_thread": False, "timeout": 30.0}
    else:
        connect_args = {}
    engine = create_engine(sync_url, connect_args=connect_args)
    try:
        if not inspect(engine).has_table(ProviderCredentialReference.__tablename__):
            return None
        with Session(engine) as session:
            row = session.scalar(
                select(ProviderCredentialReference).where(
                    ProviderCredentialReference.tenant_id
                    == os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
                    ProviderCredentialReference.user_id == owner_id,
                    ProviderCredentialReference.provider == provider,
                )
            )
            return (
                validate_persisted_credential_ref(row.credential_ref)
                if row is not None
                else None
            )
    finally:
        engine.dispose()


def setting_credential_id(setting_key: str) -> str:
    normalized = setting_key.strip().lower().replace("_", "-")
    if not normalized or len(normalized) > 110:
        raise CredentialVaultError("credential_setting_key_invalid")
    return f"setting.{normalized}"


def _configured_storage_scope() -> CredentialScope:
    owner_id = get_current_owner_id()
    configured_owner = (
        os.getenv("PROVIDER_CREDENTIAL_SCOPE_USER_ID") or ""
    ).strip()
    if owner_id is None and not configured_owner:
        raise CredentialVaultError("credential_owner_context_required")
    return CredentialScope(
        os.getenv("AUTHORED_VNEXT_TENANT_ID", "default"),
        owner_id or configured_owner,
    )


def protect_provider_settings_for_storage(
    config: Mapping[str, object],
    *,
    created_references: list[CredentialRef] | None = None,
) -> dict[str, object]:
    """Replace plaintext setting secrets with opaque vault references.

    Local compatibility profiles retain their historical plaintext behavior.
    Managed, self-host secure, and Electron profiles never return a plaintext
    secret from this storage boundary.
    """

    protected = dict(config)
    if not secure_credentials_required():
        return protected
    scope = _configured_storage_scope()
    vault = provider_credential_vault_from_environment()
    for setting_key in SECRET_SETTING_KEYS:
        value = protected.get(setting_key)
        if value is None or value == "":
            continue
        provider_id = setting_credential_id(setting_key)
        if isinstance(value, str):
            try:
                reference = validate_persisted_credential_ref(value)
            except CredentialVaultError:
                reference = None
            if reference is not None:
                if reference.provider_id != provider_id:
                    raise CredentialVaultError("credential_provider_mismatch")
                protected[setting_key] = reference.storage_value
                continue
        if not isinstance(value, str):
            raise CredentialVaultError("credential_secret_invalid")
        created = vault.store(
            scope=scope,
            provider_id=provider_id,
            secret=value,
        )
        if created_references is not None:
            created_references.append(created)
        protected[setting_key] = created.storage_value
    return protected


def delete_provider_setting_references(
    references: list[CredentialRef],
) -> None:
    """Delete staged or superseded setting secrets with ownership checks."""

    if not references:
        return
    vault = provider_credential_vault_from_environment()
    for reference in references:
        vault.delete(reference, scope=reference.scope)


def superseded_provider_setting_references(
    previous: Mapping[str, object],
    current: Mapping[str, object],
) -> list[CredentialRef]:
    """Return old refs no longer selected by the committed settings record."""

    superseded: list[CredentialRef] = []
    for setting_key in SECRET_SETTING_KEYS:
        old_value = previous.get(setting_key)
        if old_value in (None, "") or old_value == current.get(setting_key):
            continue
        try:
            old_reference = validate_persisted_credential_ref(old_value)
        except CredentialVaultError:
            continue
        superseded.append(old_reference)
    return superseded


def resolve_provider_secret(
    provider: str,
    configured_value: str | None,
) -> str | None:
    """Prefer the current user's vault ref; reject plaintext in strict mode."""

    reference: CredentialRef | None = None
    if isinstance(configured_value, str):
        try:
            reference = validate_persisted_credential_ref(configured_value)
        except CredentialVaultError:
            if configured_value and secure_credentials_required():
                raise CredentialVaultError(
                    "credential_plaintext_fallback_denied"
                ) from None
            if configured_value:
                return configured_value
    if reference is None:
        reference = _stored_reference(provider)
    if reference is None:
        if secure_credentials_required():
            raise CredentialVaultError("credential_reference_required")
        return configured_value or None
    owner_id = get_current_owner_id()
    configured_owner = (
        os.getenv("PROVIDER_CREDENTIAL_SCOPE_USER_ID") or ""
    ).strip()
    expected_user_id = (
        configured_owner
        if configured_owner and reference.user_id == configured_owner
        else str(owner_id) if owner_id is not None else ""
    )
    if not expected_user_id:
        raise CredentialVaultError("credential_owner_context_required")
    return provider_credential_vault_from_environment().resolve(
        reference,
        scope=CredentialScope(reference.tenant_id, expected_user_id),
        provider_id=provider,
    ).reveal()


__all__ = [
    "delete_provider_setting_references",
    "protect_provider_settings_for_storage",
    "resolve_provider_secret",
    "secure_credentials_required",
    "setting_credential_id",
    "superseded_provider_setting_references",
]
