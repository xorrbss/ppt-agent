from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


REGISTRY_PATH = Path(__file__).with_name("settings_registry.json")
KNOWN_SETTING_TYPES = {"string", "boolean", "integer", "number", "json", "enum"}
KNOWN_TARGETS = {
    "fastapi.runtime",
    "start.process",
    "start.user_config",
    "compose.shared",
    "docs.public",
    "user_config.managed",
    "user_config.ui",
}
_SETTING_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_SETTING_FIELDS = {
    "key",
    "type",
    "default",
    "secret",
    "targets",
    "group",
    "description",
    "choices",
}


class SettingsRegistryError(ValueError):
    pass


def _validate_registry(payload: object) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, dict) or set(payload) != {"schemaVersion", "settings"}:
        raise SettingsRegistryError("settings registry must contain schemaVersion and settings")
    if payload["schemaVersion"] != 1:
        raise SettingsRegistryError("unsupported settings registry schemaVersion")
    raw_settings = payload["settings"]
    if not isinstance(raw_settings, list):
        raise SettingsRegistryError("settings registry settings must be a list")

    seen: set[str] = set()
    validated: list[Mapping[str, Any]] = []
    for raw in raw_settings:
        if not isinstance(raw, dict):
            raise SettingsRegistryError("every settings registry entry must be an object")
        unknown_fields = set(raw) - _SETTING_FIELDS
        required_fields = _SETTING_FIELDS - {"choices"}
        if unknown_fields or not required_fields.issubset(raw):
            raise SettingsRegistryError("settings registry entry fields are invalid")

        key = raw["key"]
        setting_type = raw["type"]
        targets = raw["targets"]
        if not isinstance(key, str) or not _SETTING_KEY_PATTERN.fullmatch(key):
            raise SettingsRegistryError("settings registry key is invalid")
        if key in seen:
            raise SettingsRegistryError(f"duplicate settings registry key: {key}")
        seen.add(key)
        if setting_type not in KNOWN_SETTING_TYPES:
            raise SettingsRegistryError(f"unsupported type for settings registry key: {key}")
        if not isinstance(raw["secret"], bool):
            raise SettingsRegistryError(f"secret flag must be boolean for settings registry key: {key}")
        if raw["secret"] and raw["default"] is not None:
            raise SettingsRegistryError(f"secret settings cannot define a default: {key}")
        if not isinstance(targets, list) or not targets or any(
            target not in KNOWN_TARGETS for target in targets
        ):
            raise SettingsRegistryError(f"targets are invalid for settings registry key: {key}")
        if len(set(targets)) != len(targets):
            raise SettingsRegistryError(f"targets contain duplicates for settings registry key: {key}")
        choices = raw.get("choices")
        if setting_type == "enum":
            if not isinstance(choices, list) or not choices or any(
                not isinstance(choice, str) for choice in choices
            ):
                raise SettingsRegistryError(f"enum choices are invalid for settings registry key: {key}")
            if raw["default"] is not None and raw["default"] not in choices:
                raise SettingsRegistryError(f"enum default is invalid for settings registry key: {key}")
        elif choices is not None:
            raise SettingsRegistryError(f"choices are only valid for enum settings: {key}")
        if not isinstance(raw["group"], str) or not raw["group"]:
            raise SettingsRegistryError(f"group is invalid for settings registry key: {key}")
        if not isinstance(raw["description"], str) or not raw["description"]:
            raise SettingsRegistryError(f"description is invalid for settings registry key: {key}")

        validated.append(MappingProxyType(dict(raw)))
    return tuple(validated)


@lru_cache(maxsize=1)
def get_settings_registry() -> tuple[Mapping[str, Any], ...]:
    with REGISTRY_PATH.open("r", encoding="utf-8") as registry_file:
        return _validate_registry(json.load(registry_file))


@lru_cache(maxsize=1)
def get_settings_by_key() -> Mapping[str, Mapping[str, Any]]:
    return MappingProxyType(
        {entry["key"]: entry for entry in get_settings_registry()}
    )


def get_setting_default(key: str) -> Any:
    return get_settings_by_key()[key]["default"]


def get_setting_choices(key: str) -> tuple[str, ...]:
    choices = get_settings_by_key()[key].get("choices") or ()
    return tuple(choices)


def get_setting_keys_for_target(target: str) -> tuple[str, ...]:
    if target not in KNOWN_TARGETS:
        raise SettingsRegistryError(f"unknown settings registry target: {target}")
    return tuple(
        entry["key"]
        for entry in get_settings_registry()
        if target in entry["targets"]
    )
