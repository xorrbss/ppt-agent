from models.llm_message import LLMMessage
from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import PresentationOutlineModel
from services.llm_client import LLMClient
from utils.llm_provider import get_large_model
from utils.get_dynamic_models import get_presentation_structure_model_with_n_slides
from models.presentation_structure_model import PresentationStructureModel


def get_messages(
    presentation_layout: PresentationLayoutModel, n_slides: int, data: str
):
    return [
        LLMMessage(
            role="system",
            content=f"""
                You're a professional presentation designer with creative freedom to design engaging presentations.

                {presentation_layout.to_string()}

                # DESIGN PHILOSOPHY
                - Create visually compelling and varied presentations
                - Match layout to content purpose and audience needs
                - Prioritize engagement over rigid formatting rules

                # Layout Selection Guidelines
                1. **Content-driven choices**: Let the slide's purpose guide layout selection
                - Opening/closing → Title layouts
                - Processes/workflows → Visual process layouts  
                - Comparisons/contrasts → Side-by-side layouts
                - Data/metrics → Chart/graph layouts
                - Concepts/ideas → Image + text layouts
                - Key insights → Emphasis layouts

                2. **Visual variety**: Aim for diverse, engaging presentation flow
                - Mix text-heavy and visual-heavy slides naturally
                - Use your judgment on when repetition serves the content
                - Balance information density across slides

                3. **Audience experience**: Consider how slides work together
                - Create natural transitions between topics
                - Use layouts that enhance comprehension
                - Design for maximum impact and retention

                **Trust your design instincts. Focus on creating the most effective presentation for the content and audience.**

                Select layout index for each of the {n_slides} slides based on what will best serve the presentation's goals.
            """,
        ),
        LLMMessage(
            role="user",
            content=f"""
                {data}
            """,
        ),
    ]


async def generate_presentation_structure(
    presentation_outline: PresentationOutlineModel,
    presentation_layout: PresentationLayoutModel,
) -> PresentationStructureModel:

    client = LLMClient()
    model = get_large_model()
    response_model = get_presentation_structure_model_with_n_slides(
        len(presentation_outline.slides)
    )

    response = await client.generate_structured(
        model=model,
        messages=get_messages(
            presentation_layout,
            len(presentation_outline.slides),
            presentation_outline.to_string(),
        ),
        response_format=response_model,
    )
    return response
