from __future__ import annotations

import asyncio
import json
import os
import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Coroutine, Iterator, Mapping, TypeVar

from constants.settings_registry import get_settings_by_key, get_settings_registry


SettingValue = str | bool | int | float | None
_TaskResult = TypeVar("_TaskResult")


def _parse_boolean(value: object, *, key: str) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    # Preserve the historical env contract: unrecognized boolean text behaves
    # as unset/false. Admin/provider writes are validated by the Pydantic model.
    return None


def _coerce_value(entry: Mapping[str, Any], value: object) -> SettingValue:
    key = entry["key"]
    setting_type = entry["type"]
    if value is None or value == "":
        return None
    if setting_type == "boolean":
        return _parse_boolean(value, key=key)
    if setting_type == "integer":
        if isinstance(value, bool):
            raise ValueError(f"invalid integer value for runtime setting: {key}")
        return int(value)
    if setting_type == "number":
        if isinstance(value, bool):
            raise ValueError(f"invalid number value for runtime setting: {key}")
        return float(value)
    if setting_type == "json":
        if isinstance(value, str):
            json.loads(value)
            return value
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    normalized = str(value)
    if setting_type == "enum" and normalized not in entry["choices"]:
        raise ValueError(f"invalid enum value for runtime setting: {key}")
    return normalized


def coerce_runtime_setting(key: str, value: object) -> SettingValue:
    """Validate and coerce one registry value without exposing its contents."""
    entry = get_settings_by_key().get(key)
    if entry is None:
        raise KeyError(f"unknown runtime setting: {key}")
    return _coerce_value(entry, value)


def _has_override(value: object) -> bool:
    return value is not None and value != ""


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """An immutable, request/task-scoped snapshot of provider settings."""

    _values: Mapping[str, SettingValue]

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))

    @classmethod
    def from_sources(
        cls,
        *,
        environ: Mapping[str, object] | None = None,
        user_config: Mapping[str, object] | None = None,
    ) -> "RuntimeSettings":
        environment = os.environ if environ is None else environ
        persisted = user_config or {}
        values: dict[str, SettingValue] = {}
        for entry in get_settings_registry():
            if "fastapi.runtime" not in entry["targets"]:
                continue
            key = entry["key"]
            value: object = environment.get(key)
            if not _has_override(value):
                value = entry["default"]
            if _has_override(persisted.get(key)) or persisted.get(key) is False:
                value = persisted[key]
            values[key] = coerce_runtime_setting(key, value)
        return cls(values)

    def get(self, key: str, default: SettingValue = None) -> SettingValue:
        return self._values.get(key, default)

    def get_env(self, key: str) -> str | None:
        value = self._values.get(key)
        if value is None:
            return None
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def as_dict(self, *, include_secrets: bool = True) -> dict[str, SettingValue]:
        settings_by_key = get_settings_by_key()
        return {
            key: value
            for key, value in self._values.items()
            if include_secrets or not settings_by_key[key]["secret"]
        }

    def with_overrides(self, overrides: Mapping[str, object]) -> "RuntimeSettings":
        settings_by_key = get_settings_by_key()
        values = dict(self._values)
        for key, value in overrides.items():
            entry = settings_by_key.get(key)
            if entry is None or "fastapi.runtime" not in entry["targets"]:
                raise KeyError(f"unknown runtime setting: {key}")
            values[key] = coerce_runtime_setting(key, value)
        return RuntimeSettings(values)


@dataclass(frozen=True, slots=True)
class RuntimeSettingsEnvelope:
    """One immutable, database-versioned process publication."""

    revision: int
    settings: RuntimeSettings


class RuntimeSettingsInvariantError(RuntimeError):
    """Raised when one database revision is observed with two payloads."""


class RuntimeSettingsPublishOutcome:
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    NO_CHANGE = "no_change"


_current_runtime_settings: ContextVar[RuntimeSettings | None] = ContextVar(
    "current_runtime_settings", default=None
)
_process_runtime_settings_envelope: RuntimeSettingsEnvelope | None = None
_process_runtime_settings_lock = threading.Lock()


def get_process_runtime_settings_envelope() -> RuntimeSettingsEnvelope | None:
    return _process_runtime_settings_envelope


def get_published_process_runtime_settings() -> RuntimeSettings | None:
    """Return the published snapshot without synthesizing an env fallback."""
    envelope = _process_runtime_settings_envelope
    return envelope.settings if envelope is not None else None


def get_process_runtime_settings() -> RuntimeSettings:
    envelope = _process_runtime_settings_envelope
    return (
        envelope.settings
        if envelope is not None
        else RuntimeSettings.from_sources(environ=os.environ)
    )


def publish_process_runtime_settings(
    revision: int,
    settings: RuntimeSettings,
) -> str:
    """Publish a monotonic database snapshot as one atomic envelope assignment."""
    if revision < 0:
        raise ValueError("runtime settings revision must be non-negative")

    global _process_runtime_settings_envelope
    with _process_runtime_settings_lock:
        current = _process_runtime_settings_envelope
        if current is not None:
            if revision < current.revision:
                return RuntimeSettingsPublishOutcome.OUT_OF_ORDER
            if revision == current.revision:
                if settings == current.settings:
                    return RuntimeSettingsPublishOutcome.DUPLICATE
                # Never include either snapshot in the error: both may contain
                # credentials, private URLs, or local paths.
                raise RuntimeSettingsInvariantError(
                    "runtime settings revision payload mismatch"
                )
        _process_runtime_settings_envelope = RuntimeSettingsEnvelope(
            revision=revision,
            settings=settings,
        )
        return RuntimeSettingsPublishOutcome.APPLIED


def replace_process_runtime_settings(
    settings: RuntimeSettings | None,
) -> RuntimeSettings | None:
    """Compatibility helper for unversioned standalone/test publications.

    Database-coordinated processes use :func:`publish_process_runtime_settings`.
    This helper deliberately preserves the current revision while replacing or
    clearing the envelope so older standalone callers cannot manufacture a DB
    revision.
    """
    global _process_runtime_settings_envelope
    with _process_runtime_settings_lock:
        previous_envelope = _process_runtime_settings_envelope
        revision = previous_envelope.revision if previous_envelope else 0
        _process_runtime_settings_envelope = (
            RuntimeSettingsEnvelope(revision=revision, settings=settings)
            if settings is not None
            else None
        )
    return previous_envelope.settings if previous_envelope else None


def resolve_runtime_settings(
    settings: RuntimeSettings | None = None,
) -> RuntimeSettings:
    return settings or _current_runtime_settings.get() or get_process_runtime_settings()


def set_current_runtime_settings(settings: RuntimeSettings) -> Token[RuntimeSettings | None]:
    return _current_runtime_settings.set(settings)


def reset_current_runtime_settings(token: Token[RuntimeSettings | None]) -> None:
    _current_runtime_settings.reset(token)


@contextmanager
def runtime_settings_context(
    settings: RuntimeSettings | None = None,
) -> Iterator[RuntimeSettings]:
    snapshot = resolve_runtime_settings(settings)
    token = set_current_runtime_settings(snapshot)
    try:
        yield snapshot
    finally:
        reset_current_runtime_settings(token)


def create_runtime_settings_task(
    coroutine: Coroutine[Any, Any, _TaskResult],
    settings: RuntimeSettings | None = None,
) -> asyncio.Task[_TaskResult]:
    """Create a task that owns one immutable snapshot for its entire lifetime."""
    snapshot = resolve_runtime_settings(settings)

    async def runner() -> _TaskResult:
        with runtime_settings_context(snapshot):
            return await coroutine

    return asyncio.create_task(runner())
