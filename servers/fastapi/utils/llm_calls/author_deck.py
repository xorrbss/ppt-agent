"""Authored-mode deck builder: turn an outline into a list of bespoke HTML slides
(authored concurrently against one shared design system for cohesion) and assemble
the rendered slide images into an image-per-slide PPTX that opens in PowerPoint with
perfect fidelity. The render step itself reuses utils/slide_capture. Opt-in — the
fast default template path is untouched."""

import asyncio
import io
from typing import List

from models.presentation_outline_model import PresentationOutlineModel
from utils.llm_calls.author_slide import Brand, author_slide_html, build_design_system

# A light deck-plan: a slide's ROLE nudges the model toward a fitting bespoke layout
# (cover hero / editorial problem / numbered pillars / phased roadmap / hero metric /
# bold closing). Position drives the frame; content keywords pick the middle role.
_METRIC_HINTS = (
    "%", "퍼센트", "성장", "매출", "수익", "지표", "비율", "증가", "감소", "달성",
    "ROI", "조원", "억원", "billion", "million", "growth", "revenue",
)
_TIMELINE_HINTS = (
    "단계", "로드맵", "분기", "타임라인", "절차", "순서", "스텝",
    "phase", "roadmap", "timeline", "step", "quarter", "2024", "2025", "2026", "2027",
)


def derive_role(index: int, n: int, content: str) -> str:
    """Per-slide ROLE for the authoring prompt, from position + content keywords."""
    if index == 0:
        return "COVER"
    if index == n - 1:
        return "CLOSING"
    if index == 1:
        return "PROBLEM"
    lowered = content.lower()
    if any(h.lower() in lowered for h in _TIMELINE_HINTS):
        return "ROADMAP"
    if any(h.lower() in lowered for h in _METRIC_HINTS):
        return "OUTCOMES"
    return "PILLARS"


def plan_deck_roles(outline: PresentationOutlineModel) -> List[str]:
    """The deck-plan: one ROLE per slide. Single source of role derivation, shared by
    the authoring pass and the vision-QA re-author pass."""
    slides = list(outline.slides)
    n = len(slides)
    return [derive_role(i, n, slides[i].content) for i in range(n)]


async def author_deck(outline: PresentationOutlineModel, brand: Brand) -> List[str]:
    """Author every outline slide concurrently against the shared design system.
    Returns one complete HTML document per slide (order preserved)."""
    slides = list(outline.slides)
    n = len(slides)
    design_system = build_design_system(brand)
    roles = plan_deck_roles(outline)
    htmls = await asyncio.gather(
        *[
            author_slide_html(
                slides[i].content, design_system, brand, roles[i], i, n
            )
            for i in range(n)
        ]
    )
    return list(htmls)


def build_image_pptx(images: List[bytes], out_path: str) -> str:
    """Assemble rendered slide PNGs into a 16:9 PPTX, one full-bleed picture per
    slide (perfect fidelity, opens in PowerPoint). Returns the saved path."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    for img in images:
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(
            io.BytesIO(img), 0, 0, width=prs.slide_width, height=prs.slide_height
        )
    prs.save(out_path)
    return out_path
