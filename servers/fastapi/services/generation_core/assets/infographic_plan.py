"""Resolve a deck's infographic plan once, before any slide is authored.

This is the single place that decides which figure each page of an authored
deck is drawn as, which pages are the deck's hero moments, and — derived from
that — which pages carry a generated picture. It runs on the FINAL outline,
after the chapter plan has inserted its dividers and agenda, because the
plan's indices are replayed into the per-slide prompts and into a later
single-page remake, and those read the deck as it will actually be published.

Returning None here means "no plan", never "no figures". A deck with no plan
keeps the behaviour this fork had before plans existed, where every slide
composes itself; a deck with a plan of all-``none`` pages has been told,
deliberately, that it is a deck of type.
"""

from __future__ import annotations

import logging
from typing import Optional

from models.authored_illustration_plan import (
    AuthoredIllustrationPlan,
    illustrations_are_prohibited,
)
from models.authored_infographic_plan import (
    AuthoredInfographicPlan,
    without_scenes,
)
from models.presentation_outline_model import PresentationOutlineModel
from services.generation_core.assets.infographic_assets import deck_has_picture_source
from utils.llm_calls.author_infographic_plan import generate_infographic_plan

LOGGER = logging.getLogger(__name__)


def deck_can_illustrate(style: object, instructions: Optional[str]) -> bool:
    """Whether this deck is able to spend a generated picture at all.

    A scene's object cutout comes from a transparent-capable image provider
    and needs no style prompt, so either that provider or a style illustration
    prompt keeps the scene. A request that refused pictures is honoured
    exactly: the scene is demoted to its HTML-only fallback before the plan is
    frozen.
    """
    if not deck_has_picture_source(style):
        return False
    return not illustrations_are_prohibited(instructions)


async def resolve_infographic_plan(
    outline: PresentationOutlineModel,
    brand,
    style: object = None,
    *,
    roles: list[str],
    instructions: Optional[str] = None,
    vector_illustrations: bool = False,
    template_picture_policy: Optional[dict] = None,
) -> Optional[AuthoredInfographicPlan]:
    """Decide this deck's figures once, from its own pages.

    With ``vector_illustrations`` the deck's scenes are drawn as vector SVG by
    the model, so a scene needs no image provider or style prompt; only a
    request that refuses pictures still demotes them.

    Returns None only when the planning call failed; the caller then authors
    the deck it already had. Unlike the illustration plan this replaced, it
    runs for every style — a figure is drawn in HTML and needs no image prompt
    — and for a deck of any length.
    """
    slides = list(outline.slides)
    if not slides:
        return None
    if len(roles) != len(slides):
        raise ValueError("infographic plan roles must match the outline")
    brief = getattr(style, "brief", None)
    if template_picture_policy is not None:
        from services.generation_core.assets.vector_offer import validate_template_picture_policy

        validate_template_picture_policy(template_picture_policy)
        # Only the admitted SVG template path adds this guidance. The style is
        # the frozen template projection supplied by the driver, never its text.
        guidance = (
            "Plan content-relevant vector pictures where they explain the slide: scenes, "
            "small matched step pictures for process/cycle/timeline/roadmap figures, "
            "before/after pairs, and percentage pictographs for gauge/KPI figures. "
            "Keep labels and values faithful to the slide content and preserve quiet pages. "
            "Do not add pictures to cover, section or closing pages. "
            "Follow the frozen template palette and visual style; never copy source wording."
        )
        brief = (brief.strip() + "\n" if isinstance(brief, str) and brief.strip() else "") + guidance
        vector_illustrations = True
    plan = await generate_infographic_plan(
        topic=getattr(brand, "topic", "") or "",
        language=getattr(brand, "language", None) or "Korean",
        slide_contents=[slide.content for slide in slides],
        roles=list(roles),
        style_brief=brief.strip() if isinstance(brief, str) and brief.strip() else None,
        instructions=instructions,
        vector_illustrations=vector_illustrations,
    )
    if plan is None:
        return None
    if vector_illustrations:
        if illustrations_are_prohibited(instructions):
            plan = without_scenes(plan)
    elif not deck_can_illustrate(style, instructions):
        plan = without_scenes(plan)
    LOGGER.info(
        "[authored] infographic plan resolved: figures=%d scenes=%d slides=%d "
        "archetypes=%s",
        plan.figure_count,
        len(plan.scene_indices()),
        len(slides),
        [slide.archetype for slide in plan.slides],
    )
    return plan


def derive_illustration_plan(
    plan: Optional[AuthoredInfographicPlan],
) -> Optional[AuthoredIllustrationPlan]:
    """The picture slots this infographic plan implies: its scenes, in order.

    No infographic plan means no illustration plan, so a deck whose figure
    planning failed keeps the pre-plan behaviour end to end rather than losing
    its pictures for an unrelated reason. A plan with no scenes yields an EMPTY
    illustration plan, which is a decision and is persisted as one.
    """
    if plan is None:
        return None
    return AuthoredIllustrationPlan(slide_indices=plan.scene_indices())


def load_infographic_plan(theme: object) -> Optional[AuthoredInfographicPlan]:
    """Rebuild a persisted plan, treating an unreadable one as absent."""
    if not isinstance(theme, dict):
        return None
    payload = theme.get("infographicPlan")
    if not isinstance(payload, dict):
        return None
    try:
        return AuthoredInfographicPlan.model_validate(payload)
    except Exception as error:  # noqa: BLE001 — persisted data is untrusted here
        LOGGER.warning("Persisted infographic plan is unusable: %s", error)
        return None


__all__ = [
    "deck_can_illustrate",
    "derive_illustration_plan",
    "load_infographic_plan",
    "resolve_infographic_plan",
]
