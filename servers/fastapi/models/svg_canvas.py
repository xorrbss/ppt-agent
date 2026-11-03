"""Bounded SVG physical canvas selectors; stored geometry uses document-canvas/v1."""
from decimal import Decimal

from pydantic import Field, model_validator

from models.authored_vnext.master.base import StrictCamelModel

_EMU_PER_MM = 36000


class SvgCanvasInput(StrictCamelModel):
    """A custom canvas in millimetres (two decimals), or an exact slide size in
    EMU. The EMU form names a source slide size the millimetre form cannot
    express (a 302.992 mm template); it resolves exactly as that source does.
    Unused fields are omitted, so millimetre requests serialize as before."""

    width_mm: Decimal | None = Field(default=None, ge=50, le=1000, decimal_places=2, strict=False,
                                     exclude_if=lambda value: value is None)
    height_mm: Decimal | None = Field(default=None, ge=50, le=1000, decimal_places=2, strict=False,
                                      exclude_if=lambda value: value is None)
    width_emu: int | None = Field(default=None, ge=50 * _EMU_PER_MM, le=1000 * _EMU_PER_MM, strict=True,
                                  exclude_if=lambda value: value is None)
    height_emu: int | None = Field(default=None, ge=50 * _EMU_PER_MM, le=1000 * _EMU_PER_MM, strict=True,
                                   exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_ratio(self):
        millimetres = (self.width_mm, self.height_mm)
        emus = (self.width_emu, self.height_emu)
        if all(value is not None for value in millimetres) and all(value is None for value in emus):
            width, height = self.width_mm, self.height_mm
        elif all(value is not None for value in emus) and all(value is None for value in millimetres):
            width, height = Decimal(self.width_emu), Decimal(self.height_emu)
        else:
            raise ValueError("svg_canvas_size_form_invalid")
        if not Decimal("0.25") <= width / height <= 4:
            raise ValueError("svg_canvas_aspect_ratio_out_of_bounds")
        return self

    @property
    def exact_emu(self) -> bool:
        return self.width_emu is not None
