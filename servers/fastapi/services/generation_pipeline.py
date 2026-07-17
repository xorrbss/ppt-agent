"""Shared, non-adaptive (template-path) structure pipeline.

`/prepare` and the one-shot `/generate` handler both turn an outline + a layout
into a persisted PresentationStructure the same way — choose a layout per slide,
clamp/backfill out-of-range indices, insert a table of contents, and fit content
to capacity. That logic used to be copy-pasted into both endpoints (with subtle
drift and dead code); it lives here once so a fix lands in a single place.

The adaptive path (compose_and_project) and the authored path have their own
self-contained pipelines and do NOT use this module.
"""
import random
from typing import Optional, Tuple

from models.presentation_layout import PresentationLayoutModel
from models.presentation_outline_model import PresentationOutlineModel
from models.presentation_structure_model import PresentationStructureModel
from utils.layout_capacity import apply_capacity_fit
from utils.llm_calls.generate_presentation_structure import (
    generate_presentation_structure,
)
from utils.outline_utils import (
    get_no_of_toc_required_for_n_outlines,
    get_presentation_outline_model_with_toc,
)
from utils.ppt_utils import select_toc_or_list_slide_layout_index


def _clamp_and_backfill_structure(
    structure: PresentationStructureModel,
    total_outlines: int,
    total_slide_layouts: int,
) -> None:
    """Trim the model-chosen layout indices to the outline count and replace any
    out-of-range index with a random valid layout (mutates in place)."""
    structure.slides = structure.slides[:total_outlines]
    for index in range(total_outlines):
        if structure.slides[index] >= total_slide_layouts:
            structure.slides[index] = random.randint(0, total_slide_layouts - 1)


def _insert_toc_layouts(
    structure: PresentationStructureModel,
    n_toc_slides: int,
    include_title_slide: bool,
    toc_slide_layout_index: int,
) -> None:
    if n_toc_slides <= 0 or toc_slide_layout_index == -1:
        return
    insertion_index = 1 if include_title_slide else 0
    for i in range(n_toc_slides):
        structure.slides.insert(insertion_index + i, toc_slide_layout_index)


async def build_template_structure(
    outline: PresentationOutlineModel,
    layout: PresentationLayoutModel,
    *,
    instructions: Optional[str],
    using_slides_markdown: bool,
    include_table_of_contents: bool,
    include_title_slide: bool,
    target_n_slides: Optional[int],
) -> Tuple[PresentationOutlineModel, PresentationStructureModel]:
    """Build the (outline, structure) pair for a non-adaptive template deck.

    Returns the possibly-TOC-expanded outline and the fitted structure; callers
    persist them and derive n_slides from the result. Behaviour is identical to
    the code previously inlined in /prepare and generate_presentation_handler:
    - /prepare passes using_slides_markdown=False (interactive path), so the TOC
      and capacity-fit run exactly as before (`not False` == True).
    - the handler passes the real using_slides_markdown, so a user-supplied
      slides_markdown deck still skips TOC + capacity-fit.
    """
    total_outlines = len(outline.slides)
    total_slide_layouts = len(layout.slides)

    if layout.ordered:
        structure = layout.to_presentation_structure()
    else:
        structure = await generate_presentation_structure(
            outline, layout, instructions, using_slides_markdown
        )

    _clamp_and_backfill_structure(structure, total_outlines, total_slide_layouts)

    if include_table_of_contents and not using_slides_markdown:
        n_toc_slides = get_no_of_toc_required_for_n_outlines(
            n_outlines=total_outlines,
            title_slide=include_title_slide,
            target_total_slides=target_n_slides,
        )
        toc_slide_layout_index = select_toc_or_list_slide_layout_index(layout)
        _insert_toc_layouts(
            structure, n_toc_slides, include_title_slide, toc_slide_layout_index
        )
        if toc_slide_layout_index != -1 and n_toc_slides > 0:
            outline = get_presentation_outline_model_with_toc(
                outline=outline,
                n_toc_slides=n_toc_slides,
                title_slide=include_title_slide,
            )

    # Content-volume-aware fitting: upgrade overflowing slides to a bigger
    # same-kind layout, or split them. Skipped for user-provided slides_markdown
    # (matches the handler; /prepare always ran it because it is never markdown).
    if not using_slides_markdown:
        outline, structure = apply_capacity_fit(outline, structure, layout)

    return outline, structure
