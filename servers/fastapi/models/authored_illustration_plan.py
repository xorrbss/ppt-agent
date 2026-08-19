"""Deck-scoped authored illustration plan.

Which slides of a deck deserve a generated picture is a comparative judgement:
it is answered by ranking a deck's pages against one another, not by asking each
page about itself.  Authored slides are written independently and in parallel,
so a per-slide prompt has no way to know whether ten siblings already claimed a
picture — and steering that count from inside the slide prompt oscillates
between a deck with almost none and a deck where every page carries one.

This module is the missing deck-level decision.  Like the chapter plan, it is
decided ONCE before authoring, frozen, and replayed: the same plan is persisted
with the deck so a later single-page remake gives that page the same slot, or
the same nothing, its siblings were authored with.

Two shapes live here and they are deliberately distinct:

``AuthoredIllustrationPlanDraft``
    What the planning model returns.  Its indices are in the MODEL'S OWN
    priority order, best candidate first, because the budget is enforced by
    dropping from the tail of that order.

``AuthoredIllustrationPlan``
    What the deck is authored and published against: the surviving indices in
    outline order.  This is the only shape anything downstream reads, which is
    why it — and not the draft — is the one that gets persisted.

An EMPTY plan and NO plan mean different things and must never be conflated. An
empty plan is a decision ("this deck gets no pictures"); the absence of a plan
means the deck keeps the behaviour it had before plans existed, where any slide
may author a slot.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator


ILLUSTRATION_PLAN_SCHEMA_VERSION = "authored-illustration-plan/v1"

#: A deck shorter than this is not worth planning: with the floor of the default
#: budget at three slides, a plan would hand a slot to most of the deck's content
#: pages and so decide nothing. The floor is a plan-level rule rather than advice
#: in a prompt, so a short deck cannot be planned by a model that felt like it.
MIN_SLIDES_FOR_ILLUSTRATION_PLAN = 8

#: The share of a deck that carries a picture when nobody asked for anything
#: else. A generated illustration is the most expensive element on a slide — in
#: latency, in cost, and in the reader's attention — so the ordinary deck spends
#: it on a small minority of its pages.
DEFAULT_ILLUSTRATION_SHARE = 0.15

#: Bounds on that default. Below the floor a deck's few genuinely visual pages
#: would be cut for arithmetic reasons alone; above the ceiling the pictures stop
#: being punctuation, and a long deck would pay for dozens of them.
MIN_ILLUSTRATION_BUDGET = 3
MAX_ILLUSTRATION_BUDGET = 14

#: The highest share the user's own words can raise the budget to. Someone who
#: asks for "a picture on every slide" is asking for a texture, not a literal
#: count, and a deck where every page is half stock imagery is the failure mode
#: this plan exists to prevent — so even "every slide" stops here.
MAX_ILLUSTRATION_SHARE = 0.6

#: The three readings of the caller's free-text request.
APPETITE_FEWER = -1
APPETITE_DEFAULT = 0
APPETITE_MORE = 1

#: A quantity word only shifts the budget when the request is talking about
#: pictures at all: "모든 슬라이드에 표를 넣어줘" asks for tables on every page and
#: must not be read as a demand for 46 illustrations.
_IMAGE_WORDS = (
    "illustration",
    "image",
    "picture",
    "photo",
    "visual",
    "artwork",
    "이미지",
    "사진",
    "삽화",
    "일러스트",
    "그림",
    "비주얼",
)

_MORE_WORDS = (
    "every",
    "each",
    "all ",
    "many",
    "lots",
    "plenty",
    "more",
    "rich",
    "abundant",
    "모든",
    "매 ",
    "매장",
    "매 장",
    "매 페이지",
    "각 ",
    "전부",
    "많",
    "풍부",
    "가득",
    "최대한",
)

_FEWER_WORDS = (
    "no ",
    "none",
    "without",
    "few",
    "fewer",
    "less",
    "minimal",
    "avoid",
    "sparing",
    "text only",
    "text-only",
    "없",
    "빼",
    "제외",
    "최소",
    "적게",
    "줄여",
    "줄이",
    "금지",
    "텍스트만",
    "글만",
    # Korean states a prohibition by negating the verb rather than by naming a
    # quantity, and these forms are how a real request actually said it:
    # "이미지 추가하지 말고", "이미지는 만들지 말고". Without them the request read
    # as neutral and the deck was budgeted three illustrations the user had
    # explicitly refused.
    "말고",
    "마세요",
    "마요",
    "말아",
    "않게",
    "않도록",
    "하지 마",
    "넣지",
    "생성하지",
    "만들지",
    "추가하지",
)


#: Wording that refuses illustrations outright rather than asking for restraint.
#: Kept apart from ``_FEWER_WORDS`` because the two need different answers: "few"
#: is a preference the plan negotiates against its budget, while "none" is an
#: instruction the plan must honour exactly.
_PROHIBITION_WORDS = (
    "말고",
    "마세요",
    "마요",
    "말아",
    "하지 마",
    "넣지",
    "생성하지",
    "만들지",
    "추가하지",
    "없이",
    "금지",
    "텍스트만",
    "글만",
    "no image",
    "no picture",
    "no illustration",
    "without image",
    "without picture",
    "without illustration",
    "text only",
    "text-only",
)


def illustrations_are_prohibited(instructions: Optional[str]) -> bool:
    """Whether the caller refused illustrations outright, not merely asked for few.

    Like every other reading of this text, it only counts when the request is
    talking about pictures at all, so "표를 넣지 말고" cannot zero the budget.
    """
    if not instructions:
        return False
    text = instructions.strip().lower()
    if not any(word in text for word in _IMAGE_WORDS):
        return False
    return any(word in text for word in _PROHIBITION_WORDS)


def read_illustration_appetite(instructions: Optional[str]) -> int:
    """Read how the caller's own words move the illustration budget.

    Only the direction is taken from the text; the size of the move is decided
    by :func:`resolve_illustration_budget`, so a request can never name its own
    number. A request that pulls both ways is read as the smaller one, because
    over-illustrating is the failure this plan was built to stop.
    """
    text = (instructions or "").strip().lower()
    if not text:
        return APPETITE_DEFAULT
    if not any(word in text for word in _IMAGE_WORDS):
        return APPETITE_DEFAULT
    if any(word in text for word in _FEWER_WORDS):
        return APPETITE_FEWER
    if any(word in text for word in _MORE_WORDS):
        return APPETITE_MORE
    return APPETITE_DEFAULT


def default_illustration_budget(slide_count: int) -> int:
    """How many slides of an unremarkable deck of this length get a picture."""
    if slide_count <= 0:
        return 0
    target = round(DEFAULT_ILLUSTRATION_SHARE * slide_count)
    budget = max(MIN_ILLUSTRATION_BUDGET, min(MAX_ILLUSTRATION_BUDGET, target))
    # A deck can never illustrate more pages than it has, whatever the share says.
    return min(budget, slide_count)


def resolve_illustration_budget(
    slide_count: int,
    *,
    instructions: Optional[str] = None,
    requested: Optional[int] = None,
) -> int:
    """The number of slots this deck may spend, enforced in code.

    ``requested`` is what the planning model reports the deck asks for. It is
    advice and nothing more: the caller's text sets the window, and a reported
    count outside it is clamped rather than believed. This is the whole point of
    planning centrally — the count must not be recoverable from anything a model
    chose to return.
    """
    default = default_illustration_budget(slide_count)
    if illustrations_are_prohibited(instructions):
        # A refusal is not a preference for fewer. "이미지 추가하지 말고" left the
        # ceiling at three and the deck came back with three pictures the caller
        # had explicitly refused, because reaching zero still depended on the
        # planning model reporting zero. A prohibition is decided here instead.
        return 0
    appetite = read_illustration_appetite(instructions)
    if appetite == APPETITE_MORE:
        # Asking for more raises the ceiling and pins the floor at the ordinary
        # target, so a request for pictures can never end up with fewer.
        ceiling = min(slide_count, max(default, round(MAX_ILLUSTRATION_SHARE * slide_count)))
        floor = min(default, ceiling)
    elif appetite == APPETITE_FEWER:
        # Asking for fewer makes the floor of the ordinary range the ceiling, and
        # lets the answer reach zero: "no pictures at all" is a request the plan
        # has to be able to honour exactly.
        ceiling = min(default, MIN_ILLUSTRATION_BUDGET)
        floor = 0
    else:
        ceiling = default
        floor = 0
    if requested is None:
        requested = {
            APPETITE_MORE: ceiling,
            APPETITE_DEFAULT: default,
            APPETITE_FEWER: 0,
        }[appetite]
    return max(floor, min(ceiling, requested))


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuthoredIllustrationPlanDraft(_Spec):
    """The planning model's answer, before the budget is enforced on it."""

    #: How many slides the model reads the deck's own request as asking for.
    #: Deliberately unbounded so an absurd answer costs the deck a clamp rather
    #: than the whole plan; ``None`` means the model did not report one.
    requested_count: Optional[int] = None
    #: Candidate slide indices in the MODEL'S priority order, best first — not
    #: outline order. Truncation drops from the tail, so the order is the only
    #: thing that decides which candidates survive a tight budget.
    slide_indices: tuple[int, ...] = ()

    def to_plan(self) -> "AuthoredIllustrationPlan":
        """The frozen plan for these candidates, in outline order."""
        return AuthoredIllustrationPlan(
            slide_indices=tuple(sorted({index for index in self.slide_indices if index >= 0}))
        )


