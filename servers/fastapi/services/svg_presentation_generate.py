"""Facade: the per-deck SVG generation loop moved to
``services/generation_core/drivers/svg/`` (``author.py``, ``render.py``,
``collect.py``; P0 split, see ``docs/generation-core-architecture-20260925.md``).

``_generate`` stays importable from here for existing callers and tests --
see ``collect.py``'s docstring for how monkeypatching an attribute on
``services.svg_presentation_service`` still reaches the split-out modules.
"""
from __future__ import annotations

from services.generation_core.drivers.svg.collect import _generate

__all__ = ["_generate"]
