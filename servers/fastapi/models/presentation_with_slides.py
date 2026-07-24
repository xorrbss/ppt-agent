from typing import Any, List, Optional
from datetime import datetime
import uuid

from pydantic import BaseModel

from models.sql.slide import SlideModel
from templates.v2.constants import LEGACY_PRESENTATION_VERSION


class PresentationWithSlides(BaseModel):
    id: uuid.UUID
    content: str
    n_slides: int
    language: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    tone: Optional[str] = None
    verbosity: Optional[str] = None
    slides: List[SlideModel]
    theme: Optional[dict] = None
    fonts: Optional[Any] = None
    # "template" | "adaptive" | "authored" — lets the editor read the deck's mode
    # explicitly instead of inferring it from theme/layout sentinels.
    mode: Optional[str] = None
    version: str = LEGACY_PRESENTATION_VERSION
