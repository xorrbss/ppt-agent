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

_EXACT_SYSTEM = (
    "You are a meticulous visual-fidelity reviewer. Compare the SOURCE IMAGE with "
    "the RENDERED IMAGE and report only defects introduced by the reconstruction. "
    "Check source text character-for-character, including numbers, spaces, and line "
    "breaks, plus visible font appearance, color, position, size, spacing, alignment, "
    "visual hierarchy, overlap, and cropping. Compare the two images directly: an "
    "overlap, crop, occlusion, edge bleed, or other unusual treatment that already "
    "exists in the source is intentional and is not a defect. For photos, logos, and "
    "complex raster artwork, judge presence, visible silhouette, scale, placement, "
    "crop, and dominant colors; report a missing or clearly substituted asset, but do "
    "not invent unreadable logo text or flag harmless compression, color-profile, or "
    "subpixel antialiasing differences. You do not need to identify the font by name: "
    "judge visible glyph shape, width, weight, spacing, and line breaks. A clearly "
    "different fallback font is a defect. If source resolution, blur, occlusion, or "
    "complex raster content makes a material comparison genuinely impossible, do not "
    "guess: return needs_fix=true with one high-severity other issue whose detail starts "
    "with 'INDETERMINATE:'. Do not request redesign, summarization, added content, or "
    "discretionary improvement. "
    "If the render faithfully matches the source, return needs_fix=false with an "
    "empty issues list. Be specific and concise."
)

STYLE_COMPARISON_REGRESSION_PREFIX = "REGRESSION:"
_STYLE_COMPARISON_SYSTEM = (
    "You are a meticulous presentation design reviewer. Compare a PRE-FIX BASELINE "
    "render with a POST-FIX CANDIDATE render. The baseline is not ground truth and "
    "may contain defects; fixes and visual changes are allowed. Judge the candidate "
    "itself and report its remaining real, visible defects. Also compare it with the "
    "baseline for newly introduced or worsened clipping, overflow, overlap, content "
    "loss, readability problems, alignment errors, or aspect ratio distortion. Do not "
    "flag a difference merely because it differs from the baseline, and do not "
    "penalize a candidate for fixing a baseline defect. Every issue caused or worsened "
    "by the "
    "candidate must have detail starting exactly with "
    f"'{STYLE_COMPARISON_REGRESSION_PREFIX}'. Other real "
    "candidate defects need no such prefix. If resolution, blur, occlusion, or another "
    "image limitation makes a material comparison genuinely impossible, do not guess: "
    "return needs_fix=true with one high-severity other issue whose detail starts with "
    "'INDETERMINATE:'. If the candidate is clean and readable and has no regression, "
    "return needs_fix=false with an empty issues list. Be specific and concise."
)

STYLE_REFERENCE = "style_reference"
EXACT_RECONSTRUCTION = "exact_reconstruction"
CRITIQUE_PROMPT_VERSION = "slide-critique-v3"
STYLE_COMPARISON_PROMPT_VERSION = "slide-style-comparison-v1"


def _image_mime_type(image: bytes) -> str:
    """Infer the supported source MIME type without trusting a file extension."""
    if image.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if image.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(image) >= 12 and image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        return "image/webp"
    raise ValueError("Unsupported source image bytes; expected PNG, JPEG, or WebP")


def _build_critique_messages(
    image: bytes,
    *,
    slide_context: Optional[str] = None,
    mime_type: str = "image/png",
    source_image: Optional[bytes] = None,
    reference_mode: str = STYLE_REFERENCE,
):
    """Assemble render-only or source-vs-render messages for direct regression tests."""
    if reference_mode == STYLE_REFERENCE:
        prompt = "Review this slide image."
        if slide_context:
            prompt += f" Intended content: {slide_context}"
        return [
            SystemMessage(content=_SYSTEM),
            UserMessage(
                content=[
                    TextContentPart(text=prompt),
                    ImageContentPart(data=image, mime_type=mime_type),
                ]
            ),
        ]
    if reference_mode != EXACT_RECONSTRUCTION:
        raise ValueError(f"Unsupported reference mode {reference_mode!r}")
    if not source_image:
        raise ValueError("Exact reconstruction critique requires a source image")

    prompt = (
        "Compare the following source and rendered slide. Judge only differences "
        "introduced by the reconstruction."
    )
    if slide_context:
        prompt += f" Slide context: {slide_context}"
    return [
        SystemMessage(content=_EXACT_SYSTEM),
        UserMessage(
            content=[
                TextContentPart(text="SOURCE IMAGE (ground truth):"),
                ImageContentPart(
                    data=source_image, mime_type=_image_mime_type(source_image)
                ),
                TextContentPart(text=f"RENDERED IMAGE (candidate): {prompt}"),
                ImageContentPart(data=image, mime_type=mime_type),
            ]
        ),
    ]


def _build_style_comparison_messages(
    baseline_image: bytes,
    candidate_image: bytes,
    *,
    slide_context: Optional[str] = None,
    baseline_mime_type: str = "image/png",
    candidate_mime_type: str = "image/png",
):
    """Assemble pre-fix-baseline vs post-fix-candidate review messages."""
    if not baseline_image or not candidate_image:
        raise ValueError(
            "Style comparison requires non-empty baseline and candidate images"
        )

    prompt = (
        "Compare the candidate with the pre-fix baseline, then report the candidate's "
        "remaining real defects and any regressions."
    )
    if slide_context:
        prompt += f" Slide context: {slide_context}"
    return [
        SystemMessage(content=_STYLE_COMPARISON_SYSTEM),
        UserMessage(
            content=[
                TextContentPart(text="PRE-FIX BASELINE (not ground truth):"),
                ImageContentPart(data=baseline_image, mime_type=baseline_mime_type),
                TextContentPart(text=f"POST-FIX CANDIDATE: {prompt}"),
                ImageContentPart(data=candidate_image, mime_type=candidate_mime_type),
            ]
        ),
    ]


async def critique_slide_image(
    image: bytes,
    slide_context: Optional[str] = None,
    mime_type: str = "image/png",
    *,
    source_image: Optional[bytes] = None,
    reference_mode: str = STYLE_REFERENCE,
) -> SlideCritique:
    """Review a rendered slide image and return a structured critique."""
    client = get_client(config=get_llm_config())
    model = get_model()
    schema = prepare_schema_for_validation(
        SlideCritique.model_json_schema(), strict=False
    )
    response_format = JSONSchemaResponse(name="critique", json_schema=schema, strict=False)
    messages = _build_critique_messages(
        image,
        slide_context=slide_context,
        mime_type=mime_type,
        source_image=source_image,
        reference_mode=reference_mode,
    )
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


async def critique_slide_style_comparison(
    baseline_image: bytes,
    candidate_image: bytes,
    slide_context: Optional[str] = None,
    *,
    baseline_mime_type: str = "image/png",
    candidate_mime_type: str = "image/png",
) -> SlideCritique:
    """Review one post-fix candidate against its non-authoritative pre-fix baseline."""
    client = get_client(config=get_llm_config())
    model = get_model()
    schema = prepare_schema_for_validation(
        SlideCritique.model_json_schema(), strict=False
    )
    response_format = JSONSchemaResponse(
        name="critique", json_schema=schema, strict=False
    )
    messages = _build_style_comparison_messages(
        baseline_image,
        candidate_image,
        slide_context=slide_context,
        baseline_mime_type=baseline_mime_type,
        candidate_mime_type=candidate_mime_type,
    )
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
