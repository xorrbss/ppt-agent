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
from typing import List, Optional, Sequence

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.authored_infographic_plan import AuthoredInfographicPlan
from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel
from services.authored_deck_chapter_plan import resolve_chapter_plan
from services.authored_deck_infographic_plan import (
    derive_illustration_plan as derive_illustration_plan_from_figures,
    resolve_infographic_plan,
)
from utils.get_env import is_authored_infographic_plan_enabled
from utils.get_env_programme import get_authored_illustration_share
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


#: Roles that never carry a generated picture, whatever the share is. A cover
#: and a chapter divider compose on a full field, an agenda is a list, and a
#: closing page is one line: a picture on any of them is decoration.
_NON_CONTENT_ROLES = frozenset({"COVER", "SECTION", "AGENDA", "CLOSING"})


def illustration_share_plan(roles: Sequence[str]) -> AuthoredIllustrationPlan:
    """The deterministic per-deck picture budget for a free-composition deck.

    Free composition (the 2026-09-05 default) resolves no infographic plan, so
    ``derive_illustration_plan`` returned ``None`` and every slide decided for
    itself whether to ask for a picture. Measured on the same deck that produced
    4/77 and then 73/77, a slide authored alone cannot answer a deck-level
    question. This is not a plan and takes no model call: it is a BUDGET —
    every k-th content slide, ``k = round(1/share)``.

    The first content slide is deliberately skipped. It is the deck's style
    anchor (design G0): its stylesheet is handed verbatim to every other slide,
    so an illustration slot on it would be inherited as part of the deck's
    visual language rather than as this one page's evidence.

    ``share`` of 0 returns an EMPTY plan, which is a decision ("this deck gets
    no pictures") and is persisted as one — not ``None``, which would restore
    the per-slide judgement this replaces.
    """
    share = get_authored_illustration_share()
    if share <= 0:
        return AuthoredIllustrationPlan(slide_indices=())
    step = max(1, round(1 / share))
    content_position = 0
    selected: list[int] = []
    for index, role in enumerate(roles):
        if (role or "").strip().upper() in _NON_CONTENT_ROLES:
            continue
        # Position 0 is the anchor slide; the budget starts at position 1 and
        # takes every step-th content slide after it.
        if content_position >= 1 and (content_position - 1) % step == 0:
            selected.append(index)
        content_position += 1
    return AuthoredIllustrationPlan(slide_indices=tuple(selected))


def derive_illustration_plan(
    plan: Optional[AuthoredInfographicPlan],
    roles: Sequence[str] = (),
) -> Optional[AuthoredIllustrationPlan]:
    """This deck's picture slots, from its figures or from the share budget.

    A deck that planned its figures derives its pictures from them, so the two
    can never disagree. A free-composition deck has no figures to derive from
    and takes :func:`illustration_share_plan` instead — but only when ``roles``
    are supplied, because the budget is defined over content slides and cannot
    be computed without them. Called with the plan alone, this is exactly the
    function it shadows, which keeps every existing call site unchanged.
    """
    if plan is not None:
        return derive_illustration_plan_from_figures(plan)
    return illustration_share_plan(roles) if roles else None


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
        illustration_plan=derive_illustration_plan(infographic_plan, roles),
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
    "illustration_share_plan",
    "resolve_deck_plans",
    "resolve_final_outline_infographic_plan",
]
