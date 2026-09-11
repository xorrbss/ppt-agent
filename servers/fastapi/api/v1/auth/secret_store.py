"""Single accessor for the deployment session signing secret.

The secret lives in the credential vault selected by
``PROVIDER_CREDENTIAL_BACKEND`` (Windows DPAPI for desktop/dev, AWS Secrets
Manager for operations) under one stable key.  The legacy ``AUTH_SECRET_KEY``
field of the user config file is consulted only to migrate a pre-vault value
into the vault on first read; afterwards the vault is authoritative.

Without a configured vault every operation fails closed with
:class:`AuthSecretError` instead of minting an in-memory secret that would
silently diverge from the other processes of the deployment.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from typing import Callable, Optional

from services.provider_credential_vault import (
    CredentialBackend,
    CredentialRef,
    CredentialVaultError,
    SecretValue,
    credential_backend_from_environment,
)
from utils.get_env import get_user_config_path_env
from utils.user_config_store import read_user_config_file, update_user_config_file


AUTH_SECRET_VAULT_KEY = "auth/session-signing-secret/v1"
LEGACY_AUTH_SECRET_FIELD = "AUTH_SECRET_KEY"
# Bounds how long a rotation done by another process on the same vault stays
# invisible here; in-process rotation invalidates immediately.
AUTH_SECRET_CACHE_SECONDS = 60.0

AUTH_SECRET_VAULT_UNAVAILABLE = "auth_secret_vault_unavailable"
AUTH_SECRET_VAULT_READ_FAILED = "auth_secret_vault_read_failed"
AUTH_SECRET_VAULT_WRITE_FAILED = "auth_secret_vault_write_failed"

_VAULT_KEY_ID = hashlib.sha256(AUTH_SECRET_VAULT_KEY.encode("utf-8")).hexdigest()[:32]


class AuthSecretError(RuntimeError):
    """A stable, non-sensitive signing-secret boundary error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


_backend_factory: Callable[[], CredentialBackend] = credential_backend_from_environment
_lock = threading.Lock()
_cached_secret: Optional[str] = None
_cached_until: float = 0.0


def reset_auth_secret_cache() -> None:
    with _lock:
        _remember(None)


def _remember(secret: Optional[str]) -> Optional[str]:
    global _cached_secret, _cached_until
    _cached_secret = secret
    _cached_until = time.monotonic() + AUTH_SECRET_CACHE_SECONDS if secret else 0.0
    return secret


def _cached() -> Optional[str]:
    return _cached_secret if time.monotonic() < _cached_until else None


def _generate_secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("utf-8")


def _backend() -> CredentialBackend:
    try:
        return _backend_factory()
    except CredentialVaultError as error:
        raise AuthSecretError(AUTH_SECRET_VAULT_UNAVAILABLE) from error


def vault_reference(backend: CredentialBackend) -> CredentialRef:
    """The one stable vault entry that holds the signing secret."""
    return CredentialRef(
        backend=backend.backend_id,
        tenant_id="system",
        user_id="auth",
        provider_id="session-signing-secret",
        key_id=_VAULT_KEY_ID,
    )


def _vault_get(backend: CredentialBackend) -> Optional[str]:
    try:
        value = backend.get(vault_reference(backend))
    except CredentialVaultError as error:
        raise AuthSecretError(AUTH_SECRET_VAULT_READ_FAILED) from error
    return value.reveal() if value is not None else None


def _vault_put(backend: CredentialBackend, secret: str) -> str:
    """Write `secret`; if another process won the race, its value wins."""
    try:
        backend.put(vault_reference(backend), SecretValue(secret))
        return secret
    except CredentialVaultError as error:
        existing = _vault_get(backend)
        if existing is None:
            raise AuthSecretError(AUTH_SECRET_VAULT_WRITE_FAILED) from error
        return existing


def _vault_delete(backend: CredentialBackend) -> None:
    try:
        backend.delete(vault_reference(backend))
    except CredentialVaultError as error:
        raise AuthSecretError(AUTH_SECRET_VAULT_WRITE_FAILED) from error


def _legacy_secret() -> Optional[str]:
    user_config_path = get_user_config_path_env()
    if not user_config_path:
        return None
    value = read_user_config_file(user_config_path).get(LEGACY_AUTH_SECRET_FIELD)
    return value if isinstance(value, str) and value else None


def _drop_legacy_field() -> None:
    """Remove a stale plaintext copy so it can never be migrated again."""
    if _legacy_secret() is None:
        return

    def apply(existing: dict) -> dict:
        existing.pop(LEGACY_AUTH_SECRET_FIELD, None)
        return existing

    update_user_config_file(get_user_config_path_env(), apply)


def _load_secret(backend: CredentialBackend) -> Optional[str]:
    secret = _vault_get(backend)
    if secret is not None:
        return secret
    legacy = _legacy_secret()
    return _vault_put(backend, legacy) if legacy is not None else None


def peek_auth_secret() -> Optional[str]:
    """Return the current signing secret without creating one."""
    with _lock:
        return _cached() or _remember(_load_secret(_backend()))


def get_or_create_auth_secret() -> str:
    """Return the signing secret, generating and storing one if absent."""
    with _lock:
        cached = _cached()
        if cached is not None:
            return cached
        backend = _backend()
        secret = _load_secret(backend)
        if secret is None:
            secret = _vault_put(backend, _generate_secret())
        _remember(secret)
        return secret


def rotate_auth_secret() -> str:
    """Replace the signing secret; every existing session becomes invalid."""
    with _lock:
        backend = _backend()
        _remember(None)
        _vault_delete(backend)
        secret = _vault_put(backend, _generate_secret())
        _drop_legacy_field()
        _remember(secret)
        return secret


def clear_auth_secret() -> None:
    """Forget the signing secret; the next read or login creates a new one."""
    with _lock:
        backend = _backend()
        _remember(None)
        _vault_delete(backend)
        _drop_legacy_field()


__all__ = [
    "AUTH_SECRET_VAULT_KEY",
    "AuthSecretError",
    "LEGACY_AUTH_SECRET_FIELD",
    "clear_auth_secret",
    "get_or_create_auth_secret",
    "peek_auth_secret",
    "reset_auth_secret_cache",
    "rotate_auth_secret",
    "vault_reference",
]
