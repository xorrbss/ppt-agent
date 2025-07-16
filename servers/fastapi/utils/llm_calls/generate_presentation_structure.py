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
            # "content": f"""
            #     You're a professional presentation designer with years of experience in designing clear and engaging presentations.
            #     {presentation_layout.to_string()}
            #     # Steps
            #     1. Analyze provided Number of slides, Presentation title, Slides content and Presentation Layout.
            #     2. Select appropriate slide layout **index** for each slide.
            #     # Notes
            #     - Slide layout should be selected based on provided content for slide and notes.
            #     - Don't fall into patterns like always using layout 2 and after layout 1.
            #     - Each presentation should have its own unique flow and rhythm.
            #     - Select type for {n_slides} slides.
            #     **Go through notes and steps and make sure they are all followed. Rule breaks are strictly not allowed.**
            # """,
            "content": f"""
                You're a professional presentation designer. 
                {presentation_layout.to_string()}

                # CRITICAL RULES
                - NEVER use layout type 1 (bullet points) for more than 30% of slides
                - MUST use at least 3 different layout types across presentation
                - NO consecutive slides with same layout type

                # Selection Strategy
                1. **Ignore bullet point format** - focus on slide PURPOSE
                2. **Match content to layout**:
                - Title/intro → Title layouts
                - Process/steps → Visual process layouts
                - Comparisons → Side-by-side layouts
                - Data → Chart/graph layouts
                - Concepts → Image + text layouts
                - Key messages → Emphasis layouts

                3. **Force variety**: If recently used a layout type, pick different one
                4. **Prioritize visual layouts** over text-heavy ones

                **Think PURPOSE not FORMAT. Make it visually engaging.**

                Select layout index for each of the {n_slides} slides.
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
    print(response.choices[0].message.parsed)
    return response.choices[0].message.parsed
