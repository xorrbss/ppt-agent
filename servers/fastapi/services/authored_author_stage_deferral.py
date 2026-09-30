"""Carry a spent author budget from the pre-authoring pass into the slide's rescue.

The job lane authors the whole deck in one pre-authoring pass and only then
runs the per-slide tail. A slide that lost both of its author calls was
reported by ``services/authored_preauthor_scan.report_unresolved_slides``,
which failed the author stage for every unresolved slide and raised the first
one -- so the deck died before ``process_slide`` ever reached the D7
author-stage rescue (``services/authored_author_stage_rescue.py``). On the
shipped batch path a persistently failing slide therefore still killed the deck
with ``AUTHORED_PUBLISH_FLAGGED_SLIDES`` on, which is the one outcome D7 exists
to prevent.

This module is the seam between the two stages. The pre-authoring pass asks it
which unresolved slides are DEFERRABLE -- the deck's rescue predicate is
active, and the failure is the dead end the rescue answers -- and those slides
are neither failed nor raised. They flow into the pool like any other slide
(the legacy pass simply leaves them out of ``successful``; the streaming pass
publishes them explicitly, in index order), and in ``process_slide`` the same
record turns the "two attempts used, not completed" branch from an immediate
raise into the rescue's third, simplified attempt.

Nothing here widens who may be rescued. Only a deterministic authoring
rejection is deferred: a renderer policy refusal is a security boundary, a
transport failure belongs to the operation's own retry, and a terminal
``public_provider_*`` admission is the provider's answer rather than the
slide's. All three keep today's fail-closed raise, byte for byte.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from services.authored_checkpoint_service import (
    AuthoredStageError,
    is_retryable_exception,
)
from services.authored_terminal_slide_policy import terminal_fallback_active
from services.generation_core.authoring.author_deck import (
    AuthoredBatchFailure,
    AuthoredProviderBudgetExhausted,
)
from services.generation_core.authoring.author_slide_stream import (
    OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE,
    OUTPUT_TOKEN_LIMIT_GUIDANCE,
    output_token_limit_in_cause_chain,
)
from utils.renderer_isolation import RendererPolicyError
from utils.provider_idempotency import ProviderQuotaPause

LOGGER = logging.getLogger(__name__)

#: The code the author stage reports for a slide whose two calls are spent.
AUTHOR_BUDGET_EXHAUSTED_CODE = "authored_provider_attempt_budget_exhausted"

#: How far a cause chain is walked before it is treated as unresolvable. The
#: same bound the pipeline has always used for its public-provider lookup.
_MAX_CAUSE_DEPTH = 6

#: ``AuthoredStageError.as_safe_dict`` records the cause's class name, which is
#: how a renderer policy refusal stays recognisable across a resume.
_RENDERER_POLICY_EXCEPTION = RendererPolicyError.__name__


def author_rejection_code(error: BaseException) -> str:
    """Return a bounded reason for a rejected provider candidate."""

    code = getattr(error, "code", None)
    if isinstance(code, str) and code:
        return code[:160]
    message = str(error)
    for prefix, reason in (
        ("header contract violated:", "authored_header_contract_rejected"),
        ("authored_source_image_slots_missing:", "authored_source_image_slots_missing"),
        ("authored_semantic_qid_", "authored_semantic_qid_invalid"),
        ("authored_batch_duplicate_slide", "authored_batch_duplicate_slide"),
        ("authored_batch_slide_missing", "authored_batch_slide_missing"),
    ):
        if message.startswith(prefix):
            return reason
    return "authored_candidate_validation_failed"


def terminal_provider_code(error: BaseException) -> str | None:
    """Recover a non-retryable public admission cause through safe wrappers."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(_MAX_CAUSE_DEPTH):
        if current is None or id(current) in visited:
            break
        visited.add(id(current))
        code = getattr(current, "code", None)
        if (
            isinstance(code, str)
            and code.startswith("public_provider_")
            and getattr(current, "retryable", None) is False
        ):
            return code
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return None


def author_failure_retryable(error: BaseException) -> bool:
    """Keep deterministic slide-budget exhaustion out of operation retries."""

    if isinstance(error, AuthoredProviderBudgetExhausted):
        return False
    if output_token_limit_in_cause_chain(error):
        # Replaying the unchanged operation meets the same output ceiling.
        return False
    if terminal_provider_code(error) is not None:
        return False
    explicit_retryable = getattr(error, "retryable", None)
    if isinstance(explicit_retryable, bool):
        return explicit_retryable
    return is_retryable_exception(error)


