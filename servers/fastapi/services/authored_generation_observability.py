"""Content-free operational observations for durable authored generation.

The application exports operational metrics as structured JSON log events.
This module keeps the schema and dimensions closed so prompts, authored HTML,
job/request identifiers, paths, credentials and raw exception text cannot be
added accidentally.
"""

from __future__ import annotations

import json
import logging
from math import isfinite
from typing import Any, Literal


LOGGER = logging.getLogger(__name__)

AuthoredObservationName = Literal[
    "slide_failure",
    "resume",
    "quota_wait",
    "artifact_sweep",
    "stalled_recovery",
]

_EVENT_NAMES = {
    "slide_failure",
    "resume",
    "quota_wait",
    "artifact_sweep",
    "stalled_recovery",
}
_STAGES = {"author", "illustrate", "render", "persist"}
_FAILURE_CODES = {
    "authored_attempt_lost",
    "authored_injected_failure",
    "authored_slide_author_failed",
    "authored_slide_illustrate_failed",
    "authored_slide_persist_failed",
    "authored_slide_render_failed",
    "authored_unexpected_failure",
}
_SWEEP_OUTCOMES = {"success", "failure"}
_STALLED_STATES = {"running", "staging"}
_MAX_COUNT = 1_000_000
_MAX_SLIDES = 10_000
_MAX_BYTES = 1_099_511_627_776
_MAX_DURATION_MS = 86_400_000.0


def _bounded_int(name: str, value: int, *, maximum: int = _MAX_COUNT) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        or value > maximum
    ):
        raise ValueError(f"{name} must be a bounded nonnegative integer")
    return value


def _bounded_duration_ms(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value < 0
        or value > _MAX_DURATION_MS
    ):
        raise ValueError("duration_ms must be a bounded millisecond value")
    return float(value)


def build_authored_generation_observation(
    *,
    name: AuthoredObservationName,
    stage: str | None = None,
    code: str | None = None,
    slide_number: int | None = None,
    retryable: bool | None = None,
    attempt_number: int | None = None,
    completed_slides: int | None = None,
    total_slides: int | None = None,
    duration_ms: float | None = None,
    polls: int | None = None,
    outcome: str | None = None,
    scanned_terminal: int | None = None,
    deleted_terminal: int | None = None,
    scanned_orphan: int | None = None,
    deleted_orphan: int | None = None,
    deleted_bytes: int | None = None,
    deleted_files: int | None = None,
    skipped_budget: int | None = None,
    errors: int | None = None,
    recovered_jobs: int | None = None,
    prior_state: str | None = None,
) -> dict[str, Any]:
    """Build one validated, low-cardinality authored metric event."""

    if name not in _EVENT_NAMES:
        raise ValueError("name is not in the safe allowlist")
    event: dict[str, Any] = {
        "schema_version": 1,
        "pipeline": "authored",
        "name": name,
    }

    if name == "slide_failure":
        if stage not in _STAGES:
            raise ValueError("stage is not in the safe allowlist")
        if code not in _FAILURE_CODES:
            raise ValueError("code is not in the safe allowlist")
        if not isinstance(retryable, bool):
            raise ValueError("retryable must be boolean")
        if slide_number is None:
            raise ValueError("slide_number is required")
        slide = _bounded_int("slide_number", slide_number, maximum=_MAX_SLIDES)
        if slide < 1:
            raise ValueError("slide_number must be positive")
        event.update(
            stage=stage,
            code=code,
            slide_number=slide,
            retryable=retryable,
        )
    elif name == "resume":
        if attempt_number is None or completed_slides is None or total_slides is None:
            raise ValueError("resume counts are required")
        attempt = _bounded_int("attempt_number", attempt_number)
        completed = _bounded_int(
            "completed_slides", completed_slides, maximum=_MAX_SLIDES
        )
        total = _bounded_int("total_slides", total_slides, maximum=_MAX_SLIDES)
        if attempt < 2 or completed > total:
            raise ValueError("resume counts are inconsistent")
        event.update(
            attempt_number=attempt,
            resume_count=attempt - 1,
            completed_slides=completed,
            total_slides=total,
        )
    elif name == "quota_wait":
        if duration_ms is None or polls is None:
            raise ValueError("quota wait duration and polls are required")
        poll_count = _bounded_int("polls", polls)
        if poll_count < 1:
            raise ValueError("polls must be positive")
        event.update(
            duration_ms=_bounded_duration_ms(duration_ms),
            polls=poll_count,
        )
    elif name == "artifact_sweep":
        if outcome not in _SWEEP_OUTCOMES:
            raise ValueError("outcome is not in the safe allowlist")
        event["outcome"] = outcome
        if outcome == "success":
            sweep_counts = {
                "scanned_terminal": scanned_terminal,
                "deleted_terminal": deleted_terminal,
                "scanned_orphan": scanned_orphan,
                "deleted_orphan": deleted_orphan,
                "deleted_bytes": deleted_bytes,
                "deleted_files": deleted_files,
                "skipped_budget": skipped_budget,
                "errors": errors,
            }
            if any(value is None for value in sweep_counts.values()):
                raise ValueError("successful sweep counts are required")
            event.update(
                {
                    key: _bounded_int(
                        key,
                        value,
                        maximum=_MAX_BYTES if key == "deleted_bytes" else _MAX_COUNT,
                    )
                    for key, value in sweep_counts.items()
                    if value is not None
                }
            )
    elif name == "stalled_recovery":
        if prior_state not in _STALLED_STATES:
            raise ValueError("prior_state is not in the safe allowlist")
        if recovered_jobs is None:
            raise ValueError("recovered_jobs is required")
        recovered = _bounded_int("recovered_jobs", recovered_jobs)
        if recovered < 1:
            raise ValueError("recovered_jobs must be positive")
        event.update(prior_state=prior_state, recovered_jobs=recovered)

    return event


def log_authored_generation_observation(
    *,
    logger: logging.Logger = LOGGER,
    **values: Any,
) -> dict[str, Any]:
    """Log and return one validated authored observation."""

    event = build_authored_generation_observation(**values)
    logger.info(
        "authored_generation %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )
    return event


def log_authored_slide_failure_observation(
    *,
    stage: str,
    code: str,
    slide_number: int,
    retryable: bool,
    logger: logging.Logger = LOGGER,
) -> dict[str, Any]:
    """Log a slide failure without letting a new code break recovery.

    Error codes can evolve independently from this low-cardinality schema.
    Unknown values therefore collapse to a safe fallback instead of leaking
    arbitrary text or raising after the durable failure record has committed.
    """

    safe_code = code if code in _FAILURE_CODES else "authored_unexpected_failure"
    return log_authored_generation_observation(
        logger=logger,
        name="slide_failure",
        stage=stage,
        code=safe_code,
        slide_number=slide_number,
        retryable=retryable,
    )


__all__ = [
    "build_authored_generation_observation",
    "log_authored_generation_observation",
    "log_authored_slide_failure_observation",
]
