"""Deck-scoped authored illustration plan: which slides carry a generated picture.

Which slides of a deck deserve a generated picture is decided ONCE before
authoring, frozen, and replayed: the same plan is persisted with the deck so a
later single-page remake gives that page the same slot, or the same nothing,
its siblings were authored with.

The plan is no longer decided by its own model call or by a share-of-the-deck
budget. It is DERIVED from the deck's infographic plan
(``models/authored_infographic_plan.py``): a slide carries a picture exactly
when its figure is a ``scene`` — a real object with parts called out — and the
deck is able to produce one. The shape persisted here is unchanged so every
consumer of ``theme.illustrationPlan`` keeps working.

An EMPTY plan and NO plan mean different things and must never be conflated.
An empty plan is a decision ("this deck gets no pictures"); the absence of a
plan means the deck keeps the behaviour it had before plans existed, where any
slide may author a slot.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator


ILLUSTRATION_PLAN_SCHEMA_VERSION = "authored-illustration-plan/v1"

#: A prohibition only counts when the request is talking about pictures at
#: all: "표를 넣지 말고" asks for no tables and must not zero the pictures.
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

#: Wording that refuses illustrations outright. Korean states a prohibition by
#: negating the verb rather than by naming a quantity ("이미지 추가하지 말고",
#: "이미지는 만들지 말고"), so those forms are listed alongside the nouns.
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
    """Whether the caller refused generated pictures outright."""
    if not instructions:
        return False
    text = instructions.strip().lower()
    if not any(word in text for word in _IMAGE_WORDS):
        return False
    return any(word in text for word in _PROHIBITION_WORDS)


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
    "AuthoredIllustrationPlan",
    "ILLUSTRATION_PLAN_SCHEMA_VERSION",
    "illustrations_are_prohibited",
]
