"""Slide-count scaled generation deadline for public operation admission.

The public control plane pins one absolute deadline per operation at admission
time and never recomputes it while the operation runs.  That single budget has
to cover every stage the authored pipeline is *allowed* to run -- not just the
authoring waves.  Budgeting the waves alone is exactly what killed three bench
decks: a 24-slide deck authored all 24 slides and then died on
``public_deadline_exceeded`` at 47 min against a 45.3 min budget, and a healthy
24-slide run finished 2577 s into a 2720 s budget with only 143 s of margin.

The budget is therefore a sum of named terms rather than one wave product::

    deadline = base
             + preamble                        (content brief + outline + header)
             + wave_seconds * authoring_waves
             + remediation                     (the remake cap at the wave shape)
             + review                          (per slide, plus per remake)

Every term is a registered runtime knob (``PUBLIC_GENERATION_DEADLINE_*`` in
``constants/settings_registry.json``) and every default carries a comment
naming the attempt-timing receipt it was measured from (gpt-6-astra at medium
effort, bench runs of 2026-09-05).  The preamble is *derived* from the content
brief's own stage budget rather than guessed, so the two policies cannot drift
apart the way they did before this module was rewritten.

Everything here is a pure function of the requested slide count and the profile
resolved at call time.  The result is pinned into the admission row and is
never recomputed, so a later policy or profile change cannot move a running
deadline.
"""

from __future__ import annotations

import math

from api.v1.ppt.endpoints.content_brief_policy import content_brief_timeout_policy
from services.authored_provider_rate_profile import (
    AuthoredRateProfile,
    resolve_authored_rate_profile,
)
from utils.get_env import (
    get_public_generation_deadline_call_overhead_seconds_env,
    get_public_generation_deadline_per_slide_seconds_env,
)
from utils.public_generation_deadline_env import (
    get_public_generation_deadline_base_seconds_env,
    get_public_generation_deadline_budgeted_wave_concurrency_env,
    get_public_generation_deadline_header_seconds_env,
    get_public_generation_deadline_max_seconds_env,
    get_public_generation_deadline_outline_seconds_env,
    get_public_generation_deadline_remediation_wave_seconds_env,
    get_public_generation_deadline_review_per_remake_seconds_env,
    get_public_generation_deadline_review_per_slide_seconds_env,
)


# Residual for the stages no other term names: deck plan, illustration plan,
# image work and persistence/publication.  Measured as the residual of the
# healthy 24-slide bench wall: 2577 s total, minus the worst measured preamble
# (779 s), minus 13 authoring waves at the p90 call (13 * 89 = 1157 s), minus
# the worst zero-remake review (178 s), leaves 463 s.  Rounded up to 600 s.
PUBLIC_GENERATION_DEADLINE_BASE_SECONDS = 600

# Outline stage measured 306 s, 341 s and 374 s at medium effort.  Rounded up.
PUBLIC_GENERATION_DEADLINE_OUTLINE_SECONDS = 420

# Header-contract stage measured 29 s, 44 s and 51 s.  Rounded up.
PUBLIC_GENERATION_DEADLINE_HEADER_SECONDS = 90

# One wave costs as much as its slowest call, and a call's wall grows with the
# slides it returns.  One-slide authoring calls measured 62-89 s (p90 89 s) at
# wave concurrency 2; the three-slide calls the profile shipped with before
# 2026-09-05 took 170-240 s.  A straight line through the worst cases has a
# slope of ~76 s per slide with a ~12 s intercept; the slope is rounded up to
# 80 s and the fixed part is given 60 s of headroom for prompt assembly, rate
# slot waits and checkpoint I/O.  A one-slide wave is therefore budgeted at
# 140 s and a three-slide wave keeps the five-minute allowance the formula was
# originally calibrated on.
PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS = 80
PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS = 60
PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS = (
    PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS
    + PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS * 3
)

# The wave concurrency the authoring measurement was taken at.  The budget
# never assumes a wider configured wave is proportionally faster: a wider wave
# puts more calls in front of the same provider rate limit, so it is the
# provider and not the scheduler that bounds throughput.  Clamping here is what
# makes widening ``AUTHORED_BATCH_WAVE_CONCURRENCY`` unable to *shrink* a
# deck's budget -- the deploying environment runs that knob at 5, and under the
# previous formula that alone cut a 20-slide budget from 2440 s to 1600 s.
PUBLIC_GENERATION_DEADLINE_BUDGETED_WAVE_CONCURRENCY = 2

# One repair/remake wave.  Measured rounds: 222 s for 2 remakes (one wave at
# concurrency 2), 394 s and 555 s for 4 remakes (two waves) -- 197-277 s per
# wave.  Rounded up to 280 s.  A remake is a full re-author plus its defect
# analysis, so it is dearer than a first-pass authoring wave.
PUBLIC_GENERATION_DEADLINE_REMEDIATION_WAVE_SECONDS = 280

