from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class GeneratePresentationRequest(BaseModel):
    prompt: str = Field(..., description="The prompt for generating the presentation")
    n_slides: int = Field(default=8, description="Number of slides to generate")
    language: str = Field(
        default="English", description="Language for the presentation"
    )
    template: str = Field(
        default="general", description="Template to use for the presentation"
    )
    files: Optional[List[str]] = Field(
        default=None, description="Files to use for the presentation"
    )
    export_as: Literal["pptx", "pdf"] = Field(
        default="pptx", description="Export format"
    )
