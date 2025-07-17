from typing import List, Optional
from pydantic import Field
from ppt_config_generator.models import (
    PresentationMarkdownModel,
    PresentationStructureModel,
    SlideMarkdownModel,
    SlideStructureModel,
)


class SlideMarkdownModelWithValidation(SlideMarkdownModel):
    title: str = Field(
        description="Title of the slide in about 3 to 5 words",
        min_length=10,
        max_length=50,
    )


def get_presentation_markdown_model_with_n_slides(n_slides: int):
    class PresentationMarkdownModelWithNSlides(PresentationMarkdownModel):
        title: str = Field(
            description="Title of the presentation in about 3 to 8 words",
            min_length=10,
            max_length=50,
        )
        notes: List[str] = Field(
            default=[],
            description="Important notes for the presentation styling and formatting",
            min_length=0,
            max_length=10,
        )
        slides: List[SlideMarkdownModelWithValidation] = Field(
            description="List of slides", min_items=n_slides, max_items=n_slides
        )

    return PresentationMarkdownModelWithNSlides


def get_presentation_structure_model_with_n_slides(n_slides: int):
    class PresentationStructureModelWithNSlides(PresentationStructureModel):
        slides: List[SlideStructureModel] = Field(
            description="List of slide structure",
            min_items=n_slides,
            max_items=n_slides,
        )

    return PresentationStructureModelWithNSlides
