"""Authored generation mode pipeline (opt-in, request.template == "authored").

The model AUTHORS bespoke HTML per slide (author_deck), the slides are rendered to
PNGs, optionally self-corrected via vision-QA, assembled into an image-per-slide
PPTX (or PDF), and persisted. This bypasses the React-template
compose/structure/content/export path entirely — so it also works where the
byte-PPTX export runtime is unavailable (the converter is Linux-only). The fast
default adaptive/template path is completely untouched."""

import asyncio
import io
import logging
import os
import time
import traceback
import uuid
from dataclasses import dataclass
from typing import List, Optional

from pathvalidate import sanitize_filename
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import get_exports_directory, get_images_directory
from utils.filename_utils import safe_export_basename
from utils.authored_illustrations import apply_authored_illustration
from utils.authored_styles import resolve_authored_style
from utils.llm_calls.author_deck import (
    AuthoredDeckResult,
    author_planned_slide,
    build_image_pptx,
    plan_deck_roles,
    prepare_authored_deck,
)
from utils.llm_calls.author_slide import Brand, apply_style_defaults
from utils.llm_calls.author_vision_qa import revise_authored_deck
from utils.outline_utils import get_presentation_title_from_presentation_outline
from utils.slide_capture import _placeholder_png, find_chrome, render_html_to_png

AUTHORED_TEMPLATE = "authored"
_DEFAULT_PRIMARY = "#2563EB"

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthoredGenerationPipelineResult:
    deck: AuthoredDeckResult
    pngs: List[bytes]
    elapsed: float
    author_work: float
    illustration_work: float
    render_work: float


def _is_korean(*candidates: Optional[str]) -> bool:
    for c in candidates:
        s = (c or "").lower()
        if "korea" in s or "한국" in s or s.startswith("ko"):
            return True
    return False


def authored_title(
    request: GeneratePresentationRequest, outline: PresentationOutlineModel
) -> str:
    """A clean deck title for authored mode. Prefer the user's topic (request.content
    first line) over the verbose generated first-slide outline (which often carries
    presenter/date/agenda scaffolding that bloats the title and filename)."""
    content = (request.content or "").strip()
    if content:
        first_line = content.splitlines()[0].lstrip("#").strip()
        if first_line:
            return first_line[:80]
    return get_presentation_title_from_presentation_outline(outline)


def resolve_brand(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    language: Optional[str],
) -> Brand:
    """Build the shared design tokens for the deck. Theme injection point: brand primary
    colour + fonts + wordmark go into every slide's design-system brief. User-supplied
    request fields win; otherwise a brand-blue primary + language-aware fonts (Noto Sans
    KR for Korean, else a clean sans) and no wordmark."""
    korean = _is_korean(language, request.language)
    return Brand(
        topic=authored_title(request, outline),
        language=language or ("Korean" if korean else "the deck's language"),
        primary=(request.primary_color or _DEFAULT_PRIMARY).strip() or _DEFAULT_PRIMARY,
        fonts=(request.fonts or ("Noto Sans KR" if korean else "Inter")).strip(),
        wordmark=(request.wordmark or "").strip(),
        primary_is_explicit=bool((request.primary_color or "").strip()),
        fonts_are_explicit=bool((request.fonts or "").strip()),
    )


