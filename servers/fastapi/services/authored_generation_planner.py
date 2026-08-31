"""Deterministic text-budget planning before authored provider work.

The renderer remains the final capacity authority.  This module gives the first
author call concrete title/body/visible-text targets derived from the same
server-owned capacity envelope, so known residuals are not discovered only
after Chromium has rendered the slide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from services.authored_composition_policy import (
    AuthoredCapacityPlan,
    CompositionProfile,
    SlideCapacity,
)


_MARKDOWN_PREFIX = re.compile(r"^\s*(?:#{1,6}|[-*+] |\d+[.)]\s+)")


def _plain_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _content_parts(content: object) -> tuple[str, str, int]:
    lines = [
        _plain_text(_MARKDOWN_PREFIX.sub("", line, count=1))
        for line in str(content or "").splitlines()
    ]
    lines = [line for line in lines if line]
    if len(lines) >= 2:
        title = lines[0]
        body = " ".join(lines[1:])
    else:
        # A single prose block does not carry an explicit title owner.  Treat it
        # as body input instead of guessing a semantic split the model may not use.
        title = ""
        body = lines[0] if lines else ""
    return title, body, len(" ".join(lines))


@dataclass(frozen=True, slots=True)
class AuthoredSlideTextPlan:
    slide_index: int
    role: str
    input_title_chars: int
    input_body_chars: int
    input_visible_chars: int
    title_target_chars: int
    body_target_chars: int
    visible_target_chars: int
    semantic_target_limit: int
    condensation_required: bool
    condensation_allowed: bool
    provider_initial_call_limit: int = 1
    provider_recovery_call_limit: int = 1

    def prompt_brief(self) -> str:
        condensation = (
            "Condense low-value wording before layout"
            if self.condensation_required and self.condensation_allowed
            else (
                "Do not condense source-owned wording; fail closed if it cannot fit"
                if self.condensation_required
                else "No pre-author condensation is required"
            )
        )
        return (
            "PRE-AUTHOR TEXT PLAN (server-owned): "
            f"input estimate title={self.input_title_chars}, "
            f"body={self.input_body_chars}, visible={self.input_visible_chars}; "
            f"allocate title<={self.title_target_chars}, "
            f"body<={self.body_target_chars}, visible<={self.visible_target_chars}, "
            f"semantic targets<={self.semantic_target_limit}. {condensation}. "
            "Provider budget is one initial call plus at most one admitted "
            "slide-local correction; do not assume another whole-slide call. "
            "Do not duplicate text in nested semantic owners."
        )


@dataclass(frozen=True, slots=True)
class AuthoredTextPlan:
    profile: CompositionProfile
    slides: tuple[AuthoredSlideTextPlan, ...]

    def at(self, index: int, *, capacity: SlideCapacity | None = None) -> AuthoredSlideTextPlan:
        planned = self.slides[index]
        if capacity is None:
            return planned
        return _plan_slide(
            slide_index=index,
            role=capacity.role,
            content_counts=(
                planned.input_title_chars,
                planned.input_body_chars,
                planned.input_visible_chars,
            ),
            capacity=capacity,
            profile=self.profile,
        )


def _plan_slide(
    *,
    slide_index: int,
    role: str,
    content_counts: tuple[int, int, int],
    capacity: SlideCapacity,
    profile: CompositionProfile,
) -> AuthoredSlideTextPlan:
    title_chars, body_chars, visible_chars = content_counts
    over_capacity = (
        title_chars > capacity.title_max_chars
        or body_chars > capacity.body_max_chars
        or visible_chars > capacity.max_visible_text_chars
    )
    return AuthoredSlideTextPlan(
        slide_index=slide_index,
        role=role,
        input_title_chars=title_chars,
        input_body_chars=body_chars,
        input_visible_chars=visible_chars,
        title_target_chars=capacity.title_max_chars,
        body_target_chars=capacity.body_max_chars,
        visible_target_chars=capacity.max_visible_text_chars,
        semantic_target_limit=capacity.max_qids,
        condensation_required=over_capacity,
        condensation_allowed=bool(profile.allow_summarization),
    )


def plan_authored_text(
    contents: Sequence[str],
    capacity_plan: AuthoredCapacityPlan,
) -> AuthoredTextPlan:
    """Freeze per-slide semantic text budgets before the first provider call."""

    if len(contents) != len(capacity_plan.slides):
        raise ValueError("authored text plan contents and capacity must match")
    planned_slides: list[AuthoredSlideTextPlan] = []
    for index, content in enumerate(contents):
        title, body, visible_chars = _content_parts(content)
        planned_slides.append(_plan_slide(
            slide_index=index,
            role=capacity_plan.at(index).role,
            content_counts=(len(title), len(body), visible_chars),
            capacity=capacity_plan.at(index),
            profile=capacity_plan.profile,
        ))
    return AuthoredTextPlan(
        profile=capacity_plan.profile,
        slides=tuple(planned_slides),
    )


__all__ = [
    "AuthoredSlideTextPlan",
    "AuthoredTextPlan",
    "plan_authored_text",
]
