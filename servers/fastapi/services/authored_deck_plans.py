"""The deck-scoped decisions an authored generation takes before authoring.

An authored deck's slides are written independently and in parallel, so nothing
inside one slide's prompt can see the deck around it. Every question that is
really about the deck as a whole — is this a chaptered document, which of its
pages earn a generated picture — therefore has to be answered once, up front,
and replayed. This module is where those answers are gathered, so the
generation service reads as the sequence of stages it is.

Both plans are resolved against the FINAL outline, in this order: the chapter
plan may insert divider and agenda pages, and the illustration plan's indices
have to point at the outline the deck is actually authored and published from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel
from services.authored_deck_chapter_plan import resolve_chapter_plan
from services.authored_deck_illustration_plan import resolve_illustration_plan
from utils.llm_calls.author_deck import plan_deck_roles
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    normalize_reference_mode,
)


def _reference_mode(style) -> str:
    return normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )


def _chapter_plan_applies(request: GeneratePresentationRequest, style) -> bool:
    """Whether this generation may add chapter structure to its own outline.

    Exact reconstruction and caller-fixed outlines are left exactly as they
    arrive. A selected company standard no longer changes this decision because
    Authored HTML, not the independent Scene compiler, owns the user deck body.
    """
    if request.slides_markdown:
        return False
    return _reference_mode(style) != EXACT_RECONSTRUCTION


def _illustration_plan_applies(request: GeneratePresentationRequest, style) -> bool:
    """Whether this generation decides its own illustration slots.

    Exact reconstruction must not invent imagery. A selected company standard
    no longer suppresses body imagery because Authored owns the body and the
    company furniture identity is kept as a separate compatibility projection.

    Unlike the chapter plan this DOES apply to a caller-supplied
    ``slides_markdown``. That caller is stating the deck's pages, not which of
    them earn a picture, and a fixed outline still has to choose — otherwise the
    per-slide prompts fall back to deciding independently and in parallel, which
    is the oscillation the plan exists to end.
    """
    return _reference_mode(style) != EXACT_RECONSTRUCTION


@dataclass(frozen=True)
class AuthoredDeckPlans:
    """The outline the deck is authored from, and the plans that describe it."""

    outline: PresentationOutlineModel
    roles: List[str]
    chapter_plan: Optional[AuthoredChapterPlan] = None
    illustration_plan: Optional[AuthoredIllustrationPlan] = None


async def resolve_deck_plans(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    brand,
    style,
    roles: List[str],
    *,
    resolve_illustration: bool = True,
) -> AuthoredDeckPlans:
    """Resolve every deck-wide plan, in the order they depend on each other.

    Neither plan may fail a generation: each resolver fails open by returning
    None, and None means the deck is authored exactly as it was before that plan
    existed.
    """
    chapter_plan: Optional[AuthoredChapterPlan] = None
    # The deck's chapter structure is decided before its header, because whether
    # an eyebrow may open with a chapter number follows from whether chapters
    # exist at all. Expanding the outline here — before the roles, the header
    # contract, and every persisted artifact — is what keeps the divider pages,
    # the plan's indices, and the published slide list describing one deck.
    if _chapter_plan_applies(request, style):
        expansion = await resolve_chapter_plan(
            outline,
            brand,
            style,
            instructions=request.instructions,
            table_of_contents_requested=request.include_table_of_contents,
        )
        if expansion is not None:
            outline = expansion.outline
            chapter_plan = expansion.plan
            roles = plan_deck_roles(outline, chapter_plan)

    # How many of a deck's pages carry a generated picture is a comparative
    # judgement across the whole deck, so it is taken once, here, on the outline
    # the deck will actually be published with. Left inside the per-slide prompt
    # it oscillated: the same real 77-slide deck came back with 4, then 73, then
    # 1 illustrated slides, because the slides cannot see one another.
    illustration_plan: Optional[AuthoredIllustrationPlan] = None
    if resolve_illustration and _illustration_plan_applies(request, style):
        illustration_plan = await resolve_illustration_plan(
            outline,
            brand,
            style,
            instructions=request.instructions,
        )

    return AuthoredDeckPlans(
        outline=outline,
        roles=roles,
        chapter_plan=chapter_plan,
        illustration_plan=illustration_plan,
    )


async def resolve_final_outline_illustration_plan(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    brand,
    style,
) -> Optional[AuthoredIllustrationPlan]:
    """Resolve illustration choices only after chapter expansion is final.

    The generation service starts this alongside header-contract resolution.
    Both are pure deck-scoped reads of the same final outline, so neither can
    alter the other and their independent provider calls need not serialize.
    """

    if not _illustration_plan_applies(request, style):
        return None
    return await resolve_illustration_plan(
        outline,
        brand,
        style,
        instructions=request.instructions,
    )


__all__ = [
    "AuthoredDeckPlans",
    "resolve_deck_plans",
    "resolve_final_outline_illustration_plan",
]
