"""Scratch-built vector geometry for native Template V2 slides."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional

from pydantic import Field, model_validator

from .base import Fill, Position, Shadow, Stroke, TemplateV2Model


class VectorShape(str, Enum):
    POLYGON = "polygon"
    ELLIPSE = "ellipse"


class VectorCurve(TemplateV2Model):
    type: Literal["smooth"]
    tension: Optional[float] = Field(default=None, ge=0, le=1)
    segments: Optional[int] = Field(default=16, ge=1, le=96)


class Vector(TemplateV2Model):
    type: Literal["vector"]
    shape: Optional[VectorShape] = None
    points: list[Position] = Field(min_length=2)
    closed: Optional[bool] = None
    curve: Optional[VectorCurve] = None
    corner_radii: Optional[list[Annotated[float, Field(ge=0)]]] = None
    rotation: Optional[float] = None
    opacity: Optional[float] = None
    fill: Optional[Fill] = None
    stroke: Optional[Stroke] = None
    shadow: Optional[Shadow] = None

    @model_validator(mode="after")
    def _ellipse_uses_a_bounding_pair(self) -> "Vector":
        if self.shape is VectorShape.ELLIPSE and len(self.points) != 2:
            raise ValueError("ellipse vectors require exactly two bounding points")
        return self
