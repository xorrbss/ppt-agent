from models.llm_message import LLMMessage
from models.presentation_layout import SlideLayoutModel
from models.sql.slide import SlideModel
from services.llm_client import LLMClient
from utils.llm_provider import get_model
from utils.schema_utils import remove_fields_from_schema

system_prompt = """
    Edit Slide data based on provided prompt, follow mentioned steps and notes and provide structured output.

    # Notes
    - Provide output in language mentioned in **Input**.
    - The goal is to change Slide data based on the provided prompt.
    - Do not change **Image prompts** and **Icon queries** if not asked for in prompt.
    - Generate **Image prompts** and **Icon queries** if asked to generate or change in prompt.
    - Make sure to follow language guidelines.

    **Go through all notes and steps and make sure they are followed, including mentioned constraints**
"""


def get_user_prompt(prompt: str, slide_data: dict, language: str):
    return f"""
        ## Icon Query And Image Prompt Language
        English

        ## Slide Content Language
        {language}

        ## Prompt
        {prompt}

        ## Slide data
        {slide_data}
    """


def get_messages(
    prompt: str,
    slide_data: dict,
    language: str,
):
    return [
        LLMMessage(
            role="system",
            content=system_prompt,
        ),
        LLMMessage(
            role="user",
            content=get_user_prompt(prompt, slide_data, language),
        ),
    ]


async def get_edited_slide_content(
    prompt: str,
    slide: SlideModel,
    language: str,
    slide_layout: SlideLayoutModel,
):
    model = get_model()

    response_schema = remove_fields_from_schema(
        slide_layout.json_schema, ["__image_url__", "__icon_url__"]
    )

    client = LLMClient()
    response = await client.generate_structured(
        model=model,
        messages=get_messages(prompt, slide.content, language),
        response_format=response_schema,
        strict=False,
    )
    return response
