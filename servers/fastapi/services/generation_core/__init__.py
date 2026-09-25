"""Shared generation-core package (svg + authored drivers).

Currently holds only the SVG driver (``drivers/svg/``), split out of
``services/svg_presentation_generate.py``. Modules move in here from
``services/`` only when both the svg and authored drivers call them; see
``docs/generation-core-architecture-20260925.md``.
"""
from __future__ import annotations
