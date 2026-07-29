"""Strict Pydantic models matching the native Template V2 slide element types.

Selected from upstream ``templates/v2/models/elements.py``.  Phase 1 keeps the
serializable model layer but not the Konva renderer.  Unknown fields are
rejected here because silently dropping editor payload would violate the
lossless ``slide.ui`` persistence contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, TypeAlias, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class LayoutAlignment(str, Enum):
    FLEX_START = "flex-start"
    FLEX_END = "flex-end"
    CENTER = "center"
    STRETCH = "stretch"


class Marker(str, Enum):
    BULLET = "bullet"
    NUMBER = "number"
    NONE = "none"


class FlexDirection(str, Enum):
    ROW = "row"
    COLUMN = "column"


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


class ChartSeries(TemplateV2Model):
    name: str
    values: list[float]


class TextRun(TemplateV2Model):
    text: str
    font: Optional[Font] = None


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


class Container(TemplateV2Model):
    type: Literal["container"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    alignment: Optional[Alignment] = None
    fill: Optional[Fill] = None
    stroke: Optional[Stroke] = None
    border_radius: Optional[BorderRadius] = None
    shadow: Optional[Shadow] = None
    padding: Optional[Padding] = None
    child: Optional["SlideElement"] = None


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


class TableCell(TemplateV2Model):
    color: Optional[Fill] = None
    font: Optional[Font] = None
    alignment: Optional[HorizontalAlignment] = None
    runs: list[TextRun]


class Table(TemplateV2Model):
    type: Literal["table"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    columns: list[TableCell]
    rows: list[list[TableCell]]
    decorative: bool
    name: str
    max_columns: int
    min_columns: int
    max_rows: int
    min_rows: int

    @model_validator(mode="after")
    def _ranges_are_valid(self) -> "Table":
        if (
            self.min_columns < 0
            or self.max_columns < self.min_columns
            or self.min_rows < 0
            or self.max_rows < self.min_rows
        ):
            raise ValueError("table bounds are invalid")
        column_count = len(self.columns)
        if not self.min_columns <= column_count <= self.max_columns:
            raise ValueError("table column count is outside its declared bounds")
        if not self.min_rows <= len(self.rows) <= self.max_rows:
            raise ValueError("table row count is outside its declared bounds")
        if any(len(row) != column_count for row in self.rows):
            raise ValueError("table rows must match the declared column count")
        return self


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


class Flex(TemplateV2Model):
    type: Literal["flex"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    direction: FlexDirection
    wrap: Optional[bool] = None
    align_items: Optional[LayoutAlignment] = None
    justify_content: Optional[LayoutAlignment] = None
    gap: Optional[float] = None
    column_gap: Optional[float] = None
    row_gap: Optional[float] = None
    children: list["SlideElement"]
    name: str
    max_children: int
    min_children: int

    @model_validator(mode="after")
    def _layout_bounds_are_valid(self) -> "Flex":
        if self.min_children < 0 or self.max_children < self.min_children:
            raise ValueError("flex child bounds are invalid")
        if not self.min_children <= len(self.children) <= self.max_children:
            raise ValueError("flex child count is outside its declared bounds")
        if any(
            gap is not None and gap < 0
            for gap in (self.gap, self.column_gap, self.row_gap)
        ):
            raise ValueError("flex gaps must be non-negative")
        return self


class Grid(TemplateV2Model):
    type: Literal["grid"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    rotation: Optional[float] = None
    columns: int
    rows: Optional[int] = None
    gap: Optional[float] = None
    column_gap: Optional[float] = None
    row_gap: Optional[float] = None
    align_items: Optional[LayoutAlignment] = None
    justify_items: Optional[LayoutAlignment] = None
    children: list["SlideElement"]
    name: str
    max_children: int
    min_children: int

    @model_validator(mode="after")
    def _layout_bounds_are_valid(self) -> "Grid":
        if self.columns <= 0 or (self.rows is not None and self.rows <= 0):
            raise ValueError("grid dimensions must be positive")
        if self.min_children < 0 or self.max_children < self.min_children:
            raise ValueError("grid child bounds are invalid")
        child_count = len(self.children)
        if not self.min_children <= child_count <= self.max_children:
            raise ValueError("grid child count is outside its declared bounds")
        if any(
            gap is not None and gap < 0
            for gap in (self.gap, self.column_gap, self.row_gap)
        ):
            raise ValueError("grid gaps must be non-negative")
        if self.rows is not None:
            capacity = self.columns * self.rows
            if self.max_children > capacity or child_count > capacity:
                raise ValueError("grid child bounds exceed its declared capacity")
        return self


class Group(TemplateV2Model):
    type: Literal["group"]
    position: Optional[Position] = None
    size: Optional[Size] = None
    children: list["SlideElement"]
    name: str


SlideElement: TypeAlias = Annotated[
    Union[
        Text,
        Container,
        Image,
        TextList,
        Table,
        Vector,
        Chart,
        Infographic,
        Flex,
        Grid,
        Group,
    ],
    Field(discriminator="type"),
]


for _model in (Container, Flex, Grid, Group):
    _model.model_rebuild()
