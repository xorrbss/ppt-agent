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
profile resolved at call time.  The result is pinned into the admission row
at admission time and is never recomputed while an operation runs, so a later
policy or profile change cannot move a running deadline.
"""

from __future__ import annotations

from services.authored_provider_rate_profile import (
    AuthoredRateProfile,
    resolve_authored_rate_profile,
)


# Measured 3-slide authoring calls land between ~170s and ~240s, so one wave of
# up to two parallel calls costs roughly five minutes.  The base covers outline,
# content brief, deck plan, header contract, images, and persistence.  The api
# profile's per-slide calls are shorter, so the same per-wave allowance is a
# conservative budget there.
PUBLIC_GENERATION_DEADLINE_BASE_SECONDS = 900
PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS = 300
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

    waves = public_generation_wave_count(slide_count, profile=profile)
    scaled = (
        PUBLIC_GENERATION_DEADLINE_BASE_SECONDS
        + PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS * waves
    )
    return min(
        PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
        max(PUBLIC_GENERATION_DEADLINE_MIN_SECONDS, scaled),
    )


__all__ = [
    "PUBLIC_GENERATION_DEADLINE_BASE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_MAX_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_MIN_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS",
    "public_generation_batch_call_count",
    "public_generation_deadline_seconds",
    "public_generation_wave_count",
]
