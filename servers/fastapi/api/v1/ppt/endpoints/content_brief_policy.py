"""Timeout, retry and status policy for the Stage A content brief (QUAL-07, SPEED-10).

Both places that run the brief -- the outline stream in ``outlines.py`` and
the generation handler in ``presentation_generate.py`` -- go through
:func:`run_content_brief_with_policy`, so the deadline, the retry and the
recorded outcome are the same on both paths. Before this module the outline
stream had a flat 45 s deadline with no retry and the generation handler had
no deadline at all; production logs showed 72 brief timeouts, every one of
which silently fell back to the raw source.

Timeout formula
---------------
``timeout = min(max, base + per_kchar * input_chars / 1000)`` where
``input_chars`` counts the source content, the document context and the
instructions -- everything the brief has to read. Every knob is registered in
``constants/settings_registry.json`` and read through ``utils/get_env.py``.
Defaults:

* ``base`` 600 s (``CONTENT_BRIEF_TIMEOUT_SECONDS``, the pre-existing knob).
  The former value was 45 s, inherited from the outline endpoint, and it is
  far below what a reasoning model actually spends here: prompt-only briefs
  on gpt-6-astra at medium effort measured 325 s and 455 s, so 45 s timed out
  every single deck and threw the brief away.
* ``per_kchar`` 1 s per 1,000 characters
  (``CONTENT_BRIEF_TIMEOUT_PER_KCHAR_SECONDS``): a 50k-character document
  context adds 50 s. Structured-output briefs grow with what they read.
* ``max`` 900 s (``CONTENT_BRIEF_TIMEOUT_MAX_SECONDS``).
  ``build_document_context`` admits up to 500k characters, so without a cap a
  large upload would hold the stage indefinitely.
* ``total`` ``max + base`` = 1500 s (``CONTENT_BRIEF_TOTAL_BUDGET_SECONDS``):
  ceiling on the stage as a whole, first attempt and retry together.  It is
  the sum of what the two attempts need -- the first may need the cap (a
  large document), and the retry, which runs on a reduced input, is granted at
  least the base term.  The previous value, 900 s, equalled the first
  attempt's cap: a prompt-only brief spent 600 s and left the retry 300 s,
  below anything a brief has ever finished in, and a 500k-character input
  left it nothing.
* ``retry floor`` 0.8 x ``base`` = 480 s (``CONTENT_BRIEF_RETRY_FLOOR_SECONDS``):
  the least a retry must be able to get before it is attempted.  The slower
  of the two measured prompt-only briefs took 455 s, so a retry with less
  than that has no realistic chance and would only burn deadline the deck
  needs later; below the floor the stage falls back to the raw source at once.
  Derived from the base so it follows an operator's retuning.

``total`` is also the brief term of the whole-deck budget in
``services/public_generation_deadline_policy.py``, which reads it from here
rather than repeating it, and callers running under a public operation pass
``remaining_operation_seconds`` so the stage is additionally clamped to a share
of what the operation has left (``content_brief_operation_share``).  On a
small deck that clamp is what makes the retry unaffordable, and the floor is
what turns that into an immediate fallback instead of a doomed second call.

Retry
-----
One retry after a timeout, with a reduced input: the document context is
head-truncated to half its length (``RETRY_CONTEXT_FRACTION``) and the brief
runs with its own ``concise`` verbosity option (a shorter depth instruction
and lower schema floors in ``get_content_brief_model``). No new prompt is
introduced. The retry deadline is recomputed from the reduced input.

Both attempts share ONE physical-call budget
(``utils/structured_call_budget.py``): the public admission plan reserves the
brief's first attempt in the initial allowance and its retry in the retry
allowance, one call each, so the two attempts together may not exceed the
documented two physical calls.  Each attempt's deadline is also handed to the
transport (``call_timeout_seconds``) so the HTTP call itself ends at the
deadline instead of running on behind the cancelled await.

Status
------
``brief_status`` is one of ``applied`` (first attempt), ``retry_applied``
(retry succeeded), ``timed_out_fallback`` (the first attempt timed out and the
retry either timed out too or was not attempted; the outline proceeds on the
raw source) or ``skipped`` (the brief failed for a non-timeout reason the
caller chose to tolerate). Non-timeout exceptions are propagated unchanged so
each call site keeps its own 429 / retryability handling; the call site
records ``skipped`` in that case via :func:`log_content_brief_skipped`. A
fifth status, ``reused``, is produced only by
``services/authored_content_brief_checkpoint.py`` and means this attempt made
no provider call at all.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Optional

from api.v1.ppt.endpoints.content_brief_operation_share import (
    clamp_brief_policy_to_operation,
)
from utils.get_env import (
    get_content_brief_retry_floor_seconds_env,
    get_content_brief_timeout_max_seconds_env,
    get_content_brief_timeout_per_kchar_seconds_env,
    get_content_brief_timeout_seconds_env,
    get_content_brief_total_budget_seconds_env,
)
from utils.structured_call_budget import StructuredProviderCallBudget

LOGGER = logging.getLogger(__name__)

BriefStatus = Literal[
    "applied", "retry_applied", "timed_out_fallback", "skipped", "reused"
]

BRIEF_STATUS_APPLIED: BriefStatus = "applied"
BRIEF_STATUS_RETRY_APPLIED: BriefStatus = "retry_applied"
BRIEF_STATUS_TIMED_OUT_FALLBACK: BriefStatus = "timed_out_fallback"
BRIEF_STATUS_SKIPPED: BriefStatus = "skipped"
#: This attempt made no provider call: a previous attempt of the same
#: generation job already produced the brief and it was read back from its
#: durable checkpoint (``services/authored_content_brief_checkpoint.py``).
BRIEF_STATUS_REUSED: BriefStatus = "reused"

BASE_TIMEOUT_ENV = "CONTENT_BRIEF_TIMEOUT_SECONDS"
PER_KCHAR_TIMEOUT_ENV = "CONTENT_BRIEF_TIMEOUT_PER_KCHAR_SECONDS"
MAX_TIMEOUT_ENV = "CONTENT_BRIEF_TIMEOUT_MAX_SECONDS"
TOTAL_BUDGET_ENV = "CONTENT_BRIEF_TOTAL_BUDGET_SECONDS"
RETRY_FLOOR_ENV = "CONTENT_BRIEF_RETRY_FLOOR_SECONDS"

DEFAULT_BASE_TIMEOUT_SECONDS = 600.0
DEFAULT_PER_KCHAR_TIMEOUT_SECONDS = 1.0
DEFAULT_MAX_TIMEOUT_SECONDS = 900.0
#: Ceiling for the brief stage as a whole: the first attempt's cap plus the
#: base term the reduced retry is granted.  Unset, the knob derives this from
#: the effective ``max`` and ``base`` rather than repeating the number.
DEFAULT_TOTAL_BUDGET_SECONDS = DEFAULT_MAX_TIMEOUT_SECONDS + DEFAULT_BASE_TIMEOUT_SECONDS
#: The retry floor as a fraction of the base deadline (480 s at the default
#: base), just above the slower measured prompt-only brief (455 s).
RETRY_FLOOR_BASE_FRACTION = 0.8

#: Fraction of the document context kept (from the head) on the retry.
RETRY_CONTEXT_FRACTION = 0.5
#: The brief's own reduced-depth option, see ``generate_content_brief.py``.
RETRY_VERBOSITY = "concise"

RETRY_SKIPPED_BELOW_FLOOR = "retry_below_floor"
RETRY_SKIPPED_CALLS_EXHAUSTED = "physical_calls_exhausted"

BriefCallable = Callable[..., Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ContentBriefTimeoutPolicy:
    base_seconds: float = DEFAULT_BASE_TIMEOUT_SECONDS
    per_kchar_seconds: float = DEFAULT_PER_KCHAR_TIMEOUT_SECONDS
    max_seconds: float = DEFAULT_MAX_TIMEOUT_SECONDS
    total_budget_seconds: float = DEFAULT_TOTAL_BUDGET_SECONDS
    #: Explicit retry floor; ``None`` derives it from the base.
    retry_floor_seconds: float | None = None

    @property
    def retry_floor(self) -> float:
        """The least deadline a retry must be able to get to be worth attempting."""
        if self.retry_floor_seconds is not None:
            return max(0.0, self.retry_floor_seconds)
        return RETRY_FLOOR_BASE_FRACTION * self.base_seconds

    def timeout_for(self, input_chars: int) -> float:
        """Deadline in seconds for a brief that has to read ``input_chars``."""
        chars = max(0, int(input_chars))
        scaled = self.base_seconds + self.per_kchar_seconds * (chars / 1000.0)
        return max(0.0, min(self.max_seconds, scaled))

    def retry_timeout_for(self, input_chars: int, elapsed_seconds: float) -> float:
        """Retry deadline clamped to the stage's remaining wall, or 0.0 to skip.

        Zero means the retry is not worth making: what is left of the stage
        budget after the first attempt is below :attr:`retry_floor`, so the
        caller falls back to the raw source at once instead of spending it.
        """
        remaining = self.total_budget_seconds - max(0.0, elapsed_seconds)
        deadline = max(0.0, min(self.timeout_for(input_chars), remaining))
        return deadline if deadline >= self.retry_floor and deadline > 0.0 else 0.0


@dataclass(frozen=True, slots=True)
class ContentBriefOutcome:
    """What the policy did; ``brief`` is ``None`` only for a timed-out fallback."""

    status: BriefStatus
    brief: object | None
    timeout_seconds: float
    retry_timeout_seconds: float | None
    attempts: int
    elapsed_seconds: float
    #: Why no retry was made after a timeout, when that is what happened.
    retry_skipped_reason: str | None = None


def content_brief_timeout_policy() -> ContentBriefTimeoutPolicy:
    """Read the knobs; every one falls back to its default when unset or unusable."""
    base = _knob(get_content_brief_timeout_seconds_env(), DEFAULT_BASE_TIMEOUT_SECONDS)
    per_kchar = _knob(
        get_content_brief_timeout_per_kchar_seconds_env(),
        DEFAULT_PER_KCHAR_TIMEOUT_SECONDS,
    )
    maximum = _knob(get_content_brief_timeout_max_seconds_env(), DEFAULT_MAX_TIMEOUT_SECONDS)
    ceiling = max(maximum, base)
    total = _knob(get_content_brief_total_budget_seconds_env(), ceiling + base)
    return ContentBriefTimeoutPolicy(
        base_seconds=base,
        per_kchar_seconds=per_kchar,
        max_seconds=ceiling,
        total_budget_seconds=max(total, ceiling),
        retry_floor_seconds=get_content_brief_retry_floor_seconds_env(),
    )


def _knob(value: float | None, default: float) -> float:
    return default if value is None else value


def content_brief_input_chars(
    content: Optional[str],
    additional_context: Optional[str],
    instructions: Optional[str],
) -> int:
    return len(content or "") + len(additional_context or "") + len(instructions or "")


def reduced_brief_context(additional_context: Optional[str]) -> Optional[str]:
    """Head-truncate the document context for the retry; empty stays empty."""
    if not additional_context:
        return additional_context
    keep = int(len(additional_context) * RETRY_CONTEXT_FRACTION)
    return additional_context[:keep]


def merge_brief_context(additional_context: Optional[str], brief: object) -> str:
    """Append the brief's prompt context to the document context, as both call sites did."""
    brief_context = brief.to_prompt_context()
    if additional_context:
        return f"{additional_context}\n\n{brief_context}".strip()
    return brief_context


