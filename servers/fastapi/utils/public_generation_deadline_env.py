"""Runtime overrides for the named public generation deadline terms.

``services/public_generation_deadline_policy.py`` builds the operation budget
from named terms, each one sized from a measured stage wall.  Every term is
overridable at runtime so an operator can widen one without a deploy, and every
key here is registered in ``constants/settings_registry.json`` with the
``fastapi.runtime`` target.

These accessors live in their own module rather than in ``utils/get_env.py``
only because that module is already at the 500-line source limit; they follow
exactly the accessor style used there (``get_setting_env`` plus a positive-int
parse, an unusable value falling back to the code default).
"""

from __future__ import annotations

from utils.get_env import get_setting_env
from utils.runtime_settings import RuntimeSettings


def _positive_int_override(
    key: str, settings: RuntimeSettings | None
) -> int | None:
    """An explicit positive integer knob, or ``None`` when unset or invalid."""

    raw = (get_setting_env(key, settings) or "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def get_public_generation_deadline_base_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit residual allowance (deck plan, illustration, persistence)."""

    return _positive_int_override("PUBLIC_GENERATION_DEADLINE_BASE_SECONDS", settings)


def get_public_generation_deadline_outline_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit outline-stage allowance inside the preamble term."""

    return _positive_int_override(
        "PUBLIC_GENERATION_DEADLINE_OUTLINE_SECONDS", settings
    )


def get_public_generation_deadline_header_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit header-contract allowance inside the preamble term."""

    return _positive_int_override(
        "PUBLIC_GENERATION_DEADLINE_HEADER_SECONDS", settings
    )


def get_public_generation_deadline_remediation_wave_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit wall allowance for one repair/remake wave."""

    return _positive_int_override(
        "PUBLIC_GENERATION_DEADLINE_REMEDIATION_WAVE_SECONDS", settings
    )


def get_public_generation_deadline_review_per_slide_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit per-slide part of the chromium review allowance."""

    return _positive_int_override(
        "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_SLIDE_SECONDS", settings
    )


def get_public_generation_deadline_review_per_remake_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit per-remake re-render part of the chromium review allowance."""

    return _positive_int_override(
        "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_REMAKE_SECONDS", settings
    )


def get_public_generation_deadline_budgeted_wave_concurrency_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit ceiling on the wave concurrency the budget is allowed to assume."""

    return _positive_int_override(
        "PUBLIC_GENERATION_DEADLINE_BUDGETED_WAVE_CONCURRENCY", settings
    )


def get_public_generation_deadline_max_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit stuck-job ceiling on the whole generation budget."""

    return _positive_int_override("PUBLIC_GENERATION_DEADLINE_MAX_SECONDS", settings)


__all__ = [
    "get_public_generation_deadline_base_seconds_env",
    "get_public_generation_deadline_budgeted_wave_concurrency_env",
    "get_public_generation_deadline_header_seconds_env",
    "get_public_generation_deadline_max_seconds_env",
    "get_public_generation_deadline_outline_seconds_env",
    "get_public_generation_deadline_remediation_wave_seconds_env",
    "get_public_generation_deadline_review_per_remake_seconds_env",
    "get_public_generation_deadline_review_per_slide_seconds_env",
]
