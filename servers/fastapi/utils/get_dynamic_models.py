from typing import List, Optional
from pydantic import Field
from models.content_brief_model import BriefSection, ContentBrief
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.presentation_structure_model import PresentationStructureModel


def get_presentation_outline_model_with_n_slides(n_slides: int):
    class SlideOutlineModelWithNSlides(SlideOutlineModel):
        content: str = Field(
            description="Markdown content for each slide",
            min_length=300,
            max_length=2500,
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


def get_presentation_structure_model_with_n_slides(n_slides: int):
    class PresentationStructureModelWithNSlides(PresentationStructureModel):
        slides: List[int] = Field(
            description="List of slide layouts",
            min_length=n_slides,
            max_length=n_slides,
        )

    return PresentationStructureModelWithNSlides
