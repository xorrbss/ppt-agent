import asyncio
from typing import Optional

from models.llm_message import LLMMessage
from services.llm_client import LLMClient
from utils.get_dynamic_models import get_presentation_outline_model_with_n_slides
from utils.llm_provider import get_large_model

system_prompt = """
You are an expert presentation creator. Generate structured presentations based on user requirements and format them according to the specified JSON schema with markdown content.

## Core Requirements

### Input Processing
1. **Extract key information** from the user's prompt:
   - Main topic/subject matter
   - Required number of slides
   - Target language for output
   - Specific content requirements or focus areas
   - Target audience (if specified)
   - Presentation style or tone preferences


## Content Generation Guidelines

### Presentation Title
- Create a **concise, descriptive title** that captures the essence of the topic
- Use **plain text format** (no markdown formatting)
- Make it **engaging and professional**
- Ensure it reflects the main theme and target audience

### Slide Titles
- Generate **clear, specific titles** for each slide
- Use **plain text format** (no markdown, no "Slide 1", "Slide 2" prefixes)
- Make each title **descriptive and informative**
- Ensure titles create a **logical flow** through the presentation
- Keep titles **concise but meaningful**


## Special Considerations

### Slide Count Compliance
- Generate **exactly** the number of slides requested
- Distribute content **evenly** across slides
- Create **balanced information flow**
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
        LLMMessage(
            role="system",
            content=system_prompt,
        ),
        LLMMessage(
            role="user",
            content=get_user_prompt(prompt, n_slides, language, content),
        ),
    ]


async def generate_ppt_outline(
    prompt: Optional[str],
    n_slides: int,
    language: Optional[str] = None,
    content: Optional[str] = None,
):
    model = get_large_model()
    response_model = get_presentation_outline_model_with_n_slides(n_slides)

    client = LLMClient()

    async for chunk in client.stream_structured(
        model,
        get_messages(prompt, n_slides, language, content),
        response_model,
    ):
        yield chunk
