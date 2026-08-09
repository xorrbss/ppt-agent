from __future__ import annotations

from pydantic import BaseModel, ConfigDict, create_model

from constants.settings_registry import get_settings_registry


_PERSISTED_TARGETS = {
    "start.user_config",
    "user_config.managed",
    "user_config.ui",
}
_PYTHON_TYPES = {
    "string": str,
    "enum": str,
    "json": str,
    "boolean": bool,
    "integer": int,
    "number": float,
}


class _UserConfigBase(BaseModel):
    model_config = ConfigDict(extra="ignore")


UserConfig = create_model(
    "UserConfig",
    __base__=_UserConfigBase,
    __module__=__name__,
    **{
        entry["key"]: (_PYTHON_TYPES[entry["type"]] | None, None)
        for entry in get_settings_registry()
        if _PERSISTED_TARGETS.intersection(entry["targets"])
    },
)
