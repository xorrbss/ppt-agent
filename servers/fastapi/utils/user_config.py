from __future__ import annotations

import os
from typing import Any, Mapping

from constants.settings_registry import get_settings_registry
from models.user_config import UserConfig
from utils.runtime_settings import (
    RuntimeSettings,
    replace_process_runtime_settings,
    resolve_runtime_settings,
)
from utils.user_config_store import read_user_config_file, update_user_config_file


_PERSISTED_TARGETS = {
    "start.user_config",
    "user_config.managed",
    "user_config.ui",
}
_PERSISTED_KEYS = tuple(
    entry["key"]
    for entry in get_settings_registry()
    if _PERSISTED_TARGETS.intersection(entry["targets"])
)
_CODEX_MANAGED_KEYS = frozenset(
    entry["key"]
    for entry in get_settings_registry()
    if "user_config.managed" in entry["targets"]
)


def _read_persisted_user_config(
    environ: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    environment = os.environ if environ is None else environ
    path = environment.get("USER_CONFIG_PATH")
    if not path:
        return {}
    try:
        return read_user_config_file(str(path))
    except Exception:
        # Do not include paths, values, or secrets in startup/request logs.
        return {}


def load_runtime_settings(
    environ: Mapping[str, object] | None = None,
    *,
    persisted_config: Mapping[str, object] | None = None,
) -> RuntimeSettings:
    """Load one immutable process/request snapshot without changing the environment."""
    environment = os.environ if environ is None else environ
    environment_only = RuntimeSettings.from_sources(environ=environment)
    can_change_keys = environment_only.get("CAN_CHANGE_KEYS") is not False
    persisted = (
        _read_persisted_user_config(environment)
        if persisted_config is None and can_change_keys
        else (persisted_config or {})
    )
    if not can_change_keys:
        persisted = {}
    return RuntimeSettings.from_sources(environ=environment, user_config=persisted)


def refresh_process_runtime_settings(
    environ: Mapping[str, object] | None = None,
    *,
    persisted_config: Mapping[str, object] | None = None,
    overrides: Mapping[str, object] | None = None,
) -> RuntimeSettings:
    """Publish a newly loaded process snapshot at startup or after an admin write."""
    snapshot = load_runtime_settings(
        environ,
        persisted_config=persisted_config,
    )
    if overrides:
        snapshot = snapshot.with_overrides(overrides)
    replace_process_runtime_settings(snapshot)
    return snapshot


def get_user_config(
    settings: RuntimeSettings | None = None,
    *,
    environ: Mapping[str, object] | None = None,
) -> UserConfig:
    """Return the persisted provider shape while preserving historical precedence."""
    environment = os.environ if environ is None else environ
    persisted = _read_persisted_user_config(environment)
    active = resolve_runtime_settings(settings) if settings is not None else None
    values: dict[str, Any] = {}
    for key in _PERSISTED_KEYS:
        value = persisted.get(key)
        if value is None or value == "":
            value = active.get(key) if active is not None else environment.get(key)
        if value is not None and value != "":
            values[key] = value
    return UserConfig(**values)


def save_codex_tokens_to_user_config(values: Mapping[str, object]) -> None:
    """Persist managed Codex fields without staging them in ``os.environ``."""
    unknown = set(values) - _CODEX_MANAGED_KEYS
    if unknown:
        raise ValueError("only managed Codex settings may be persisted by this path")

    path = os.getenv("USER_CONFIG_PATH")
    if not path:
        return

    def merge_codex_tokens(existing: dict[str, Any]) -> dict[str, Any]:
        for key in _CODEX_MANAGED_KEYS:
            if key in values:
                value = values[key]
                if value is None or value == "":
                    existing.pop(key, None)
                else:
                    existing[key] = value
        return existing

    update_user_config_file(path, merge_codex_tokens)
    from services.provider_settings import sync_legacy_file_to_provider_settings

    sync_legacy_file_to_provider_settings()
    refresh_process_runtime_settings()
