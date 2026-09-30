"""One ledger for what a deck's authoring pass accepted and what still failed.

The pre-authoring pass carries two containers per deck: ``successful`` -- the
slide indexes whose HTML this attempt has persisted -- and ``failures`` -- why a
slide is still unresolved. They must stay disjoint. Downstream,
``plan_authored_recovery_batches`` refuses to schedule a recovery call for a
slide that is already accepted, and that refusal kills the whole deck.

Bench run 35 (2026-09-05) died exactly there. The style anchor's first call
authored slide 1, which was booked as successful and published to the tail; the
anchor's gate then found blocking defects and asked for one remake, whose
provider-attempt reservation raised (the completed author stage had cleared the
slide's attempt metadata, so ordinal 2 conflicted). The pipeline booked that
raise as a failure of slide 1 -- a slide it had already accepted -- and the
recovery planner then saw index 1 in both sets.

Every acceptance and every rejection of a slide goes through this module, so the
invariant lives in one place instead of at the four call sites that maintain the
two containers (initial wave, anchor stage, recovery pass, streamed publish).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Mapping, MutableMapping

from services.generation_core.authoring.author_deck import AuthoredBatchFailure

logger = logging.getLogger(__name__)

# ``successful`` is a ``set[int]`` in the pipeline and a mapping in some
# callers; both accept ``update(<mapping of index -> html>)`` and ``in``.
SuccessfulSlides = Any


def record_slide_successes(
    htmls: Mapping[int, str],
    *,
    successful: SuccessfulSlides,
    failures: MutableMapping[int, AuthoredBatchFailure],
) -> None:
    """Book authored slides as accepted; they are no longer failures."""

    successful.update(htmls)
    for index in htmls:
        failures.pop(index, None)


def record_slide_failures(
    rejected: Mapping[int, AuthoredBatchFailure],
    *,
    successful: SuccessfulSlides,
    failures: MutableMapping[int, AuthoredBatchFailure],
) -> None:
    """Book unresolved slides, never one this pass has already accepted.

    A later call may fail for a slide whose HTML is already persisted -- the
    style anchor's remake is exactly that call. Its failure costs the deck
    nothing: the accepted HTML stands, so the slide must not re-enter
    ``failures`` and must not be scheduled for a recovery call.
    """

    for index, failure in rejected.items():
        if index in successful:
            logger.info(
                "authored_ledger_failure_after_success slide=%s error=%s",
                index,
                type(getattr(failure, "error", None)).__name__,
            )
            continue
        failures[index] = failure


def batch_recorder(
    *,
    successful: SuccessfulSlides,
    failures: MutableMapping[int, AuthoredBatchFailure],
    publish: Any | None = None,
) -> Callable[[tuple[int, ...], Any], bool]:
    """Book one finished authoring call into a deck's ledger.

    Returns the ``record`` callable the wave scheduler and the anchor stage
    hand their outcomes to; the answer is whether the call succeeded, which is
    what the batch policy is advanced with. ``publish`` receives the slides
    this call persisted -- ``_persist_batch_success`` has already written them,
    so the tail's pool finds their HTML and spends no call of its own on them.
    """

    def record(indexes: tuple[int, ...], outcome: Any) -> bool:
        if isinstance(outcome, BaseException):
            record_slide_failures(
                {
                    index: AuthoredBatchFailure(outcome, str(outcome))
                    for index in indexes
                },
                successful=successful,
                failures=failures,
            )
            return False
        record_slide_successes(
            outcome.htmls, successful=successful, failures=failures
        )
        record_slide_failures(
            outcome.failures, successful=successful, failures=failures
        )
        if publish is not None:
            publish.publish_many(outcome.htmls)
        return bool(outcome.htmls)

    return record


def assert_authored_ledger_disjoint(
    successful: SuccessfulSlides,
    failures: Mapping[int, AuthoredBatchFailure],
    *,
    stage: str,
) -> None:
    """Fail loudly, here, if a slide is both accepted and failed.

    The recovery planner's own guardrail is three frames away from whichever
    call wrote the stale entry; this one names the stage that drifted.
    """

    overlap = sorted(index for index in failures if index in successful)
    if overlap:
        raise ValueError(
            f"authored slide ledger drifted at {stage}: slides {overlap} are "
            "both accepted and failed"
        )


__all__ = [
    "assert_authored_ledger_disjoint",
    "batch_recorder",
    "record_slide_failures",
    "record_slide_successes",
]
