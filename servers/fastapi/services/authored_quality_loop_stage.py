"""Adapter that runs the Stage 4 quality loop from the authored render pipeline.

Kept out of ``authored_presentation_pipeline`` so the pipeline gains one call
rather than a stage, and out of ``authored_quality_loop`` so that module stays
free of checkpoint types. Two pipeline invariants are honoured here:

* the loop runs **before** the scene envelope is sealed, so the envelope, the
  durable render checkpoint and the published slide always describe the same
  document; and
* a resumed attempt that already has a completed render for a document
  differing only in the knobs this loop varies adopts that earlier winner
  instead of re-rendering the whole candidate set.
"""

from __future__ import annotations

import logging
from typing import Any

from models.authored_infographic_plan import AuthoredSlideInfographic
from services.authored_checkpoint_html import checkpoint_stage_html
from services.authored_quality_loop import run_authored_quality_loop
from utils.authored_figure_variants import same_figure_document
from utils.get_env import is_authored_quality_loop_enabled
from utils.llm_calls.author_slide import EXACT_RECONSTRUCTION

LOGGER = logging.getLogger(__name__)


def _checkpoint_winner(checkpoint: Any, html: str) -> str | None:
    """The earlier attempt's chosen document, when it is one of this slide's."""
    if checkpoint is None or getattr(checkpoint, "render_status", None) != "completed":
        return None
    previous = checkpoint_stage_html(checkpoint, stage="render")
    if not isinstance(previous, str) or not previous:
        return None
    return previous if same_figure_document(previous, html) else None


async def apply_slide_quality_loop(
    html: str,
    *,
    slide_index: int = 0,
    checkpoint: Any = None,
    reference_mode: str,
    composition_profile: Any = None,
    slide_content: str = "",
    infographic: AuthoredSlideInfographic | None = None,
    capture=None,
) -> tuple[str, dict[str, Any]]:
    """Return the slide's chosen HTML and the receipt describing the choice.

    The receipt travels with the document rather than only reaching a log line,
    because the measurement stage persists ``variant_chosen`` and
    ``critic_score`` per slide and has no other channel to them: slides are
    authored in parallel child tasks, so nothing ambient survives back here.
    An empty receipt means the loop did not run for this slide.
    """
    if reference_mode == EXACT_RECONSTRUCTION or not is_authored_quality_loop_enabled():
        return html, {}
    reused = _checkpoint_winner(checkpoint, html)
    if reused is not None:
        # A resumed attempt adopts the earlier winner. Its receipt belongs to
        # the attempt that chose it and is not re-derived here.
        return reused, {}
    labels = tuple(node.label for node in infographic.nodes) if infographic else ()
    prepared_candidates: dict[str, str] = {}

    async def render_candidate(candidate: str):
        # Every candidate render of this slide -- best-of variants and the one
        # critique correction alike -- goes through the deck's capture owner,
        # attributed to this slide.
        from services.authored_admission_capture import capture_once
        if capture is None:
            from utils.slide_capture import render_html_to_png_with_metrics

            capture_fn = render_html_to_png_with_metrics
        else:
            capture_fn = capture

        result = await capture_once(
            candidate, index=slide_index, capture=capture_fn
        )
        if result.prepared_html is None:
            raise RuntimeError("authored quality capture preparation unavailable")
        prepared_candidates[candidate] = result.prepared_html
        return result

    try:
        outcome = await run_authored_quality_loop(
            html,
            render=render_candidate,
            reference_mode=reference_mode,
            strict_structural_off_canvas=(
                getattr(composition_profile, "profile_id", "ordinary") != "ordinary"
            ),
            slide_context=slide_content or None,
            archetype=getattr(infographic, "archetype", None),
            labels=labels,
        )
    except Exception as error:  # noqa: BLE001 - the loop is never load-bearing
        LOGGER.warning("[authored] quality loop failed, keeping the authored slide: %s", error)
        return prepared_candidates.get(html, html), {}
    LOGGER.info(
        "[authored] slide %d quality loop: %s", slide_index + 1, outcome.receipt
    )
    return prepared_candidates.get(outcome.html, outcome.html), dict(outcome.receipt)


__all__ = ["apply_slide_quality_loop"]
