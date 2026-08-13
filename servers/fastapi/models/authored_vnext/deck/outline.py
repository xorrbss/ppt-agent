"""Strict source outline and master-layout assignment contracts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..master.base import Digest, LayoutId, StrictCamelModel


OutlineItemId = Annotated[
    str, Field(pattern=r"^outline:[a-z0-9][a-z0-9._-]*$")
]
SlideRole = Literal["cover", "section", "standard", "longTitle"]


class OutlineItem(StrictCamelModel):
    item_id: OutlineItemId
    index: int = Field(ge=0, le=499)
    title: str = Field(min_length=1, max_length=4000)
    summary: str = Field(min_length=1, max_length=20000)
    role_hint: SlideRole | None = None


class OutlineInput(StrictCamelModel):
    schema_version: Literal["authored-outline/v1"] = "authored-outline/v1"
    source_digest: Digest
    deck_title: str = Field(min_length=1, max_length=4000)
    items: list[OutlineItem] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _ordered_unique_items(self) -> "OutlineInput":
        if [item.index for item in self.items] != list(range(len(self.items))):
            raise ValueError("outline item indexes must be contiguous from zero")
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("outline item IDs must be unique")
        return self


class OutlineAssignment(StrictCamelModel):
    item_id: OutlineItemId
    index: int = Field(ge=0, le=499)
    role: SlideRole
    layout_id: LayoutId
    title_promoted: bool = False
    reason: Literal[
        "first-slide-cover",
        "section-boundary",
        "standard-content",
        "title-fit-promotion",
        "explicit-role-hint",
    ]

    @model_validator(mode="after")
    def _promotion_matches_role(self) -> "OutlineAssignment":
        if self.title_promoted and self.role != "longTitle":
            raise ValueError("titlePromoted requires longTitle role")
        if (self.reason == "title-fit-promotion") != self.title_promoted:
            raise ValueError("title-fit promotion reason and flag must agree")
        return self


class OutlineAssignmentSet(StrictCamelModel):
    schema_version: Literal["outline-assignment/v1"] = "outline-assignment/v1"
    source_digest: Digest
    master_digest: Digest
    layout_registry_digest: Digest
    assignments: list[OutlineAssignment] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _ordered_unique_with_one_cover(self) -> "OutlineAssignmentSet":
        if [item.index for item in self.assignments] != list(
            range(len(self.assignments))
        ):
            raise ValueError("assignment indexes must be contiguous from zero")
        if len({item.item_id for item in self.assignments}) != len(self.assignments):
            raise ValueError("assignment item IDs must be unique")
        covers = [item for item in self.assignments if item.role == "cover"]
        if len(covers) != 1 or covers[0].index != 0:
            raise ValueError("exactly the first assignment must use cover role")
        return self
