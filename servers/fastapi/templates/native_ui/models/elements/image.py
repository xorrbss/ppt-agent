"""Raster, vector-asset, and icon native Template V2 elements."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from .base import BorderRadius, Position, Size, TemplateV2Model


class ImageFit(str, Enum):
    CONTAIN = "contain"
    COVER = "cover"
    FILL = "fill"


class IconType(str, Enum):
    BOLD = "bold"
    DUOTONE = "duotone"
    FILL = "fill"
    LIGHT = "light"
    REGULAR = "regular"
    THIN = "thin"


class Image(TemplateV2Model):
    type: Literal["image"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    flip_h: Optional[bool] = None
    flip_v: Optional[bool] = None
    opacity: Optional[float] = None
    data: str
    fit: Optional[ImageFit] = None
    focus_x: Optional[float] = None
    focus_y: Optional[float] = None
    crop_scale: Optional[float] = None
    border_radius: Optional[BorderRadius] = None
    clip_path: Optional[str] = None
    color: Optional[str] = None
    decorative: bool
    name: str
    prompt: Optional[str] = None
    is_icon: bool
    icon_type: Optional[IconType] = None
