"""Resolve the effective generation strategy: svg is the only one admitted.

Every caller in this codebase that needs to know whether a request runs the
svg or the authored pipeline calls :func:`resolve_generation_strategy` rather
than reading ``request.strategy`` directly -- the raw field stays ``None``
when the caller omitted it (that is the wire contract), and "omitted" always
means svg. See ``docs/generation-core-architecture-20260925.md`` Part A for
the original design and ``docs/authored-removal-design-20261105.md`` (P1) for
the removal of authored generation.

Authored generation is removed (owner decision 2026-11-09): no request may
start one. An explicit ``strategy="authored"`` (or the authored-only
``generation_engine="deck-ir"``) still parses, so the payload shape is
unchanged and durable authored payloads still restore, but every admission
entry point rejects it with :data:`AUTHORED_STRATEGY_REMOVED` through
:func:`require_generation_strategy_admission`, and a durable job staged with
authored before the removal fails with the same code when it is dispatched.
The resolver itself still reports an explicit ``"authored"`` verbatim, so
code that reads existing authored decks keeps working until the authored
code is deleted.
"""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

GenerationStrategy = Literal["svg", "authored"]

#: Why a request resolved the way it did. "explicit" covers both literal
#: choices; "default" is the omitted-strategy pick (always svg). Durable
#: payloads staged before the authored removal may also carry one of
#: services.svg_generation_admission.svg_creation_admission_violation's own
#: structural reason codes (e.g. "svg_source_verbatim_required"), recorded
#: when an omitted strategy used to fall back to authored silently; such a
#: job restores as authored and fails on dispatch.
StrategyReason = Literal["explicit", "default"]

#: Error code for any request that would start a new authored generation.
AUTHORED_STRATEGY_REMOVED = "authored_strategy_removed"

#: The request ``template`` value generation admission normalizes svg requests
#: to (kept for wire compatibility; it no longer selects a pipeline). Moved
#: here unchanged from ``services.authored_presentation_contracts``.
AUTHORED_TEMPLATE = "authored"


def resolve_generation_strategy_with_reason(
    request: object,
) -> tuple[GenerationStrategy, str]:
    """Resolve ``request`` to ``("svg" | "authored", reason)``.

    An explicit ``strategy`` is reported verbatim -- admission for that
    explicit choice still runs downstream and rejects it (authored is
    removed; svg admission may 422 an svg request). An omitted strategy
    always resolves to svg: an omitted-strategy request svg admission would
    reject is a 422 carrying the violation's own code
    (:func:`services.svg_generation_admission.require_svg_creation_admission`),
    never a silent switch to another pipeline.
    """
    explicit = getattr(request, "strategy", None)
    if explicit in ("svg", "authored"):
        return explicit, "explicit"
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


def removed_generation_strategy_code(request: object) -> str | None:
    """The removal code if ``request`` asks for authored generation, else ``None``.

    Covers an explicit ``strategy="authored"`` and the authored-only
    ``generation_engine="deck-ir"``. Field-only: it never resolves styles or
    touches durable state, so every entry point can call it first.
    """
    if getattr(request, "strategy", None) == "authored":
        return AUTHORED_STRATEGY_REMOVED
    if getattr(request, "generation_engine", None) == "deck-ir":
        return AUTHORED_STRATEGY_REMOVED
    return None


def require_generation_strategy_admission(request: object) -> None:
    """Reject a request that would start a new authored generation (HTTP 422).

    Also used when a durable job restored from a payload staged before the
    removal is dispatched: the exception carries the stable ``code`` and is
    not retryable, so the job's recorded error names the removal.
    """
    code = removed_generation_strategy_code(request)
    if code is not None:
        error = HTTPException(status_code=422, detail=code)
        error.code = code
        error.retryable = False
        raise error


__all__ = [
    "AUTHORED_STRATEGY_REMOVED",
    "AUTHORED_TEMPLATE",
    "GenerationStrategy",
    "StrategyReason",
    "recorded_generation_strategy",
    "removed_generation_strategy_code",
    "require_generation_strategy_admission",
    "resolve_generation_strategy",
    "resolve_generation_strategy_with_reason",
]
