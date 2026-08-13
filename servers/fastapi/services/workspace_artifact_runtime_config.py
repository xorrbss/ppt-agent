from __future__ import annotations

import os


def configured_positive_int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
) -> int:
    """Read a positive bounded artifact lifecycle setting fail-closed."""

    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value) if value > 0 else default
