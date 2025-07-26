from typing import List, Optional, Literal
from pydantic import BaseModel, Field
from fastapi import UploadFile

class GeneratePresentationRequest(BaseModel):
    prompt: str
    n_slides: int = Field(default=8, ge=5, le=20)
    language: str = Field(default="English")
    layout: str = Field(default="general")
    documents: Optional[List[UploadFile]] = None
    export_as: Literal["pptx", "pdf"] = Field(default="pptx")


class PresentationAndPath(BaseModel):
    presentation_id: str
    path: str

class PresentationPathAndEditPath(PresentationAndPath):
    edit_path: str
