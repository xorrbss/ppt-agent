"""Strict Pydantic models matching the native Template V2 slide element types.

Selected from upstream ``templates/v2/models/elements.py``.  Phase 1 keeps the
serializable model layer but not the Konva renderer.  Unknown fields are
rejected here because silently dropping editor payload would violate the
lossless ``slide.ui`` persistence contract.

The models are grouped by element family across this package; every public name
stays importable from ``templates.v2.models.elements`` itself.
"""

from __future__ import annotations

from .base import (
    Alignment,
    BorderRadius,
    Fill,
    Font,
    HorizontalAlignment,
    Padding,
    Position,
    Shadow,
    Size,
    Stroke,
    TemplateV2Model,
    TextRun,
    VerticalAlignment,
)
from .chart import Chart, ChartSeries, ChartType, DataLabelPosition
from .containers import (
    Container,
    Flex,
    FlexDirection,
    Grid,
    Group,
    LayoutAlignment,
    SlideElement,
)
from .image import IconType, Image, ImageFit
from .infographic import (
    GaugeInfographicData,
    Infographic,
    ProgressBarInfographicData,
)
from .table import Table, TableCell, TableCellReference
from .text import Marker, MathExpression, Text, TextList
from .vector import Vector, VectorCurve, VectorShape


__all__ = [
    "Alignment",
    "BorderRadius",
    "Chart",
    "ChartSeries",
    "ChartType",
    "Container",
    "DataLabelPosition",
    "Fill",
    "Flex",
    "FlexDirection",
    "Font",
    "GaugeInfographicData",
    "Grid",
    "Group",
    "HorizontalAlignment",
    "IconType",
    "Image",
    "ImageFit",
    "Infographic",
    "LayoutAlignment",
    "Marker",
    "MathExpression",
    "Padding",
    "Position",
    "ProgressBarInfographicData",
    "Shadow",
    "Size",
    "SlideElement",
    "Stroke",
    "Table",
    "TableCell",
    "TableCellReference",
    "TemplateV2Model",
    "Text",
    "TextList",
    "TextRun",
    "VerticalAlignment",
    "Vector",
    "VectorCurve",
    "VectorShape",
]
