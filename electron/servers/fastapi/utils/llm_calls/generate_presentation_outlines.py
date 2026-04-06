from datetime import datetime
from typing import Optional

from models.llm_message import LLMSystemMessage, LLMUserMessage
from models.presentation_outline_model import PresentationOutlineModel
from models.llm_tools import SearchWebTool
from services.llm_client import LLMClient
from utils.get_dynamic_models import get_presentation_outline_model_with_n_slides
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_provider import get_model

"""
Previously there was a dedicated search-query generation prompt constant.
Removed in favor of embedding short, actionable web-search steps into the
system prompt when web grounding is requested.
"""


def get_system_prompt(
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
    include_table_of_contents: bool = False,
):
    verbosity_instruction = (
        "Slide content should be abound 20 words but detailed enough to generate a good slide."
        if verbosity == "concise"
        else (
            "Slide content should be abound 60 words but detailed enough to generate a good slide."
            if verbosity == "text-heavy"
            else "Slide content should be abound 40 words but detailed enough to generate a good slide."
        )
    )

    title_slide_instruction = (
        "Include presenter name in first slide."
        if include_title_slide
        else "Do not include presenter name in any slides."
    )

    toc_instruction = (
        "Include a table of contents slide in the outline sequence."
        if include_table_of_contents
        else ""
    )
    toc_block = f"{toc_instruction}\n" if toc_instruction else ""

    slide_outline_structure = (
        "Each slide content:\n"
        "   - Must have a ## title.\n"
        "   - Must have content either in multiple bullet points or table or both.\n"
        "   - Must be in Markdown format.\n"
        "   - Don't use **bold** and __italic__ text."
        "   - First slide title must be the same as the presentation title."
    )

    system = (
        "Generate presentation title and content for slides.\n"
        "Generate flow based on user **content** and use **context** just for reference.\n"
        "Presentation title should be plain text, not markdown. It should be a concise title for the presentation.\n"
        "Each slide content should contain the content for that slide.\n"
        f"{verbosity_instruction}\n"
        "Minimize repetitive content and make sure to use different words and phrases for different slides.\n"
        "Include numerical data or tables if required or asked by the user.\n"
        "If 'auto-detect' is used, figure it out from the content/context.\n"
        f"{title_slide_instruction}\n"
        f"{toc_block}"
        f"{slide_outline_structure}\n"
        "Slide content must not contain any presentation branding/styling information.\n"
        "Title slide must only contain title, presenter name, date and overview.\n"
        "Only include URLs if they appear in the provided content/context.\n"
        "Make sure data used is strictly from the provided content/context.\n"
        "Make sure data is consistent across all slides."
    )

    return system


def _resolve_prompt_language(language: Optional[str]) -> str:
    if language is None:
        return "auto-detect"
    s = str(language).strip()
    if not s:
        return "auto-detect"
    if s.lower() in {"auto", "auto-detect"}:
        return "auto-detect"
    return s


def _resolve_prompt_n_slides(n_slides: Optional[int]) -> str:
    if n_slides is None:
        return "auto-detect"
    return str(n_slides)


def get_user_prompt(
    content: str,
    n_slides: Optional[int],
    language: Optional[str],
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
    include_table_of_contents: bool = False,
):
    display_language = _resolve_prompt_language(language)
    display_slides = _resolve_prompt_n_slides(n_slides)
    toc_text = f"Include Table Of Contents: {str(include_table_of_contents).lower()}\n"
    return (
        f"Content: {content or ''}\n"
        f"Number of Slides: {display_slides}\n"
        f"Language: {display_language}\n"
        f"Tone: {tone or ''}\n"
        f"Today's Date: {datetime.now().strftime('%Y-%m-%d')}\n"
        f"Include Title Slide: {include_title_slide}\n"
        f"{toc_text if include_table_of_contents else ''}"
        f"Instructions: {instructions or ''}\n"
        f"Context: {additional_context or ''}"
    )


def get_messages(
    content: str,
    n_slides: Optional[int],
    language: Optional[str],
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
    include_table_of_contents: bool = False,
):
    return [
        LLMSystemMessage(
            content=get_system_prompt(
                tone,
                verbosity,
                instructions,
                include_title_slide,
                include_table_of_contents,
            ),
        ),
        LLMUserMessage(
            content=get_user_prompt(
                content,
                n_slides,
                language,
                additional_context,
                tone,
                instructions,
                include_title_slide,
                include_table_of_contents,
            ),
        ),
    ]


async def generate_ppt_outline(
    content: str,
    n_slides: Optional[int],
    language: Optional[str] = None,
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
    web_search: bool = False,
    include_table_of_contents: bool = False,
):
    model = get_model()
    response_model = (
        get_presentation_outline_model_with_n_slides(n_slides)
        if n_slides is not None
        else PresentationOutlineModel
    )

    client = LLMClient()

    try:
        async for chunk in client.stream_structured(
            model,
            get_messages(
                content,
                n_slides,
                language,
                additional_context,
                tone,
                verbosity,
                instructions,
                include_title_slide,
                include_table_of_contents,
            ),
            response_model.model_json_schema(),
            strict=True,
            tools=(
                [SearchWebTool]
                if (client.enable_web_grounding() and web_search)
                else None
            ),
        ):
            yield chunk
    except Exception as e:
        yield handle_llm_client_exceptions(e)
