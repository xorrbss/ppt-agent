"""Slide-count scaled generation deadline for public operation admission.

The public control plane pins one absolute deadline per operation at admission
time.  A single fixed budget cannot cover both a three-slide deck and a
hundred-slide deck: authored generation issues one provider call per
``slides_per_call`` slides, runs ``initial_wave_concurrency`` calls in the
first wave, and then runs at most ``steady_wave_concurrency`` calls per wave
(see ``services/authored_batch_policy.py``; the numbers come from the provider
rate profile in ``services/authored_provider_rate_profile.py``).  The number
of scheduling waves is therefore the only structural driver of wall-clock
time, and this module turns it into a second budget.

Everything here is a pure function of the requested slide count and the
profile resolved at call time -- including every explicit knob the profile
honours (``AUTHORED_BATCH_SLIDES_PER_CALL``, ``AUTHORED_BATCH_WAVE_CONCURRENCY``,
``AUTHORED_AUTHOR_CONCURRENCY``), so a wider wave shortens the budget and a
larger call lengthens each wave.  The result is pinned into the admission row
at admission time and is never recomputed while an operation runs, so a later
policy or profile change cannot move a running deadline.
"""

from __future__ import annotations

from services.authored_provider_rate_profile import (
    AuthoredRateProfile,
    resolve_authored_rate_profile,
)
from utils.get_env import (
    get_public_generation_deadline_call_overhead_seconds_env,
    get_public_generation_deadline_per_slide_seconds_env,
)


# One wave costs as much as its slowest call, and a call's wall clock grows
# with the slides it returns.  Measured authoring calls on gpt-6-astra
# (docs/authored-quality-speed-programme-design-20260905.md, sections 1.1 and
# 4): one-slide calls take 43-88 s (p50 43 s at low effort, 63 s at
# medium/high); the three-slide calls the profile shipped with before
# 2026-09-05 took 170-240 s.  A straight line through the worst cases has a
# slope of ~76 s per slide with a ~12 s intercept; the budget below rounds the
# slope up to 80 s and gives the fixed part 60 s of headroom for prompt
# assembly, rate-slot waits and checkpoint I/O, so a three-slide wave keeps
# the five-minute allowance the formula was calibrated on and a one-slide wave
# is budgeted at 140 s.
#
# The base covers outline, content brief, deck plan, header contract, images,
# and persistence.  ``PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS`` is kept as
# the documented three-slide reference wave; the live per-wave figure comes
# from ``public_generation_wave_seconds``.
PUBLIC_GENERATION_DEADLINE_BASE_SECONDS = 900
PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS = 80
PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS = 60
PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS = (
    PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS
    + PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS * 3
)
PUBLIC_GENERATION_DEADLINE_MIN_SECONDS = 1200
PUBLIC_GENERATION_DEADLINE_MAX_SECONDS = 7200


def _slide_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("public_generation_slide_count_must_be_nonnegative")
    return value


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _profile(profile: AuthoredRateProfile | None) -> AuthoredRateProfile:
    return profile if profile is not None else resolve_authored_rate_profile()


def public_generation_per_slide_seconds() -> int:
    """Per-slide-in-call allowance, honouring the explicit runtime override."""

    explicit = get_public_generation_deadline_per_slide_seconds_env()
    return (
        explicit
        if explicit is not None
        else PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS
    )


def public_generation_call_overhead_seconds() -> int:
    """Fixed per-call allowance, honouring the explicit runtime override."""

    explicit = get_public_generation_deadline_call_overhead_seconds_env()
    return (
        explicit
        if explicit is not None
        else PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS
    )


def public_generation_wave_seconds(
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the budget for one scheduling wave under ``profile``.

    A wave is bounded by its slowest call, so the allowance scales with the
    slides one call returns rather than with the calls running side by side.
    """

    return (
        public_generation_call_overhead_seconds()
        + public_generation_per_slide_seconds() * _profile(profile).slides_per_call
    )


def public_generation_batch_call_count(
    slide_count: int,
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the number of authoring provider calls the deck needs."""

    return _ceil_div(_slide_count(slide_count), _profile(profile).slides_per_call)


def public_generation_wave_count(
    slide_count: int,
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the scheduling waves: one initial wave, then steady-state waves."""

    resolved = _profile(profile)
    batch_calls = public_generation_batch_call_count(slide_count, profile=resolved)
    remaining = max(batch_calls - resolved.initial_wave_concurrency, 0)
    return 1 + _ceil_div(remaining, resolved.steady_wave_concurrency)


def public_generation_deadline_seconds(
    slide_count: int,
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the clamped whole-job generation budget for ``slide_count``."""

    resolved = _profile(profile)
    waves = public_generation_wave_count(slide_count, profile=resolved)
    scaled = (
        PUBLIC_GENERATION_DEADLINE_BASE_SECONDS
        + public_generation_wave_seconds(profile=resolved) * waves
    )
    return min(
        PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
        max(PUBLIC_GENERATION_DEADLINE_MIN_SECONDS, scaled),
    )


__all__ = [
    "PUBLIC_GENERATION_DEADLINE_BASE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_MAX_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_MIN_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS",
    "public_generation_batch_call_count",
    "public_generation_call_overhead_seconds",
    "public_generation_deadline_seconds",
    "public_generation_per_slide_seconds",
    "public_generation_wave_count",
    "public_generation_wave_seconds",
]
