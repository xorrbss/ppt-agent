"""The deck-scoped decisions an authored generation takes before authoring.

An authored deck's slides are written independently and in parallel, so nothing
inside one slide's prompt can see the deck around it. Every question that is
really about the deck as a whole — is this a chaptered document, which figure
is each page drawn as, which pages are the deck's hero moments — therefore has
to be answered once, up front, and replayed. This module is where those
answers are gathered, so the generation service reads as the sequence of
stages it is.

The plans are resolved against the FINAL outline, in this order: the chapter
plan may insert divider and agenda pages, and the infographic plan's indices
have to point at the outline the deck is actually authored and published from.
The illustration plan is not decided separately any more: it is derived from
the infographic plan (a slide carries a picture exactly when its figure is a
scene) so the two can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.authored_infographic_plan import AuthoredInfographicPlan
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel
from services.authored_deck_chapter_plan import resolve_chapter_plan
from services.authored_deck_infographic_plan import (
    derive_illustration_plan,
    resolve_infographic_plan,
)
from utils.get_env import is_authored_infographic_plan_enabled
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
    if request.slides_markdown or getattr(request, "n_slides", None) is not None:
        return False
    return _reference_mode(style) != EXACT_RECONSTRUCTION


def _infographic_plan_applies(request: GeneratePresentationRequest, style) -> bool:
    """Whether this generation decides its own figures.

    Exact reconstruction must reproduce a source, not redraw it, and a source
    restyle keeps the source's own text bindings, which a planned figure would
    have to break. Every other deck decides — including a caller-supplied
    ``slides_markdown``: that caller is stating the deck's pages, not how each
    of them is drawn.

    The plan is off unless ``AUTHORED_INFOGRAPHIC_PLAN_ENABLED`` is set
    (2026-09-05). Planned decks came back visibly thinner than free
    composition — one server-drawn figure of 3-6 short labels per page, no side
    content, no charts — so the ordinary deck takes exactly the path it took
    before the library existed: no plan call, no figure brief, no library CSS,
    and each slide decides its own picture. This is the single seam for that
    decision; a half-on state (brief emitted, CSS absent) would fail the
    figure checker on every planned slide.
    """
    if not is_authored_infographic_plan_enabled():
        return False
    if getattr(request, "_source_content_authored_reflow", False):
        return False
    return _reference_mode(style) != EXACT_RECONSTRUCTION


@dataclass(frozen=True)
class AuthoredDeckPlans:
    """The outline the deck is authored from, and the plans that describe it."""

    outline: PresentationOutlineModel
    roles: List[str]
    chapter_plan: Optional[AuthoredChapterPlan] = None
    infographic_plan: Optional[AuthoredInfographicPlan] = None
    illustration_plan: Optional[AuthoredIllustrationPlan] = None


async def resolve_deck_plans(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    brand,
    style,
    roles: List[str],
    *,
    resolve_infographic: bool = True,
) -> AuthoredDeckPlans:
    """Resolve every deck-wide plan, in the order they depend on each other.

    No plan may fail a generation: each resolver fails open by returning None,
    and None means the deck is authored exactly as it was before that plan
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

    infographic_plan: Optional[AuthoredInfographicPlan] = None
    if resolve_infographic and _infographic_plan_applies(request, style):
        infographic_plan = await resolve_infographic_plan(
            outline,
            brand,
            style,
            roles=list(roles),
            instructions=request.instructions,
        )

    return AuthoredDeckPlans(
        outline=outline,
        roles=roles,
        chapter_plan=chapter_plan,
        infographic_plan=infographic_plan,
        illustration_plan=derive_illustration_plan(infographic_plan),
    )


async def resolve_final_outline_infographic_plan(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    brand,
    style,
    roles: List[str],
) -> Optional[AuthoredInfographicPlan]:
    """Resolve the deck's figures only after chapter expansion is final.

    The generation service starts this alongside header-contract resolution.
    Both are pure deck-scoped reads of the same final outline, so neither can
    alter the other and their independent provider calls need not serialize.
    """

    if not _infographic_plan_applies(request, style):
        return None
    return await resolve_infographic_plan(
        outline,
        brand,
        style,
        roles=list(roles),
        instructions=request.instructions,
    )


__all__ = [
    "AuthoredDeckPlans",
    "derive_illustration_plan",
    "resolve_deck_plans",
    "resolve_final_outline_infographic_plan",
]
