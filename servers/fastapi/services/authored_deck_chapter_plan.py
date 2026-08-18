"""Resolve a deck's chapter plan and expand its outline to match.

This is the single place that decides whether an authored deck is a chaptered
document.  It runs once, before any slide is authored and before the header
contract is resolved, because the contract's eyebrow form follows from whether
chapters exist at all.

Two things come out of it, and they must stay in step: the expanded outline that
the deck is authored from, and the frozen plan whose indices point into that
same outline.  Everything downstream — the eyebrow prefix a slide is handed, the
role a divider slide is forced into, the page a single-slide remake rebuilds —
reads the plan, so it is persisted with the deck exactly as the header contract
is.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from models.authored_chapter_plan import (
    DEFAULT_CHAPTER_SEPARATOR,
    MIN_CHAPTERS,
    MIN_SLIDES_FOR_CHAPTER_PLAN,
    AuthoredChapter,
    AuthoredChapterPlan,
    AuthoredChapterPlanDraft,
)
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from utils.llm_calls.author_chapter_plan import generate_chapter_plan, slide_heading

LOGGER = logging.getLogger(__name__)

#: How many of a chapter's own slide headings its divider page may list. A
#: divider is a breath, not a second agenda: past a handful of lines it stops
#: reading as a transition and starts competing with the pages behind it.
_MAX_DIVIDER_ITEMS = 6


@dataclass(frozen=True)
class ChapterExpansion:
    """The outline the deck is authored from, and the plan that describes it."""

    outline: PresentationOutlineModel
    plan: AuthoredChapterPlan
    #: How many slides the expansion added, for the caller's own log line. The
    #: deck's requested slide count governs its CONTENT pages; dividers and the
    #: agenda are chrome added on top, like the cover, so a caller that reports
    #: a slide count needs to be able to say how many of them it did not ask for.
    inserted: int


def _divider_content(
    numeral: str, title: str, member_headings: tuple[str, ...]
) -> str:
    """The semantic content of a chapter divider page.

    It carries the chapter's own heading and the headings of the pages behind
    it, and nothing else. Every word of it comes from the outline, so the page
    is written in the deck's language without this module knowing which one that
    is.
    """
    lines = [f"## {numeral}. {title}"]
    if member_headings:
        lines.append("")
        lines.extend(f"- {heading}" for heading in member_headings)
    return "\n".join(lines)


def _agenda_content(agenda_title: str, chapter_labels: tuple[str, ...]) -> str:
    lines = [f"## {agenda_title}", ""]
    lines.extend(f"- {label}" for label in chapter_labels)
    return "\n".join(lines)


def expand_outline_with_chapters(
    outline: PresentationOutlineModel,
    draft: AuthoredChapterPlanDraft,
    *,
    separator: str = DEFAULT_CHAPTER_SEPARATOR,
) -> Optional[ChapterExpansion]:
    """Insert the agenda and divider pages a draft calls for.

    Returns None when the draft survives its own coercion with too few chapters
    to be a structure, which is the same answer as "this deck has no chapters".
    """
    source = list(outline.slides)
    starts = {
        chapter.first_slide_index: chapter
        for chapter in draft.chapters
        if 1 <= chapter.first_slide_index < len(source)
    }
    if len(starts) < MIN_CHAPTERS:
        return None

    # A chapter's members run to the slide before the next chapter opens, and
    # the closing page belongs to no chapter, so the last one stops short of it.
    boundaries = sorted(starts)
    members: dict[int, tuple[str, ...]] = {}
    for position, start in enumerate(boundaries):
        end = (
            boundaries[position + 1]
            if position + 1 < len(boundaries)
            else max(start + 1, len(source) - 1)
        )
        members[start] = tuple(
            slide_heading(source[index].content)
            for index in range(start, min(end, len(source)))
        )[:_MAX_DIVIDER_ITEMS]

    final: list[SlideOutlineModel] = []
    chapters: list[AuthoredChapter] = []
    agenda_index: Optional[int] = None

    # The cover always stays first; the agenda, when there is one, sits directly
    # behind it so the reader meets the structure before the argument.
    final.append(source[0])
    if draft.include_agenda:
        agenda_index = len(final)
        final.append(
            SlideOutlineModel(
                content=_agenda_content(
                    draft.agenda_title.strip() or "Contents",
                    tuple(
                        f"{starts[start].numeral}. {starts[start].title}"
                        for start in boundaries
                    ),
                )
            )
        )

    for index in range(1, len(source)):
        chapter = starts.get(index)
        if chapter is not None:
            chapters.append(
                AuthoredChapter(
                    numeral=chapter.numeral,
                    title=chapter.title,
                    start_index=len(final),
                    has_divider=True,
                )
            )
            final.append(
                SlideOutlineModel(
                    content=_divider_content(
                        chapter.numeral, chapter.title, members[index]
                    )
                )
            )
        final.append(source[index])

    plan = AuthoredChapterPlan(
        separator=separator,
        agenda_index=agenda_index,
        chapters=tuple(chapters),
    )
    return ChapterExpansion(
        outline=PresentationOutlineModel(slides=final),
        plan=plan,
        inserted=len(final) - len(source),
    )


async def resolve_chapter_plan(
    outline: PresentationOutlineModel,
    brand,
    style: object = None,
    *,
    instructions: Optional[str] = None,
    table_of_contents_requested: bool = False,
) -> Optional[ChapterExpansion]:
    """Decide this deck's chapters once, from its own content and request.

    Returns None for every deck that should stay as it is: one too short to read
    as a document, one the planning model finds no structure in, and one whose
    planning call failed. The caller then authors the outline it already had,
    which is the deck this fork produced before chapters existed.
    """
    slides = list(outline.slides)
    if len(slides) < MIN_SLIDES_FOR_CHAPTER_PLAN:
        return None
    brief = getattr(style, "brief", None)
    draft = await generate_chapter_plan(
        topic=getattr(brand, "topic", "") or "",
        language=getattr(brand, "language", None) or "Korean",
        slide_contents=[slide.content for slide in slides],
        style_brief=brief.strip() if isinstance(brief, str) and brief.strip() else None,
        instructions=instructions,
        table_of_contents_requested=table_of_contents_requested,
    )
    if draft is None:
        return None
    try:
        expansion = expand_outline_with_chapters(outline, draft)
    except Exception as error:  # noqa: BLE001 — a model answer is untrusted here
        # The plan model validates the expansion, and a deck must never fail to
        # generate because its chapter structure did. An unusable draft leaves
        # the outline exactly as it arrived.
        LOGGER.warning("Chapter plan expansion was unusable: %s", error)
        return None
    if expansion is None:
        return None
    LOGGER.info(
        "[authored] chapter plan resolved: chapters=%d agenda=%s inserted=%d "
        "slides=%d->%d",
        len(expansion.plan.chapters),
        expansion.plan.agenda_index is not None,
        expansion.inserted,
        len(slides),
        len(expansion.outline.slides),
    )
    return expansion


def load_chapter_plan(theme: object) -> Optional[AuthoredChapterPlan]:
    """Rebuild a persisted plan, treating an unreadable one as absent."""
    if not isinstance(theme, dict):
        return None
    payload = theme.get("chapterPlan")
    if not isinstance(payload, dict):
        return None
    try:
        return AuthoredChapterPlan.model_validate(payload)
    except Exception as error:  # noqa: BLE001 — persisted data is untrusted here
        LOGGER.warning("Persisted chapter plan is unusable: %s", error)
        return None


__all__ = [
    "ChapterExpansion",
    "expand_outline_with_chapters",
    "load_chapter_plan",
    "resolve_chapter_plan",
]
