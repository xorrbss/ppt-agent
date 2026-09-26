"""Native Template V2 infographic element and its discriminated data payloads."""

from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import Field, model_validator

from .base import Position, Size, TemplateV2Model


class ProgressBarInfographicData(TemplateV2Model):
    type: Literal["progress_bar"]
    max_value: float
    min_value: float
    value: float

    @model_validator(mode="after")
    def _range_is_valid(self) -> "ProgressBarInfographicData":
        if self.min_value >= self.max_value:
            raise ValueError("infographic min_value must be less than max_value")
        if not self.min_value <= self.value <= self.max_value:
            raise ValueError("infographic value must be within its declared range")
        return self


class GaugeInfographicData(TemplateV2Model):
    type: Literal["gauge"]
    max_value: float
    min_value: float
    value: float

    @model_validator(mode="after")
    def _range_is_valid(self) -> "GaugeInfographicData":
        if self.min_value >= self.max_value:
            raise ValueError("infographic min_value must be less than max_value")
        if not self.min_value <= self.value <= self.max_value:
            raise ValueError("infographic value must be within its declared range")
        return self


class Infographic(TemplateV2Model):
    type: Literal["infographic"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    data: Annotated[
        Union[ProgressBarInfographicData, GaugeInfographicData],
        Field(discriminator="type"),
    ]
    colors: list[str] = Field(default_factory=list)
    decorative: bool
    name: str
