from __future__ import annotations

import json

import pytest

from api.v1.auth import secret_store
from services.provider_credential_backends import (
    LOCAL_FILE_VAULT_DIRECTORY_ENV,
    LocalFileCredentialBackend,
    backend_from_environment,
)
from services.provider_credential_vault import (
    CredentialRef,
    CredentialVaultError,
    InMemoryCredentialBackend,
    SecretValue,
)
from utils import simple_auth
from utils.user_config_store import update_user_config_file


@pytest.fixture(autouse=True)
def isolated_secret_store(monkeypatch, tmp_path):
    """Unconfigured by default, with no signing secret cached from another test."""
    monkeypatch.delenv("PROVIDER_CREDENTIAL_BACKEND", raising=False)
    monkeypatch.setenv("USER_CONFIG_PATH", str(tmp_path / "userConfig.json"))
    secret_store.reset_auth_secret_cache()
    try:
        yield
    finally:
        secret_store.reset_auth_secret_cache()


@pytest.fixture
def vault(monkeypatch) -> InMemoryCredentialBackend:
    """Make an in-memory vault the configured authority for the signing secret."""
    backend = InMemoryCredentialBackend()
    monkeypatch.setenv("PROVIDER_CREDENTIAL_BACKEND", "memory")
    monkeypatch.setattr(secret_store, "_backend_factory", lambda: backend)
    return backend


def _config_path(tmp_path):
    return tmp_path / "userConfig.json"


def _write_config(path, config: dict) -> None:
    # Go through the store so the file carries the private ACL that
    # `read_user_config_file` insists on before it returns any content.
    update_user_config_file(str(path), lambda _existing: dict(config))