def author_stage_failure(
    slide_index: int,
    error: BaseException,
    *,
    provider_attempt_count: int | None = None,
) -> AuthoredStageError | ProviderQuotaPause:
    """Build the exact author-stage envelope consumed by operation retry policy."""

    if isinstance(error, ProviderQuotaPause):
        # The durable runner owns this deck-level pause. Wrapping it as a slide
        # error would consume an operation retry and lose the reset boundary.
        return error

    # On the chatgpt transport the ceiling abort wraps the outcome guard's
    # ``public_provider_response_incomplete``; the more specific cause names
    # the envelope so the operator sees the budget, not the guard.
    token_limited = output_token_limit_in_cause_chain(error)
    code = (
        "authored_slide_author_failed"
        if token_limited
        else terminal_provider_code(error) or "authored_slide_author_failed"
    )
    return AuthoredStageError(
        slide_index=slide_index,
        stage="author",
        code=code,
        cause=error,
        retryable=author_failure_retryable(error),
        # The envelope's message is what the caller shows; a spent output
        # budget is the one author failure whose fix is a setting, so say so.
        message=OUTPUT_TOKEN_LIMIT_GUIDANCE if token_limited else None,
        provider_attempt_count=provider_attempt_count,
    )


class DeferredAuthorBudgetExhausted(AuthoredProviderBudgetExhausted):
    """One slide's spent author budget, handed to the rescue instead of raised.

    ``rescue_author_stage`` rescues an ``AuthoredProviderBudgetExhausted`` and
    nothing else, and the batch lane's own failures are the raw validation
    errors of the two rejected candidates. This carries those across the stage
    boundary in the one shape the rescue accepts, keeping the real cause so the
    log line and the failure the deck reports still name it.
    """

    def __init__(
        self,
        slide_index: int,
        *,
        code: str,
        detail: object,
        provider_attempt_count: int,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(code, detail)
        self.slide_index = int(slide_index)
        self.provider_attempt_count = int(provider_attempt_count)
        # Read by ``_author_stage_failure`` when the rescue declines the slide
        # and the deck falls back to today's non-retryable author failure.
        self.cause = cause
        self.retryable = False
        self.prior_failure = (
            {
                "code": author_rejection_code(cause),
                "exception_type": type(cause).__name__,
                "cause": str(cause),
            }
            if isinstance(cause, BaseException)
            else None
        )


def _wraps_renderer_policy_refusal(error: BaseException) -> bool:
    """Whether a renderer policy refusal is anywhere in this cause chain."""

    current: BaseException | None = error
    visited: set[int] = set()
    for _ in range(_MAX_CAUSE_DEPTH):
        if current is None or id(current) in visited:
            return False
        visited.add(id(current))
        if isinstance(current, RendererPolicyError):
            return True
        nested = getattr(current, "cause", None) or getattr(current, "__cause__", None)
        current = nested if isinstance(nested, BaseException) else None
    return False


def author_failure_is_rescuable(failure: AuthoredBatchFailure) -> bool:
    """Whether this unresolved slide is the dead end the rescue answers.

    The deterministic signal on the batch lane is that the provider ANSWERED
    and the deck rejected the answer: a rejected candidate travels with the
    failure. Without one -- a transport error, a checkpoint write that failed
    after an accepted candidate -- the failure is the operation retry's, not
    the rescue's.
    """

    error = getattr(failure, "error", None)
    if not isinstance(error, BaseException):
        return False
    if _wraps_renderer_policy_refusal(error):
        return False
    if output_token_limit_in_cause_chain(error):
        # The provider answered and ran out of output budget. The abort is
        # ``retryable`` in the stream reader's vocabulary (the slide's next
        # call may fix it), but the operation replaying the same call will
        # not; the rescue's plainer, shorter brief is the fix. Checked before
        # the terminal-code walk: on the chatgpt transport the abort wraps
        # the guard's ``public_provider_response_incomplete``.
        return True
    if terminal_provider_code(error) is not None:
        return False
    if getattr(error, "retryable", None) is True:
        return False
    if isinstance(error, AuthoredProviderBudgetExhausted):
        return True
    if isinstance(getattr(failure, "rejected_html", None), str) and failure.rejected_html:
        return True
    return not author_failure_retryable(error)


def defer_rescuable_author_failures(
    slide_count: int,
    *,
    successful: Any,
    failures: Mapping[int, AuthoredBatchFailure],
    attempt_counts: Mapping[int, int],
    profile: Any,
    reference_mode: str,
    source_reflow: bool,
) -> dict[int, DeferredAuthorBudgetExhausted]:
    """The unresolved slides the tail may rescue, keyed by slide index.

    Source-reflow decks may use the same bounded third provider call, but never
    the lossy placeholder fallback. The downstream rescue keeps that boundary.
    """

    if not source_reflow and not terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=False
    ):
        return {}
    deferred: dict[int, DeferredAuthorBudgetExhausted] = {}
    for index in range(slide_count):
        if index in successful or index not in failures:
            continue
        failure = failures[index]
        if not author_failure_is_rescuable(failure):
            continue
        error = failure.error
        deferred[index] = DeferredAuthorBudgetExhausted(
            index,
            code=(
                error.code
                if isinstance(error, AuthoredProviderBudgetExhausted)
                else OUTPUT_TOKEN_LIMIT_BUDGET_EXHAUSTED_CODE
                if output_token_limit_in_cause_chain(error)
                else AUTHOR_BUDGET_EXHAUSTED_CODE
            ),
            detail=failure.correction_hint or str(error),
            provider_attempt_count=max(2, int(attempt_counts.get(index, 2) or 2)),
            cause=error,
        )
        LOGGER.warning(
            "[authored] slide %d unresolved after its author budget (%s:%s); "
            "deferred to the tail's rescue instead of failing the deck",
            index + 1,
            type(error).__name__,
            author_rejection_code(error),
        )
    return deferred