# Chromium review, per slide.  Measured 143-178 s at 24 slides with zero
# remakes, i.e. 6.0-7.4 s per slide.  Rounded up.
PUBLIC_GENERATION_DEADLINE_REVIEW_PER_SLIDE_SECONDS = 8

# Chromium review, per remade slide.  The same review measured 469 s and 612 s
# at 24 slides with 4 remakes; net of the per-slide part (24 * 8 = 192 s) that
# is 69-105 s of re-render per remake.  Rounded up.
PUBLIC_GENERATION_DEADLINE_REVIEW_PER_REMAKE_SECONDS = 110

# Floor for a trivially small deck, unchanged.
PUBLIC_GENERATION_DEADLINE_MIN_SECONDS = 1200

# The ceiling exists for one reason only: to bound a STUCK job, one that has
# stopped making progress and would otherwise hold a lease forever.  It is a
# bad instrument for that -- it cannot tell a stuck 30-slide deck from a
# healthy 100-slide one, and at its previous value of 7200 s it silently killed
# large decks that production really does generate (90-slide decks are
# routine).  The right instrument is liveness-based termination on checkpoint
# progress: kill an operation that has published no new checkpoint for N
# minutes, whatever its size.  THAT DOES NOT EXIST YET.  Until it does, this
# default is set well above the formula for the largest deck the policy admits
# (100 slides budget to 11,950 s under the shipped token profile) so the
# formula, not the ceiling, decides for every realistic deck.
PUBLIC_GENERATION_DEADLINE_MAX_SECONDS = 14400


def _slide_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("public_generation_slide_count_must_be_nonnegative")
    return value


