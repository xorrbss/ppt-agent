"""Detect a figure headline the planning model typed without word spaces.

Found 2026-09-05 by looking at rendered decks: the plan asked for
``수용성이 최우선`` style headlines and 27 of 206 (13 %) came back as one
unbroken run such as ``수용성이최우선``. The headline is set in the page's
largest type, and a run of Hangul with no space has no break opportunity, so
the browser breaks it mid-word (``수용성이최`` / ``우선``).

Nothing catches it today. The infographic contract checker compares text with
**every** whitespace character removed (``checker._norm``) so that a ``<br>``
or a split inline span does not read as a wrong label, which makes it blind to
spacing by construction. The authoring model is not at fault: it reproduced the
planned string faithfully in all 154 figure slides measured.

Korean word spacing cannot be restored by rule, so this module only measures.
The fix is the plan prompt (``llm_calls/author_infographic_plan``); this is how
we tell whether the prompt worked.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only
    from models.authored_infographic_plan import (
        AuthoredInfographicPlan,
        AuthoredSlideInfographic,
    )

#: A phrase of at least this many Hangul syllables with no space in it is a
#: jam rather than a compound noun.  Korean compounds of five syllables are
#: common ("품질관리팀"); of six with a particle in them, not.
MIN_JAMMED_SYLLABLES = 6

_HANGUL = re.compile(r"[가-힣]")
_WHITESPACE = re.compile(r"\s")


def is_jammed_takeaway(text: str | None) -> bool:
    """Whether this headline reads as words run together."""
    if not text or _WHITESPACE.search(text):
        return False
    return len(_HANGUL.findall(text)) >= MIN_JAMMED_SYLLABLES


def jammed_takeaways(
    slides: "Iterable[AuthoredSlideInfographic]",
) -> tuple[str, ...]:
    """Every headline in this plan that lost its word spacing, in order."""
    return tuple(
        slide.takeaway
        for slide in slides
        if slide.takeaway and is_jammed_takeaway(slide.takeaway)
    )


def plan_takeaway_spacing_report(plan: "AuthoredInfographicPlan") -> dict[str, object]:
    """A receipt-shaped count of the defect for one deck's plan."""
    takeaways = [slide.takeaway for slide in plan.slides if slide.takeaway]
    jammed = jammed_takeaways(plan.slides)
    return {
        "takeaways": len(takeaways),
        "jammed": len(jammed),
        "examples": list(jammed[:5]),
    }


__all__ = [
    "MIN_JAMMED_SYLLABLES",
    "is_jammed_takeaway",
    "jammed_takeaways",
    "plan_takeaway_spacing_report",
]
