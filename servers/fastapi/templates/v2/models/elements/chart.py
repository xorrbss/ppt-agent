"""Native Template V2 chart element, its series, and its enumerations."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import field_validator, model_validator

from .base import Position, Size, TemplateV2Model


class ChartType(str, Enum):
    AREA = "area"
    BAR = "bar"
    BUBBLE = "bubble"
    DONUT = "donut"
    HORIZONTAL_BAR = "horizontal_bar"
    HORIZONTAL_STACKED_BAR = "horizontal_stacked_bar"
    LINE = "line"
    PIE = "pie"
    POLAR_AREA = "polar_area"
    RADAR = "radar"
    SCATTER = "scatter"
    STACKED_BAR = "stacked_bar"


class DataLabelPosition(str, Enum):
    BASE = "base"
    MID = "mid"
    TOP = "top"
    OUTSIDE = "outside"


class ChartSeries(TemplateV2Model):
    name: str
    values: list[float]


class Chart(TemplateV2Model):
    type: Literal["chart"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    chart_type: ChartType
    title: Optional[str] = None
    title_color: Optional[str] = None
    legend_color: Optional[str] = None
    colors: Optional[list[str]] = None
    x_axis: Optional[bool] = None
    y_axis: Optional[bool] = None
    x_axis_title: Optional[str] = None
    y_axis_title: Optional[str] = None
    axis_color: Optional[str] = None
    categories: Optional[list[str]] = None
    series: Optional[list[ChartSeries]] = None
    data_labels: Optional[DataLabelPosition] = None
    legend: Optional[bool] = None
    x_axis_grid: Optional[bool] = None
    y_axis_grid: Optional[bool] = None
    grid_color: Optional[str] = None
    source: Optional[str] = None
    decorative: bool
    name: str

    @model_validator(mode="after")
    def _data_shape_is_valid(self) -> "Chart":
        if (
            self.chart_type in {ChartType.PIE, ChartType.DONUT}
            and self.series
            and len(self.series) > 1
        ):
            raise ValueError("pie and donut charts support at most one series")
        if self.categories is not None and self.series is not None:
            category_count = len(self.categories)
            if any(len(series.values) != category_count for series in self.series):
                raise ValueError(
                    "chart series values must match the category count"
                )
        return self

    @field_validator("data_labels", mode="before")
    @classmethod
    def _coerce_legacy_data_labels(cls, value: object) -> object:
        if value is True:
            return DataLabelPosition.TOP
        if value is False or value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {position.value for position in DataLabelPosition}:
                return normalized
        return value

    @model_validator(mode="after")
    def _size_must_be_visible_when_explicit(self) -> "Chart":
        if self.size is not None and (
            self.size.width < 80 or self.size.height < 60
        ):
            raise ValueError("chart size must be at least 80x60 px")
        return self
