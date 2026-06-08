"""Vision-QA: ask a multimodal model to review a RENDERED slide image and report
real visual problems (overflow, overlap, low contrast, leftover placeholder,
misalignment, unbalanced empty space). The core, model-dependent piece of the
optional high-quality "vision-QA" pass — capture a slide, critique it, and (later)
regenerate the offending slide from the feedback. Returns structured output so the
caller can act on it deterministically."""

from typing import List, Literal, Optional

from llmai import get_client
from llmai.shared import (
    ImageContentPart,
    JSONSchemaResponse,
    SystemMessage,
    TextContentPart,
    UserMessage,
)
from pydantic import BaseModel, Field

from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_config import get_llm_config
from utils.llm_provider import get_model
from utils.llm_utils import generate_structured_with_schema_retries
from utils.schema_utils import prepare_schema_for_validation

IssueType = Literal[
    "overflow",
    "overlap",
    "low-contrast",
    "placeholder",
    "misalignment",
    "empty-space",
    "other",
]


class CritiqueIssue(BaseModel):
    type: IssueType
    severity: Literal["high", "medium", "low"]
    detail: str = Field(max_length=200)


class SlideCritique(BaseModel):
    needs_fix: bool
    issues: List[CritiqueIssue] = Field(default_factory=list, max_length=8)


_SYSTEM = (
    "You are a meticulous presentation design reviewer. Inspect the rendered slide "
    "IMAGE and report only REAL, visible problems: text overflow or clipping, element "
    "overlap, low contrast, leftover placeholder text, misalignment, or large/unbalanced "
    "empty space. Do not invent issues. If the slide looks clean and readable, return "
    "needs_fix=false with an empty issues list. Be specific and concise; ignore the small "
    "navigation badge that may appear in a corner of the capture."
)


async def critique_slide_image(
    image: bytes,
    slide_context: Optional[str] = None,
    mime_type: str = "image/png",
) -> SlideCritique:
    """Review a rendered slide image and return a structured critique."""
    client = get_client(config=get_llm_config())
    model = get_model()
    schema = prepare_schema_for_validation(
        SlideCritique.model_json_schema(), strict=False
    )
    response_format = JSONSchemaResponse(name="critique", json_schema=schema, strict=False)
    prompt = "Review this slide image."
    if slide_context:
        prompt += f" Intended content: {slide_context}"
    messages = [
        SystemMessage(content=_SYSTEM),
        UserMessage(content=[
            TextContentPart(text=prompt),
            ImageContentPart(data=image, mime_type=mime_type),
        ]),
    ]
    try:
        content = await generate_structured_with_schema_retries(
            client,
            model,
            messages=messages,
            response_format=response_format,
            json_schema=schema,
            strict=False,
            validate_schema=True,
        )
        return SlideCritique(**content)
    except Exception as e:
        raise handle_llm_client_exceptions(e)
