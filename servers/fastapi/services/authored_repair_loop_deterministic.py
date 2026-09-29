"""Step one of the single repair loop: the repairs that need no model.

The candidate itself is the existing composed deterministic repair
(``services/authored_presentation_generation._deterministic_repair_candidate``:
normalization rewrites, paint-order and overlap repairs, the off-canvas and
blank-field reducers, the header-eyebrow repair, alignment) plus the local fit
the capture callback applies. This module only spends the bounded capture and
puts the result in front of the same evaluator every other candidate faces; it
introduces no repair of its own and no new predicate.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any, Awaitable, Callable

LOGGER = logging.getLogger(__name__)


def digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


async def deterministic_candidate(
    *,
    slide_index: int,
    current_html: str,
    candidate_html: str,
    evaluator: Callable[[int, str, Mapping[str, Any]], Awaitable[Any]],
    capture: Callable[[str, int], Awaitable[tuple[str, bytes, Mapping[str, Any]]]],
) -> tuple[tuple[str, bytes, dict[str, Any], Any] | None, dict[str, Any]]:
    """Capture and assess the deterministic candidate once.

    Returns the accepted ``(html, png, metrics, assessment)`` or ``None`` --
    a rejected candidate leaves the caller's frozen admission in place -- and
    the receipt entry either way.
    """

    receipt: dict[str, Any] = {
        "cycle": -1,
        "path": "deterministic",
        "capture_calls": 0,
        "proposal_calls": 0,
        "selected_html_digest": digest(current_html),
    }
    if digest(candidate_html) == digest(current_html):
        receipt["stop_reason"] = "deterministic_unchanged"
        return None, receipt
    try:
        html, png, metrics = await capture(candidate_html, slide_index)
        receipt["capture_calls"] = 1
        assessment = await evaluator(slide_index, html, metrics)
    except Exception as error:
        # One unusable candidate may not end a deck (validation run-14).
        LOGGER.warning(
            "[authored-repair-loop] slide %d deterministic candidate failed (%s)",
            slide_index + 1,
            type(error).__name__,
        )
        receipt["stop_reason"] = "deterministic_candidate_failed"
        receipt["error_type"] = type(error).__name__
        return None, receipt
    receipt["decision"] = assessment.decision
    receipt["reasons"] = list(getattr(assessment, "reasons", ()))
    receipt["html_digest"] = digest(html)
    if assessment.decision not in {"provisional", "admitted"}:
        receipt["stop_reason"] = "deterministic_rejected"
        return None, receipt
    receipt["stop_reason"] = (
        "admitted" if assessment.decision == "admitted" else "provisional_selected"
    )
    return (html, png, dict(metrics), assessment), receipt


__all__ = ["deterministic_candidate", "digest"]
