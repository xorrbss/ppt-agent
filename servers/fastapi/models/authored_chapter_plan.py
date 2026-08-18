"""Deck-scoped authored chapter plan.

A Korean proposal deck is read as a numbered document: every page belongs to a
chapter, and the reader traces the argument from chapter, to sub-clause, to
evidence.  Nothing in the authored path used to carry that structure — a slide
knew its own ``content`` and a keyword-derived ``role`` and nothing else — so
neither a breadcrumb eyebrow ("Ⅱ. 제안개요 > 목표 아키텍처") nor a chapter
divider slide could be produced reliably.

This module is that missing structure.  Like the header contract, the plan is
decided ONCE per deck, frozen, and replayed: the same plan is persisted with the
deck so a later single-page remake reproduces the eyebrow its siblings carry.

Two shapes live here and they are deliberately distinct:

``AuthoredChapterPlanDraft``
    What the model returns.  Its indices point into the SOURCE outline, before
    any divider slide exists.

``AuthoredChapterPlan``
    What the deck is authored and published against.  Its indices point into the
    FINAL outline, after the agenda and divider slides have been inserted.  This
    is the only shape anything downstream reads, which is why it — and not the
    draft — is the one that gets persisted.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CHAPTER_PLAN_SCHEMA_VERSION = "authored-chapter-plan/v1"

#: What joins the chapter prefix to the slide's own section name in the eyebrow.
DEFAULT_CHAPTER_SEPARATOR = " > "

#: Roles the plan assigns to the slides it inserts.  ``SECTION`` is a chapter
#: divider — a full-bleed composition that opts out of the deck's header band,
#: like the cover.  ``AGENDA`` is the table-of-contents page, an ordinary
#: header-carrying slide that simply belongs to no chapter.
SECTION_ROLE = "SECTION"
AGENDA_ROLE = "AGENDA"

#: A deck shorter than this reads as a deck, not as a document: dividers would
#: cost it a large share of its pages to say what its cover already says. The
#: floor is deliberately a plan-level rule rather than advice in a prompt, so a
#: short deck cannot be given chapters by a model that thought them elegant.
MIN_SLIDES_FOR_CHAPTER_PLAN = 10

#: Bound on how finely a deck may be chaptered. A chapter per two slides is a
#: numbering scheme, not a structure, and every chapter costs a divider page.
MAX_CHAPTERS = 9
MIN_CHAPTERS = 2

#: Punctuation a document puts AFTER a chapter marker to separate it from the
#: chapter's name. A source that writes "Ⅱ. 제안개요" reports the numeral as
#: "Ⅱ." because that is how the page reads, and every renderer here adds the
#: separator itself — so the marker is stored without it, or the deck prints
#: "Ⅱ.. 제안개요" on every page of that chapter.
_NUMERAL_TRAILING_PUNCTUATION = ".)·、,: 	"


def normalize_numeral(value: str) -> str:
    """Return a chapter marker with any trailing separator punctuation removed."""
    stripped = value.strip().rstrip(_NUMERAL_TRAILING_PUNCTUATION)
    # A marker that is nothing BUT punctuation is not a marker; keep what was
    # given rather than failing the whole plan on a field bound of 1 character.
    return stripped or value.strip()


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthoredChapterDraft(_Spec):
    """One chapter as the planning model reports it."""

    #: The chapter's own marker as the deck writes it: "Ⅱ", "2", "제2장".
    #: Kept verbatim rather than derived from the position, because which
    #: numbering a document uses is part of how it reads.
    numeral: str = Field(min_length=1, max_length=8)
    title: str = Field(min_length=1, max_length=40)
    #: Index into the SOURCE outline of the chapter's first content slide.
    first_slide_index: int = Field(ge=0)

    _normalize_numeral = field_validator("numeral")(normalize_numeral)


class AuthoredChapterPlanDraft(_Spec):
    """The planning model's answer, before any slide has been inserted."""

    #: Whether this deck opens with a table-of-contents page. The model decides
    #: it from the deck's own request, so a prompt that asked for 목차 in prose
    #: reaches the deck the same way the request flag does.
    include_agenda: bool = False
    #: What the agenda page is called in the deck's own language ("목차").
    #: Empty when no agenda was asked for.
    agenda_title: str = Field(default="", max_length=24)
    #: Index into the SOURCE outline of a page that is ALREADY this deck's table
    #: of contents. The outline writer produces one whenever the request asked
    #: for it, so without this the deck would carry two: the writer's and the
    #: one this plan inserts, each listing its own idea of the chapters.
    existing_agenda_index: Optional[int] = Field(default=None, ge=0)
    chapters: tuple[AuthoredChapterDraft, ...] = ()


