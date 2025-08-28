from typing import List, Optional
from datetime import datetime

from pydantic import BaseModel

from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import PresentationOutlineModel
from models.presentation_structure_model import PresentationStructureModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel


class PresentationWithSlides(BaseModel):
    id: str
    content: str
    n_slides: int
    language: str
    title: Optional[str] = None
    outlines: Optional[PresentationOutlineModel]
    created_at: datetime
    updated_at: datetime
    layout: Optional[PresentationLayoutModel]
    structure: Optional[PresentationStructureModel]
    instruction: Optional[str] = None
    slides: List[SlideModel]

    def to_presentation_model(self) -> PresentationModel:
        return PresentationModel(**self.model_dump())
