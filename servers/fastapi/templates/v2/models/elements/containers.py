"""Container-shaped native Template V2 elements and the slide element union.

``SlideElement`` lives here because the container models below reference it as a
forward annotation; keeping the union in the same module is what lets
``model_rebuild()`` resolve those references from this module's namespace.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Optional, TypeAlias, Union

from pydantic import Field, model_validator

from .base import (
    Alignment,
    BorderRadius,
    Fill,
    Padding,
    Position,
    Shadow,
    Size,
    Stroke,
    TemplateV2Model,
)
from .chart import Chart
from .image import Image
from .infographic import Infographic
from .table import Table
from .text import MathExpression, Text, TextList
from .vector import Vector


class LayoutAlignment(str, Enum):
    FLEX_START = "flex-start"
    FLEX_END = "flex-end"
    CENTER = "center"
    STRETCH = "stretch"


class FlexDirection(str, Enum):
    ROW = "row"
    COLUMN = "column"


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
        MathExpression,
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
