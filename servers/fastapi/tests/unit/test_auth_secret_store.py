from __future__ import annotations

import json

import pytest

from api.v1.auth import secret_store
from services.provider_credential_vault import (
    CredentialVaultError,
    InMemoryCredentialBackend,
    SecretValue,
)
from utils import simple_auth
from utils.user_config_store import update_user_config_file


def _write_config(path, config: dict) -> None:
    # Go through the store so the file carries the private ACL that
    # `read_user_config_file` insists on before it returns any content.
    update_user_config_file(str(path), lambda _existing: dict(config))


def _read_config(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _vault_value(backend: InMemoryCredentialBackend) -> str | None:
    value = backend.get(secret_store.vault_reference(backend))
    return value.reveal() if value is not None else None


def test_get_or_create_stores_a_new_secret_in_the_vault_only(
    monkeypatch, tmp_path, auth_secret_backend
) -> None:
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))

    secret = secret_store.get_or_create_auth_secret()

    assert secret and secret == secret_store.get_or_create_auth_secret()
    assert _vault_value(auth_secret_backend) == secret
    assert auth_secret_backend.entry_count == 1
    assert not config_path.exists()


def test_peek_returns_none_without_creating(auth_secret_backend, monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("USER_CONFIG_PATH", str(tmp_path / "userConfig.json"))

    assert secret_store.peek_auth_secret() is None
    assert auth_secret_backend.entry_count == 0


def test_legacy_field_is_migrated_into_the_vault_then_vault_wins(
    monkeypatch, tmp_path, auth_secret_backend
) -> None:
    config_path = tmp_path / "userConfig.json"
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret", "LLM": "openai"})
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))

    assert secret_store.peek_auth_secret() == "legacy-secret"
    assert _vault_value(auth_secret_backend) == "legacy-secret"
    # Migration copies; it does not rewrite the config file.
    assert _read_config(config_path)["AUTH_SECRET_KEY"] == "legacy-secret"

    # A later edit of the plaintext field is ignored once the vault holds a value.
    secret_store.reset_auth_secret_cache()
    _write_config(config_path, {"AUTH_SECRET_KEY": "edited-later", "LLM": "openai"})
    assert secret_store.get_or_create_auth_secret() == "legacy-secret"


def test_rotation_replaces_the_vault_value_and_drops_the_plaintext_copy(
    monkeypatch, tmp_path, auth_secret_backend
) -> None:
    config_path = tmp_path / "userConfig.json"
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret", "LLM": "openai"})
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    before = secret_store.get_or_create_auth_secret()

    rotated = secret_store.rotate_auth_secret()

    assert rotated != before
    assert secret_store.peek_auth_secret() == rotated
    assert _vault_value(auth_secret_backend) == rotated
    assert auth_secret_backend.entry_count == 1
    assert _read_config(config_path) == {"LLM": "openai"}


def test_clear_removes_vault_and_plaintext_copies(
    monkeypatch, tmp_path, auth_secret_backend
) -> None:
    config_path = tmp_path / "userConfig.json"
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret"})
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    secret_store.get_or_create_auth_secret()

    secret_store.clear_auth_secret()

    assert auth_secret_backend.entry_count == 0
    assert _read_config(config_path) == {}
    assert secret_store.peek_auth_secret() is None


def test_concurrent_first_write_prefers_the_value_already_in_the_vault(
    monkeypatch, tmp_path, auth_secret_backend
) -> None:
    monkeypatch.setenv("USER_CONFIG_PATH", str(tmp_path / "userConfig.json"))
    reference = secret_store.vault_reference(auth_secret_backend)
    original_put = auth_secret_backend.put

    def racing_put(ref, value):
        # Another process stores its secret between our read and our write.
        original_put(ref, SecretValue("winner"))
        original_put(ref, value)

    monkeypatch.setattr(auth_secret_backend, "put", racing_put)

    assert secret_store.get_or_create_auth_secret() == "winner"
    assert auth_secret_backend.get(reference).reveal() == "winner"


def test_unconfigured_vault_fails_closed_with_a_stable_code(
    monkeypatch, tmp_path
) -> None:
    config_path = tmp_path / "userConfig.json"
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret"})
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))

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


def test_vault_read_failure_is_not_masked_by_the_legacy_field(
    monkeypatch, tmp_path, auth_secret_backend
) -> None:
    config_path = tmp_path / "userConfig.json"
    _write_config(config_path, {"AUTH_SECRET_KEY": "legacy-secret"})
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))

    def broken_get(_reference):
        raise CredentialVaultError("credential_desktop_vault_read_failed")

    monkeypatch.setattr(auth_secret_backend, "get", broken_get)

    with pytest.raises(secret_store.AuthSecretError) as info:
        secret_store.get_or_create_auth_secret()
    assert info.value.code == "auth_secret_vault_read_failed"


def test_legacy_session_tokens_and_jwt_share_the_vault_secret(
    monkeypatch, tmp_path, auth_secret_backend
) -> None:
    config_path = tmp_path / "userConfig.json"
    monkeypatch.setenv("USER_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    simple_auth.setup_initial_credentials("admin", "secret123")

    token = simple_auth.create_session_token("admin")

    assert simple_auth.validate_session_token(token) == "admin"
    assert "AUTH_SECRET_KEY" not in _read_config(config_path)
    assert _vault_value(auth_secret_backend) == secret_store.get_or_create_auth_secret()

    simple_auth.force_set_credentials("admin", "secret456")
    assert simple_auth.validate_session_token(token) is None

    simple_auth.clear_stored_credentials()
    assert auth_secret_backend.entry_count == 0
    assert simple_auth.is_auth_configured() is False
