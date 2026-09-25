"""Facade: ``_publish`` moved to ``services/generation_core/drivers/svg/publish.py``.

Kept importable from here for existing callers and tests (see that module's
docstring).
"""
from __future__ import annotations

from services.generation_core.drivers.svg.publish import _publish

__all__ = ["_publish"]
