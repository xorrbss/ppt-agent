from datetime import datetime
from typing import Optional

from models.llm_message import LLMSystemMessage, LLMUserMessage
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

    slide_structure_instruction = (
        "Each slide should have `structure` field and it"
        "  - Must briefly describe the components in the slide layout:"
        "  - Must start with one of given EXAMPLE STRUCTURES and add/update components as welll as change layouts as per the content."
        "  - Must match with the provided content in structure."
        "  - It shouldn't be very creative. You must pick of the given structures and make slight changes(not more than 5 words) to fit all content."
    )

    slide_outline_structure = (
        "Each slide content:\n"
        "   - Must have a ## title.\n"
        "   - Must have content in exactly the format to be displayed in slide.\n"
        "   - Where content should be structured in Markdown format exactly as how it should be shown in slide layout.\n"
        "   - First slide title must be the same as the presentation title."
    )

    user_instruction_block = (
        f"# User Instruction:\n{instructions}\n"
        if instructions
        else ""
    )
    tone_block = f"# Tone:\n{tone}\n" if tone else ""

    system = (
        "Generate presentation title and outlines for slides.\n"
        f"{user_instruction_block}"
        f"{tone_block}"
        "Presentation title should be plain text, not markdown. It should be a concise title for the presentation.\n"
        "Each slide outline should contain the content for that slide.\n"
        "First slide should be intro and second should be table of contents, then start with regular content slides.\n"
        "Do not overstuff content within same slide. Consider using a slide for a single heading and not more than 2 sub-headings. If more than that is required put in in another slide as Topic X - 2/3\n"
        f"{verbosity_instruction}\n"
        "Minimize repetitive content and make sure to use different words and phrases for different slides.\n"
        "Include numerical data or tables if required or asked by the user.\n"
        "Strictly follow given language and generate content is the prescribed language despite of content or other instructions."
        f"Each slide should object should have `structure` and `content` fields.\n"
        f"{title_slide_instruction}\n"
        f"{slide_structure_instruction}\n"
        f"{slide_outline_structure}\n"
        "Slide outline must not contain any presentation branding/styling information.\n"
        "Title slide must only contain title, presenter name, date and overview.\n"
        "Make sure data used is strictly from the provided content/context.\n"
        "Make sure data is consistent across all slides.\n"
        "**Pick different types of slide structures where appropriate to maintain diversity**.\n"
        "If language is arabic then generate content is Modern Standard English (MSA).\n"
        "If instructed to generate a template then leave spaces with '____' in the content. Do not add arbitrary content, just add fillers."
        "**Never give out chinese text/content.**\n"
        "**Search web to get latest information about the topic**\n"
        "**Use Memory if available should be used to make presentation more personalized and engaging.**\n"
        "User instruction should always be followed and should supercede any other instruction, except for slide numbers."
    )

    return system


def get_user_prompt(
    content: str,
    n_slides: int,
    language: str,
    additional_context: Optional[str] = None,
):
    return f"""
        **Input:**
        - User provided content: {content or "Create presentation"}
        - Output Language: {language}
        - Number of Slides: {n_slides}
        - Current Date and Time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
        - Additional Information: {additional_context or ""}
    """


def get_messages(
    content: str,
    n_slides: int,
    language: str,
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
):
    return [
        LLMSystemMessage(
            content=get_system_prompt(
                tone, verbosity, instructions, include_title_slide
            ),
        ),
        LLMUserMessage(
            content=get_user_prompt(content, n_slides, language, additional_context),
        ),
    ]


async def generate_ppt_outline(
    content: str,
    n_slides: int,
    language: Optional[str] = None,
    additional_context: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
    include_title_slide: bool = True,
    web_search: bool = False,
):
    model = get_model()
    response_model = get_presentation_outline_model_with_n_slides(n_slides)

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
