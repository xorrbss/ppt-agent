import json
from typing import Dict, Any, Optional
from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.generate_presentation_outlines import generate_ppt_outline


async def generate_outline(
    prompt: str,
    n_slides: int = 8,
    language: str = "English",
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Generate presentation outlines given a prompt, number of slides, language, and optional summary.
    Returns the parsed outline data.
    """
    presentation_content_text = ""
    async for chunk in generate_ppt_outline(
        prompt,
        n_slides,
        language,
        summary,
    ):
        presentation_content_text += chunk

    presentation_content_json = json.loads(presentation_content_text)
    presentation_content = PresentationOutlineModel(
        **presentation_content_json)
    outlines = [slide.model_dump()
                for slide in presentation_content.slides[:n_slides]]
    return {
        "title": presentation_content.title,
        "outlines": outlines,
        "notes": presentation_content.notes
    }
