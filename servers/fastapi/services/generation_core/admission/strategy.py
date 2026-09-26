"""Resolve the effective generation strategy: svg is the default, authored the fallback.

Every caller in this codebase that needs to know whether a request runs the
svg or the authored pipeline calls :func:`resolve_generation_strategy` rather
than reading ``request.strategy`` directly -- the raw field stays ``None``
when the caller omitted it (that is the wire contract), but "omitted" no
longer means authored. See ``docs/generation-core-architecture-20260925.md``
Part A for the design and the exceptions below.
"""
from __future__ import annotations

from typing import Literal

GenerationStrategy = Literal["svg", "authored"]


def resolve_generation_strategy(request: object) -> GenerationStrategy:
    """Resolve ``request`` to ``"svg"`` or ``"authored"``.

    An explicit ``strategy`` is always honoured verbatim -- admission for
    that explicit choice (e.g. :func:`services.svg_generation_admission.require_svg_creation_admission`)
    still runs downstream and may 422 it. Only the *omitted* case is resolved
    here, and only for that case do the exceptions below apply:

    1. ``generation_mode == "registered-template"`` or a ``design_pack_pin``:
       design packs patch an element of a source-native document, so they
       cannot project onto from-scratch svg authoring.
    2. Any other combination svg admission would reject (checked structurally,
       not by source_mode alone) falls back to authored silently -- the new
       default must never turn a previously-valid authored request into a 422.
    """
    explicit = getattr(request, "strategy", None)
    if explicit in ("svg", "authored"):
        return explicit
    if getattr(request, "generation_mode", "auto") == "registered-template":
        return "authored"
    if getattr(request, "design_pack_pin", None) is not None:
        return "authored"
    from services.svg_generation_admission import svg_creation_admission_violation

    if svg_creation_admission_violation(request) is not None:
        return "authored"
    return "svg"


__all__ = ["GenerationStrategy", "resolve_generation_strategy"]
