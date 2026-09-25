"""Shared generation core, called by both drivers (svg reuses first, extracts second).

See ``docs/generation-core-architecture-20260925.md`` for the target shape. A
module belongs here only once both the svg and authored drivers call it;
until then it stays in its current, driver-specific module.
"""

from __future__ import annotations
