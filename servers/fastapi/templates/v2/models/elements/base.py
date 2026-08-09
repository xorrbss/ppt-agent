"""Shared primitives every native Template V2 element family builds on."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict


class TemplateV2Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HorizontalAlignment(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class VerticalAlignment(str, Enum):
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class Position(TemplateV2Model):
    x: float
    y: float


class Size(TemplateV2Model):
    width: float
    height: float


class Padding(TemplateV2Model):
    top: float
    right: float
    bottom: float
    left: float


class Alignment(TemplateV2Model):
    horizontal: Optional[HorizontalAlignment] = None
    vertical: Optional[VerticalAlignment] = None


class Font(TemplateV2Model):
    size: Optional[float] = None
    family: Optional[str] = None
    color: Optional[str] = None
    bold: Optional[bool] = None
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    line_height: Optional[float] = None
    letter_spacing: Optional[float] = None
    ellipsis: Optional[bool] = None
    opacity: Optional[float] = None


class Fill(TemplateV2Model):
    color: str
    opacity: Optional[float] = None


class Stroke(TemplateV2Model):
    color: str
    opacity: Optional[float] = None
    width: float
    dash: Optional[list[float]] = None


class BorderRadius(TemplateV2Model):
    tl: float
    tr: float
    bl: float
    br: float


class Shadow(TemplateV2Model):
    color: str
    blur: Optional[float] = None
    opacity: Optional[float] = None
    offset_x: Optional[float] = None
    offset_y: Optional[float] = None


class TextRun(TemplateV2Model):
    text: str
    font: Optional[Font] = None
