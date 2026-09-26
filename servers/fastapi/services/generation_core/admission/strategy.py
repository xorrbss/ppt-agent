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

#: Why an *omitted* strategy resolved the way it did. "explicit" covers both
#: literal choices (the exceptions below never apply to those); any other
#: value is only possible for authored, and (besides "default", the ordinary
#: svg pick) explains why svg was skipped without a 422 the caller never
#: asked to avoid. A value outside this fixed set is one of
#: services.svg_generation_admission.svg_creation_admission_violation's own
#: structural reason codes (e.g. "svg_source_verbatim_required").
StrategyReason = Literal[
    "explicit", "registered_template", "design_pack_pin", "default"
]


def resolve_generation_strategy_with_reason(
    request: object,
) -> tuple[GenerationStrategy, str]:
    """Resolve ``request`` to ``("svg" | "authored", reason)``.

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
       ``reason`` is the violation's own code, so an operator can tell "why
       not svg" apart from an ordinary explicit authored request.
    """
    explicit = getattr(request, "strategy", None)
    if explicit in ("svg", "authored"):
        return explicit, "explicit"
    if getattr(request, "generation_mode", "auto") == "registered-template":
        return "authored", "registered_template"
    if getattr(request, "design_pack_pin", None) is not None:
        return "authored", "design_pack_pin"
    from services.svg_generation_admission import svg_creation_admission_violation

    violation = svg_creation_admission_violation(request)
    if violation is not None:
        return "authored", violation
    return "svg", "default"


def resolve_generation_strategy(request: object) -> GenerationStrategy:
    """Thin wrapper over :func:`resolve_generation_strategy_with_reason`.

    Use the ``_with_reason`` form at any admission/observability boundary
    that records *why* -- durable payloads, route receipts, async status.
    """
    strategy, _reason = resolve_generation_strategy_with_reason(request)
    return strategy


def recorded_generation_strategy(request: object) -> tuple[GenerationStrategy, str]:
    """Keep the admission reason when a durable request pins its strategy."""
    strategy, derived_reason = resolve_generation_strategy_with_reason(request)
    return strategy, getattr(request, "_generation_strategy_reason", None) or derived_reason


__all__ = [
    "GenerationStrategy",
    "StrategyReason",
    "resolve_generation_strategy",
    "resolve_generation_strategy_with_reason",
    "recorded_generation_strategy",
]
