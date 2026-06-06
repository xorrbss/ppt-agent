import json
from typing import Optional, Tuple

from llmai import get_client
from llmai.shared import JSONSchemaResponse, Message, SystemMessage, UserMessage

from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import (
    PresentationOutlineModel,
    SlideOutlineModel,
)
from models.presentation_structure_model import PresentationStructureModel
from models.slide_spec_model import (
    PresentationComposition,
    archetype_to_layout_id,
    spec_to_blocks,
)
from utils.archetype_profiles import capacity_menu_text
from utils.get_dynamic_models import get_composition_model_with_n_slides
from utils.llm_client_error_handler import handle_llm_client_exceptions
from utils.llm_config import get_llm_config
from utils.llm_provider import get_model
from utils.llm_utils import generate_structured_with_schema_retries
from utils.schema_utils import prepare_schema_for_validation


def _resolve_prompt_language(language: Optional[str]) -> str:
    if language is None:
        return "auto-detect"
    s = str(language).strip()
    if not s or s.lower() in {"auto", "auto-detect"}:
        return "auto-detect"
    return s


def get_system_prompt(
    instructions: Optional[str] = None, tone: Optional[str] = None
) -> str:
    return (
        "You are an expert presentation slide composer (NotebookLM / Gamma style).\n"
        "For EACH input outline slide, compose exactly ONE adaptive slide, in order:\n"
        "1. Choose the archetype from the menu that best fits the slide's content KIND "
        "(numbers/metrics → stat-hero; a single idea with supporting points → "
        "one-column-bullets; the opening/title slide → cover).\n"
        "2. Fill that archetype's fields from the slide's content, staying within EVERY "
        "max-length and max-item limit.\n"
        "3. VARIETY: avoid the same archetype on adjacent slides where the content allows; "
        "use 'cover' only for the first/opening slide.\n"
        "4. Write a concise plain-text speaker_note per slide.\n"
        f"{capacity_menu_text()}\n"
        "Be specific and concrete; never invent facts beyond the provided content. "
        "Output one composed slide per input outline slide, in the same order.\n"
        + (f"\n# Tone\nMake the slides {tone}.\n" if tone else "")
        + (f"\n# User Instructions\n{instructions}\n" if instructions else "")
    )


def get_user_prompt(outline_text: str, language: Optional[str]) -> str:
    return (
        f"# Slide Language\n{_resolve_prompt_language(language)}\n\n"
        f"# Outline slides (compose one adaptive slide per entry, in order):\n{outline_text}\n"
    )


def get_messages(
    outline_text: str,
    language: Optional[str],
    instructions: Optional[str] = None,
    tone: Optional[str] = None,
) -> list[Message]:
    return [
        SystemMessage(content=get_system_prompt(instructions, tone)),
        UserMessage(content=get_user_prompt(outline_text, language)),
    ]


async def compose_slides(
    outline: PresentationOutlineModel,
    language: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
) -> PresentationComposition:
    """Stage B/C fused — compose one adaptive SlideSpec per outline slide.

    Replaces generate_presentation_structure + get_slide_content_from_type_and_outline
    on the adaptive path. Picks an archetype per slide and fills its typed blocks.
    """
    n_slides = len(outline.slides)
    client = get_client(config=get_llm_config())
    model = get_model()
    response_model = get_composition_model_with_n_slides(n_slides)

    schema = prepare_schema_for_validation(
        response_model.model_json_schema(), strict=False
    )
    response_format = JSONSchemaResponse(
        name="response", json_schema=schema, strict=False
    )

    try:
        content = await generate_structured_with_schema_retries(
            client,
            model,
            messages=get_messages(outline.to_string(), language, instructions, tone),
            response_format=response_format,
            json_schema=schema,
            strict=False,
            validate_schema=True,
        )
        return PresentationComposition(**content)
    except Exception as e:
        raise handle_llm_client_exceptions(e)


async def compose_and_project(
    outline: PresentationOutlineModel,
    layout: PresentationLayoutModel,
    language: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
    instructions: Optional[str] = None,
) -> Tuple[
    PresentationComposition, PresentationOutlineModel, PresentationStructureModel
]:
    """Compose an adaptive deck and project it to the (outline, structure) pair
    the existing readers / stream loop / editor consume unchanged.

    Shared by the interactive /prepare branch and the one-shot /generate handler.
    Returns the authoritative composition (persist to deck_plan) plus a projection
    where each outline holds the rendered blocks dict and each structure index
    points at the archetype's slot in the adaptive layout.
    """
    composition = await compose_slides(
        outline,
        language=language,
        tone=tone,
        verbosity=verbosity,
        instructions=instructions,
    )
    proj_outlines = []
    proj_indices = []
    for spec in composition.slides:
        proj_outlines.append(
            SlideOutlineModel(
                content=json.dumps(spec_to_blocks(spec), ensure_ascii=False)
            )
        )
        proj_indices.append(
            layout.get_slide_layout_index(archetype_to_layout_id(spec.archetype))
        )
    return (
        composition,
        PresentationOutlineModel(slides=proj_outlines),
        PresentationStructureModel(slides=proj_indices),
    )
