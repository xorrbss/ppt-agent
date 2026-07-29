"""Pure Phase 1 Template V2 generation orchestration.

The adapter boundary preserves upstream's input/output contract without
importing its preview renderer, Vision/PPTX pipeline, provider version, or
semantic merge implementation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Protocol

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field

from .models.layouts import RawSlideLayout, RawSlideLayouts, SlideLayout, SlideLayouts
from .schema import get_template_schema


class SlideLayoutGenerator(Protocol):
    def __call__(
        self,
        layout: RawSlideLayout,
        index: int,
        slide_image_url: str,
        fonts: dict[str, str] | None,
    ) -> SlideLayout: ...


class GeneratedTemplateSlide(BaseModel):
    """Lossless native editor payload plus its validated editable content."""

    model_config = ConfigDict(extra="forbid")

    layout_id: str = Field(min_length=1)
    content: dict[str, Any]
    ui: dict[str, Any]


def generate_template(
    layouts: RawSlideLayouts,
    slide_image_urls: list[str],
    fonts: dict[str, str] | None = None,
    *,
    generate_slide_layout: SlideLayoutGenerator,
) -> SlideLayouts:
    if not layouts.layouts:
        raise ValueError("layouts must contain at least one slide layout")
    if len(slide_image_urls) != len(layouts.layouts):
        raise ValueError("slide_image_urls must contain one image for each layout")

    generated = [
        generate_slide_layout(
            raw_layout.model_copy(deep=True),
            index,
            slide_image_urls[index],
            deepcopy(fonts),
        )
        for index, raw_layout in enumerate(layouts.layouts)
    ]
    return SlideLayouts(layouts=_ensure_unique_slide_layout_ids(generated))


def build_generated_slide(
    layout: SlideLayout,
    content: dict[str, Any],
) -> GeneratedTemplateSlide:
    """Validate content and retain the entire layout as native ``slide.ui``."""

    # Persist the complete validated native payload. Optional fields that are
    # explicitly null are part of the editor contract and must not disappear
    # during projection into ``slide.ui``.
    ui = layout.model_dump(mode="json")
    template_schema = get_template_schema({"layouts": [ui]})
    content_schema = template_schema["layouts"][0]["schema"]
    if content_schema is None:
        if content:
            raise ValueError("layout contains no editable Template V2 content")
        content_schema = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    Draft202012Validator(content_schema).validate(content)
    return GeneratedTemplateSlide(
        layout_id=layout.id,
        content=deepcopy(content),
        ui=deepcopy(ui),
    )


def _ensure_unique_slide_layout_ids(layouts: list[SlideLayout]) -> list[SlideLayout]:
    used_ids: set[str] = set()
    unique_layouts: list[SlideLayout] = []
    for index, layout in enumerate(layouts):
        if layout.id not in used_ids:
            used_ids.add(layout.id)
            unique_layouts.append(layout.model_copy(deep=True))
            continue
        suffix = index + 1
        candidate = f"{layout.id}_{suffix}"
        while candidate in used_ids:
            suffix += 1
            candidate = f"{layout.id}_{suffix}"
        used_ids.add(candidate)
        unique_layouts.append(layout.model_copy(deep=True, update={"id": candidate}))
    return unique_layouts
