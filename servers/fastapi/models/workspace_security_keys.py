"""Credential-key guards for workspace contracts."""

from __future__ import annotations

import re
from typing import Any


_SENSITIVE_WORKSPACE_KEYS = {
    "access_key",
    "access_token",
    "api_key",
    "auth_token",
    "authorization",
    "bearer_token",
    "cookie",
    "cookies",
    "credential",
    "credentials",
    "password",
    "passwd",
    "private_key",
    "refresh_token",
    "secret",
    "secret_key",
    "session_cookie",
    "token",
}


def canonical_workspace_key(value: object) -> str:
    """Normalize separators and CamelCase before applying security policies."""

    raw = str(value).strip()
    # Both acronym boundaries (``APIKey``) and ordinary camel case
    # (``accessToken``) must normalize to the same tokens as snake/kebab case.
    snake = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", raw)
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", snake)
    return re.sub(r"[^a-zA-Z0-9]+", "_", snake).strip("_").lower()


def is_sensitive_workspace_key(value: object) -> bool:
    """Return whether a request/result key may contain authentication material."""

    normalized = canonical_workspace_key(value)
    if normalized in _SENSITIVE_WORKSPACE_KEYS:
        return True
    return any(
        normalized.endswith(f"_{suffix}")
        for suffix in _SENSITIVE_WORKSPACE_KEYS
    )


def _reject_sensitive_workspace_keys(value: Any) -> Any:
    if isinstance(value, dict):
        for key, child in value.items():
            if is_sensitive_workspace_key(key):
                raise ValueError("workspace metadata must not contain credentials")
            _reject_sensitive_workspace_keys(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _reject_sensitive_workspace_keys(child)
    return value
