from __future__ import annotations

from utils.env_settings.workspace_maintenance import positive_int_knob


def configured_positive_int(
    name: str,
    default: int,
    *,
    minimum: int = 1,
) -> int:
    """Read a positive bounded artifact lifecycle setting fail-closed."""

    return positive_int_knob(name, default, minimum=minimum)
