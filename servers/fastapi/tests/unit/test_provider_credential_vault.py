from __future__ import annotations

import json
from uuid import UUID

import pytest

from services.provider_credential_vault import (
    CredentialRef,
    CredentialScope,
    CredentialVaultError,
    InMemoryCredentialBackend,
    ProviderCredentialVault,
    SecretValue,
    validate_persisted_credential_ref,
)


def test_credential_ref_round_trip_is_tenant_user_and_provider_scoped() -> None:
    scope = CredentialScope(
        tenant_id="tenant-a",
        user_id=UUID("12345678-1234-5678-1234-567812345678"),
    )
    vault = ProviderCredentialVault(
        InMemoryCredentialBackend(backend_id="desktop_vault")
    )

    reference = vault.store(
        scope=scope,
        provider_id="openai",
        secret="sk-private-canary",
    )

    assert reference.backend == "desktop_vault"
    assert reference.tenant_id == "tenant-a"
    assert reference.user_id == "12345678-1234-5678-1234-567812345678"
    assert reference.provider_id == "openai"
    assert CredentialRef.parse(reference.storage_value) == reference
    assert validate_persisted_credential_ref(reference.storage_value) == reference
    assert "sk-private-canary" not in reference.storage_value
    assert vault.resolve(reference.storage_value, scope=scope).reveal() == (
        "sk-private-canary"
    )


def test_secret_value_never_prints_or_serializes_secret_material() -> None:
    secret = SecretValue("provider-secret-canary")

    assert str(secret) == "<redacted>"
    assert repr(secret) == "SecretValue(<redacted>)"
    assert "provider-secret-canary" not in json.dumps({"secret": repr(secret)})


@pytest.mark.parametrize(
    "value",
    [
        "sk-raw-api-key",
        {"api_key": "secret"},
        "credential-ref:v1:desktop_vault:tenant:user:provider:not-hex",
        "credential-ref:v2:desktop_vault:tenant:user:provider:"
        "0123456789abcdef0123456789abcdef",
    ],
)
def test_database_boundary_rejects_raw_or_malformed_secret_values(value: object) -> None:
    with pytest.raises(
        CredentialVaultError, match="credential_storage_requires_ref"
    ):
        validate_persisted_credential_ref(value)


def test_cross_tenant_cross_user_and_cross_provider_resolution_fail_closed() -> None:
    backend = InMemoryCredentialBackend(backend_id="server_kms")
    vault = ProviderCredentialVault(backend)
    owner = CredentialScope("tenant-a", "user-a")
    reference = vault.store(
        scope=owner,
        provider_id="anthropic",
        secret="private-canary",
    )

    for other_scope in (
        CredentialScope("tenant-b", "user-a"),
        CredentialScope("tenant-a", "user-b"),
    ):
        with pytest.raises(CredentialVaultError, match="credential_scope_mismatch"):
            vault.resolve(reference, scope=other_scope)

    with pytest.raises(CredentialVaultError, match="credential_provider_mismatch"):
        vault.resolve(reference, scope=owner, provider_id="openai")
    assert backend.entry_count == 1


def test_delete_is_scoped_and_missing_reference_is_indistinguishable() -> None:
    backend = InMemoryCredentialBackend()
    vault = ProviderCredentialVault(backend)
    owner = CredentialScope("tenant-a", "user-a")
    reference = vault.store(
        scope=owner,
        provider_id="google",
        secret="private-canary",
    )

    with pytest.raises(CredentialVaultError, match="credential_scope_mismatch"):
        vault.delete(reference, scope=CredentialScope("tenant-a", "user-b"))
    assert backend.entry_count == 1

    vault.delete(reference.storage_value, scope=owner)
    assert backend.entry_count == 0
    with pytest.raises(CredentialVaultError, match="credential_not_found"):
        vault.resolve(reference, scope=owner)


@pytest.mark.parametrize(
    ("tenant_id", "user_id", "provider_id", "secret"),
    [
        ("../tenant", "user", "openai", "key"),
        ("tenant", "user:other", "openai", "key"),
        ("tenant", "user", "openai/other", "key"),
        ("tenant", "user", "openai", ""),
        ("tenant", "user", "openai", "nul\x00secret"),
    ],
)
def test_invalid_scope_provider_and_secret_inputs_are_rejected(
    tenant_id: str, user_id: str, provider_id: str, secret: str
) -> None:
    vault = ProviderCredentialVault(InMemoryCredentialBackend())

    with pytest.raises(CredentialVaultError):
        scope = CredentialScope(tenant_id, user_id)
        vault.store(scope=scope, provider_id=provider_id, secret=secret)


def test_backend_mismatch_cannot_resolve_reference() -> None:
    scope = CredentialScope("tenant", "user")
    desktop = ProviderCredentialVault(
        InMemoryCredentialBackend(backend_id="desktop_vault")
    )
    server = ProviderCredentialVault(
        InMemoryCredentialBackend(backend_id="server_kms")
    )
    reference = desktop.store(
        scope=scope,
        provider_id="openai",
        secret="private-canary",
    )

    with pytest.raises(CredentialVaultError, match="credential_backend_mismatch"):
        server.resolve(reference, scope=scope)