def resumed_author_budget_is_rescuable(checkpoint_error: Any) -> bool:
    """Whether a resumed slide's recorded failure may still reach the rescue.

    A deferred slide is never ``fail_stage``d, so its checkpoint carries only
    the reserved ordinals -- no code, and no refusal. A slide that DID record a
    refusal keeps it: a renderer policy cause, a terminal public-provider code
    and a retryable failure each stay fail-closed across the resume.
    """

    if not isinstance(checkpoint_error, Mapping):
        return True
    if str(checkpoint_error.get("code") or "").startswith("public_provider_"):
        return False
    if checkpoint_error.get("retryable") is True:
        return False
    return str(checkpoint_error.get("exception_type") or "") != _RENDERER_POLICY_EXCEPTION


def deferred_author_budget_error(
    index: int,
    *,
    checkpoint: Any,
    deferrals: Mapping[int, DeferredAuthorBudgetExhausted],
    provider_attempt_count: int,
    profile: Any,
    reference_mode: str,
    source_reflow: bool,
) -> DeferredAuthorBudgetExhausted | None:
    """The budget-exhausted error to hand this slide's rescue, or ``None``.

    ``None`` is today's behaviour: ``process_slide`` raises its own
    ``AuthoredStageError`` and the deck fails.
    """

    if not terminal_fallback_active(
        profile=profile, reference_mode=reference_mode, source_reflow=source_reflow
    ) and not source_reflow:
        return None
    if provider_attempt_count != 2:
        return None
    deferred = deferrals.get(index)
    if deferred is not None:
        return deferred
    checkpoint_error = getattr(checkpoint, "error", None)
    if not resumed_author_budget_is_rescuable(checkpoint_error):
        return None
    prior_code = (
        str(checkpoint_error.get("code") or "")
        if isinstance(checkpoint_error, Mapping)
        else ""
    )
    return DeferredAuthorBudgetExhausted(
        index,
        code=prior_code or AUTHOR_BUDGET_EXHAUSTED_CODE,
        detail=(
            str(checkpoint_error.get("cause") or "")
            if isinstance(checkpoint_error, Mapping)
            else ""
        )
        or "the slide's author calls are spent",
        provider_attempt_count=provider_attempt_count,
    )


__all__ = [
    "AUTHOR_BUDGET_EXHAUSTED_CODE",
    "DeferredAuthorBudgetExhausted",
    "author_rejection_code",
    "author_failure_is_rescuable",
    "author_failure_retryable",
    "author_stage_failure",
    "defer_rescuable_author_failures",
    "deferred_author_budget_error",
    "resumed_author_budget_is_rescuable",
    "terminal_provider_code",
]
