"""Authored publication feature flags kept behind the get_env facade."""

from __future__ import annotations

from utils.parsers import parse_bool_or_none
from utils.runtime_settings import RuntimeSettings


def is_authored_publish_flagged_slides_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Whether a terminal slide may be published flagged after one remake."""
    from utils.get_env import get_setting_env

    parsed = parse_bool_or_none(get_setting_env("AUTHORED_PUBLISH_FLAGGED_SLIDES", settings))
    return True if parsed is None else parsed


def is_authored_publish_flagged_slides_source_reflow_enabled(settings: RuntimeSettings | None = None) -> bool:
    """Whether source-reflow decks may publish terminal slides flagged."""
    from utils.get_env import get_setting_env

    parsed = parse_bool_or_none(
        get_setting_env("AUTHORED_PUBLISH_FLAGGED_SLIDES_SOURCE_REFLOW", settings)
    )
    return False if parsed is None else parsed
