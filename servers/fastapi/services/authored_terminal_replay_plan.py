"""Replay the existing deck decisions for a terminal authored slide remake."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


def replayed_plan_kwargs(
    indices: Sequence[int],
    *,
    chapter_plan: Any = None, illustration_plan: Any = None,
    infographic_plan: Any = None, style: Any = None, brand: Any = None,
    instructions: object = None, exact_reconstruction: bool = False,
    with_assets: bool = False,
) -> dict[str, Any]:
    """The deck's own per-slide plan decisions, replayed into a revise call.

    A repaired or re-authored slide must be produced under the same deck-wide
    decisions its first draft was: without the chapter prefix, the one slide a
    revise pass touched is the one whose eyebrow disagrees with the deck.
    ``None`` stays distinct from an empty plan -- with no plan at all the
    legacy behaviour still lets a slide request an illustration.

    An exact-reconstruction deck replays only the chapter prefixes; its
    illustration and infographic slots are dictated by the reference page.
    ``with_assets`` adds the deck-level infographic asset context and the image
    service a reviser needs for an allowed replacement slot; the bounded patch
    round passes neither, because a local patch may not replace an image.
    """

    from services import authored_terminal_slide_fallback as runtime

    kwargs: dict[str, Any] = {}
    if chapter_plan is not None:
        kwargs["chapter_prefixes"] = [
            chapter_plan.eyebrow_prefix_at(index) for index in indices
        ]
    if exact_reconstruction:
        return kwargs
    if illustration_plan is not None:
        kwargs["illustration_slots"] = [
            illustration_plan.allows(index) for index in indices
        ]
    if infographic_plan is not None:
        kwargs["infographic_briefs"] = [infographic_plan.at(i) for i in indices]
        if with_assets:
            kwargs["infographic_assets"] = runtime.build_infographic_asset_context(
                style=style, brand=brand, instructions=instructions
            )
    if with_assets and runtime.deck_has_picture_source(style):
        kwargs["image_service"] = runtime.ImageGenerationService(runtime.get_images_directory())
    return kwargs
