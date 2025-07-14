from typing import List
from pydantic import BaseModel, Field

from models.presentation_layout import PresentationLayoutModel, SlideLayoutModel


class TitleDescriptionSlide(BaseModel):
    title: str = Field(min_length=10, max_length=100)
    description: str = Field(min_length=50, max_length=200)


class ContentSlide(BaseModel):
    title: str = Field(min_length=10, max_length=100)
    content: List[str] = Field(min_length=1, max_length=5)


presentation_layout = PresentationLayoutModel(
    name="Basic Presentation",
    slides=[
        SlideLayoutModel(
            id="title_description",
            name="Title Description",
            json_schema=TitleDescriptionSlide.model_json_schema(),
        ),
        SlideLayoutModel(
            id="content",
            name="Content",
            json_schema=ContentSlide.model_json_schema(),
        ),
    ],
)

print(presentation_layout.model_dump_json())
