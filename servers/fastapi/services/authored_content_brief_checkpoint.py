"""Durable checkpoint for the Stage A content brief.

The public admission deadline is pinned per *operation* at admission time and
is never extended when an operation is retried. A 20-slide bench run measured
the consequence: the brief took 535 s, the outline call behind it failed with a
provider 500, and attempt 2 spent another 508 s reproducing the same brief.
Authoring started 26.5 minutes into a 40.7 minute deadline and nothing was
published. A provider 500 on the outline is ordinary at peak, not an edge case.

The outline itself is already pinned on the generation job
(``generation_job.authored_outline``) and the deck plan, header and
illustration stages already checkpoint through
``services/authored_deck_plan_checkpoint.py``. Only the brief was exposed,
purely because its stage name was not in the checkpoint stage whitelist.

Reuse rule
----------
The stored brief is served only when the digest of *this* attempt's brief
inputs -- source content, language, document context, tone, verbosity and
instructions -- matches the digest stored with it. Anything else recomputes.

Best-effort rule
----------------
The brief stage is itself optional: both call sites already fall back to the
raw source when it fails. A checkpoint is an optimisation on top of that, so a
checkpoint read or write that fails must never turn a working brief into a
failed generation. Checkpoint faults are logged and the stage continues.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import logging
from time import monotonic
from typing import Optional

from services.content_brief_policy import (
    BRIEF_STATUS_APPLIED,
    BRIEF_STATUS_RETRY_APPLIED,
    BRIEF_STATUS_REUSED,
    ContentBriefOutcome,
    ContentBriefTimeoutPolicy,
    run_content_brief_with_policy,
)
from models.content_brief_model import ContentBrief
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_deck_plan_checkpoint import (
    AuthoredDeckPlanCheckpointError,
    canonical_checkpoint_digest,
    commit_authored_deck_plan_checkpoint,
    load_authored_deck_plan_checkpoint,
)

LOGGER = logging.getLogger(__name__)

#: Stage name in the checkpoint table's `stage` CHECK constraint. Widened by
#: Alembic revision ``e8b3d1f6a4c2``.
CONTENT_BRIEF_CHECKPOINT_STAGE = "content_brief"

_INPUT_SCHEMA = "authored-content-brief-input/v1"
_PAYLOAD_SCHEMA = "authored-content-brief/v1"

#: Statuses whose brief is a genuine result of this attempt and may be stored.
#: ``timed_out_fallback`` carries no brief and ``skipped`` never reaches here.
_STORABLE_STATUSES = frozenset({BRIEF_STATUS_APPLIED, BRIEF_STATUS_RETRY_APPLIED})

BriefCallable = Callable[..., Awaitable[object]]


def content_brief_checkpoint_input(
    *,
    content: Optional[str],
    language: Optional[str],
    additional_context: Optional[str],
    tone: Optional[str],
    verbosity: Optional[str],
    instructions: Optional[str],
) -> dict[str, object]:
    """Digest-only contract covering exactly what the brief prompt reads.

    The long free-text inputs are hashed rather than embedded so the contract
    stays small; the outer digest is over this whole mapping either way.
    """

    return {
        "schemaVersion": _INPUT_SCHEMA,
        "contentDigest": canonical_checkpoint_digest(content or ""),
        "language": (language or None),
        "additionalContextDigest": canonical_checkpoint_digest(
            additional_context or ""
        ),
        "tone": (tone or None),
        "verbosity": (verbosity or None),
        "instructionsDigest": canonical_checkpoint_digest(instructions or ""),
    }


def _encode_brief(brief: ContentBrief) -> dict[str, object]:
    return {
        "schemaVersion": _PAYLOAD_SCHEMA,
        "brief": brief.model_dump(mode="json"),
    }


def _decode_brief(payload: Mapping[str, object]) -> ContentBrief:
    if payload.get("schemaVersion") != _PAYLOAD_SCHEMA:
        raise AuthoredDeckPlanCheckpointError(
            "authored_content_brief_checkpoint_schema_invalid"
        )
    stored = payload.get("brief")
    if not isinstance(stored, Mapping):
        raise AuthoredDeckPlanCheckpointError(
            "authored_content_brief_checkpoint_payload_invalid"
        )
    return ContentBrief.model_validate(dict(stored))


async def _load_reusable_brief(
    *, generation_job_id, input_digest: str
) -> ContentBrief | None:
    """Return a stored brief for the same inputs, or ``None`` to recompute."""

    try:
        payload = await load_authored_deck_plan_checkpoint(
            generation_job_id=generation_job_id,
            stage=CONTENT_BRIEF_CHECKPOINT_STAGE,
            input_digest=input_digest,
        )
    except AuthoredDeckPlanCheckpointError as error:
        if error.code == "authored_deck_checkpoint_input_conflict":
            # The stored brief was produced from different inputs. Serving it
            # would ground the outline in the wrong source, so recompute.
            LOGGER.info(
                "Content brief checkpoint ignored for job %s: stored inputs differ",
                generation_job_id,
            )
            return None
        LOGGER.warning(
            "Content brief checkpoint unusable for job %s (%s); recomputing",
            generation_job_id,
            error.code,
        )
        return None
    if payload is None:
        return None
    try:
        return _decode_brief(payload)
    except Exception:
        LOGGER.warning(
            "Content brief checkpoint for job %s did not decode; recomputing",
            generation_job_id,
            exc_info=True,
        )
        return None


async def _store_brief(
    *, generation_job: PresentationGenerationJob, input_digest: str, brief: ContentBrief
) -> None:
    """Persist one successful brief; a checkpoint fault never fails the stage."""

    try:
        await commit_authored_deck_plan_checkpoint(
            generation_job_id=generation_job.id,
            attempt_token=generation_job.attempt_token or "",
            attempt_number=generation_job.attempt_number,
            stage=CONTENT_BRIEF_CHECKPOINT_STAGE,
            input_digest=input_digest,
            payload=_encode_brief(brief),
            replace_on_input_change=True,
        )
    except Exception:
        LOGGER.warning(
            "Content brief checkpoint was not stored for job %s; a resume will "
            "recompute the brief",
            generation_job.id,
            exc_info=True,
        )


async def run_content_brief_with_checkpoint(
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
    generation_job: PresentationGenerationJob | None = None,
) -> ContentBriefOutcome:
    """Run the brief once per generation job instead of once per attempt.

    Without a ``generation_job`` there is nothing to key a checkpoint on -- the
    outline streaming endpoint has no job -- and this delegates straight to
    :func:`run_content_brief_with_policy` with identical behaviour.
    """

    if generation_job is None:
        return await run_content_brief_with_policy(
            brief_fn,
            content=content,
            language=language,
            additional_context=additional_context,
            tone=tone,
            verbosity=verbosity,
            instructions=instructions,
            presentation_id=presentation_id,
            policy=policy,
            remaining_operation_seconds=remaining_operation_seconds,
        )

    input_digest = canonical_checkpoint_digest(
        content_brief_checkpoint_input(
            content=content,
            language=language,
            additional_context=additional_context,
            tone=tone,
            verbosity=verbosity,
            instructions=instructions,
        )
    )
    started = monotonic()
    try:
        stored = await _load_reusable_brief(
            generation_job_id=generation_job.id, input_digest=input_digest
        )
    except Exception:
        LOGGER.warning(
            "Content brief checkpoint lookup failed for job %s; recomputing",
            generation_job.id,
            exc_info=True,
        )
        stored = None
    if stored is not None:
        outcome = ContentBriefOutcome(
            BRIEF_STATUS_REUSED, stored, 0.0, None, 0, monotonic() - started
        )
        LOGGER.info(
            "content brief status=%s presentation=%s job=%s attempt=%d "
            "lookup=%.1fs (no provider call)",
            outcome.status,
            presentation_id,
            generation_job.id,
            generation_job.attempt_number,
            outcome.elapsed_seconds,
        )
        return outcome

    outcome = await run_content_brief_with_policy(
        brief_fn,
        content=content,
        language=language,
        additional_context=additional_context,
        tone=tone,
        verbosity=verbosity,
        instructions=instructions,
        presentation_id=presentation_id,
        policy=policy,
        remaining_operation_seconds=remaining_operation_seconds,
    )
    # A timed-out fallback carries no brief and must not be stored as a result;
    # the returned status keeps describing what this attempt actually did.
    if outcome.status in _STORABLE_STATUSES and isinstance(
        outcome.brief, ContentBrief
    ):
        await _store_brief(
            generation_job=generation_job,
            input_digest=input_digest,
            brief=outcome.brief,
        )
    return outcome


__all__ = [
    "CONTENT_BRIEF_CHECKPOINT_STAGE",
    "content_brief_checkpoint_input",
    "run_content_brief_with_checkpoint",
]
