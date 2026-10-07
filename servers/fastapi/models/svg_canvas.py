"""Bounded SVG physical canvas selectors; stored geometry uses document-canvas/v1."""
from decimal import Decimal

from pydantic import Field, model_validator

from models.authored_vnext.master.base import StrictCamelModel


class SvgCanvasInput(StrictCamelModel):
    width_mm: Decimal = Field(ge=50, le=1000, decimal_places=2, strict=False)
    height_mm: Decimal = Field(ge=50, le=1000, decimal_places=2, strict=False)

    @model_validator(mode="after")
    def validate_ratio(self):
        if not Decimal("0.25") <= self.width_mm / self.height_mm <= 4:
            raise ValueError("svg_canvas_aspect_ratio_out_of_bounds")
        return self
