"""Body-only model candidate contract; master furniture is unrepresentable."""

from __future__ import annotations

from typing import Literal, Union

from pydantic import Field, model_validator

from ..master.base import Digest, LayoutId, SlideId, StrictCamelModel
from ..master.contract import Rect
from .outline import OutlineItemId, SlideRole


class BodyTextElement(StrictCamelModel):
    kind: Literal["text"]
    element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    bounds: Rect
    semantic_role: Literal["body", "note", "rightCallout", "warning", "caption", "source"]
    text: str = Field(min_length=1, max_length=20000)


class BodyImageElement(StrictCamelModel):
    kind: Literal["image"]
    element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    bounds: Rect
    asset_digest: Digest
    alt_text: str = Field(min_length=1, max_length=1000)
    crop: Literal["contain", "cover"] = "cover"


class BodyChartElement(StrictCamelModel):
    kind: Literal["chart"]
    element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    bounds: Rect
    chart_type: Literal["bar", "column", "line", "area", "pie", "donut"]
    data_digest: Digest
    accessibility_summary: str = Field(min_length=1, max_length=2000)


class BodyTableElement(StrictCamelModel):
    kind: Literal["table"]
    element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    bounds: Rect
    data_digest: Digest
    rows: int = Field(ge=1, le=100)
    columns: int = Field(ge=1, le=30)


class BodyShapeElement(StrictCamelModel):
    kind: Literal["shape"]
    element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    bounds: Rect
    shape: Literal["rect", "roundRect", "ellipse", "line"]
    color_token: Literal["surface", "textPrimary", "textSecondary", "accent", "warning"]


BodyElement = Union[
    BodyTextElement,
    BodyImageElement,
    BodyChartElement,
    BodyTableElement,
    BodyShapeElement,
]


class SlideVisualPlan(StrictCamelModel):
    """Unvalidated model output constrained to one writable body slot."""

    schema_version: Literal["slide-visual-plan/v1"] = "slide-visual-plan/v1"
    scope: Literal["bodyOnly"] = "bodyOnly"
    slide_id: SlideId
    outline_item_id: OutlineItemId
    role: SlideRole
    layout_id: LayoutId
    body_slot_id: str = Field(pattern=r"^slot:[a-z0-9][a-z0-9._-]*$")
    source_digest: Digest
    elements: list[BodyElement] = Field(min_length=1, max_length=100)
    plan_digest: Digest

    @model_validator(mode="after")
    def _unique_body_elements(self) -> "SlideVisualPlan":
        ids = [element.element_id for element in self.elements]
        if len(set(ids)) != len(ids):
            raise ValueError("body element IDs must be unique")
        return self
