from typing import List, Literal
from pydantic import BaseModel


class SlideContentUpdate(BaseModel):
    index: int
    content: dict


class GetPresentationUsingTemplateRequest(BaseModel):
    presentation_id: str
    data: List[SlideContentUpdate]
    export_as: Literal["pptx", "pdf"] = "pptx"