def _save_slide_pngs(presentation_id: uuid.UUID, pngs: List[bytes]) -> List[str]:
    """Persist slide PNGs under the images dir; return paths relative to it (the form
    resolve_app_path_to_filesystem / the /app_data static mount understand)."""
    rel_dir = os.path.join("authored", str(presentation_id))
    abs_dir = os.path.join(get_images_directory(), rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    refs: List[str] = []
    for i, png in enumerate(pngs):
        fname = f"slide_{i}.png"
        with open(os.path.join(abs_dir, fname), "wb") as f:
            f.write(png)
        refs.append(f"{rel_dir.replace(os.sep, '/')}/{fname}")
    return refs


def _build_image_pdf(pngs: List[bytes], out_path: str) -> str:
    from PIL import Image

    imgs = [Image.open(io.BytesIO(p)).convert("RGB") for p in pngs]
    imgs[0].save(out_path, save_all=True, append_images=imgs[1:])
    return out_path


def _build_authored_export(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    pngs: List[bytes],
    title: str,
) -> str:
    """Assemble the rendered slides into the requested export format (pure Python:
    python-pptx / PIL — no external converter). Returns the absolute file path."""
    base = safe_export_basename(
        sanitize_filename((title or str(presentation_id)).strip() or str(presentation_id))
    )
    stem = f"{base}-{presentation_id.hex[:8]}"
    exports = get_exports_directory()
    if request.export_as == "pdf":
        return _build_image_pdf(pngs, os.path.join(exports, f"{stem}.pdf"))
    return build_image_pptx(pngs, os.path.join(exports, f"{stem}.pptx"))


async def _author_illustrate_render_pipeline(
    outline: PresentationOutlineModel,
    brand: Brand,
    style,
) -> AuthoredGenerationPipelineResult:
    """Pipeline each slide through author -> illustration -> render.

    Every slide advances as soon as its previous stage finishes, instead of waiting
    for the slowest slide at each deck-wide stage boundary. Per-stage process-wide
    limits remain enforced by the author, image and render helpers. ``gather`` starts
    all slide pipelines concurrently and its ordered result keeps deck order stable.
    """
    started = time.monotonic()
    plan = prepare_authored_deck(outline, brand, style)
    image_service = (
        ImageGenerationService(get_images_directory())
        if getattr(style, "illustration_prompt", None)
        else None
    )

    async def process_slide(
        index: int,
    ) -> tuple[int, str, bytes, bool, float, float, float]:
        author_started = time.monotonic()
        html = await author_planned_slide(plan, index)
        author_work = time.monotonic() - author_started

        illustration_work = 0.0
        if image_service is not None:
            illustration_started = time.monotonic()
            html = await apply_authored_illustration(
                html, style, image_service, slide_index=index
            )
            illustration_work = time.monotonic() - illustration_started

        render_started = time.monotonic()
        render_failed = False
        try:
            png = await render_html_to_png(html)
        except Exception as exc:  # preserve the existing per-slide fallback behavior
            LOGGER.warning("Authored slide render failed at index %d: %s", index, exc)
            png = _placeholder_png()
            render_failed = True
        render_work = time.monotonic() - render_started
        return (
            index,
            html,
            png,
            render_failed,
            author_work,
            illustration_work,
            render_work,
        )

    results = list(
        await asyncio.gather(
            *(process_slide(index) for index in range(len(plan.contents)))
        )
    )
    if results and all(render_failed for _, _, _, render_failed, *_ in results):
        raise RuntimeError(
            "Every authored slide failed to render. Install Playwright's Chromium "
            "headless shell or set CHROME_PATH to a working headless browser."
        )

    htmls = [html for _, html, _, _, _, _, _ in results]
    pngs = [png for _, _, png, _, _, _, _ in results]
    return AuthoredGenerationPipelineResult(
        deck=AuthoredDeckResult(htmls=htmls, design_system=plan.design_system),
        pngs=pngs,
        elapsed=time.monotonic() - started,
        author_work=sum(result[4] for result in results),
        illustration_work=sum(result[5] for result in results),
        render_work=sum(result[6] for result in results),
    )


async def generate_authored_presentation(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    language: Optional[str],
    sql_session: AsyncSession,
) -> PresentationPathAndEditPath:
    """Author -> render -> (optional vision-QA) -> assemble image PPTX/PDF -> persist.
    Returns the path to the produced file plus the editor path."""
    started = time.monotonic()
    title = authored_title(request, outline)
    style = resolve_authored_style(request.authored_style)
    brand = apply_style_defaults(resolve_brand(request, outline, language), style)
    roles = plan_deck_roles(outline)

    # Render needs a headless Chrome. Without it every slide silently degrades to a
    # blank placeholder (resilience by design) — so warn loudly to surface deploy
    # misconfiguration instead of shipping a deck of blank slides. (No secrets logged.)
    if not find_chrome():
        LOGGER.warning(
            "[authored] No Chrome/Chromium found for rendering (set CHROME_PATH); "
            "authored slides will render as blank placeholders."
        )

    pipeline = await _author_illustrate_render_pipeline(outline, brand, style)
    authored_deck = pipeline.deck
    htmls = authored_deck.htmls
    pngs = pipeline.pngs

    fixed_count = 0
    vision_elapsed = 0.0
    if getattr(request, "vision_qa", False):
        vision_started = time.monotonic()
        try:
            contents = [s.content for s in outline.slides]
            htmls, pngs, fixed = await revise_authored_deck(
                htmls,
                pngs,
                contents,
                roles,
                brand,
                authored_deck.design_system,
                max_cycles=1,
                style=style,
            )
            fixed_count = len(fixed)
        except Exception:
            traceback.print_exc()
        finally:
            vision_elapsed = time.monotonic() - vision_started

    # Observability: one structured summary (counts/timing/model only — no secrets).
    placeholder = _placeholder_png()
    blank_renders = sum(1 for p in pngs if p == placeholder)
    from utils.llm_provider import get_llm_provider, get_model

    persist_started = time.monotonic()
    image_refs = _save_slide_pngs(presentation_id, pngs)

    presentation = PresentationModel(
        id=presentation_id,
        content=request.content,
        n_slides=len(htmls),
        language=language or brand.language,
        title=title,
        outlines=outline.model_dump(),
        layout=None,
        structure=None,
        tone=request.tone.value,
        verbosity=request.verbosity.value,
        instructions=request.instructions,
        mode=AUTHORED_TEMPLATE,
        theme={
            "mode": AUTHORED_TEMPLATE,
            "style": style.id,
            "primary": brand.primary,
            "fonts": brand.fonts,
            "language": brand.language,
        },
    )
    slides = [
        SlideModel(
            presentation=presentation_id,
            layout_group=AUTHORED_TEMPLATE,
            layout=f"{AUTHORED_TEMPLATE}:{roles[i]}",
            index=i,
            content={"__authored__": True, "image": image_refs[i], "role": roles[i]},
            html_content=htmls[i],
            properties={"image": image_refs[i]},
        )
        for i in range(len(htmls))
    ]
    sql_session.add(presentation)
    sql_session.add_all(slides)
    await sql_session.commit()
    persist_elapsed = time.monotonic() - persist_started

    export_started = time.monotonic()
    out_path = _build_authored_export(request, presentation_id, pngs, title)
    export_elapsed = time.monotonic() - export_started
    LOGGER.info(
        "[authored] done id=%s slides=%d provider=%s model=%s vision_qa=%s "
        "vqa_fixed=%d blank_renders=%d total=%.1fs author=%.1fs "
        "illustrations=%.1fs render=%.1fs pipeline=%.1fs vision=%.1fs "
        "persist=%.1fs export=%.1fs",
        presentation_id,
        len(htmls),
        get_llm_provider().value,
        get_model(),
        bool(getattr(request, "vision_qa", False)),
        fixed_count,
        blank_renders,
        time.monotonic() - started,
        pipeline.author_work,
        pipeline.illustration_work,
        pipeline.render_work,
        pipeline.elapsed,
        vision_elapsed,
        persist_elapsed,
        export_elapsed,
    )
    return PresentationPathAndEditPath(
        presentation_id=presentation_id,
        path=out_path,
        edit_path=f"/presentation?id={presentation_id}",
    )
