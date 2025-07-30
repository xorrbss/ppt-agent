import asyncio
import json

from models.presentation_layout import SlideLayoutModel
from models.sql.slide import SlideModel
from google.genai.types import GenerateContentConfig
from utils.llm_provider import (
    get_anthropic_llm_client,
    get_google_llm_client,
    get_large_model,
    get_llm_client,
    is_anthropic_selected,
    is_google_selected,
)
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


def get_prompt_to_edit_slide_content(
    prompt: str,
    slide_data: dict,
    language: str,
):
    return [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": get_user_prompt(prompt, slide_data, language),
        },
    ]


async def get_edited_slide_content(
    prompt: str,
    slide_layout: SlideLayoutModel,
    slide: SlideModel,
    language: str,
):
    model = get_large_model()
    response_schema = remove_fields_from_schema(
        slide_layout.json_schema, ["__image_url__", "__icon_url__"]
    )

    if is_google_selected():
        client = get_google_llm_client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[get_user_prompt(prompt, slide.content, language)],
            config=GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                response_json_schema=response_schema,
            ),
        )
        slide_content_json = json.loads(response.text)

    else:
        client = get_llm_client()
        response = await client.beta.chat.completions.parse(
            model=model,
            messages=get_prompt_to_edit_slide_content(
                prompt,
                slide.content,
                language,
            ),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "slide_content",
                    "schema": response_schema,
                },
            },
        )
        slide_content_json = json.loads(response.choices[0].message.content)

    return slide_content_json