def _ceil_div(numerator: int, denominator: int) -> int:
    return -(-numerator // denominator)


def _profile(profile: AuthoredRateProfile | None) -> AuthoredRateProfile:
    return profile if profile is not None else resolve_authored_rate_profile()


def _override(explicit: int | None, default: int) -> int:
    return explicit if explicit is not None else default


def public_generation_per_slide_seconds() -> int:
    """Per-slide-in-call allowance, honouring the explicit runtime override."""

    return _override(
        get_public_generation_deadline_per_slide_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS,
    )


def public_generation_call_overhead_seconds() -> int:
    """Fixed per-call allowance, honouring the explicit runtime override."""

    return _override(
        get_public_generation_deadline_call_overhead_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS,
    )


def public_generation_base_seconds() -> int:
    """Residual allowance for deck plan, illustration, images and persistence."""

    return _override(
        get_public_generation_deadline_base_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_BASE_SECONDS,
    )


def public_generation_preamble_seconds() -> int:
    """Return the allowance for content brief + outline + header contract.

    The brief part is **derived** from the brief stage's own wall ceiling
    (``ContentBriefTimeoutPolicy.total_budget_seconds``, which already bounds
    the first attempt and its single retry together) instead of being repeated
    as a constant here.  The two policies used to be independent numbers, and
    that drift is what let a brief legally spend more wall than the whole deck
    had been granted.  Raising ``CONTENT_BRIEF_TOTAL_BUDGET_SECONDS`` now
    raises this term with it.

    The runtime share clamp in ``content_brief_operation_share`` can only
    *reduce* what a brief actually spends, so this stays an upper bound.
    """

    brief_seconds = math.ceil(content_brief_timeout_policy().total_budget_seconds)
    outline_seconds = _override(
        get_public_generation_deadline_outline_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_OUTLINE_SECONDS,
    )
    header_seconds = _override(
        get_public_generation_deadline_header_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_HEADER_SECONDS,
    )
    return brief_seconds + outline_seconds + header_seconds


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


def public_generation_budgeted_wave_concurrency(
    *,
    profile: AuthoredRateProfile | None = None,
) -> tuple[int, int]:
    """Return the ``(initial, steady)`` wave widths the budget may assume.

    Never wider than the measured shape: a configured wave wider than
    ``PUBLIC_GENERATION_DEADLINE_BUDGETED_WAVE_CONCURRENCY`` is not credited
    with a proportional speed-up, so widening the wave can never shrink a
    deck's budget.  A *narrower* configured wave is honoured, because fewer
    calls in flight really do take longer.
    """

    resolved = _profile(profile)
    ceiling = _override(
        get_public_generation_deadline_budgeted_wave_concurrency_env(),
        PUBLIC_GENERATION_DEADLINE_BUDGETED_WAVE_CONCURRENCY,
    )
    steady = min(resolved.steady_wave_concurrency, ceiling)
    initial = min(resolved.initial_wave_concurrency, steady)
    return max(initial, 1), max(steady, 1)


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
    """Return the scheduler's waves: one initial wave, then steady-state waves."""

    resolved = _profile(profile)
    batch_calls = public_generation_batch_call_count(slide_count, profile=resolved)
    remaining = max(batch_calls - resolved.initial_wave_concurrency, 0)
    return 1 + _ceil_div(remaining, resolved.steady_wave_concurrency)


def public_generation_budgeted_wave_count(
    slide_count: int,
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the waves the *budget* pays for, at the budgeted wave widths."""

    resolved = _profile(profile)
    initial, steady = public_generation_budgeted_wave_concurrency(profile=resolved)
    batch_calls = public_generation_batch_call_count(slide_count, profile=resolved)
    return 1 + _ceil_div(max(batch_calls - initial, 0), steady)


def public_generation_remake_cap(slide_count: int) -> int:
    """Return the remakes one deck may spend, read from the fallback policy.

    Both bounds are read rather than copied so this budget cannot drift from
    the module that enforces them.  The import is deferred because
    ``authored_terminal_slide_fallback`` pulls in the browser capture stack,
    which admission must not load.
    """

    from services.authored_terminal_slide_fallback import (
        MAX_REMAKES_PER_SLIDE,
        MAX_TERMINAL_REMAKES_PER_DECK,
    )

    return min(
        MAX_TERMINAL_REMAKES_PER_DECK,
        MAX_REMAKES_PER_SLIDE * _slide_count(slide_count),
    )


def public_generation_remediation_seconds(
    slide_count: int,
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the allowance for the bounded repair/remake rounds.

    The deck may spend up to its remake cap, and remakes are scheduled at the
    same wave shape as authoring, so the cost is the number of remake waves the
    cap implies times the measured remediation wave.
    """

    cap = public_generation_remake_cap(slide_count)
    if cap <= 0:
        return 0
    _, steady = public_generation_budgeted_wave_concurrency(profile=_profile(profile))
    wave_seconds = _override(
        get_public_generation_deadline_remediation_wave_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_REMEDIATION_WAVE_SECONDS,
    )
    return _ceil_div(cap, steady) * wave_seconds


def public_generation_review_seconds(slide_count: int) -> int:
    """Return the chromium review allowance: a per-slide part and a per-remake part.

    The per-remake part is what the wave-only formula missed hardest: at 24
    slides the review cost 143-178 s with no remakes and 469-612 s with four,
    because every remade slide is re-rendered and re-measured.
    """

    slides = _slide_count(slide_count)
    per_slide = _override(
        get_public_generation_deadline_review_per_slide_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_REVIEW_PER_SLIDE_SECONDS,
    )
    per_remake = _override(
        get_public_generation_deadline_review_per_remake_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_REVIEW_PER_REMAKE_SECONDS,
    )
    return per_slide * slides + per_remake * public_generation_remake_cap(slides)


def public_generation_deadline_max_seconds() -> int:
    """Stuck-job ceiling on the whole budget, honouring the runtime override."""

    return _override(
        get_public_generation_deadline_max_seconds_env(),
        PUBLIC_GENERATION_DEADLINE_MAX_SECONDS,
    )


def public_generation_deadline_seconds(
    slide_count: int,
    *,
    profile: AuthoredRateProfile | None = None,
) -> int:
    """Return the clamped whole-job generation budget for ``slide_count``."""

    resolved = _profile(profile)
    scaled = (
        public_generation_base_seconds()
        + public_generation_preamble_seconds()
        + public_generation_wave_seconds(profile=resolved)
        * public_generation_budgeted_wave_count(slide_count, profile=resolved)
        + public_generation_remediation_seconds(slide_count, profile=resolved)
        + public_generation_review_seconds(slide_count)
    )
    return min(
        public_generation_deadline_max_seconds(),
        max(PUBLIC_GENERATION_DEADLINE_MIN_SECONDS, scaled),
    )


__all__ = [
    "PUBLIC_GENERATION_DEADLINE_BASE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_BUDGETED_WAVE_CONCURRENCY",
    "PUBLIC_GENERATION_DEADLINE_CALL_OVERHEAD_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_HEADER_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_MAX_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_MIN_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_OUTLINE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_PER_SLIDE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_PER_WAVE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REMEDIATION_WAVE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_REMAKE_SECONDS",
    "PUBLIC_GENERATION_DEADLINE_REVIEW_PER_SLIDE_SECONDS",
    "public_generation_base_seconds",
    "public_generation_batch_call_count",
    "public_generation_budgeted_wave_concurrency",
    "public_generation_budgeted_wave_count",
    "public_generation_call_overhead_seconds",
    "public_generation_deadline_max_seconds",
    "public_generation_deadline_seconds",
    "public_generation_per_slide_seconds",
    "public_generation_preamble_seconds",
    "public_generation_remake_cap",
    "public_generation_remediation_seconds",
    "public_generation_review_seconds",
    "public_generation_wave_count",
    "public_generation_wave_seconds",
]
