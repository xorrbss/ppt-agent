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


def _verbosity_instructions(verbosity: Optional[str]) -> str:
    """Density target per field, mirroring generate_slide_content's verbosity scale
    so the adaptive composer respects the same concise/standard/text-heavy signal."""
    if verbosity == "concise":
        return (
            "Keep body text tight (roughly 50-60% of each body field's budget) while "
            "still including most allowed list items. Be concrete and specific."
        )
    if verbosity == "text-heavy":
        return (
            "Make body text rich and detailed and populate the MAXIMUM number of list "
            "items each archetype allows. Always stay within every field's max-length; "
            "keep titles, eyebrows, labels and stat values short."
        )
    # standard / default: still apply real density pressure (the common path)
    return (
        "Give body text real substance and populate most or all allowed list items, "
        "while staying within every field's max-length. Favor substance over brevity; "
        "keep titles, eyebrows, labels and stat values short."
    )


def get_system_prompt(
    instructions: Optional[str] = None,
    tone: Optional[str] = None,
    verbosity: Optional[str] = None,
) -> str:
    return (
        "You are an expert presentation slide composer (NotebookLM / Gamma style).\n"
        "For EACH input outline slide, compose exactly ONE adaptive slide, in order:\n"
        "1. KIND-FIRST archetype choice — pick the archetype from the menu that fits the "
        "slide's content shape:\n"
        "   - numbers/KPIs/metrics → stat-hero (a few big numbers) or chart-insight (a trend/series);\n"
        "   - ordered steps/phases/roadmap → timeline;\n"
        "   - 'X vs Y' / before-after / option tiers → comparison;\n"
        "   - several equal-weight parallel items/features → card-grid;\n"
        "   - real tabular data (rows x columns) → table;\n"
        "   - a narrative paired with one visual → two-column or image-led;\n"
        "   - one idea + supporting points → one-column-bullets;\n"
        "   - the opening/title slide → cover; a table-of-contents → agenda;\n"
        "   - a single bold message/quote → big-statement; a section transition → section-divider;\n"
        "   - the final thank-you/contact slide → closing.\n"
        "2. Fill that archetype's fields from the slide's content. Expand the BODY fields "
        "(leads, bullets, card text, takeaways, captions, speaker notes) with substantive "
        "detail and populate the MAXIMUM number of list items the archetype allows — never "
        "ship under-filled lists. Keep SHORT fields (titles, eyebrows, labels, stat values, "
        "step labels, attributions) tight. NEVER exceed any field's max-length or max-item "
        "limit — over-long output is rejected. For image slides, write a vivid English "
        "__image_prompt__ and leave __image_url__ empty.\n"
        "3. VARIETY & RHYTHM (compose the deck as a whole, not slide-by-slide):\n"
        "   - NEVER use the same archetype on two adjacent slides; avoid 3 of the same kind in a row.\n"
        "   - Insert a breather (section-divider, big-statement, or image-led) roughly every "
        "3-5 content slides to vary visual density.\n"
        "   - Use 'cover' only for the first slide; prefer 'closing' for the last slide.\n"
        "   - Alternate dense slides (table, card-grid 6-8) with lighter ones (big-statement, image-led).\n"
        "4. Write a concise plain-text speaker_note per slide.\n"
        f"{capacity_menu_text()}\n"
        "Be specific and concrete; never invent facts beyond the provided content. "
        "Output one composed slide per input outline slide, in the same order.\n"
        "\n# Composition Variants (optional `variant` field)\n"
        "Some archetypes accept a `variant` to vary the layout. Use them sparingly for "
        "rhythm — like archetypes, do not repeat the same variant on adjacent slides:\n"
        "   - cover: \"centered\" (default) or \"left\" (left-anchored, editorial).\n"
        "   - section-divider: \"plain\" (default) or \"bold\" (full-colour section break) — "
        "use \"bold\" as an occasional strong breather between major sections.\n"
        "   - stat-hero: \"even\" (default) or \"featured\" (oversize the single most "
        "important number) — use \"featured\" when one metric clearly leads.\n"
        "Omit `variant` to use the default. Only set the values listed above.\n"
        "\n# Content Depth\n"
        "- Be specific and concrete: prefer named examples, figures, dates, percentages, "
        "and cause-effect detail over generic statements.\n"
        "- Genuinely expand each outline point into substantive prose — do not merely "
        "restate or summarize the outline.\n"
        "- Every bullet, card, stat, or point must carry real, distinct information; never "
        'use filler such as "various aspects" or vague placeholders.\n'
        f"\n# Verbosity\n{_verbosity_instructions(verbosity)}\n"
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
    verbosity: Optional[str] = None,
) -> list[Message]:
    return [
        SystemMessage(content=get_system_prompt(instructions, tone, verbosity)),
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
            messages=get_messages(
                outline.to_string(), language, instructions, tone, verbosity
            ),
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
