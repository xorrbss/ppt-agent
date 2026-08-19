"""Resolve a deck's illustration plan once, before any slide is authored.

This is the single place that decides how many of an authored deck's pages carry
a generated picture, and which ones. It runs on the FINAL outline — after the
chapter plan has inserted its dividers and agenda — because the plan's indices
are replayed into the per-slide prompts and into a later single-page remake, and
those read the deck as it will actually be published.

Returning None here means "no plan", never "no illustrations". A deck with no
plan keeps the behaviour this fork had before plans existed, where any slide may
author its own slot; a deck with an EMPTY plan has been told, deliberately, that
none of its pages earned one.
"""

from __future__ import annotations

import logging
from typing import Optional

from models.authored_illustration_plan import (
    MIN_SLIDES_FOR_ILLUSTRATION_PLAN,
    AuthoredIllustrationPlan,
)
from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.author_illustration_plan import generate_illustration_plan

LOGGER = logging.getLogger(__name__)


async def resolve_illustration_plan(
    outline: PresentationOutlineModel,
    brand,
    style: object = None,
    *,
    instructions: Optional[str] = None,
) -> Optional[AuthoredIllustrationPlan]:
    """Decide this deck's illustration slots once, from its own pages.

    Returns None for every deck that should keep today's behaviour: a style that
    does not illustrate at all, a deck too short for a budget to decide anything,
    and a planning call that failed. The caller then authors the deck it already
    had.
    """
    # A style with no illustration prompt never renders a slot, so planning one
    # would spend a model call to describe pictures that cannot exist.
    if not getattr(style, "illustration_prompt", None):
        return None
    slides = list(outline.slides)
    if len(slides) < MIN_SLIDES_FOR_ILLUSTRATION_PLAN:
        return None
    brief = getattr(style, "brief", None)
    draft = await generate_illustration_plan(
        topic=getattr(brand, "topic", "") or "",
        language=getattr(brand, "language", None) or "Korean",
        slide_contents=[slide.content for slide in slides],
        style_brief=brief.strip() if isinstance(brief, str) and brief.strip() else None,
        instructions=instructions,
    )
    if draft is None:
        return None
    try:
        plan = draft.to_plan()
    except Exception as error:  # noqa: BLE001 — a model answer is untrusted here
        # The plan model validates the draft, and a deck must never fail to
        # generate because its illustration plan did.
        LOGGER.warning("Illustration plan was unusable: %s", error)
        return None
    LOGGER.info(
        "[authored] illustration plan resolved: illustrated=%d slides=%d indices=%s",
        plan.count,
        len(slides),
        list(plan.slide_indices),
    )
    return plan


def load_illustration_plan(theme: object) -> Optional[AuthoredIllustrationPlan]:
    """Rebuild a persisted plan, treating an unreadable one as absent."""
    if not isinstance(theme, dict):
        return None
    payload = theme.get("illustrationPlan")
    if not isinstance(payload, dict):
        return None
    try:
        return AuthoredIllustrationPlan.model_validate(payload)
    except Exception as error:  # noqa: BLE001 — persisted data is untrusted here
        LOGGER.warning("Persisted illustration plan is unusable: %s", error)
        return None


__all__ = [
    "load_illustration_plan",
    "resolve_illustration_plan",
]
