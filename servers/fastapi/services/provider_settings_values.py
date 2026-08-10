from typing import Any

from pydantic import ValidationError

from constants.settings_registry import get_settings_registry
from models.user_config import UserConfig
from utils.runtime_settings import coerce_runtime_setting


CODEX_MANAGED_FIELDS = {
    "CODEX_ACCESS_TOKEN",
    "CODEX_REFRESH_TOKEN",
    "CODEX_TOKEN_EXPIRES",
    "CODEX_ACCOUNT_ID",
    "CODEX_USERNAME",
    "CODEX_EMAIL",
    "CODEX_IS_PRO",
}
EMPTY_VALUE_PRESERVED_FIELDS = {
    "OPEN_WEBUI_IMAGE_URL",
    "OPEN_WEBUI_IMAGE_API_KEY",
    "CODEX_MODEL",
}
PROVIDER_SETTING_KEYS = frozenset(
    entry["key"]
    for entry in get_settings_registry()
    if {
        "start.user_config",
        "user_config.managed",
        "user_config.ui",
    }.intersection(entry["targets"])
)
SECRET_SETTING_KEYS = frozenset(
    entry["key"] for entry in get_settings_registry() if entry["secret"]
)
REDACTED_SECRET_PLACEHOLDER = "__configured__"


class ProviderSettingsValidationError(ValueError):
    def __init__(self, key: str):
        super().__init__(f"Invalid provider setting: {key}")


def sanitize_provider_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Allow only registry-declared provider settings and validate their types."""
    filtered = {
        key: value
        for key, value in config.items()
        if key in PROVIDER_SETTING_KEYS
    }
    try:
        validated = UserConfig(**filtered).model_dump(exclude_none=True)
    except ValidationError as error:
        location = error.errors()[0].get("loc", ("unknown",))
        raise ProviderSettingsValidationError(str(location[0])) from None

    result: dict[str, Any] = {}
    for key, value in validated.items():
        try:
            coerced = coerce_runtime_setting(key, value)
        except (TypeError, ValueError):
            raise ProviderSettingsValidationError(key) from None
        result[key] = value if value == "" else coerced
    return result


def redact_provider_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return API-safe settings with configured secrets represented write-only."""
    sanitized = sanitize_provider_settings(config)
    return {
        key: REDACTED_SECRET_PLACEHOLDER
        if key in SECRET_SETTING_KEYS and value
        else value
        for key, value in sanitized.items()
    }


def merge_provider_settings(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Preserve the previous settings API's patch and managed-token behavior."""
    sanitized_existing = sanitize_provider_settings(existing)
    sanitized = sanitize_provider_settings(incoming)
    merged = {**sanitized_existing, **sanitized}

    for key in SECRET_SETTING_KEYS:
        if sanitized.get(key) == REDACTED_SECRET_PLACEHOLDER:
            existing_secret = sanitized_existing.get(key)
            if existing_secret and existing_secret != REDACTED_SECRET_PLACEHOLDER:
                merged[key] = existing_secret
            else:
                merged.pop(key, None)
        elif (
            key not in sanitized
            and sanitized_existing.get(key) == REDACTED_SECRET_PLACEHOLDER
        ):
            # The placeholder is an API representation, never a credential. If
            # an older writer persisted it, clean it up on the next update.
            merged.pop(key, None)

    for key in CODEX_MANAGED_FIELDS:
        if key in existing:
            merged[key] = existing[key]
        else:
            merged.pop(key, None)

    for key in EMPTY_VALUE_PRESERVED_FIELDS:
        if not sanitized.get(key) and key in existing:
            merged[key] = existing[key]

    return merged


def sanitize_provider_settings_for_storage(
    config: dict[str, Any],
) -> dict[str, Any]:
    sanitized = sanitize_provider_settings(config)
    return {
        key: value
        for key, value in sanitized.items()
        if not (
            key in SECRET_SETTING_KEYS
            and value == REDACTED_SECRET_PLACEHOLDER
        )
    }
