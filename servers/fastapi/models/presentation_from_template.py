from typing import List, Literal
from pydantic import BaseModel, Field
import uuid

class SlideContentUpdate(BaseModel):
    index: int
    content: dict


class EditPresentationRequest(BaseModel):
    presentation_id: uuid.UUID
    slides: List[SlideContentUpdate]
    export_as: Literal["pptx", "pdf"] = "pptx"


class RetemplatePresentationRequest(BaseModel):
    """Re-author a saved authored deck with another authored style preset."""

    authored_style: str
    vision_qa: bool = False


class AuthoredQualityReviewRequest(BaseModel):
    """Review an existing AI-authored deck without regenerating the whole deck."""

    scope: Literal["all", "current"] = "all"
    slide_indices: List[int] = Field(default_factory=list)
    mode: Literal["analyze_only", "analyze_and_fix"] = "analyze_and_fix"
