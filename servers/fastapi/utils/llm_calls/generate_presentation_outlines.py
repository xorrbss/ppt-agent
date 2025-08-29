from datetime import datetime
from typing import Optional

from models.llm_message import LLMSystemMessage, LLMUserMessage
from models.llm_tools import SearchWebTool
from services.llm_client import LLMClient
from utils.get_dynamic_models import get_presentation_outline_model_with_n_slides
from utils.llm_provider import get_model


def get_system_prompt(
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
):
    return f"""
        You are an expert presentation creator. Generate structured presentations based on user requirements and format them according to the specified JSON schema with markdown content.

        Try to use available tools for better results.

        {"# User Instruction:" if instructions else ""}
        {instructions or ""}

        {"# Tone:" if tone else ""}
        {tone or ""}

        {"# Verbosity:" if verbosity else ""}
        {verbosity or ""}

        - Provide content for each slide in markdown format.
        - Make sure that flow of the presentation is logical and consistent.
        - Place greater emphasis on numerical data.
        - If Additional Information is provided, divide it into slides.
        - Make sure no images are provided in the content.
        - Make sure that content follows language guidelines.
    """


def get_user_prompt(
    content: str,
    n_slides: int,
    language: str,
    additional_context: Optional[str] = None,
):
    return f"""
        **Input:**
        - User provided content: {content}
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
):
    return [
        LLMSystemMessage(
            content=get_system_prompt(tone, verbosity, instructions),
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
    web_search: bool = False,
):
    model = get_model()
    response_model = get_presentation_outline_model_with_n_slides(n_slides)

    client = LLMClient()

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
        ),
        response_model.model_json_schema(),
        strict=True,
        tools=(
            [SearchWebTool] if (client.enable_web_grounding() and web_search) else None
        ),
    ):
        yield chunk