class AuthoredIllustrationPlan(_Spec):
    """The frozen, deck-wide set of slides that may author an illustration."""

    schema_version: Literal["authored-illustration-plan/v1"] = (
        ILLUSTRATION_PLAN_SCHEMA_VERSION
    )
    #: Indices into the FINAL outline — the same outline the deck is authored
    #: from, after any chapter dividers and agenda page have been inserted. An
    #: empty tuple is a decision, not an absence: this deck gets no pictures.
    slide_indices: tuple[int, ...] = ()

    @model_validator(mode="after")
    def _indices_are_ordered(self) -> "AuthoredIllustrationPlan":
        previous = -1
        for index in self.slide_indices:
            if index < 0:
                raise ValueError("illustration slide indices must not be negative")
            # Ascending order is what makes the plan comparable with an outline
            # at a glance, and strictness is what rejects a duplicated index —
            # which would otherwise quietly spend two slots on one slide.
            if index <= previous:
                raise ValueError("illustration slide indices must strictly ascend")
            previous = index
        return self

    def allows(self, index: int) -> bool:
        """Whether the slide at this final-outline index may author a slot."""
        return index in self.slide_indices

    @property
    def count(self) -> int:
        return len(self.slide_indices)


__all__ = [
    "APPETITE_DEFAULT",
    "APPETITE_FEWER",
    "APPETITE_MORE",
    "AuthoredIllustrationPlan",
    "AuthoredIllustrationPlanDraft",
    "DEFAULT_ILLUSTRATION_SHARE",
    "ILLUSTRATION_PLAN_SCHEMA_VERSION",
    "MAX_ILLUSTRATION_BUDGET",
    "MAX_ILLUSTRATION_SHARE",
    "MIN_ILLUSTRATION_BUDGET",
    "MIN_SLIDES_FOR_ILLUSTRATION_PLAN",
    "default_illustration_budget",
    "read_illustration_appetite",
    "resolve_illustration_budget",
]
