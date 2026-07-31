from typing import List, Optional

from pydantic import BaseModel

from templates.v2.constants import LEGACY_PRESENTATION_VERSION


class PresentationGenerationSettings(BaseModel):
    n_slides: int
    language: str
    tone: Optional[str] = None
    verbosity: Optional[str] = None
    instructions: Optional[str] = None
    include_table_of_contents: bool = False
    include_title_slide: bool = True
    web_search: bool = False
    mode: Optional[str] = None
    version: str = LEGACY_PRESENTATION_VERSION


class PresentationGenerationInfo(BaseModel):
    prompt: str
    attachment_file_names: List[str]
    settings: PresentationGenerationSettings
