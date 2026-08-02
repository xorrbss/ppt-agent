"""Read-time page-number normalization for existing authored presentations."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.authored_exact_provenance import AuthoredExactProvenance
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from utils.authored_page_number import normalize_authored_page_number
from utils.authored_styles import load_authored_styles
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    normalize_reference_mode,
)


async def authored_slides_with_standard_page_numbers(
    session: AsyncSession,
    presentation: PresentationModel,
    slides: Sequence[SlideModel],
) -> list[SlideModel]:
    """Return response-only slide copies with the canonical page marker.

    Existing databases are intentionally not rewritten.  Exact decks are
    detected from their private provenance first; a missing or unavailable
    preset is handled conservatively by returning the original slides.
    """

    if not presentation.is_authored():
        return list(slides)
    provenance = await session.get(AuthoredExactProvenance, presentation.id)
    if provenance is not None:
        return list(slides)

    theme = presentation.theme if isinstance(presentation.theme, dict) else {}
    legacy_mode = theme.get("reference_mode")
    if isinstance(legacy_mode, str):
        try:
            if normalize_reference_mode(legacy_mode) == EXACT_RECONSTRUCTION:
                return list(slides)
        except ValueError:
            return list(slides)

    style_id = str(theme.get("style") or "default").strip() or "default"
    style = {candidate.id: candidate for candidate in load_authored_styles()}.get(
        style_id
    )
    if style is None:
        return list(slides)
    if normalize_reference_mode(style.reference_mode) != STYLE_REFERENCE:
        return list(slides)

    primary = str(theme.get("primary") or style.primary_color or style.preview_accent)
    normalized: list[SlideModel] = []
    for slide in slides:
        source = slide.html_content
        if not isinstance(source, str) or not source:
            normalized.append(slide)
            continue
        html = normalize_authored_page_number(
            source,
            slide_index=slide.index,
            primary=primary,
            reference_mode=STYLE_REFERENCE,
        )
        normalized.append(
            SlideModel.model_validate(
                {
                    **slide.model_dump(),
                    "html_content": html,
                }
            )
        )
    return normalized
