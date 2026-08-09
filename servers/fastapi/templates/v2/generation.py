"""Template V2 generation orchestration.

The original Phase 1 adapter remains injectable for PPTX conversion. Prompted
layout generation is the one advanced upstream workflow that talks to the
configured LLM directly; it returns a validated in-memory draft and never
persists template state.

Prompt construction and the LLM retry/repair plumbing live in
``templates.v2.generation_prompting``; this module resolves layouts.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from llmai import get_client
from llmai.shared import (
    SystemMessage,
    UserMessage,
)
from pydantic import BaseModel, ConfigDict, Field

from utils.llm_config import get_llm_config
from utils.llm_provider import get_model

from .generation_prompting import (
    GENERATE_PROMPTED_TEMPLATE_LAYOUT_SYSTEM_PROMPT,
    _generate_with_validation_retries,
    _prompted_layout_context,
)
from .models.elements import Image as SlideImageElement
from .models.elements import ImageFit
from .models.layouts import (
    MergedComponents,
    RawSlideLayout,
    RawSlideLayouts,
    SlideLayout,
    SlideLayouts,
)
from .schema import get_template_schema


DEFAULT_PROMPTED_LAYOUT_VALIDATION_RETRIES = 5
CONTENT_IMAGE_PLACEHOLDER_URL = (
    "/static/images/replaceable_template_image.png"
)
CONTENT_ICON_PLACEHOLDER_URL = "/static/icons/placeholder.svg"
PROMPTED_LAYOUT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,79}$")


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


def generate_prompted_slide_layout(
    prompt: str,
    template_layouts: SlideLayouts,
    merged_components: MergedComponents | None = None,
    template_name: str | None = None,
    template_description: str | None = None,
    fonts: dict[str, str] | None = None,
) -> SlideLayout:
    """Generate one validated draft layout without mutating the template."""

    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ValueError("Prompt is required")

    context = _prompted_layout_context(
        prompt=normalized_prompt,
        template_layouts=template_layouts,
        merged_components=merged_components,
        template_name=template_name,
        template_description=template_description,
        fonts=fonts,
    )
    existing_layout_ids = {layout.id for layout in template_layouts.layouts}

    def validate_generated_layout(layout: SlideLayout) -> None:
        if not PROMPTED_LAYOUT_ID_PATTERN.fullmatch(layout.id):
            raise ValueError(
                "layout.id must be snake_case and begin with a lowercase letter"
            )
        if layout.id in existing_layout_ids:
            raise ValueError(
                f"layout.id '{layout.id}' already exists; "
                "create a unique layout id"
            )
        if not layout.components:
            raise ValueError(
                "layout.components must contain at least one component"
            )
        if not _contains_editable_template_content(
            layout.model_dump(mode="json", exclude_none=True)
        ):
            raise ValueError(
                "layout must contain at least one semantic element "
                "with decorative=false"
            )

    generated = _generate_with_validation_retries(
        client=get_client(config=get_llm_config()),
        model=get_model(),
        messages=[
            SystemMessage(
                content=GENERATE_PROMPTED_TEMPLATE_LAYOUT_SYSTEM_PROMPT
            ),
            UserMessage(
                content=(
                    f"User request:\n{normalized_prompt}\n\n"
                    "Template context:\n"
                    f"{json.dumps(context, ensure_ascii=False)}"
                )
            ),
        ],
        label="prompted template layout",
        output_model=SlideLayout,
        response_name="PromptedTemplateLayoutResponse",
        validation_retries=DEFAULT_PROMPTED_LAYOUT_VALIDATION_RETRIES,
        extra_validator=validate_generated_layout,
        max_tokens=16_000,
    )
    return _replace_content_image_urls(SlideLayout.model_validate(generated))


def _contains_editable_template_content(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("decorative") is False and isinstance(
            value.get("type"), str
        ):
            return True
        return any(
            _contains_editable_template_content(child)
            for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_editable_template_content(child) for child in value)
    return False


def _replace_content_image_urls(layout: SlideLayout) -> SlideLayout:
    normalized = layout.model_copy(deep=True)
    for component in normalized.components:
        _replace_content_image_urls_in_elements(component.elements)
    return normalized


def _replace_content_image_urls_in_elements(elements: list[Any]) -> None:
    for element in elements:
        _replace_content_image_url_in_element(element)


def _replace_content_image_url_in_element(element: Any) -> None:
    if isinstance(element, SlideImageElement) and element.decorative is False:
        element.data = (
            CONTENT_ICON_PLACEHOLDER_URL
            if element.is_icon
            else CONTENT_IMAGE_PLACEHOLDER_URL
        )
        if not element.is_icon and element.fit != ImageFit.COVER:
            element.fit = ImageFit.COVER

    child = getattr(element, "child", None)
    if child is not None:
        _replace_content_image_url_in_element(child)

    children = getattr(element, "children", None)
    if isinstance(children, list):
        _replace_content_image_urls_in_elements(children)


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
