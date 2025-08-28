from datetime import datetime
from models.llm_message import LLMSystemMessage, LLMUserMessage
from models.presentation_layout import SlideLayoutModel
from models.presentation_outline_model import SlideOutlineModel
from services.llm_client import LLMClient
from utils.llm_provider import get_model
from utils.schema_utils import add_field_in_schema, remove_fields_from_schema

system_prompt = """
    Generate structured slide based on provided outline, follow mentioned steps and notes and provide structured output.

    # Steps
    1. Analyze the outline.
    2. Generate structured slide based on the outline.
    3. Generate speaker note that is simple, clear, concise and to the point.

    # Notes
    - Slide body should not use words like "This slide", "This presentation".
    - Rephrase the slide body to make it flow naturally.
    - Only use markdown to highlight important points.
    - Make sure to follow language guidelines.
    - Speaker note should be normal text, not markdown.
    **Strictly follow the max and min character limit for every property in the slide.**

    # Image and Icon Output Format
    image: {
        __image_prompt__: string,
    }
    icon: {
        __icon_query__: string,
    }
"""


def get_user_prompt(outline: str, language: str):
    return f"""
        ## Current Date and Time
        {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

        ## Icon Query And Image Prompt Language
        English

        ## Slide Content Language
        {language}

        ## Slide Outline
        {outline}
    """


def get_messages(outline: str, language: str):

    return [
        LLMSystemMessage(
            content=system_prompt,
        ),
        LLMUserMessage(
            content=get_user_prompt(outline, language),
        ),
    ]


async def get_slide_content_from_type_and_outline(
    slide_layout: SlideLayoutModel, outline: SlideOutlineModel, language: str
):
    client = LLMClient()
    model = get_model()

    response_schema = remove_fields_from_schema(
        slide_layout.json_schema, ["__image_url__", "__icon_url__"]
    )
    response_schema = add_field_in_schema(
        response_schema,
        {
            "__speaker_note__": {
                "type": "string",
                "minLength": 100,
                "maxLength": 250,
                "description": "Speaker note for the slide",
            }
        },
        True,
    )

    response = await client.generate_structured(
        model=model,
        messages=get_messages(
            outline.content,
            language,
        ),
        response_format=response_schema,
        strict=False,
    )
    return response
