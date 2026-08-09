"""Text-bearing native Template V2 elements."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import Field, model_validator

from .base import (
    Alignment,
    Fill,
    Font,
    Position,
    Shadow,
    Size,
    Stroke,
    TemplateV2Model,
    TextRun,
)


class Marker(str, Enum):
    BULLET = "bullet"
    NUMBER = "number"
    NONE = "none"


class Text(TemplateV2Model):
    type: Literal["text"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    font: Optional[Font] = None
    alignment: Optional[Alignment] = None
    fill: Optional[Fill] = None
    stroke: Optional[Stroke] = None
    shadow: Optional[Shadow] = None
    runs: list[TextRun]
    decorative: bool
    name: str
    max_length: int
    min_length: int

    @model_validator(mode="after")
    def _length_range_is_valid(self) -> "Text":
        if self.min_length < 0 or self.max_length < self.min_length:
            raise ValueError("text length bounds are invalid")
        return self


class MathExpression(TemplateV2Model):
    type: Literal["math"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    opacity: Optional[float] = None
    latex: str = Field(min_length=1, max_length=4000)
    display_mode: bool = True
    font: Optional[Font] = None
    alignment: Optional[Alignment] = None
    shadow: Optional[Shadow] = None
    decorative: bool
    name: str
    max_length: int = 4000
    min_length: int = 1

    @model_validator(mode="after")
    def _length_range_is_valid(self) -> "MathExpression":
        if self.min_length < 1 or self.max_length < self.min_length:
            raise ValueError("math length bounds are invalid")
        return self


class TextList(TemplateV2Model):
    type: Literal["text-list"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    font: Optional[Font] = None
    marker: Optional[Marker] = None
    items: list[list[TextRun]]
    decorative: bool
    name: str
    max_items: int
    min_items: int
    max_item_length: int
    min_item_length: int

    @model_validator(mode="after")
    def _ranges_are_valid(self) -> "TextList":
        if (
            self.min_items < 0
            or self.max_items < self.min_items
            or self.min_item_length < 0
            or self.max_item_length < self.min_item_length
        ):
            raise ValueError("text-list bounds are invalid")
        if not self.min_items <= len(self.items) <= self.max_items:
            raise ValueError("text-list item count is outside its declared bounds")
        return self
