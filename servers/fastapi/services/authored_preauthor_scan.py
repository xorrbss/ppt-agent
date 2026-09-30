"""What one attempt's checkpoints say about which slides still need authoring.

Extracted from ``services.authored_presentation_pipeline`` (above the file-size
ratchet) so the pre-authoring pass reads as: scan the checkpoints, schedule the
work, report what is still unresolved.

Nothing here talks to a provider or to the database directly; the caller hands
in one ``read_checkpoint`` coroutine and one attempt-count function, which keeps
this classification unit-testable and keeps the fencing rules with the caller
that owns the attempt token.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from services.authored_checkpoint_service import AuthoredStageError, fail_stage
from services.generation_core.authoring.author_deck import (
    AuthoredBatchFailure,
    author_correction_from_stored_failure,
)


@dataclass
class PreauthorScan:
    """Slides split by what this attempt's checkpoints already record."""

    # Never authored in this attempt: due one initial call.
    initial: list[int] = field(default_factory=list)
    # Authored once and rejected: due the one recovery call.
    resumed_recovery: list[int] = field(default_factory=list)
    # Already completed; no call is due.
    successful: set[int] = field(default_factory=set)
    # Why a slide is currently unresolved, keyed by slide index.
    failures: dict[int, AuthoredBatchFailure] = field(default_factory=dict)
    # Why an earlier attempt of this operation rejected a slide. A retry exists
    # to redo exactly those slides, and re-sending the prompt that already
    # failed reproduces the same rejection, so the reason travels with the
    # slide as its correction.
    carried_failures: dict[int, AuthoredBatchFailure] = field(default_factory=dict)
    # Provider calls each slide has already spent in this attempt.
    attempt_counts: dict[int, int] = field(default_factory=dict)


def _exhausted_failure(index: int, checkpoint: Any, prior: int) -> AuthoredBatchFailure:
    prior_error = (
        checkpoint.error
        if checkpoint is not None and isinstance(checkpoint.error, Mapping)
        else None
    )
    prior_code = str((prior_error or {}).get("code") or "")
    error = AuthoredStageError(
        slide_index=index,
        stage="author",
        code=(
            prior_code
            if prior_code.startswith("public_provider_")
            else "authored_provider_attempt_budget_exhausted"
        ),
        retryable=False,
        provider_attempt_count=prior,
        # Without this the retry records only that the slide ran out of calls,
        # replacing the first attempt's record of why it failed -- the only
        # thing that says where to look.
        prior_failure=prior_error,
    )
    return AuthoredBatchFailure(error, str(error))


async def scan_preauthor_checkpoints(
    slide_count: int,
    *,
    read_checkpoint: Callable[[int], Awaitable[Any]],
    attempt_count: Callable[[Any], int],
) -> PreauthorScan:
    """Classify every slide of one deck from its checkpoint row."""

    scan = PreauthorScan()
    for index in range(slide_count):
        checkpoint = await read_checkpoint(index)
        if (
            checkpoint is not None
            and checkpoint.author_status == "completed"
            and checkpoint.author_html
        ):
            scan.successful.add(index)
            continue
        prior = attempt_count(checkpoint)
        scan.attempt_counts[index] = prior
        carried_hint = author_correction_from_stored_failure(
            getattr(checkpoint, "error", None)
        )
        if carried_hint:
            scan.carried_failures[index] = AuthoredBatchFailure(
                RuntimeError("authored_prior_attempt_failed"), carried_hint
            )
        if prior >= 2:
            scan.failures[index] = _exhausted_failure(index, checkpoint, prior)
        elif prior == 1:
            scan.resumed_recovery.append(index)
            scan.failures[index] = scan.carried_failures.get(
                index,
                AuthoredBatchFailure(
                    RuntimeError("authored_prior_attempt_failed"),
                    "Regenerate this slide while obeying every original contract.",
                ),
            )
        else:
            scan.initial.append(index)
    return scan


async def report_unresolved_slides(
    slide_count: int,
    *,
    successful: set[int],
    failures: Mapping[int, AuthoredBatchFailure],
    attempt_counts: Mapping[int, int],
    stage_failure: Callable[..., AuthoredStageError],
    generation_job: Any = None,
    attempt_token: str | None = None,
    fencing_token: Any = None,
) -> None:
    """Record every still-unresolved slide, then raise the first one's error.

    Returns quietly when the whole deck is authored. Failing every slide before
    raising is deliberate: the operation retry reads the checkpoint rows, not
    the exception, so a slide whose failure was never written would silently
    reopen its provider budget.
    """

    unresolved = [
        index
        for index in range(slide_count)
        if index not in successful and index in failures
    ]
    if not unresolved:
        return
    for index in unresolved:
        error = failures[index].error
        stage_error = (
            error
            if isinstance(error, AuthoredStageError)
            else stage_failure(
                index,
                error,
                provider_attempt_count=attempt_counts.get(index),
            )
        )
        if generation_job is not None:
            await fail_stage(
                job_id=generation_job.id,
                slide_index=index,
                stage="author",
                attempt_token=attempt_token,
                fencing_token=fencing_token,
                error=stage_error,
                request_id=generation_job.request_id,
            )
    first = unresolved[0]
    error = failures[first].error
    if isinstance(error, AuthoredStageError):
        raise error
    raise stage_failure(
        first, error, provider_attempt_count=attempt_counts.get(first)
    ) from error


__all__ = [
    "PreauthorScan",
    "report_unresolved_slides",
    "scan_preauthor_checkpoints",
]