def _read_config(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _vault_value(backend: InMemoryCredentialBackend) -> str | None:
    value = backend.get(secret_store.vault_reference(backend))
    return value.reveal() if value is not None else None


# --- (a) unconfigured: the user config file stays the only store ------------


def test_unconfigured_creates_the_secret_in_the_user_config_file(tmp_path) -> None:
    secret = secret_store.get_or_create_auth_secret()

    assert secret and secret == secret_store.get_or_create_auth_secret()
    assert secret == secret_store.peek_auth_secret()
    assert _read_config(_config_path(tmp_path))["AUTH_SECRET_KEY"] == secret


def test_unconfigured_never_touches_a_vault(monkeypatch, tmp_path) -> None:
    def fail(*_args, **_kwargs):  # pragma: no cover - the test fails if it runs
        raise AssertionError("an unconfigured deployment must not build a vault")

    monkeypatch.setattr(secret_store, "_backend_factory", fail)

    assert secret_store.peek_auth_secret() is None
    created = secret_store.get_or_create_auth_secret()
    assert secret_store.rotate_auth_secret() != created
    secret_store.clear_auth_secret()
    assert secret_store.peek_auth_secret() is None


def test_unconfigured_reads_an_existing_plaintext_secret(tmp_path) -> None:
    _write_config(_config_path(tmp_path), {"AUTH_SECRET_KEY": "legacy-secret"})

    assert secret_store.peek_auth_secret() == "legacy-secret"
    assert secret_store.get_or_create_auth_secret() == "legacy-secret"


# --- (b) configured: the vault is authoritative, the legacy value migrates ---


def test_configured_stores_a_new_secret_in_the_vault_only(tmp_path, vault) -> None:
    secret = secret_store.get_or_create_auth_secret()

    assert secret and secret == secret_store.get_or_create_auth_secret()
    assert _vault_value(vault) == secret
    assert vault.entry_count == 1
    assert not _config_path(tmp_path).exists()


def test_configured_peek_returns_none_without_creating(vault) -> None:
    assert secret_store.peek_auth_secret() is None
    assert vault.entry_count == 0


def test_legacy_field_is_migrated_into_the_vault_then_vault_wins(tmp_path, vault) -> None:
    config_path = _config_path(tmp_path)
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret", "LLM": "openai"})

    assert secret_store.peek_auth_secret() == "legacy-secret"
    assert _vault_value(vault) == "legacy-secret"
    # Migration copies; it does not rewrite the config file.
    assert _read_config(config_path)["AUTH_SECRET_KEY"] == "legacy-secret"

    # A later edit of the plaintext field is ignored once the vault holds a value.
    secret_store.reset_auth_secret_cache()
    _write_config(config_path, {"AUTH_SECRET_KEY": "edited-later", "LLM": "openai"})
    assert secret_store.get_or_create_auth_secret() == "legacy-secret"


def test_configured_first_write_prefers_the_value_already_in_the_vault(vault) -> None:
    reference = secret_store.vault_reference(vault)
    original_put = vault.put

    def racing_put(ref, value):
        # Another process stores its secret between our read and our write.
        original_put(ref, SecretValue("winner"))
        original_put(ref, value)

    vault.put = racing_put

    assert secret_store.get_or_create_auth_secret() == "winner"
    assert vault.get(reference).reveal() == "winner"


# --- (c) rotation in both modes --------------------------------------------


def test_rotation_unconfigured_rewrites_the_plaintext_field(tmp_path) -> None:
    config_path = _config_path(tmp_path)
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret", "LLM": "openai"})

    rotated = secret_store.rotate_auth_secret()

    assert rotated != "legacy-secret"
    assert secret_store.peek_auth_secret() == rotated
    assert _read_config(config_path) == {"AUTH_SECRET_KEY": rotated, "LLM": "openai"}


def test_rotation_configured_replaces_the_vault_value_and_drops_the_plaintext_copy(
    tmp_path, vault
) -> None:
    config_path = _config_path(tmp_path)
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret", "LLM": "openai"})
    before = secret_store.get_or_create_auth_secret()

    rotated = secret_store.rotate_auth_secret()

    assert rotated != before
    assert secret_store.peek_auth_secret() == rotated
    assert _vault_value(vault) == rotated
    assert vault.entry_count == 1
    assert _read_config(config_path) == {"LLM": "openai"}


def test_clear_removes_vault_and_plaintext_copies(tmp_path, vault) -> None:
    config_path = _config_path(tmp_path)
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret"})
    secret_store.get_or_create_auth_secret()

    secret_store.clear_auth_secret()

    assert vault.entry_count == 0
    assert _read_config(config_path) == {}
    assert secret_store.peek_auth_secret() is None


# --- (d) configured but broken: fail closed ---------------------------------


def test_configured_backend_that_cannot_be_built_fails_closed(monkeypatch, tmp_path) -> None:
    config_path = _config_path(tmp_path)
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret"})
    monkeypatch.setenv("PROVIDER_CREDENTIAL_BACKEND", "memory")

    def unavailable():
        raise CredentialVaultError("credential_backend_not_configured")

    monkeypatch.setattr(secret_store, "_backend_factory", unavailable)

    for operation in (
        secret_store.peek_auth_secret,
        secret_store.get_or_create_auth_secret,
        secret_store.rotate_auth_secret,
        secret_store.clear_auth_secret,
    ):
        with pytest.raises(secret_store.AuthSecretError) as info:
            operation()
        assert info.value.code == "auth_secret_vault_unavailable"

    # No in-memory or plaintext fallback was produced.
    assert _read_config(config_path) == {"AUTH_SECRET_KEY": "legacy-secret"}


def test_vault_read_failure_is_not_masked_by_the_legacy_field(tmp_path, vault) -> None:
    _write_config(_config_path(tmp_path), {"AUTH_SECRET_KEY": "legacy-secret"})

    def broken_get(_reference):
        raise CredentialVaultError("credential_desktop_vault_read_failed")

    vault.get = broken_get

    with pytest.raises(secret_store.AuthSecretError) as info:
        secret_store.get_or_create_auth_secret()
    assert info.value.code == "auth_secret_vault_read_failed"


# --- (e) the development file backend ---------------------------------------


def _local_file_reference() -> CredentialRef:
    return CredentialRef(
        backend="local_file",
        tenant_id="system",
        user_id="auth",
        provider_id="session-signing-secret",
        key_id="a" * 32,
    )


def test_local_file_backend_is_refused_under_production(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENVIRONMENT", "production")

    with pytest.raises(CredentialVaultError) as info:
        LocalFileCredentialBackend(tmp_path / "vault")
    assert info.value.code == "credential_local_file_vault_forbidden"

    # An unset ENVIRONMENT is treated as production, not as development.
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("PROVIDER_CREDENTIAL_BACKEND", "local_file")
    monkeypatch.setenv(LOCAL_FILE_VAULT_DIRECTORY_ENV, str(tmp_path / "vault"))
    with pytest.raises(CredentialVaultError) as info:
        backend_from_environment()
    assert info.value.code == "credential_local_file_vault_forbidden"


def test_local_file_backend_round_trips_in_a_development_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("PROVIDER_CREDENTIAL_BACKEND", "local_file")
    monkeypatch.setenv(LOCAL_FILE_VAULT_DIRECTORY_ENV, str(tmp_path / "vault"))

    backend = backend_from_environment()
    assert isinstance(backend, LocalFileCredentialBackend)

    reference = _local_file_reference()
    assert backend.get(reference) is None
    backend.put(reference, SecretValue("file-secret"))
    assert backend.get(reference).reveal() == "file-secret"
    with pytest.raises(CredentialVaultError) as info:
        backend.put(reference, SecretValue("second"))
    assert info.value.code == "credential_ref_conflict"
    backend.delete(reference)
    assert backend.get(reference) is None


def test_secret_store_uses_the_local_file_backend_end_to_end(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("PROVIDER_CREDENTIAL_BACKEND", "local_file")
    monkeypatch.setenv(LOCAL_FILE_VAULT_DIRECTORY_ENV, str(tmp_path / "vault"))

    created = secret_store.get_or_create_auth_secret()
    secret_store.reset_auth_secret_cache()
    assert secret_store.peek_auth_secret() == created
    assert not _config_path(tmp_path).exists()

    rotated = secret_store.rotate_auth_secret()
    assert rotated != created
    secret_store.reset_auth_secret_cache()
    assert secret_store.peek_auth_secret() == rotated


# --- the consumers of the accessor ------------------------------------------


def test_legacy_session_tokens_and_jwt_share_the_vault_secret(monkeypatch, tmp_path, vault) -> None:
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    simple_auth.setup_initial_credentials("admin", "secret123")

    token = simple_auth.create_session_token("admin")

    assert simple_auth.validate_session_token(token) == "admin"
    assert "AUTH_SECRET_KEY" not in _read_config(_config_path(tmp_path))
    assert _vault_value(vault) == secret_store.get_or_create_auth_secret()

    simple_auth.force_set_credentials("admin", "secret456")
    assert simple_auth.validate_session_token(token) is None

    simple_auth.clear_stored_credentials()
    assert vault.entry_count == 0
    assert simple_auth.is_auth_configured() is False


def test_legacy_session_tokens_work_without_a_configured_backend(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    simple_auth.setup_initial_credentials("admin", "secret123")

    token = simple_auth.create_session_token("admin")

    assert simple_auth.validate_session_token(token) == "admin"
    assert _read_config(_config_path(tmp_path))["AUTH_SECRET_KEY"]

    simple_auth.clear_stored_credentials()
    assert _read_config(_config_path(tmp_path)) == {}
    assert simple_auth.is_auth_configured() is False
