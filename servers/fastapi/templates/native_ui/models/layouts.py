from __future__ import annotations

from pydantic import Field, model_validator

from .elements import Position, SlideElement, TemplateV2Model


class RawSlideLayout(TemplateV2Model):
    id: str = Field(min_length=1, max_length=80)
    description: str
    elements: list[SlideElement]


class Component(TemplateV2Model):
    id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=10, max_length=300)
    position: Position
    elements: list[SlideElement] = Field(min_length=1)


class SlideLayout(TemplateV2Model):
    id: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=10, max_length=300)
    components: list[Component]

    @model_validator(mode="after")
    def _component_ids_must_be_unique(self) -> "SlideLayout":
        ids = [component.id for component in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("component ids must be unique within a slide layout")
        return self