async def run_content_brief_with_policy(
    brief_fn: BriefCallable,
    *,
    content: str,
    language: Optional[str],
    additional_context: Optional[str],
    tone: Optional[str],
    verbosity: Optional[str],
    instructions: Optional[str],
    presentation_id: object = None,
    policy: ContentBriefTimeoutPolicy | None = None,
    remaining_operation_seconds: float | None = None,
    clock: Callable[[], float] = monotonic,
) -> ContentBriefOutcome:
    """Run ``brief_fn`` under the scaled deadline, retrying once on timeout.

    ``brief_fn`` is called positionally in ``generate_content_brief``'s
    argument order so call sites can pass the module-level function they
    already expose (and tests can keep monkeypatching that name).  Two
    keywords travel with every call: ``call_budget``, the one physical-call
    budget both attempts share, and ``call_timeout_seconds``, that attempt's
    deadline for the transport.  The retry adds ``is_retry=True``.
    Exceptions other than the timeout propagate untouched.

    ``remaining_operation_seconds`` is what the caller has left on its public
    operation deadline.  The stage budget is clamped to a share of it (see
    ``content_brief_operation_share``) so a slow brief cannot spend a small
    deck's whole allowance; ``None`` means "not under an operation deadline"
    and leaves the policy untouched.  ``clock`` measures the stage's elapsed
    wall and exists so tests can drive the budget arithmetic deterministically.
    """
    active = clamp_brief_policy_to_operation(
        policy or content_brief_timeout_policy(), remaining_operation_seconds
    )
    input_chars = content_brief_input_chars(content, additional_context, instructions)
    timeout = active.timeout_for(input_chars)
    call_budget = StructuredProviderCallBudget()
    started = clock()

    try:
        brief = await asyncio.wait_for(
            brief_fn(
                content,
                language,
                additional_context,
                tone,
                verbosity,
                instructions,
                call_budget=call_budget,
                call_timeout_seconds=timeout,
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        LOGGER.warning(
            "Content brief timed out after %.1f s for presentation %s (input %d chars)",
            timeout,
            presentation_id,
            input_chars,
        )
    else:
        outcome = ContentBriefOutcome(
            BRIEF_STATUS_APPLIED, brief, timeout, None, 1, clock() - started
        )
        _log_outcome(outcome, presentation_id)
        return outcome

    retry_context = reduced_brief_context(additional_context)
    retry_timeout = active.retry_timeout_for(
        content_brief_input_chars(content, retry_context, instructions),
        clock() - started,
    )
    skip_reason = None
    if call_budget.remaining <= 0:
        skip_reason = RETRY_SKIPPED_CALLS_EXHAUSTED
    elif retry_timeout <= 0:
        skip_reason = RETRY_SKIPPED_BELOW_FLOOR
    if skip_reason is not None:
        outcome = ContentBriefOutcome(
            BRIEF_STATUS_TIMED_OUT_FALLBACK,
            None,
            timeout,
            0.0,
            1,
            clock() - started,
            retry_skipped_reason=skip_reason,
        )
        LOGGER.warning(
            "Content brief for presentation %s is not retried (%s: %.1f s of the "
            "%.1f s stage budget left, floor %.1f s, %d physical call(s) left); "
            "continuing with the source content",
            presentation_id,
            skip_reason,
            max(0.0, active.total_budget_seconds - (clock() - started)),
            active.total_budget_seconds,
            active.retry_floor,
            call_budget.remaining,
        )
        _log_outcome(outcome, presentation_id)
        return outcome
    LOGGER.warning(
        "Content brief for presentation %s retrying once with a reduced input "
        "under a %.1f s deadline",
        presentation_id,
        retry_timeout,
    )
    try:
        brief = await asyncio.wait_for(
            brief_fn(
                content,
                language,
                retry_context,
                tone,
                RETRY_VERBOSITY,
                instructions,
                # The first attempt left a terminal provider row behind. Without
                # its own semantic slot this retry reopens that row, which a
                # provider without idempotency support refuses outright and the
                # whole operation fails instead of falling back.
                is_retry=True,
                call_budget=call_budget,
                call_timeout_seconds=retry_timeout,
            ),
            timeout=retry_timeout,
        )
    except asyncio.TimeoutError:
        outcome = ContentBriefOutcome(
            BRIEF_STATUS_TIMED_OUT_FALLBACK,
            None,
            timeout,
            retry_timeout,
            2,
            clock() - started,
        )
        LOGGER.warning(
            "Content brief retry timed out after %.1f s for presentation %s; "
            "continuing with the source content",
            retry_timeout,
            presentation_id,
        )
    else:
        outcome = ContentBriefOutcome(
            BRIEF_STATUS_RETRY_APPLIED,
            brief,
            timeout,
            retry_timeout,
            2,
            clock() - started,
        )
    _log_outcome(outcome, presentation_id)
    return outcome


def _log_outcome(outcome: ContentBriefOutcome, presentation_id: object) -> None:
    LOGGER.info(
        "content brief status=%s presentation=%s attempts=%d timeout=%.1fs "
        "retry_timeout=%s elapsed=%.1fs%s",
        outcome.status,
        presentation_id,
        outcome.attempts,
        outcome.timeout_seconds,
        (
            f"{outcome.retry_timeout_seconds:.1f}s"
            if outcome.retry_timeout_seconds is not None
            else "-"
        ),
        outcome.elapsed_seconds,
        (
            f" retry_skipped={outcome.retry_skipped_reason}"
            if outcome.retry_skipped_reason
            else ""
        ),
    )


def brief_failure_is_terminal(error: BaseException) -> bool:
    """Generation-path tolerance rule for a brief that failed for a non-timeout reason.

    A 429 ends generation; any other HTTP failure is tolerated. Non-HTTP
    failures are tolerated unless they carry an explicit ``retryable=False``
    (durable budget/admission decisions must keep their terminal
    retryability instead of degrading into a generic HTTP 500).
    """
    from fastapi import HTTPException

    if isinstance(error, HTTPException):
        return error.status_code == 429
    return getattr(error, "retryable", None) is False


def log_content_brief_skipped(presentation_id: object, error: BaseException) -> None:
    """Record the ``skipped`` status for a tolerated non-timeout brief failure."""
    LOGGER.info(
        "content brief status=%s presentation=%s reason=%s: %s",
        BRIEF_STATUS_SKIPPED,
        presentation_id,
        type(error).__name__,
        error,
    )


__all__ = [
    "BRIEF_STATUS_APPLIED",
    "BRIEF_STATUS_RETRY_APPLIED",
    "BRIEF_STATUS_REUSED",
    "BRIEF_STATUS_SKIPPED",
    "BRIEF_STATUS_TIMED_OUT_FALLBACK",
    "RETRY_SKIPPED_BELOW_FLOOR",
    "RETRY_SKIPPED_CALLS_EXHAUSTED",
    "BriefStatus",
    "ContentBriefOutcome",
    "ContentBriefTimeoutPolicy",
    "brief_failure_is_terminal",
    "content_brief_input_chars",
    "content_brief_timeout_policy",
    "log_content_brief_skipped",
    "merge_brief_context",
    "reduced_brief_context",
    "run_content_brief_with_policy",
]
