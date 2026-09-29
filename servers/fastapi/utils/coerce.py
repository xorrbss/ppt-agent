"""Lenient value coercion shared by the geometry and evidence readers.

Every reader of browser metrics, receipts and LLM output used to carry its
own ``_number``/``_mapping``; they disagreed on whether ``True`` is ``1.0``
and on the default for garbage.  This module fixes both invariants once:

* ``bool`` is never a number (``True`` is not ``1.0``);
* ``NaN``/``inf`` are never numbers;
* the caller names the default explicitly (``0.0``, ``math.nan``, ``None``);
* a non-mapping reads as an empty mapping.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any, TypeVar

_DefaultT = TypeVar("_DefaultT")


def finite_float(value: object, default: _DefaultT = None) -> float | _DefaultT:
    """``float(value)`` when it is a finite number, else ``default``.

    Rejects ``bool`` (a ``bool`` is an ``int`` to Python but never a
    measurement), non-numeric strings, ``None`` and non-finite values.
    Numeric strings such as ``"12.5"`` are accepted, as every copy did.
    """

    if isinstance(value, bool):
        return default
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def strip_px_number(value: object, default: _DefaultT = None) -> float | _DefaultT:
    """``finite_float`` of ``value`` with a trailing ``px`` unit removed."""

    if isinstance(value, bool) or value is None:
        return default
    return finite_float(str(value).removesuffix("px"), default)


def as_mapping(value: object) -> Mapping[str, Any]:
    """``value`` when it is a ``Mapping``, else an empty mapping."""

    return value if isinstance(value, Mapping) else {}


__all__ = ["as_mapping", "finite_float", "strip_px_number"]
