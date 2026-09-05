"""Provider rate profile for the Authored generation path (workstream B1).

Two shapes of provider sit behind authored generation.  A personal OAuth token
(``codex``) is quota-bound and answers best to a few large calls; an API key is
billed per token and tolerates many small calls.  This module is the *only*
place that turns the configured provider into a rate decision.  Every other
module asks for the resolved :class:`AuthoredRateProfile` and never looks at
the provider name itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from utils.get_env import (
    get_authored_api_provider_concurrency_max,
    get_authored_author_concurrency_env,
    get_authored_batch_recovery_successes_env,
    get_authored_batch_slides_per_call_env,
    get_authored_batch_wave_concurrency_env,
    get_authored_provider_rate_profile_env,
    get_llm_provider_env,
)
from utils.runtime_settings import RuntimeSettings


TOKEN_PROFILE_NAME = "token"
API_PROFILE_NAME = "api"
AUTO_PROFILE_NAME = "auto"
PROFILE_NAMES = frozenset({TOKEN_PROFILE_NAME, API_PROFILE_NAME})

# Providers whose throughput is a personal quota rather than a billed API.
_TOKEN_PROVIDER_IDS = frozenset({"codex"})

# Token-profile values: one call to warm up, then two calls per wave, on a
# 5..8 author pool. Each call authors ONE slide (2026-09-05): the three-slide
# batch the profile shipped with made the model compress every document it
# had to return alongside two others, and the decks it produced carried about
# a quarter of the per-slide markup of the one-slide-per-call August decks.
# ``AUTHORED_BATCH_SLIDES_PER_CALL`` restores batching for a quota-bound run.
_TOKEN_SLIDES_PER_CALL = 1
_TOKEN_INITIAL_WAVE_CONCURRENCY = 1
_TOKEN_STEADY_WAVE_CONCURRENCY = 2
_TOKEN_AUTHOR_CONCURRENCY_DEFAULT = 5
_TOKEN_AUTHOR_CONCURRENCY_MAX = 8

# The api profile authors one slide per call at the author concurrency, which
# is the sync path's proven shape; its ceiling is a registered knob.
_API_SLIDES_PER_CALL = 1
_API_AUTHOR_CONCURRENCY_DEFAULT = 5

_RECOVERY_SUCCESSES_DEFAULT = 3


@dataclass(frozen=True)
class AuthoredRateProfile:
    """Resolved throughput contract shared by the batch policy and executors."""

    name: str
    slides_per_call: int
    initial_wave_concurrency: int
    steady_wave_concurrency: int
    recovery_successes: int
    author_concurrency_default: int
    author_concurrency_max: int

    def __post_init__(self) -> None:
        if self.name not in PROFILE_NAMES:
            raise ValueError(f"unknown authored rate profile: {self.name!r}")
        for field_name in (
            "slides_per_call",
            "initial_wave_concurrency",
            "steady_wave_concurrency",
            "recovery_successes",
            "author_concurrency_default",
            "author_concurrency_max",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.author_concurrency_default > self.author_concurrency_max:
            raise ValueError(
                "author_concurrency_default must not exceed author_concurrency_max"
            )


def _configured_provider_id(settings: RuntimeSettings | None) -> str:
    return (get_llm_provider_env(settings) or "").strip().lower()


def resolve_authored_rate_profile_name(
    settings: RuntimeSettings | None = None,
) -> str:
    """Return ``token`` or ``api`` for the configured profile knob.

    ``auto`` (the default) derives the profile from the configured provider:
    a personal-token provider takes ``token``; everything else takes ``api``.
    An unrecognised knob value falls back to ``auto`` derivation rather than
    silently choosing the aggressive profile.
    """

    requested = get_authored_provider_rate_profile_env(settings)
    if requested in PROFILE_NAMES:
        return requested
    if _configured_provider_id(settings) in _TOKEN_PROVIDER_IDS:
        return TOKEN_PROFILE_NAME
    return API_PROFILE_NAME


def resolve_authored_rate_profile(
    settings: RuntimeSettings | None = None,
) -> AuthoredRateProfile:
    """Resolve the profile, then apply the explicit knobs on top of it.

    Explicit knobs (``AUTHORED_BATCH_SLIDES_PER_CALL``,
    ``AUTHORED_BATCH_WAVE_CONCURRENCY``, ``AUTHORED_BATCH_RECOVERY_SUCCESSES``,
    ``AUTHORED_AUTHOR_CONCURRENCY``) override the profile's values when set.
    Author concurrency is always clamped to the profile ceiling, and a wave can
    never run more calls than the author pool can hold.
    """

    name = resolve_authored_rate_profile_name(settings)
    if name == TOKEN_PROFILE_NAME:
        author_max = _TOKEN_AUTHOR_CONCURRENCY_MAX
        author_default = _TOKEN_AUTHOR_CONCURRENCY_DEFAULT
        slides_per_call = _TOKEN_SLIDES_PER_CALL
    else:
        author_max = get_authored_api_provider_concurrency_max(settings)
        author_default = min(_API_AUTHOR_CONCURRENCY_DEFAULT, author_max)
        slides_per_call = _API_SLIDES_PER_CALL

    explicit_author = get_authored_author_concurrency_env(settings)
    author_concurrency = (
        min(explicit_author, author_max)
        if explicit_author is not None
        else author_default
    )

    if name == TOKEN_PROFILE_NAME:
        initial_wave = _TOKEN_INITIAL_WAVE_CONCURRENCY
        steady_wave = _TOKEN_STEADY_WAVE_CONCURRENCY
    else:
        initial_wave = steady_wave = author_concurrency

    explicit_wave = get_authored_batch_wave_concurrency_env(settings)
    if explicit_wave is not None:
        steady_wave = min(explicit_wave, author_concurrency)
        initial_wave = min(initial_wave, steady_wave)

    explicit_slides = get_authored_batch_slides_per_call_env(settings)
    if explicit_slides is not None:
        slides_per_call = explicit_slides

    explicit_recovery = get_authored_batch_recovery_successes_env(settings)
    recovery_successes = (
        explicit_recovery
        if explicit_recovery is not None
        else _RECOVERY_SUCCESSES_DEFAULT
    )

    return AuthoredRateProfile(
        name=name,
        slides_per_call=slides_per_call,
        initial_wave_concurrency=initial_wave,
        steady_wave_concurrency=steady_wave,
        recovery_successes=recovery_successes,
        author_concurrency_default=author_concurrency,
        author_concurrency_max=author_max,
    )


__all__ = [
    "API_PROFILE_NAME",
    "AUTO_PROFILE_NAME",
    "AuthoredRateProfile",
    "PROFILE_NAMES",
    "TOKEN_PROFILE_NAME",
    "resolve_authored_rate_profile",
    "resolve_authored_rate_profile_name",
]
