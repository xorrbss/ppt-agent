"""Output budget and reasoning-effort resolution for one authoring call.

Extracted from ``services.generation_core.authoring.author_slide`` (which is above the file-size
ratchet) so both the single-slide and the batched authoring paths resolve the
same profile from one place.

D18 / G9 of ``docs/authored-quality-speed-programme-design-20260905.md``: the
fast roles (cover, section, agenda, closing) keep the full 16k output budget --
truncating them was blamed for the cover overlap defect. Authored slides now use
medium reasoning consistently so fixed-canvas layout decisions receive the same
reasoning budget across roles.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# A bespoke full-bleed HTML slide with inline CSS is large; give the model room
# so the document is never truncated mid-markup (truncation => broken render).
AUTHOR_MAX_TOKENS = 16000

# Kept here, next to the profile that reads it, so this module never imports
# ``author_slide`` back.
EXACT_RECONSTRUCTION = "exact_reconstruction"

# Fixed-canvas HTML requires the model to reconcile content density, component
# geometry, and typography. Medium is the authored default for every role.
DEFAULT_REASONING_EFFORT = "medium"

# codex (gpt-5.x) reasoning-effort levels we forward verbatim. 'off'/'default' are OUR
# sentinels meaning "send no override, let the provider apply its own default effort" --
# they are NOT codex levels. ('none' and 'minimal' ARE real codex levels, so they belong
# in the forward list, not the disable list.)
REASONING_EFFORTS = ("minimal", "none", "low", "medium", "high", "xhigh")
REASONING_DISABLE = ("off", "default")
CODEX_REASONING_CAPABILITIES = {
    # gpt-5.6-sol rejects the otherwise valid "minimal" level. Keep this
    # capability exception local to the one authoring request builder so every
    # author call has the same deterministic normalization before egress.
    "gpt-5.6-sol": frozenset(("none", "low", "medium", "high", "xhigh", "max")),
    # gpt-6-astra (probed 2026-09-05) rejects "minimal" and "none" alike:
    # "Supported values are: 'low', 'medium', 'high', 'xhigh', and 'max'."
    "gpt-6-astra": frozenset(("low", "medium", "high", "xhigh", "max")),
    "gpt-6.1-sol": frozenset(("low", "medium", "high", "xhigh", "max", "ultra")),
    "gpt-6-sol": frozenset(("low", "medium", "high", "xhigh", "max", "ultra")),
    # Direct probe (2026-09-26): "minimal" is rejected; these levels are accepted.
    "gpt-6-luna": frozenset(("none", "low", "medium", "high", "xhigh", "max")),
}
# The provider's own order, cheapest first.
_LEVELS_CHEAPEST_FIRST = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

# Roles whose page is mostly one headline and a rule. They still get the full
# output budget (D18): a truncated cover is a broken slide, and the tokens are
# not spent unless the model needs them.
FAST_ROLE_OUTPUT_BUDGETS = {
    "COVER": AUTHOR_MAX_TOKENS,
    "SECTION": AUTHOR_MAX_TOKENS,
    "AGENDA": AUTHOR_MAX_TOKENS,
    "CLOSING": AUTHOR_MAX_TOKENS,
}


def normalized_authoring_reasoning_effort(model: object, effort: str) -> str:
    """Map a recognized Codex effort to the selected model's capability set."""

    model_id = str(model or "").strip().lower()
    if (model_id == "gpt-6.1-sol" or model_id.startswith("gpt-6.1-sol-")) and effort == "ultra":
        return "max"
    capabilities = next(
        (
            supported
            for family, supported in CODEX_REASONING_CAPABILITIES.items()
            if model_id == family or model_id.startswith(f"{family}-")
        ),
        None,
    )
    if capabilities is None or effort in capabilities:
        return effort
    # Fall back to the lowest level the model supports, in the provider's
    # own order. Returning the unsupported level unchanged (the previous
    # behaviour when "none" was also unsupported) sent "minimal" to
    # gpt-6-astra for every fast-role page and cost the slide an attempt on
    # a 400 that is not retried.
    for level in _LEVELS_CHEAPEST_FIRST:
        if level in capabilities:
            return level
    return effort


