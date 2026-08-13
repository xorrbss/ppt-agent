from __future__ import annotations

import pytest

from services.provider_credential_vault import (
    InMemoryCredentialBackend,
    ProviderCredentialVault,
)
from services.provider_secure_runtime import (
    delete_provider_setting_references,
    protect_provider_settings_for_storage,
    resolve_provider_secret,
    secure_credentials_required,
    superseded_provider_setting_references,
)


@pytest.mark.parametrize("tier", ["staging", "production"])
def test_managed_deployment_always_denies_plaintext_compatibility(tier: str) -> None:
    assert secure_credentials_required(
        {
            "TEMPLATE_V2_DEPLOYMENT_TIER": tier,
            "PROVIDER_SECURE_CREDENTIALS_REQUIRED": "false",
        }
    )


def test_self_host_secure_profile_requires_vault_references_by_default() -> None:
    assert secure_credentials_required({"PROVIDER_SECURE_MODE": "true"})


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "typo"])
def test_explicit_or_malformed_required_flag_fails_closed(value: str) -> None:
    assert secure_credentials_required(
        {
            "TEMPLATE_V2_DEPLOYMENT_TIER": "local",
            "PROVIDER_SECURE_CREDENTIALS_REQUIRED": value,
        }
    )


@pytest.mark.parametrize("value", ["false", "0", "no", "off"])
def test_plaintext_compatibility_is_limited_to_explicit_local_profiles(
    value: str,
) -> None:
    assert not secure_credentials_required(
        {
            "TEMPLATE_V2_DEPLOYMENT_TIER": "development",
            "PROVIDER_SECURE_CREDENTIALS_REQUIRED": value,
        }
    )


def test_secure_storage_replaces_plaintext_with_owner_scoped_reference(
    monkeypatch,
) -> None:
    backend = InMemoryCredentialBackend()
    vault = ProviderCredentialVault(backend)
    monkeypatch.setenv("PROVIDER_SECURE_MODE", "true")
    monkeypatch.setenv(
        "PROVIDER_CREDENTIAL_SCOPE_USER_ID",
        "00000000-0000-4000-8000-000000000001",
    )
    monkeypatch.setattr(
        "services.provider_secure_runtime.provider_credential_vault_from_environment",
        lambda: vault,
    )

    protected = protect_provider_settings_for_storage(
        {"OPENAI_API_KEY": "must-not-persist", "OPENAI_MODEL": "gpt-test"}
    )

    reference = protected["OPENAI_API_KEY"]
    assert isinstance(reference, str)
    assert reference.startswith("credential-ref:v1:memory:")
    assert "must-not-persist" not in reference
    assert backend.entry_count == 1
    assert (
        resolve_provider_secret("setting.openai-api-key", reference)
        == "must-not-persist"
    )


def test_secure_storage_fails_closed_without_owner_scope(monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_SECURE_MODE", "true")
    monkeypatch.delenv("PROVIDER_CREDENTIAL_SCOPE_USER_ID", raising=False)

    with pytest.raises(RuntimeError, match="credential_owner_context_required"):
        protect_provider_settings_for_storage({"OPENAI_API_KEY": "secret"})


def test_staged_setting_reference_can_be_compensated_after_cas_loss(
    monkeypatch,
) -> None:
    backend = InMemoryCredentialBackend()
    vault = ProviderCredentialVault(backend)
    monkeypatch.setenv("PROVIDER_SECURE_MODE", "true")
    monkeypatch.setenv("PROVIDER_CREDENTIAL_SCOPE_USER_ID", "desktop-user")
    monkeypatch.setattr(
        "services.provider_secure_runtime.provider_credential_vault_from_environment",
        lambda: vault,
    )
    created = []

    protected = protect_provider_settings_for_storage(
        {"OPENAI_API_KEY": "staged-secret"},
        created_references=created,
    )

    assert backend.entry_count == 1
    assert len(created) == 1
    assert superseded_provider_setting_references(
        protected, {"OPENAI_API_KEY": ""}
    ) == created
    delete_provider_setting_references(created)
    assert backend.entry_count == 0
