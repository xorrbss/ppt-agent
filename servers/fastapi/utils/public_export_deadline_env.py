"""Runtime overrides for the named public export deadline terms.

``services/public_export_deadline_policy.py`` builds the export budget from
named terms sized from a measured hybrid export wall.  Every term is
overridable at runtime so an operator can widen one without a deploy, and
every key here is registered in ``constants/settings_registry.json`` with the
``fastapi.runtime`` target.

These accessors live beside ``utils/public_generation_deadline_env.py`` rather
than in ``utils/get_env.py`` only because that module is at the 500-line
source limit; they reuse the same positive-int parse (an unusable value falls
back to the code default).
"""

from __future__ import annotations

from utils.public_generation_deadline_env import _positive_int_override
from utils.runtime_settings import RuntimeSettings


def get_public_export_deadline_per_slide_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit per-slide allowance for the isolated export worker."""

    return _positive_int_override(
        "PUBLIC_EXPORT_DEADLINE_PER_SLIDE_SECONDS", settings
    )


def get_public_export_deadline_base_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit fixed allowance (bundle, worker launch, verify, publish)."""

    return _positive_int_override("PUBLIC_EXPORT_DEADLINE_BASE_SECONDS", settings)


def get_public_export_deadline_min_seconds_env(
    settings: RuntimeSettings | None = None,
) -> int | None:
    """Explicit floor for a small or unknown-size deck."""

    return _positive_int_override("PUBLIC_EXPORT_DEADLINE_MIN_SECONDS", settings)


__all__ = [
    "get_public_export_deadline_base_seconds_env",
    "get_public_export_deadline_min_seconds_env",
    "get_public_export_deadline_per_slide_seconds_env",
]
