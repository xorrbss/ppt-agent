from typing import List, Optional
from pydantic import Field
from models.content_brief_model import BriefSection, ContentBrief
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)


#: Schema-enforced slide-text bounds per verbosity, as (min_length, max_length).
#: These are the decoder's hard limits, so the floor must sit BELOW the length
#: the outline prompt asks for. A fixed 300-character floor under a prompt that
#: asked for ~140 left the model unable to close the string on its own text, so
#: it padded with whitespace until it hit the ceiling — every slide arriving as
#: ~100 characters of content and 2,400 blanks. The lowered floors were still
#: floors: on 2026-09-05 a 150-character minimum under a prompt whose honest
#: agenda and section pages run 70–90 characters made the model append a
#: "페이지: N" line and 600–700 zero-width spaces to reach it. A schema minimum
#: cannot make a page say more, only pad, so the floor is now nominal (one
#: character: the slide must exist) and the ceiling stays the real bound. The
#: ceilings sit above the prompt's restored content floors (about 250 / 500 /
#: 800 Korean characters) with room for a long bullet.
_OUTLINE_CONTENT_BOUNDS = {
    "concise": (1, 400),
    "standard": (1, 1000),
    "text-heavy": (1, 2500),
}


def outline_model(
    n_slides: Optional[int],
    verbosity: Optional[str] = None,
    max_length: Optional[int] = None,
):
    """The outline response schema: content length capped, slide count optional.

    ``n_slides`` given caps the slide list to exactly that count (today's
    behaviour). ``None`` leaves the slide count unconstrained -- the model
    picks it -- while the per-slide content cap still applies. ``max_length``
    overrides the per-verbosity default cap (a canvas content budget); ``None``
    keeps today's default (``_OUTLINE_CONTENT_BOUNDS``).
    """
    min_length, default_max_length = _OUTLINE_CONTENT_BOUNDS.get(
        verbosity or "standard", _OUTLINE_CONTENT_BOUNDS["standard"]
    )
    content_max_length = default_max_length if max_length is None else max_length

    # Class names match the pre-canvas-budget names (SlideOutlineModelWithNSlides /
    # PresentationOutlineModelWithNSlides) so ``$defs`` keys in the generated
    # JSON schema stay stable for existing callers/tests, even though ``n_slides``
    # may now be ``None``.
    class SlideOutlineModelWithNSlides(SlideOutlineModel):
        content: str = Field(
            description="Markdown content for each slide",
            min_length=min_length,
            max_length=content_max_length,
        )

    if n_slides is None:
        class PresentationOutlineModelWithNSlides(PresentationOutlineModel):
            slides: List[SlideOutlineModelWithNSlides] = Field(
                description="List of slide outlines",
            )
    else:
        class PresentationOutlineModelWithNSlides(PresentationOutlineModel):
            slides: List[SlideOutlineModelWithNSlides] = Field(
                description="List of slide outlines",
                min_length=n_slides,
                max_length=n_slides,
            )

    return PresentationOutlineModelWithNSlides


def get_presentation_outline_model_with_n_slides(
    n_slides: int, verbosity: Optional[str] = None
):
    return outline_model(n_slides, verbosity)


def get_content_brief_model(verbosity: Optional[str] = None):
    """Stage A richness floor: enforce minimum section/point counts via minItems only.

    No maxItems/maxLength caps — the brief stays uncapped; depth is the goal.
    """
    if verbosity == "concise":
        min_sections, min_points, min_facts = 2, 2, 1
    elif verbosity == "text-heavy":
        min_sections, min_points, min_facts = 4, 3, 2
    else:
        min_sections, min_points, min_facts = 3, 3, 1

    class BriefSectionWithFloor(BriefSection):
        key_points: List[str] = Field(
            description="Substantive claims/insights, each a full sentence",
            min_length=min_points,
        )
        facts_figures: List[str] = Field(
            description="Concrete facts: stats, dates, named examples",
            min_length=min_facts,
        )

    class ContentBriefWithFloor(ContentBrief):
        sections: List[BriefSectionWithFloor] = Field(
            description="Logically ordered coverage of the topic",
            min_length=min_sections,
        )

    return ContentBriefWithFloor
