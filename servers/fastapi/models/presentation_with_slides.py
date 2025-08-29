from typing import List, Optional
from datetime import datetime
import uuid

from pydantic import BaseModel

from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import PresentationOutlineModel
from models.presentation_structure_model import PresentationStructureModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel


class PresentationWithSlides(BaseModel):
    id: uuid.UUID
    content: str
    n_slides: int
    language: str
    file_paths: Optional[List[str]]
    title: Optional[str] = None
    outlines: Optional[PresentationOutlineModel] = None
    created_at: datetime
    updated_at: datetime
    layout: Optional[PresentationLayoutModel]
    structure: Optional[PresentationStructureModel] = None
    instructions: Optional[str] = None
    tone: Optional[str] = None
    verbosity: Optional[str] = None
    slides: List[SlideModel]

    def to_presentation_model(self) -> PresentationModel:
        return PresentationModel(
            id=self.id,
            content=self.content,
            n_slides=self.n_slides,
            language=self.language,
            file_paths=self.file_paths,
            title=self.title,
            outlines=self.outlines.model_dump(mode="json") if self.outlines else None,
            created_at=self.created_at,
            updated_at=self.updated_at,
            layout=self.layout.model_dump(mode="json") if self.layout else None,
            structure=(
                self.structure.model_dump(mode="json") if self.structure else None
            ),
            instructions=self.instructions,
            tone=self.tone,
            verbosity=self.verbosity,
        )
