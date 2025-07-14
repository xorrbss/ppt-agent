from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_provider import get_llm_client, get_small_model
from utils.get_dynamic_models import (
    get_presentation_structure_model_with_n_slides,
)
from models.presentation_structure_model import (
    PresentationStructureModel,
)


def get_prompt(presentation_layout: PresentationLayoutModel, n_slides: int, data: str):
    return [
        {
            "role": "system",
            "content": f"""
                You're a professional presentation designer with years of experience in designing clear and engaging presentations.

                {presentation_layout.to_string()}

                # Steps
                1. Analyze provided Number of slides, Presentation title, Slides content and Presentation Layout.
                2. Select appropriate slide layout **index** for each slide.

                # Notes
                - Slide layout should be selected based on provided content for slide and notes.
                - Don't fall into patterns like always using layout 2 and after layout 1.
                - Each presentation should have its own unique flow and rhythm.
                - Select type for {n_slides} slides.

                **Go through notes and steps and make sure they are all followed. Rule breaks are strictly not allowed.**
            """,
        },
        {
            "role": "user",
            "content": f"""
                {data}
            """,
        },
    ]


async def generate_presentation_structure(
    presentation_outline: PresentationOutlineModel,
    presentation_layout: PresentationLayoutModel,
) -> PresentationStructureModel:

    client = get_llm_client()
    model = get_small_model()
    response_model = get_presentation_structure_model_with_n_slides(
        len(presentation_outline.slides)
    )

    response = await client.beta.chat.completions.parse(
        model=model,
        messages=get_prompt(
            presentation_layout,
            len(presentation_outline.slides),
            presentation_outline.to_string(),
        ),
        response_format=response_model,
    )
    return response.choices[0].message.parsed
