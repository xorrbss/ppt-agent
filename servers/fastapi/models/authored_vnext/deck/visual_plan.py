"""Body-only model candidate contract; master furniture is unrepresentable."""

from __future__ import annotations

from typing import Literal, Union

from pydantic import Field, model_validator

from ..master.base import Digest, LayoutId, SlideId, StrictCamelModel
from ..master.contract import Rect
from .outline import OutlineItemId, SlideRole


class _BodyElementBase(StrictCamelModel):
    element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    bounds: Rect
    z_order: int = Field(default=0, ge=-100000, le=100000)
    rotation: float = Field(default=0, ge=-360, le=360)


class BodyTextElement(_BodyElementBase):
    kind: Literal["text"]
    semantic_role: Literal["body", "note", "rightCallout", "warning", "caption", "source"]
    text: str = Field(min_length=1, max_length=20000)


class BodyImageElement(_BodyElementBase):
    kind: Literal["image"]
    asset_digest: Digest
    alt_text: str = Field(min_length=1, max_length=1000)
    crop: Literal["contain", "cover"] = "cover"
    semantic_role: str = Field(default="bodyImage", min_length=1, max_length=200)


class BodyChartElement(_BodyElementBase):
    kind: Literal["chart"]
    chart_type: Literal["bar", "column", "line", "area", "pie", "donut"]
    data_digest: Digest
    accessibility_summary: str = Field(min_length=1, max_length=2000)
    semantic_role: str = Field(default="bodyChart", min_length=1, max_length=200)


class BodyTableElement(_BodyElementBase):
    kind: Literal["table"]
    data_digest: Digest
    rows: int = Field(ge=1, le=100)
    columns: int = Field(ge=1, le=30)
    semantic_role: str = Field(default="bodyTable", min_length=1, max_length=200)


class BodyShapeElement(_BodyElementBase):
    kind: Literal["shape"]
    shape: Literal["rect", "roundRect", "ellipse", "line"]
    color_token: Literal["surface", "textPrimary", "textSecondary", "accent", "warning"]
    semantic_role: str = Field(default="decoration", min_length=1, max_length=200)


class BodyConnectorElement(_BodyElementBase):
    kind: Literal["connector"]
    from_element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    to_element_id: str = Field(pattern=r"^body:[a-z0-9][a-z0-9._-]*$")
    connector_type: Literal["straight", "elbow", "curve"] = "straight"
    semantic_role: str = Field(default="connector", min_length=1, max_length=200)


class BodyGroupElement(_BodyElementBase):
    kind: Literal["group"]
    child_element_ids: list[str] = Field(min_length=1, max_length=100)
    semantic_role: str = Field(default="group", min_length=1, max_length=200)

    @model_validator(mode="after")
    def _unique_children(self) -> "BodyGroupElement":
        if len(set(self.child_element_ids)) != len(self.child_element_ids):
            raise ValueError("group childElementIds must be unique")
        return self


BodyElement = Union[
    BodyTextElement,
    BodyImageElement,
    BodyChartElement,
    BodyConnectorElement,
    BodyGroupElement,
    BodyTableElement,
    BodyShapeElement,
]


class SlideVisualPlan(StrictCamelModel):
    """Authored-owned body objects constrained to one writable body envelope.

    The canonical scene compiler may deterministically compose a single source
    text object into several native text/shape objects.  Company furniture is
    intentionally still unrepresentable here.
    """

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
        id_set = set(ids)
        for element in self.elements:
            if isinstance(element, BodyConnectorElement) and (
                element.from_element_id not in id_set
                or element.to_element_id not in id_set
            ):
                raise ValueError("connector endpoint does not exist in the visual plan")
            if isinstance(element, BodyGroupElement) and any(
                child not in id_set for child in element.child_element_ids
            ):
                raise ValueError("group child does not exist in the visual plan")
        return self
