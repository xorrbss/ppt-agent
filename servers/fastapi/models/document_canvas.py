"""Mode-neutral document geometry; physical EMU are authoritative.

The metadata model and its constants live here so ``models`` callers can
validate a document canvas without importing ``services``; the derivations
(orientation presets, source projection, digests) stay in
``services.document_canvas``, which re-exports these names.
"""
from __future__ import annotations

from decimal import Decimal
from math import gcd
from typing import Literal

from pydantic import Field, field_validator, model_validator

from models.authored_vnext.master.base import StrictCamelModel
from models.authored_vnext.master.contract import CanvasContract, Rect

DOCUMENT_CANVAS_CONTRACT_VERSION = "document-canvas/v1"
# Enable only after all reader/capability and product readback gates pass.
DOCUMENT_CANVAS_A4_RELEASE_VERIFIED = False
# Local managed candidate stage; never set by a request or advertised as verified.
DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED = True


class LogicalSize(StrictCamelModel):
    width: int = Field(ge=1, le=7680)
    height: int = Field(ge=1, le=7680)


class PhysicalSize(StrictCamelModel):
    width_emu: int = Field(ge=1, le=2147483647)
    height_emu: int = Field(ge=1, le=2147483647)


class SafeArea(StrictCamelModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)

    @field_validator("*")
    @classmethod
    def decimal_precision(cls, value: float) -> float:
        micro = Decimal(str(value)) * 1000000
        if micro != micro.to_integral_value():
            raise ValueError("document_canvas_safe_area_precision")
        return value


class DocumentCanvasMetadata(StrictCamelModel):
    contract_version: Literal["document-canvas/v1"]
    logical_size: LogicalSize
    physical_size: PhysicalSize
    safe_area: SafeArea

    @model_validator(mode="after")
    def geometry(self) -> "DocumentCanvasMetadata":
        logical, physical, safe = self.logical_size, self.physical_size, self.safe_area
        # Source PPTX dimensions are authoritative integers in EMU.  Their
        # aspect ratio is commonly not representable by the bounded logical
        # raster using another exact integer pair (the real A4 sample is
        # 10691813 x 7559675 EMU).  Admit only the nearest raster projection:
        # the cross-axis rounding error must stay within half a logical pixel.
        projected_height = (
            Decimal(logical.width)
            * Decimal(physical.height_emu)
            / Decimal(physical.width_emu)
        )
        if abs(Decimal(logical.height) - projected_height) > Decimal("0.5"):
            raise ValueError("document_canvas_nonuniform_scale")
        if (Decimal(str(safe.x)) + Decimal(str(safe.width)) > logical.width
                or Decimal(str(safe.y)) + Decimal(str(safe.height)) > logical.height):
            raise ValueError("document_canvas_safe_area_out_of_bounds")
        return self

    @property
    def width_px(self) -> int:
        return self.logical_size.width

    @property
    def height_px(self) -> int:
        return self.logical_size.height

    @property
    def locale(self) -> str:
        return "ko-KR"

    @property
    def aspect_ratio(self) -> str:
        divisor = gcd(self.width_px, self.height_px)
        return f"{self.width_px // divisor}:{self.height_px // divisor}"

    @property
    def orientation(self) -> str:
        return "landscape" if self.width_px >= self.height_px else "portrait"

    @property
    def emu_per_unit(self) -> float:
        return self.physical_size.width_emu / self.width_px

    @property
    def point_per_unit(self) -> float:
        return self.emu_per_unit / 12700

    def canvas_contract(self) -> CanvasContract:
        return CanvasContract(width_px=self.width_px, height_px=self.height_px,
                              locale=self.locale, safe_area=Rect(**self.safe_area.model_dump()))


__all__ = [
    "DOCUMENT_CANVAS_A4_CANDIDATE_ENABLED",
    "DOCUMENT_CANVAS_A4_RELEASE_VERIFIED",
    "DOCUMENT_CANVAS_CONTRACT_VERSION",
    "DocumentCanvasMetadata",
    "LogicalSize",
    "PhysicalSize",
    "SafeArea",
]
