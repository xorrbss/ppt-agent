from typing import Optional

from models.llm_message import LLMSystemMessage, LLMUserMessage
from models.llm_tools import GetCurrentDatetimeTool, SearchWebTool
from services.llm_client import LLMClient
from utils.get_dynamic_models import get_presentation_outline_model_with_n_slides
from utils.get_env import get_web_grounding_env
from utils.llm_provider import get_model
from utils.parsers import parse_bool_or_none
from utils.user_config import get_user_config

system_prompt = """
    You are an expert presentation creator. Generate structured presentations based on user requirements and format them according to the specified JSON schema with markdown content.

    - Provide content for each slide in markdown format.
    - Make sure that flow of the presentation is logical and consistent.
    - Place greater emphasis on numerical data.
    - If Additional Information is provided, divide it into slides.
    - Make sure that content follows language guidelines.
"""


def get_user_prompt(prompt: str, n_slides: int, language: str, content: str):
    return f"""
        **Input:**
        - Prompt: {prompt}
        - Output Language: {language}
        - Number of Slides: {n_slides}
        - Additional Information: {content}
    """


def get_messages(prompt: str, n_slides: int, language: str, content: str):
    return [
        LLMSystemMessage(
            content=system_prompt,
        ),
        LLMUserMessage(
            content=get_user_prompt(prompt, n_slides, language, content),
        ),
    ]


async def generate_ppt_outline(
    prompt: Optional[str],
    n_slides: int,
    language: Optional[str] = None,
    content: Optional[str] = None,
):
    model = get_model()
    response_model = get_presentation_outline_model_with_n_slides(n_slides)

    client = LLMClient()

    tools = [SearchWebTool, GetCurrentDatetimeTool]

    async for chunk in client.stream_structured(
        model,
        get_messages(prompt, n_slides, language, content),
        response_model.model_json_schema(),
        strict=True,
        tools=tools if client.enable_web_grounding() else None,
    ):
        yield chunk
