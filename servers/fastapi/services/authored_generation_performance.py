"""Shared provider-throughput policy for the Authored generation path.

The default author concurrency and its ceiling come from the provider rate
profile (``services/authored_provider_rate_profile.py``): the token profile
keeps the historical 8, the api profile takes ``AUTHORED_API_PROVIDER_CONCURRENCY_MAX``.
"""

from __future__ import annotations

from utils.get_env import env_int
import re

from services.authored_provider_rate_profile import (
    AuthoredRateProfile,
    resolve_authored_rate_profile,
)


AUTHORED_PROVIDER_CONCURRENCY_MIN = 1


def _positive_int(name: str) -> int | None:
    """Explicit positive override for ``name``, or ``None`` when unset/invalid."""
    return env_int(name, None, minimum=1)


def _provider_setting_name(provider: str | None) -> str | None:
    normalized = re.sub(r"[^A-Z0-9]+", "_", (provider or "").upper()).strip("_")
    return f"AUTHORED_PROVIDER_{normalized}_CONCURRENCY" if normalized else None


def authored_provider_concurrency(
    provider: str | None = None,
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the approved author-pool size for a selected provider.

    A provider override (``AUTHORED_PROVIDER_<PROVIDER>_CONCURRENCY``) wins;
    ``AUTHORED_PROVIDER_CONCURRENCY`` is the shared fallback; otherwise the
    profile's author concurrency (itself overridable through
    ``AUTHORED_AUTHOR_CONCURRENCY``) applies.  Every path is clamped to the
    profile ceiling so an operator cannot exceed what the profile approves.
    """

    resolved = profile if profile is not None else resolve_authored_rate_profile()
    provider_name = _provider_setting_name(provider)
    configured = _positive_int(provider_name) if provider_name else None
    if configured is None:
        configured = _positive_int("AUTHORED_PROVIDER_CONCURRENCY")
    if configured is None:
        configured = resolved.author_concurrency_default
    return max(
        AUTHORED_PROVIDER_CONCURRENCY_MIN,
        min(resolved.author_concurrency_max, configured),
    )


__all__ = [
    "AUTHORED_PROVIDER_CONCURRENCY_MIN",
    "authored_provider_concurrency",
]
