"""Shared provider-throughput policy for the Authored generation path."""

from __future__ import annotations

import os
import re


AUTHORED_PROVIDER_CONCURRENCY_MIN = 5
AUTHORED_PROVIDER_CONCURRENCY_MAX = 8
AUTHORED_PROVIDER_CONCURRENCY_DEFAULT = 5


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int((value or "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _provider_setting_name(provider: str | None) -> str | None:
    normalized = re.sub(r"[^A-Z0-9]+", "_", (provider or "").upper()).strip("_")
    return f"AUTHORED_PROVIDER_{normalized}_CONCURRENCY" if normalized else None


def authored_provider_concurrency(provider: str | None = None) -> int:
    """Return the one approved 5..8 cap for a selected provider.

    A provider override (``AUTHORED_PROVIDER_<PROVIDER>_CONCURRENCY``) wins;
    ``AUTHORED_PROVIDER_CONCURRENCY`` is the shared fallback.  The old separate
    author/pipeline settings intentionally do not participate.
    """

    provider_name = _provider_setting_name(provider)
    configured = _positive_int(os.getenv(provider_name)) if provider_name else None
    if configured is None:
        configured = _positive_int(os.getenv("AUTHORED_PROVIDER_CONCURRENCY"))
    if configured is None:
        return AUTHORED_PROVIDER_CONCURRENCY_DEFAULT
    return max(
        AUTHORED_PROVIDER_CONCURRENCY_MIN,
        min(AUTHORED_PROVIDER_CONCURRENCY_MAX, configured),
    )


__all__ = [
    "AUTHORED_PROVIDER_CONCURRENCY_DEFAULT",
    "AUTHORED_PROVIDER_CONCURRENCY_MAX",
    "AUTHORED_PROVIDER_CONCURRENCY_MIN",
    "authored_provider_concurrency",
]
