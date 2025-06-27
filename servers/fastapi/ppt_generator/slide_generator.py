from typing import Optional

from pydantic import BaseModel

from api.utils.model_utils import get_large_model, get_llm_client, get_small_model
from ppt_config_generator.models import SlideMarkdownModel

from ppt_generator.models.llm_models import (
    LLM_CONTENT_TYPE_MAPPING,
    LLMContentUnion,
)
from ppt_generator.models.other_models import SlideTypeModel
from ppt_generator.models.slide_model import SlideModel


def get_prompt_to_generate_slide_content(title: str, outline: str):
    return [
        {
            "role": "system",
            "content": f"""
                Generate structured slide based on provided title and outline, follow mentioned steps and notes and provide structured output.

                # Steps
                1. Analyze the outline and title.
                2. Generate structured slide based on the outline and title.
                3. Generate image prompts and icon queries if mentioned in schema.
                4. Generate graph if mentioned in schema.

                # Notes
                - Slide body should not use words like "This slide", "This presentation".
                - Rephrase the slide body to make it flow naturally.
                - Do not use markdown formatting in slide body.
            """,
        },
        {
            "role": "user",
            "content": f"""
                ## Slide Title
                {title}

                ## Slide Outline
                {outline}
            """,
        },
    ]


def get_prompt_to_edit_slide_content(
    prompt: str,
    slide_data: dict,
    theme: Optional[dict] = None,
    language: Optional[str] = None,
):
    return [
        {
            "role": "system",
            "content": """
                Edit Slide data based on provided prompt, follow mentioned steps and notes and provide structured output.

                # Notes
                - Provide output in language mentioned in **Input**.
                - The goal is to change Slide data based on the provided prompt.
                - Do not change **Image prompts** and **Icon queries** if not asked for in prompt.
                - Generate **Image prompts** and **Icon queries** if asked to generate or change in prompt.

                **Go through all notes and steps and make sure they are followed, including mentioned constraints**
            """,
        },
        {
            "role": "user",
            "content": f"""
                - Prompt: {prompt}
                - Output Language: {language}
                - Image Prompts and Icon Queries Language: English
                - Theme: {theme}
                - Slide data: {slide_data}
            """,
        },
    ]


def get_prompt_to_select_slide_type(prompt: str, slide_data: dict, slide_type: int):
    return [
        {
            "role": "system",
            "content": """
                Select a Slide Type based on provided user prompt and current slide data.

                Select slide based on following slide description and make sure it matches user requirement:
                # Slide Types (Slide Type : Slide Description)
                    - **1**: contains title, description and image.
                    - **2**: contains title and list of items.
                    - **4**: contains title and list of items with images.
                    - **5**: contains title, description and a graph.
                    - **6**: contains title, description and list of items.
                    - **7**: contains title and list of items with icons.
                    - **8**: contains title, description and list of items with icons.
                    - **9**: contains title, list of items and a graph.

                # Notes
                - Do not select different slide type than current unless absolutely necessary as per user prompt.

                **Go through all notes and steps and make sure they are followed, including mentioned constraints**
            """,
        },
        {
            "role": "user",
            "content": f"""
                - User Prompt: {prompt}
                - Current Slide Data: {slide_data}
                - Current Slide Type: {slide_type}
            """,
        },
    ]


async def get_slide_content_from_type_and_outline(
    slide_type: int, outline: SlideMarkdownModel
) -> LLMContentUnion:
    response_model = LLM_CONTENT_TYPE_MAPPING[slide_type]

    client = get_llm_client()
    model = get_small_model()

    response = await client.beta.chat.completions.parse(
        model=model,
        temperature=0.2,
        messages=get_prompt_to_generate_slide_content(
            outline.title,
            outline.body,
        ),
        response_format=response_model,
    )
    return response.choices[0].message.parsed


async def get_edited_slide_content_model(
    prompt: str,
    slide_type: int,
    slide: SlideModel,
    theme: Optional[dict] = None,
    language: Optional[str] = None,
) -> LLMContentUnion:
    client = get_llm_client()
    model = get_large_model()

    content_type_model_type = LLM_CONTENT_TYPE_MAPPING[slide_type]
    slide_data = slide.content.to_llm_content().model_dump_json()
    response = await client.beta.chat.completions.parse(
        model=model,
        temperature=0.2,
        messages=get_prompt_to_edit_slide_content(
            prompt,
            slide_data,
            theme,
            language,
        ),
        response_format=content_type_model_type,
    )
    return response.choices[0].message.parsed


async def get_slide_type_from_prompt(
    prompt: str,
    slide: SlideModel,
) -> SlideTypeModel:

    client = get_llm_client()
    model = get_small_model()

    response = await client.beta.chat.completions.parse(
        model=model,
        temperature=0.2,
        messages=get_prompt_to_select_slide_type(
            prompt, slide.content.to_llm_content().model_dump_json(), slide.type
        ),
        response_format=SlideTypeModel,
    )
    return response.choices[0].message.parsed
