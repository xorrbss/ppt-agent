import asyncio
import json
from google.genai.types import GenerateContentConfig
from models.presentation_layout import SlideLayoutModel
from models.presentation_outline_model import SlideOutlineModel
from utils.llm_provider import (
    get_google_llm_client,
    get_llm_client,
    get_small_model,
    is_google_selected,
)
from utils.schema_utils import remove_fields_from_schema, generate_constraint_sentences

system_prompt = """
    Generate structured slide based on provided title and outline, follow mentioned steps and notes and provide structured output.

    # Steps
    1. Analyze the outline and title.
    2. Generate structured slide based on the outline and title.

    # Notes
    - **Strictly follow the max and min character limit for each property in the slide.**
    - Slide body should not use words like "This slide", "This presentation".
    - Rephrase the slide body to make it flow naturally.
    - Do not use markdown formatting in slide body.
"""


def get_user_prompt(title: str, outline: str):
    return f"""
        ## Slide Title
        {title}

        ## Slide Outline
        {outline}
    """


def get_prompt_to_generate_slide_content(title: str, outline: str, schema_constraints: str = ""):
    
    return [
        {
            "role": "system",
            "content": system_prompt + f"\n{schema_constraints}",
        },
        {
            "role": "user",
            "content": get_user_prompt(title, outline),
        },
    ]


async def get_slide_content_from_type_and_outline(
    slide_layout: SlideLayoutModel, outline: SlideOutlineModel
):
    model = get_small_model()
    
    schema_constraints = generate_constraint_sentences(slide_layout.json_schema)

    if not is_google_selected():
        client = get_llm_client()
        response = await client.beta.chat.completions.parse(
            model=model,
            messages=get_prompt_to_generate_slide_content(
                outline.title,
                outline.body,
                schema_constraints,
            ),
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "SlideContent",
                    "schema": remove_fields_from_schema(
                        slide_layout.json_schema, ["__image_url__", "__icon_url__"]
                    ),
                },
            },
        )
        return json.loads(response.choices[0].message.content)
    else:
        client = get_google_llm_client()
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=model,
            contents=[get_user_prompt(outline.title, outline.body)],
            config=GenerateContentConfig(
                system_instruction=system_prompt + f"\n{schema_constraints}",
                response_mime_type="application/json",
                response_json_schema=slide_layout.json_schema,
            ),
        )
        return json.loads(response.text)
