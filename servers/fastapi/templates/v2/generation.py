"""Template V2 generation orchestration.

The original Phase 1 adapter remains injectable for PPTX conversion. Prompted
layout generation is the one advanced upstream workflow that talks to the
configured LLM directly; it returns a validated in-memory draft and never
persists template state.
"""

from __future__ import annotations

from copy import deepcopy
from json import JSONDecodeError
import json
import logging
import re
from time import perf_counter
from typing import Any, Callable, Protocol

from jsonschema import Draft202012Validator
from llmai import get_client
from llmai.shared import (
    AssistantMessage,
    JSONSchemaResponse,
    SystemMessage,
    UserMessage,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from utils.llm_config import get_llm_config
from utils.llm_provider import get_model

from .models.elements import Image as SlideImageElement
from .models.layouts import (
    MergedComponent,
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
PROMPTED_LAYOUT_MAX_REFERENCE_LAYOUTS = 4
PROMPTED_LAYOUT_MAX_REUSABLE_COMPONENTS = 8
PROMPTED_LAYOUT_MAX_CONTEXT_CHARS = 160_000
PROMPTED_LAYOUT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,79}$")

LOGGER = logging.getLogger(__name__)

GENERATE_PROMPTED_TEMPLATE_LAYOUT_SYSTEM_PROMPT = """
Create exactly one new editable Template V2 slide layout for the user's request.
The result is an in-memory draft; do not modify the template or a presentation.

Use the supplied reference layouts and reusable components to match the template's
visual language: typography, colors, spacing, geometry, decorative assets, and
component patterns.

Rules:
- Return one complete SlideLayout JSON object and no prose.
- Create a unique structural snake_case layout id and a 10-300 character description.
- Compose within a 1280x720 canvas. Component positions are canvas-relative and
  element positions are local to their component.
- Prefer adapting supplied layouts and reusable components over inventing structure.
- Reuse decorative asset URLs only when they occur in the supplied template JSON.
  Never invent an asset URL.
- Mark replaceable semantic text, lists, images, icons, charts, tables, and metrics
  as decorative=false. Keep backgrounds, logos, frames, dividers, and ornaments fixed.
- Use /static/images/replaceable_template_image.png for new replaceable images and
  /static/icons/placeholder.svg for new replaceable icons.
- Include realistic neutral preview content, valid schema limits, unique component
  ids, and enough room for the maximum declared content.
- Use type=vector for scratch-built shapes, lines, dividers, connectors, and arrows.
- Do not copy topic-specific wording from a reference layout.
""".strip()


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


def _prompted_layout_context(
    *,
    prompt: str,
    template_layouts: SlideLayouts,
    merged_components: MergedComponents | None,
    template_name: str | None,
    template_description: str | None,
    fonts: dict[str, str] | None,
) -> dict[str, Any]:
    prompt_terms = _search_terms(prompt)
    ranked_layouts = sorted(
        enumerate(template_layouts.layouts),
        key=lambda item: (
            -_template_context_match_score(
                prompt_terms,
                item[1].id,
                item[1].description,
                *(component.description for component in item[1].components),
            ),
            item[0],
        ),
    )
    selected_layouts = [
        layout.model_dump(mode="json", exclude_none=True)
        for _, layout in ranked_layouts[:PROMPTED_LAYOUT_MAX_REFERENCE_LAYOUTS]
    ]

    ranked_components: list[tuple[int, int, MergedComponent]] = []
    if merged_components is not None:
        ranked_components = sorted(
            (
                (
                    -_template_context_match_score(
                        prompt_terms,
                        component.id,
                        component.description,
                    ),
                    index,
                    component,
                )
                for index, component in enumerate(merged_components.components)
            ),
            key=lambda item: (item[0], item[1]),
        )
    selected_components = [
        component.model_dump(mode="json", exclude_none=True)
        for _, _, component in ranked_components[
            :PROMPTED_LAYOUT_MAX_REUSABLE_COMPONENTS
        ]
    ]

    context: dict[str, Any] = {
        "template": {
            "name": template_name,
            "description": template_description,
            "fonts": sorted((fonts or {}).keys()),
            "existing_layout_ids": [
                layout.id for layout in template_layouts.layouts
            ],
        },
        "reference_layouts": selected_layouts,
        "reusable_components": selected_components,
    }
    while (
        len(json.dumps(context, ensure_ascii=False))
        > PROMPTED_LAYOUT_MAX_CONTEXT_CHARS
    ):
        if context["reusable_components"]:
            context["reusable_components"].pop()
            continue
        if len(context["reference_layouts"]) > 1:
            context["reference_layouts"].pop()
            continue
        break
    return context


def _search_terms(value: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", value.lower())
        if len(term) > 2
    }


def _template_context_match_score(
    prompt_terms: set[str],
    *values: str,
) -> int:
    searchable = " ".join(values).lower().replace("_", " ")
    return sum(1 for term in prompt_terms if term in searchable)


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

    child = getattr(element, "child", None)
    if child is not None:
        _replace_content_image_url_in_element(child)

    children = getattr(element, "children", None)
    if isinstance(children, list):
        _replace_content_image_urls_in_elements(children)


def _generate_with_validation_retries(
    *,
    client: Any,
    model: str,
    messages: list[Any],
    label: str,
    output_model: type[BaseModel],
    response_name: str,
    validation_retries: int,
    extra_validator: Callable[[Any], None] | None = None,
    max_tokens: int = 8192,
) -> dict[str, Any]:
    attempt_messages = list(messages)
    last_error: Exception | None = None
    max_attempts = validation_retries + 1

    for attempt in range(1, max_attempts + 1):
        attempt_started_at = perf_counter()
        LOGGER.info(
            "[templates.v2.llm] request start label=%s model=%s "
            "attempt=%d/%d messages=%d",
            label,
            model,
            attempt,
            max_attempts,
            len(attempt_messages),
        )
        try:
            response = client.generate(
                model=model,
                messages=attempt_messages,
                response_format=JSONSchemaResponse(
                    name=response_name,
                    strict=False,
                    json_schema=output_model,
                ),
                max_tokens=max_tokens,
            )
        except Exception as error:
            last_error = error
            LOGGER.warning(
                "[templates.v2.llm] request failed label=%s model=%s "
                "attempt=%d/%d duration_ms=%.1f error=%s",
                label,
                model,
                attempt,
                max_attempts,
                (perf_counter() - attempt_started_at) * 1000,
                error,
            )
            if attempt > validation_retries:
                raise
            attempt_messages = [
                *attempt_messages,
                UserMessage(
                    content=_json_repair_prompt(
                        label=label,
                        invalid_response=None,
                        error=error,
                    )
                ),
            ]
            continue

        try:
            parsed = _parse_json_content(response.content)
            validated = output_model.model_validate(parsed)
            if extra_validator is not None:
                extra_validator(validated)
            return validated.model_dump(mode="json")
        except (JSONDecodeError, ValidationError, ValueError) as error:
            last_error = error
            if attempt > validation_retries:
                raise
            invalid_response = (
                _text_from_content(response.content) or response.content
            )
            attempt_messages = [
                *attempt_messages,
                AssistantMessage(
                    content=[_json_dumps_for_prompt(invalid_response)]
                ),
                UserMessage(
                    content=_json_repair_prompt(
                        label=label,
                        invalid_response=invalid_response,
                        error=error,
                    )
                ),
            ]

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"LLM failed to generate {label}")


def _parse_json_content(content: Any) -> dict[str, Any]:
    text_content = _text_from_content(content)
    parsed = json.loads(text_content) if text_content is not None else content
    if not isinstance(parsed, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed


def _text_from_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None

    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts) if parts else None


def _json_repair_prompt(
    *,
    label: str,
    invalid_response: Any | None,
    error: Exception,
) -> str:
    parts = [
        f"The previous {label} response was not valid for this task.",
        "Return a complete replacement JSON object.",
        "Return raw JSON only. Do not include markdown fences or prose.",
        "",
        "errors:",
        _format_error_for_prompt(error),
    ]
    if invalid_response is not None:
        parts.extend(
            [
                "",
                "invalid_response:",
                _json_dumps_for_prompt(invalid_response),
            ]
        )
    return "\n".join(parts)


def _format_error_for_prompt(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return _json_dumps_for_prompt(error.errors(include_input=False))
    if isinstance(error, JSONDecodeError):
        return _json_dumps_for_prompt(
            [{"type": "JSONDecodeError", "msg": str(error)}]
        )
    return _json_dumps_for_prompt(
        [{"type": type(error).__name__, "msg": str(error)}]
    )


def _json_dumps_for_prompt(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


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
