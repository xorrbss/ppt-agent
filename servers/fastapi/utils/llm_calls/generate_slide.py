import json
from pydantic import BaseModel, Field
from models.presentation_layout import SlideLayoutModel
from models.presentation_outline_model import SlideOutlineModel
from utils.llm_provider import get_llm_client, get_small_model


def get_prompt_to_generate_slide_content(title: str, outline: str):
    return [
        {
            "role": "system",
            "content": f"""
                Generate structured slide based on provided title and outline, follow mentioned steps and notes and provide structured output.

                # Steps
                1. Analyze the outline and title.
                2. Generate structured slide based on the outline and title.

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


async def get_slide_content_from_type_and_outline(
    slide_layout: SlideLayoutModel, outline: SlideOutlineModel
):
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": slide_layout.name or slide_layout.id,
            "schema": slide_layout.json_schema,
        },
    }

    client = get_llm_client()
    model = get_small_model()

    response = await client.beta.chat.completions.parse(
        model=model,
        messages=get_prompt_to_generate_slide_content(
            outline.title,
            outline.body,
        ),
        response_format=response_format,
    )

    return json.loads(response.choices[0].message.content)
