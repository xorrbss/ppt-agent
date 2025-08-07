from typing import List
from pydantic import Field
from models.presentation_outline_model import PresentationOutlineModel
from models.presentation_structure_model import PresentationStructureModel


def get_presentation_outline_model_with_n_slides(n_slides: int):
    class PresentationOutlineModelWithNSlides(PresentationOutlineModel):
        slides: List[str] = Field(
            description="Markdown content for each slide in about 100 to 200 words",
            min_items=n_slides,
            max_items=n_slides,
        )

    return PresentationOutlineModelWithNSlides


def get_presentation_structure_model_with_n_slides(n_slides: int):
    class PresentationStructureModelWithNSlides(PresentationStructureModel):
        slides: List[int] = Field(
            description="List of slide layouts",
            min_items=n_slides,
            max_items=n_slides,
        )

    return PresentationStructureModelWithNSlides
