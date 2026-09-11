"""Single accessor for the deployment session signing secret.

Every reader and writer of the signing secret (database-backed JWT auth,
the legacy single-user session tokens, and owner-scope derivation) goes
through this module so the storage location can change in one place.
"""

from __future__ import annotations

import base64
import secrets
from typing import Optional

from utils.get_env import get_user_config_path_env
from utils.user_config_store import read_user_config_file, update_user_config_file


LEGACY_AUTH_SECRET_FIELD = "AUTH_SECRET_KEY"


def _generate_secret() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("utf-8")


def _load_user_config() -> dict:
    user_config_path = get_user_config_path_env()
    if not user_config_path:
        return {}
    return read_user_config_file(user_config_path)


def _write_legacy_field(value: Optional[str]) -> None:
    user_config_path = get_user_config_path_env()
    if not user_config_path:
        raise ValueError("USER_CONFIG_PATH is not set")

    def apply(existing: dict) -> dict:
        if value is None:
            existing.pop(LEGACY_AUTH_SECRET_FIELD, None)
        else:
            existing[LEGACY_AUTH_SECRET_FIELD] = value
        return existing

    update_user_config_file(user_config_path, apply)


def peek_auth_secret() -> Optional[str]:
    """Return the current signing secret without creating one."""
    secret = _load_user_config().get(LEGACY_AUTH_SECRET_FIELD)
    if isinstance(secret, str) and secret:
        return secret
    return None


def get_or_create_auth_secret() -> str:
    """Return the signing secret, generating and persisting one if absent."""
    secret = peek_auth_secret()
    if secret is not None:
        return secret
    return rotate_auth_secret()


def rotate_auth_secret() -> str:
    """Replace the signing secret; every existing session becomes invalid."""
    secret = _generate_secret()
    _write_legacy_field(secret)
    return secret


__all__ = [
    "LEGACY_AUTH_SECRET_FIELD",
    "get_or_create_auth_secret",
    "peek_auth_secret",
    "rotate_auth_secret",
]
