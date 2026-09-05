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
instructions -- everything the brief has to read. Defaults:

* ``base`` 45 s (``CONTENT_BRIEF_TIMEOUT_SECONDS``, the pre-existing knob):
  a bare topic with no documents keeps the former deadline.
* ``per_kchar`` 1 s per 1,000 characters
  (``CONTENT_BRIEF_TIMEOUT_PER_KCHAR_SECONDS``): a 50k-character document
  context gets 95 s. Structured-output briefs grow with what they read, and
  the flat 45 s was tuned for prompt-only decks.
* ``max`` 180 s (``CONTENT_BRIEF_TIMEOUT_MAX_SECONDS``): reached at 135k
  characters. ``build_document_context`` admits up to 500k characters, so
  without a cap a large upload would hold the outline stage for minutes.

Retry
-----
One retry after a timeout, with a reduced input: the document context is
head-truncated to half its length (``RETRY_CONTEXT_FRACTION``) and the brief
runs with its own ``concise`` verbosity option (a shorter depth instruction
and lower schema floors in ``get_content_brief_model``). No new prompt is
introduced. The retry deadline is recomputed from the reduced input.

Status
------
``brief_status`` is one of ``applied`` (first attempt), ``retry_applied``
(retry succeeded), ``timed_out_fallback`` (both attempts timed out; the
outline proceeds on the raw source) or ``skipped`` (the brief failed for a
non-timeout reason the caller chose to tolerate). Non-timeout exceptions are
propagated unchanged so each call site keeps its own 429 / retryability
handling; the call site records ``skipped`` in that case via
:func:`log_content_brief_skipped`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Literal, Optional

LOGGER = logging.getLogger(__name__)

BriefStatus = Literal["applied", "retry_applied", "timed_out_fallback", "skipped"]

BRIEF_STATUS_APPLIED: BriefStatus = "applied"
BRIEF_STATUS_RETRY_APPLIED: BriefStatus = "retry_applied"
BRIEF_STATUS_TIMED_OUT_FALLBACK: BriefStatus = "timed_out_fallback"
BRIEF_STATUS_SKIPPED: BriefStatus = "skipped"

BASE_TIMEOUT_ENV = "CONTENT_BRIEF_TIMEOUT_SECONDS"
PER_KCHAR_TIMEOUT_ENV = "CONTENT_BRIEF_TIMEOUT_PER_KCHAR_SECONDS"
MAX_TIMEOUT_ENV = "CONTENT_BRIEF_TIMEOUT_MAX_SECONDS"

DEFAULT_BASE_TIMEOUT_SECONDS = 45.0
DEFAULT_PER_KCHAR_TIMEOUT_SECONDS = 1.0
DEFAULT_MAX_TIMEOUT_SECONDS = 180.0

#: Fraction of the document context kept (from the head) on the retry.
RETRY_CONTEXT_FRACTION = 0.5
#: The brief's own reduced-depth option, see ``generate_content_brief.py``.
RETRY_VERBOSITY = "concise"

BriefCallable = Callable[..., Awaitable[object]]


@dataclass(frozen=True, slots=True)
class ContentBriefTimeoutPolicy:
    base_seconds: float = DEFAULT_BASE_TIMEOUT_SECONDS
    per_kchar_seconds: float = DEFAULT_PER_KCHAR_TIMEOUT_SECONDS
    max_seconds: float = DEFAULT_MAX_TIMEOUT_SECONDS

    def timeout_for(self, input_chars: int) -> float:
        """Deadline in seconds for a brief that has to read ``input_chars``."""
        chars = max(0, int(input_chars))
        scaled = self.base_seconds + self.per_kchar_seconds * (chars / 1000.0)
        return max(0.0, min(self.max_seconds, scaled))


@dataclass(frozen=True, slots=True)
class ContentBriefOutcome:
    """What the policy did; ``brief`` is ``None`` only for a timed-out fallback."""

    status: BriefStatus
    brief: object | None
    timeout_seconds: float
    retry_timeout_seconds: float | None
    attempts: int
    elapsed_seconds: float


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("Ignoring non-numeric %s=%r; using %.1f", name, raw, default)
        return default
    if value < 0:
        return default
    return value


def content_brief_timeout_policy() -> ContentBriefTimeoutPolicy:
    """Read the three env knobs; every one falls back to its default when unset."""
    base = _float_env(BASE_TIMEOUT_ENV, DEFAULT_BASE_TIMEOUT_SECONDS)
    per_kchar = _float_env(PER_KCHAR_TIMEOUT_ENV, DEFAULT_PER_KCHAR_TIMEOUT_SECONDS)
    maximum = _float_env(MAX_TIMEOUT_ENV, DEFAULT_MAX_TIMEOUT_SECONDS)
    return ContentBriefTimeoutPolicy(
        base_seconds=base,
        per_kchar_seconds=per_kchar,
        max_seconds=max(maximum, base),
    )


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
) -> ContentBriefOutcome:
    """Run ``brief_fn`` under the scaled deadline, retrying once on timeout.

    ``brief_fn`` is called positionally in ``generate_content_brief``'s
    argument order so call sites can pass the module-level function they
    already expose (and tests can keep monkeypatching that name).
    Exceptions other than the timeout propagate untouched.
    """
    active = policy or content_brief_timeout_policy()
    timeout = active.timeout_for(
        content_brief_input_chars(content, additional_context, instructions)
    )
    started = monotonic()

    try:
        brief = await asyncio.wait_for(
            brief_fn(content, language, additional_context, tone, verbosity, instructions),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        LOGGER.warning(
            "Content brief timed out after %.1f s for presentation %s "
            "(input %d chars); retrying once with a reduced input",
            timeout,
            presentation_id,
            content_brief_input_chars(content, additional_context, instructions),
        )
    else:
        outcome = ContentBriefOutcome(
            BRIEF_STATUS_APPLIED, brief, timeout, None, 1, monotonic() - started
        )
        _log_outcome(outcome, presentation_id)
        return outcome

    retry_context = reduced_brief_context(additional_context)
    retry_timeout = active.timeout_for(
        content_brief_input_chars(content, retry_context, instructions)
    )
    try:
        brief = await asyncio.wait_for(
            brief_fn(content, language, retry_context, tone, RETRY_VERBOSITY, instructions),
            timeout=retry_timeout,
        )
    except asyncio.TimeoutError:
        outcome = ContentBriefOutcome(
            BRIEF_STATUS_TIMED_OUT_FALLBACK,
            None,
            timeout,
            retry_timeout,
            2,
            monotonic() - started,
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
            monotonic() - started,
        )
    _log_outcome(outcome, presentation_id)
    return outcome


def _log_outcome(outcome: ContentBriefOutcome, presentation_id: object) -> None:
    LOGGER.info(
        "content brief status=%s presentation=%s attempts=%d timeout=%.1fs "
        "retry_timeout=%s elapsed=%.1fs",
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
    "BRIEF_STATUS_SKIPPED",
    "BRIEF_STATUS_TIMED_OUT_FALLBACK",
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
