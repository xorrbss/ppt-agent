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

    Three decks are left exactly as they arrive. A master-owned deck takes its
    chrome from company-standard furniture, so a divider page authored here
    would not be one of its layouts. Exact reconstruction reproduces a source
    slide-for-slide, and inserting a page it does not have is the one thing that
    mode forbids. And a caller that supplied ``slides_markdown`` — the retemplate
    and design-replay paths among them — is stating the deck's pages itself, the
    same rule the template path applies when it skips its own table of contents.
    """
    if request._authored_master_resolution is not None:
        return False
    if request.slides_markdown:
        return False
    return _reference_mode(style) != EXACT_RECONSTRUCTION


def _illustration_plan_applies(request: GeneratePresentationRequest, style) -> bool:
    """Whether this generation decides its own illustration slots.

    The two decks that must not be planned are the two the chapter plan also
    leaves alone, for the same reasons: a master-owned deck draws its imagery
    from company-standard furniture rather than from an image provider, and
    exact reconstruction reproduces a source slide-for-slide, where an invented
    illustration is precisely what the mode forbids.

    Unlike the chapter plan this DOES apply to a caller-supplied
    ``slides_markdown``. That caller is stating the deck's pages, not which of
    them earn a picture, and a fixed outline still has to choose — otherwise the
    per-slide prompts fall back to deciding independently and in parallel, which
    is the oscillation the plan exists to end.
    """
    if request._authored_master_resolution is not None:
        return False
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
    if _illustration_plan_applies(request, style):
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


__all__ = ["AuthoredDeckPlans", "resolve_deck_plans"]
