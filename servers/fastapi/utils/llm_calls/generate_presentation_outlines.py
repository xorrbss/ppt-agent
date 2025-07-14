from typing import Optional
from openai.lib.streaming.chat._events import ContentDeltaEvent

from utils.get_dynamic_models import get_presentation_outline_model_with_n_slides
from utils.llm_provider import (
    get_large_model,
    get_llm_client,
)

system_prompt = """
    Create a presentation based on the provided prompt, number of slides, output language, and additional informational details.
    Format the output in the specified JSON schema with structured markdown content.

    # Steps

    1. Identify key points from the provided prompt, including the topic, number of slides, output language, and additional content directions.
    2. Create a concise and descriptive title reflecting the main topic, adhering to the specified language.
    3. Generate a clear title for each slide.
    4. Develop comprehensive content using markdown structure:
        * Use bullet points (- or *) for lists.
        * Use **bold** for emphasis, *italic* for secondary emphasis, and `code` for technical terms.
    5. Provide important points from prompt as notes.
    
    # Notes
    - Content must be generated for every slide.
    - Images or Icons information provided in **Input** must be included in the **notes**.
    - Notes should cleary define if it is for specific slide or for the presentation.
    - Slide **body** should not contain slide **title**.
    - Slide **title** should not contain "Slide 1", "Slide 2", etc.
    - Slide **title** should not be in markdown format.
    - There must be exact **Number of Slides** as specified.
"""


def get_user_prompt(prompt: str, n_slides: int, language: str, content: str):
    return f"""
        **Input:**
        - Prompt: {prompt}
        - Output Language: {language}
        - Number of Slides: {n_slides}
        - Additional Information: {content}
    """


def get_prompt_template(prompt: str, n_slides: int, language: str, content: str):
    return [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": get_user_prompt(prompt, n_slides, language, content),
        },
    ]


async def generate_ppt_outline(
    prompt: Optional[str],
    n_slides: int,
    language: Optional[str] = None,
    content: Optional[str] = None,
):
    model = get_large_model()
    response_model = get_presentation_outline_model_with_n_slides(n_slides)

    client = get_llm_client()
    async with client.beta.chat.completions.stream(
        model=model,
        messages=get_prompt_template(prompt, n_slides, language, content),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "PresentationOutline",
                "schema": response_model.model_json_schema(),
            },
        },
    ) as stream:
        async for event in stream:
            if isinstance(event, ContentDeltaEvent):
                yield event.delta
