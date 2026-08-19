from typing import List, Optional
from pydantic import Field
from models.content_brief_model import BriefSection, ContentBrief
from models.slide_spec_model import PresentationComposition, SlideSpecUnion
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.presentation_structure_model import PresentationStructureModel


#: Schema-enforced slide-text bounds per verbosity, as (min_length, max_length).
#: These are the decoder's hard limits, so the floor must sit BELOW the length
#: the outline prompt asks for. A fixed 300-character floor under a prompt that
#: asked for ~140 left the model unable to close the string on its own text, so
#: it padded with whitespace until it hit the ceiling — every slide arriving as
#: ~100 characters of content and 2,400 blanks.
_OUTLINE_CONTENT_BOUNDS = {
    "concise": (50, 250),
    "standard": (150, 800),
    "text-heavy": (300, 2500),
}


def get_presentation_outline_model_with_n_slides(
    n_slides: int, verbosity: Optional[str] = None
):
    min_length, max_length = _OUTLINE_CONTENT_BOUNDS.get(
        verbosity or "standard", _OUTLINE_CONTENT_BOUNDS["standard"]
    )

    class SlideOutlineModelWithNSlides(SlideOutlineModel):
        content: str = Field(
            description="Markdown content for each slide",
            min_length=min_length,
            max_length=max_length,
        )

    class PresentationOutlineModelWithNSlides(PresentationOutlineModel):
        slides: List[SlideOutlineModelWithNSlides] = Field(
            description="List of slide outlines",
            min_length=n_slides,
            max_length=n_slides,
        )

    return PresentationOutlineModelWithNSlides


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


def get_composition_model_with_n_slides(n_slides: Optional[int]):
    """Adaptive composer output. Pins slide count to n_slides when given;
    'auto' (None/<=0) leaves it unconstrained (composer decides count)."""
    if not n_slides or n_slides <= 0:
        return PresentationComposition

    class PresentationCompositionWithNSlides(PresentationComposition):
        slides: List[SlideSpecUnion] = Field(
            description="Ordered adaptive slides",
            min_length=n_slides,
            max_length=n_slides,
        )

    return PresentationCompositionWithNSlides


def get_presentation_structure_model_with_n_slides(n_slides: int):
    class PresentationStructureModelWithNSlides(PresentationStructureModel):
        slides: List[int] = Field(
            description="List of slide layouts",
            min_length=n_slides,
            max_length=n_slides,
        )

    return PresentationStructureModelWithNSlides