class AuthoredChapter(_Spec):
    """One chapter, resolved against the final outline."""

    numeral: str = Field(min_length=1, max_length=8)
    title: str = Field(min_length=1, max_length=40)
    #: Index into the FINAL outline of this chapter's first slide.
    start_index: int = Field(ge=0)

    _normalize_numeral = field_validator("numeral")(normalize_numeral)
    #: Whether ``start_index`` is the chapter's divider slide rather than its
    #: first content slide. A plan carries this instead of assuming it, so a
    #: deck whose dividers were suppressed still describes itself correctly.
    has_divider: bool = True

    @property
    def prefix(self) -> str:
        """The chapter half of a breadcrumb eyebrow: ``Ⅱ. 제안개요``."""
        return f"{self.numeral}. {self.title}"


class AuthoredChapterPlan(_Spec):
    """The frozen, deck-wide chapter structure, in final-outline indices."""

    schema_version: Literal["authored-chapter-plan/v1"] = CHAPTER_PLAN_SCHEMA_VERSION
    separator: str = Field(default=DEFAULT_CHAPTER_SEPARATOR, min_length=1, max_length=8)
    #: Final-outline index of the agenda page, when the deck has one.
    agenda_index: Optional[int] = Field(default=None, ge=0)
    chapters: tuple[AuthoredChapter, ...] = Field(min_length=MIN_CHAPTERS)

    @model_validator(mode="after")
    def _indices_are_ordered(self) -> "AuthoredChapterPlan":
        if len(self.chapters) > MAX_CHAPTERS:
            raise ValueError(f"a deck may carry at most {MAX_CHAPTERS} chapters")
        previous = -1
        for chapter in self.chapters:
            if chapter.start_index <= previous:
                raise ValueError("chapter start indices must strictly ascend")
            previous = chapter.start_index
        numerals = [chapter.numeral.strip() for chapter in self.chapters]
        if len(set(numerals)) != len(numerals):
            raise ValueError("chapter numerals must be unique within a deck")
        if self.agenda_index is not None and (
            self.agenda_index >= self.chapters[0].start_index
        ):
            raise ValueError("the agenda page must precede the first chapter")
        return self

    def chapter_at(self, index: int) -> Optional[AuthoredChapter]:
        """The chapter a slide belongs to, or None for front matter.

        Slides before the first chapter — the cover and the agenda — belong to
        no chapter on purpose: labelling them with the first chapter's number
        would claim a page for a chapter that has not started.
        """
        found: Optional[AuthoredChapter] = None
        for chapter in self.chapters:
            if chapter.start_index > index:
                break
            found = chapter
        return found

    def role_at(self, index: int) -> Optional[str]:
        """The role the plan forces on a slide it inserted, if it inserted one."""
        if self.agenda_index is not None and index == self.agenda_index:
            return AGENDA_ROLE
        for chapter in self.chapters:
            if chapter.has_divider and chapter.start_index == index:
                return SECTION_ROLE
        return None

    def eyebrow_prefix_at(self, index: int) -> Optional[str]:
        """The literal opening of this slide's eyebrow, separator included.

        None means the slide writes an ordinary section-name eyebrow: the deck's
        front matter belongs to no chapter, and a divider slide carries no
        header band at all.
        """
        if self.role_at(index) is not None:
            return None
        chapter = self.chapter_at(index)
        if chapter is None:
            return None
        return f"{chapter.prefix}{self.separator}"

    def outline_labels(self) -> tuple[str, ...]:
        """Every chapter as ``Ⅱ. 제안개요``, in order — the agenda page's body."""
        return tuple(chapter.prefix for chapter in self.chapters)


__all__ = [
    "AGENDA_ROLE",
    "normalize_numeral",
    "AuthoredChapter",
    "AuthoredChapterDraft",
    "AuthoredChapterPlan",
    "AuthoredChapterPlanDraft",
    "CHAPTER_PLAN_SCHEMA_VERSION",
    "DEFAULT_CHAPTER_SEPARATOR",
    "MAX_CHAPTERS",
    "MIN_CHAPTERS",
    "MIN_SLIDES_FOR_CHAPTER_PLAN",
    "SECTION_ROLE",
]
