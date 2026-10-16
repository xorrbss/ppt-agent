import re
from typing import List
from pydantic import BaseModel, field_validator, model_serializer

#: Zero-width and invisible format characters. A constrained decoder that must
#: reach a schema minimum length pads its string with these rather than with
#: words — 2026-09-05: four of ten fixture slides carried 600–700 U+200B after
#: their last bullet. They are never content, so they are removed on parse.
_INVISIBLE_FORMAT_CHARS = re.compile("[​‌‍⁠﻿]+")


def strip_invisible_format_chars(text: str) -> str:
    """Drop zero-width padding a model used to satisfy a length floor."""
    return _INVISIBLE_FORMAT_CHARS.sub("", text)


class SlideOutlineModel(BaseModel):
    content: str
    page_id: str | None = None
    title: str | None = None
    title_source_digest: str | None = None
    common_header: dict | None = None

    @model_serializer(mode="wrap")
    def _serialize_optional_header(self, handler):
        # Old outlines must retain their original JSON/digest when no header
        # metadata was supplied, including through nested presentation dumps.
        result = handler(self)
        for key in ("title", "title_source_digest", "common_header", "page_id"):
            if result.get(key) is None:
                result.pop(key, None)
        return result

    @field_validator("content", mode="before")
    @classmethod
    def _strip_invisible_padding(cls, value):
        if isinstance(value, str):
            return strip_invisible_format_chars(value).rstrip()
        return value


class PresentationOutlineModel(BaseModel):
    slides: List[SlideOutlineModel]

    def to_string(self):
        message = ""
        for i, slide in enumerate(self.slides):
            message += f"## Slide {i+1}:\n"
            message += f"  - Content: {slide} \n"
        return message
