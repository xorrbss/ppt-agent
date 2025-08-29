from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class GeneratePresentationRequest(BaseModel):
    content: str = Field(..., description="The content for generating the presentation")
    instructions: Optional[str] = Field(
        default=None, description="The instruction for generating the presentation"
    )
    tone: Optional[str] = Field(
        default=None, description="The tone for the presentation"
    )
    verbosity: Optional[str] = Field(
        default=None, description="The verbosity for the presentation"
    )
    web_search: bool = Field(default=False, description="Whether to enable web search")
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
