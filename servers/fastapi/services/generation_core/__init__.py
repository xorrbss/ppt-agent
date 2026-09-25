"""Shared generation core, called by both drivers (svg reuses first, extracts second).

Holds the SVG driver (``drivers/svg/``, split out of
``services/svg_presentation_generate.py``) and the shared modules. A module
belongs here only once both the svg and authored drivers call it; until then
it stays behind a facade at its historical import path. See
``docs/generation-core-architecture-20260925.md`` for the target shape.
"""

from __future__ import annotations