def resolve_authoring_reasoning_level(
    effort: Optional[str] = None, model: object = None
) -> Optional[str]:
    """Return the codex reasoning level for this call, or None for no override.

    Resolution: a non-blank ``effort`` argument wins; otherwise the configured
    Codex effort, then AUTHORED_REASONING_EFFORT; if neither is meaningfully set
    the authoring default is the authored 'medium' effort.

    Returns a level in :data:`REASONING_EFFORTS` normalized to the model's
    capability set. Returns ``None`` (no override -> the provider's own default
    effort) for the 'off'/'default' sentinels. An UNRECOGNIZED value is logged
    (attributing the env vs the argument as its source) and also returns
    ``None``, so a misconfiguration is never silent.
    """

    override = (effort or "").strip()
    if override:
        raw, source = override, "reasoning_effort argument"
    else:
        from utils.env_settings.authored_runtime import (
            get_authored_reasoning_effort,
            get_codex_reasoning_effort,
        )

        codex_effort = get_codex_reasoning_effort() or ""
        raw, source = (
            (codex_effort, "CODEX_REASONING_EFFORT")
            if codex_effort.strip()
            else (get_authored_reasoning_effort(), "AUTHORED_REASONING_EFFORT")
        )
    level = raw.strip().lower()
    if not level:
        level = DEFAULT_REASONING_EFFORT  # unset / blank -> authored default
    if level in REASONING_DISABLE:
        return None
    model_id = str(model or "").strip().lower()
    sol_61 = model_id == "gpt-6.1-sol" or model_id.startswith("gpt-6.1-sol-")
    if level in REASONING_EFFORTS or (sol_61 and level in ("max", "ultra")):
        return normalized_authoring_reasoning_effort(model, level)
    logger.warning(
        "%s=%r is not a recognized reasoning effort %s or a disable word %s; "
        "authoring at the provider default reasoning effort.",
        source,
        raw,
        REASONING_EFFORTS,
        REASONING_DISABLE,
    )
    return None


def selected_authoring_model() -> object:
    """The configured model id, or ``None`` when no provider is selected.

    Imported lazily: this module is imported while ``author_slide`` is still
    being defined, and an offline/unit context may have no provider at all.
    """

    try:
        from utils.llm_provider import get_model

        return get_model()
    except Exception:
        return None


def authoring_output_profile(
    *,
    role: str,
    reference_mode: str,
    structured_context: dict | None,
    correction_hint: str | None,
    model: object = None,
) -> tuple[int, str]:
    """Select a fast role profile without weakening strict authoring modes.

    ``model`` defaults to the configured model, read at call time; tests pass
    one explicitly so the lowest-supported level is deterministic.
    """

    # Structured contexts include source-verbatim requests. Exact reconstruction
    # and repairs likewise need the established 16k/low profile so a fixed
    # source or a complex rejected document is never truncated to save time.
    configured_effort = resolve_authoring_reasoning_level(None, model)
    profile_effort = configured_effort or DEFAULT_REASONING_EFFORT
    if (
        reference_mode == EXACT_RECONSTRUCTION
        or structured_context is not None
        or correction_hint is not None
    ):
        return AUTHOR_MAX_TOKENS, profile_effort
    budget = FAST_ROLE_OUTPUT_BUDGETS.get((role or "").strip().upper())
    if budget is None:
        return AUTHOR_MAX_TOKENS, profile_effort
    return budget, profile_effort


__all__ = [
    "AUTHOR_MAX_TOKENS",
    "CODEX_REASONING_CAPABILITIES",
    "DEFAULT_REASONING_EFFORT",
    "FAST_ROLE_OUTPUT_BUDGETS",
    "REASONING_DISABLE",
    "REASONING_EFFORTS",
    "authoring_output_profile",
    "normalized_authoring_reasoning_effort",
    "resolve_authoring_reasoning_level",
    "selected_authoring_model",
]
