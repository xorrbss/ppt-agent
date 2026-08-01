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
from pathlib import Path
import shutil
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from pathvalidate import sanitize_filename
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.async_presentation_generation_status import (
    AsyncPresentationGenerationTaskModel,
)
from models.sql.presentation import PresentationModel
from models.sql.presentation import PRESENTATION_LIFECYCLE_PUBLISHED
from models.sql.presentation_generation_job import PresentationGenerationJob
from models.sql.slide import SlideModel
from services.authored_checkpoint_service import (
    AuthoredStageError,
    atomic_write,
    begin_publish,
    cleanup_checkpoint_artifacts,
    clear_checkpoint_html,
    complete_publish,
    complete_stage,
    fail_stage,
    get_checkpoint,
    initialize_checkpoints,
    is_retryable_exception,
    read_valid_render,
    should_inject_failure,
    start_stage,
)
from services.image_generation_service import ImageGenerationService
from utils.asset_directory_utils import (
    filesystem_image_path_to_app_data_reference,
    get_exports_directory,
    get_images_directory,
)
from utils.authored_illustrations import apply_authored_illustration
from utils.authored_styles import resolve_authored_style
from utils.filename_utils import safe_export_basename
from utils.llm_calls.author_deck import (
    AuthoredDeckResult,
    author_planned_slide,
    build_image_pptx,
    plan_deck_roles,
    prepare_authored_deck,
)
from utils.llm_calls.author_slide import (
    Brand,
    apply_style_defaults,
    is_fallback_slide_html,
)
from utils.llm_calls.author_vision_qa import revise_authored_deck
from utils.outline_utils import get_presentation_title_from_presentation_outline
from utils.slide_capture import _placeholder_png, find_chrome, render_html_to_png

AUTHORED_TEMPLATE = "authored"
_DEFAULT_PRIMARY = "#2563EB"

LOGGER = logging.getLogger(__name__)
_AUTHORED_JOB_SEMAPHORE = asyncio.Semaphore(
    max(1, int(os.getenv("AUTHORED_JOB_CONCURRENCY", "1")))
)
_AUTHORED_SLIDE_WORKERS = max(
    1, int(os.getenv("AUTHORED_SLIDE_PIPELINE_CONCURRENCY", "8"))
)


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
    """Persist slide PNGs and return references relative to /app_data/images."""
    rel_dir = os.path.join("authored", str(presentation_id))
    abs_dir = os.path.join(get_images_directory(), rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    refs: List[str] = []
    for i, png in enumerate(pngs):
        fname = f"slide_{i}.png"
        absolute_path = os.path.join(abs_dir, fname)
        atomic_write(Path(absolute_path), png)
        refs.append(filesystem_image_path_to_app_data_reference(absolute_path))
    return refs


def _remove_partial_slide_pngs(presentation_id: uuid.UUID) -> None:
    """Remove an unpublished image directory after a pre-commit write failure."""
    images_root = Path(get_images_directory()).resolve()
    authored_root = (images_root / "authored").resolve()
    target = (authored_root / str(presentation_id)).resolve()
    if target.parent != authored_root:
        raise ValueError("invalid authored slide image cleanup target")
    if target.is_dir():
        shutil.rmtree(target)


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
    destination = _authored_export_path(request, presentation_id, title)
    exports = os.path.dirname(destination)
    stem, extension = os.path.splitext(os.path.basename(destination))
    os.makedirs(exports, exist_ok=True)
    if extension == ".pdf":
        from PIL import Image

        temporary = os.path.join(exports, f".{stem}.{uuid.uuid4().hex}.pdf")
        try:
            _build_image_pdf(pngs, temporary)
            with Image.open(temporary) as exported_pdf:
                if getattr(exported_pdf, "n_frames", 1) != len(pngs):
                    raise RuntimeError("authored PDF slide count validation failed")
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination
    temporary = os.path.join(exports, f".{stem}.{uuid.uuid4().hex}.pptx")
    try:
        build_image_pptx(pngs, temporary)
        from pptx import Presentation as PptxPresentation

        if len(PptxPresentation(temporary).slides) != len(pngs):
            raise RuntimeError("authored PPTX slide count validation failed")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def _authored_export_path(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    title: str,
) -> str:
    base = safe_export_basename(
        sanitize_filename((title or str(presentation_id)).strip() or str(presentation_id))
    )
    stem = f"{base}-{presentation_id.hex[:8]}"
    extension = "pdf" if request.export_as == "pdf" else "pptx"
    return os.path.join(get_exports_directory(), f"{stem}.{extension}")


async def _cleanup_published_checkpoints(job_id: uuid.UUID) -> None:
    """Best-effort post-publish cleanup: render artifacts and stored slide HTML.

    Failures never unwind an already-published deck; leftovers remain for the
    TTL sweeper (files) or job CASCADE delete (rows)."""
    try:
        cleanup_checkpoint_artifacts(job_id)
    except OSError:
        LOGGER.warning(
            "Unable to clean authored checkpoint artifacts for job %s",
            job_id,
            exc_info=True,
        )
    try:
        await clear_checkpoint_html(job_id)
    except SQLAlchemyError:
        LOGGER.warning(
            "Unable to clear authored checkpoint HTML for job %s",
            job_id,
            exc_info=True,
        )


async def _complete_stage_after_external_success(**kwargs):
    """Durably record a returned provider result before propagating cancellation.

    Shielding alone is insufficient because the worker would finish while the DB
    commit kept running in the background. Waiting for the shielded task here
    lets the pipeline join all workers before its attempt token can be revoked.
    """

    checkpoint_task = asyncio.create_task(complete_stage(**kwargs))
    try:
        return await asyncio.shield(checkpoint_task)
    except asyncio.CancelledError:
        await checkpoint_task
        raise


async def _author_illustrate_render_pipeline(
    outline: PresentationOutlineModel,
    brand: Brand,
    style,
    *,
    generation_job: PresentationGenerationJob | None = None,
) -> AuthoredGenerationPipelineResult:
    """Pipeline slides through durable, fenced checkpoints with bounded workers."""
    started = time.monotonic()
    plan = prepare_authored_deck(outline, brand, style)
    roles = plan_deck_roles(outline)
    image_service = (
        ImageGenerationService(get_images_directory())
        if getattr(style, "illustration_prompt", None)
        else None
    )
    attempt_token = generation_job.attempt_token if generation_job else None
    if generation_job is not None:
        if not attempt_token:
            raise AuthoredStageError(
                slide_index=0,
                stage="persist",
                code="authored_attempt_lost",
            )
        await initialize_checkpoints(
            job_id=generation_job.id,
            attempt_token=attempt_token,
            roles=roles,
        )

    async def process_slide(
        index: int,
    ) -> tuple[int, str, bytes, float, float, float]:
        author_work = 0.0
        illustration_work = 0.0
        render_work = 0.0
        checkpoint = (
            await get_checkpoint(generation_job.id, index)
            if generation_job is not None
            else None
        )

        try:
            html = checkpoint.author_html if checkpoint else None
            if not html or checkpoint.author_status != "completed":
                if checkpoint and should_inject_failure(
                    checkpoint, slide_index=index, stage="author"
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_injected_failure",
                    )
                if generation_job is not None:
                    await start_stage(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="author",
                        attempt_token=attempt_token,
                    )
                author_started = time.monotonic()
                try:
                    if generation_job is None:
                        html = await author_planned_slide(plan, index)
                    else:
                        html = await author_planned_slide(
                            plan, index, raise_on_failure=True
                        )
                except Exception as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        cause=exc,
                        retryable=is_retryable_exception(exc),
                    ) from exc
                author_work = time.monotonic() - author_started
                if is_fallback_slide_html(html):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="author",
                        code="authored_slide_author_failed",
                        message=(
                            "Authored slide generation failed after retries for "
                            f"slide(s): {index + 1}. The incomplete fallback deck "
                            "was not published."
                        ),
                    )
                if generation_job is not None:
                    checkpoint = await _complete_stage_after_external_success(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="author",
                        attempt_token=attempt_token,
                        html=html,
                    )

            illustrated_html = (
                checkpoint.illustrated_html
                if checkpoint
                and checkpoint.illustrate_status in ("completed", "skipped")
                and checkpoint.illustrated_html
                else None
            )
            if illustrated_html is None:
                if checkpoint and should_inject_failure(
                    checkpoint, slide_index=index, stage="illustrate"
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="illustrate",
                        code="authored_injected_failure",
                    )
                if generation_job is not None:
                    await start_stage(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="illustrate",
                        attempt_token=attempt_token,
                    )
                illustrated_html = html
                skipped = image_service is None
                if image_service is not None:
                    illustration_started = time.monotonic()
                    try:
                        if generation_job is None:
                            illustrated_html = await apply_authored_illustration(
                                html, style, image_service, slide_index=index
                            )
                        else:
                            illustrated_html = await apply_authored_illustration(
                                html,
                                style,
                                image_service,
                                slide_index=index,
                                raise_on_failure=True,
                            )
                    except Exception as exc:
                        raise AuthoredStageError(
                            slide_index=index,
                            stage="illustrate",
                            code="authored_slide_illustrate_failed",
                            cause=exc,
                            retryable=is_retryable_exception(exc),
                        ) from exc
                    illustration_work = time.monotonic() - illustration_started
                if generation_job is not None:
                    checkpoint = await _complete_stage_after_external_success(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="illustrate",
                        attempt_token=attempt_token,
                        html=illustrated_html,
                        skipped=skipped,
                    )

            png = read_valid_render(checkpoint) if checkpoint else None
            if png is None or checkpoint.render_status != "completed":
                if checkpoint and should_inject_failure(
                    checkpoint, slide_index=index, stage="render"
                ):
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="render",
                        code="authored_injected_failure",
                    )
                if generation_job is not None:
                    await start_stage(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="render",
                        attempt_token=attempt_token,
                    )
                render_started = time.monotonic()
                try:
                    png = await render_html_to_png(illustrated_html)
                    if png == _placeholder_png():
                        raise RuntimeError("renderer returned a placeholder")
                except Exception as exc:
                    raise AuthoredStageError(
                        slide_index=index,
                        stage="render",
                        code="authored_slide_render_failed",
                        cause=exc,
                        retryable=is_retryable_exception(exc),
                    ) from exc
                render_work = time.monotonic() - render_started
                if generation_job is not None:
                    checkpoint = await _complete_stage_after_external_success(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="render",
                        attempt_token=attempt_token,
                        png=png,
                    )

            if generation_job is not None and checkpoint.persist_status != "completed":
                await start_stage(
                    job_id=generation_job.id,
                    slide_index=index,
                    stage="persist",
                    attempt_token=attempt_token,
                )
                await _complete_stage_after_external_success(
                    job_id=generation_job.id,
                    slide_index=index,
                    stage="persist",
                    attempt_token=attempt_token,
                )
            return (
                index,
                illustrated_html,
                png,
                author_work,
                illustration_work,
                render_work,
            )
        except AuthoredStageError as exc:
            if generation_job is not None:
                await fail_stage(
                    job_id=generation_job.id,
                    slide_index=index,
                    stage=exc.stage,
                    attempt_token=attempt_token,
                    error=exc,
                    request_id=generation_job.request_id,
                )
            raise

    results: list[tuple[int, str, bytes, float, float, float] | None] = [
        None
    ] * len(plan.contents)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(len(plan.contents)):
        queue.put_nowait(index)

    stop_workers = asyncio.Event()

    async def worker() -> None:
        while not stop_workers.is_set():
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                results[index] = await process_slide(index)
            except BaseException:
                # Do not cancel sibling provider calls that are already in
                # flight: they may already be billable. Let each active slide
                # finish its cancellation-safe checkpoint, but prevent workers
                # from claiming more queued slides.
                stop_workers.set()
                raise
            finally:
                queue.task_done()

    worker_tasks = [
        asyncio.create_task(worker())
        for _ in range(min(_AUTHORED_SLIDE_WORKERS, len(plan.contents)))
    ]
    try:
        worker_outcomes = await asyncio.gather(
            *worker_tasks, return_exceptions=True
        )
    except asyncio.CancelledError:
        # User/server cancellation still interrupts the deck attempt. Any
        # provider result that already returned is checkpointed by the helper
        # above before the worker can finish cancellation.
        for task in worker_tasks:
            task.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        raise
    failures = [
        outcome
        for outcome in worker_outcomes
        if isinstance(outcome, BaseException)
    ]
    if failures:
        raise failures[0]
    completed_results = [result for result in results if result is not None]
    if len(completed_results) != len(plan.contents):
        raise RuntimeError("authored slide worker exited before completion")
    htmls = [html for _, html, _, _, _, _ in completed_results]
    pngs = [png for _, _, png, _, _, _ in completed_results]
    return AuthoredGenerationPipelineResult(
        deck=AuthoredDeckResult(htmls=htmls, design_system=plan.design_system),
        pngs=pngs,
        elapsed=time.monotonic() - started,
        author_work=sum(result[3] for result in completed_results),
        illustration_work=sum(result[4] for result in completed_results),
        render_work=sum(result[5] for result in completed_results),
    )


async def generate_authored_presentation(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    language: Optional[str],
    sql_session: AsyncSession,
    generation_job: PresentationGenerationJob | None = None,
) -> PresentationPathAndEditPath:
    async with _AUTHORED_JOB_SEMAPHORE:
        return await _generate_authored_presentation(
            request,
            presentation_id,
            outline,
            language,
            sql_session,
            generation_job,
        )


async def _generate_authored_presentation(
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    language: Optional[str],
    sql_session: AsyncSession,
    generation_job: PresentationGenerationJob | None,
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
            "%s",
            (
                "durable authored generation will fail before publication."
                if generation_job is not None
                else "non-durable authored slides may render as blank placeholders."
            ),
        )

    existing = await sql_session.get(PresentationModel, presentation_id)
    if (
        existing is not None
        and existing.lifecycle_status == PRESENTATION_LIFECYCLE_PUBLISHED
        and os.path.isfile(
            existing_export := _authored_export_path(request, presentation_id, title)
        )
    ):
        if generation_job is not None:
            await _cleanup_published_checkpoints(generation_job.id)
        return PresentationPathAndEditPath(
            presentation_id=presentation_id,
            path=existing_export,
            edit_path=f"/presentation?id={presentation_id}",
        )

    # A row with this deterministic id but without a completed export is not a
    # valid recovery point. Continuing would skip the insert below and could
    # falsely report success while leaving an unpublished/conflicting row.
    if existing is not None:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_slide_persist_failed",
            retryable=False,
            message="A conflicting presentation record prevented final publication",
        )

    if generation_job is None:
        pipeline = await _author_illustrate_render_pipeline(outline, brand, style)
    else:
        pipeline = await _author_illustrate_render_pipeline(
            outline, brand, style, generation_job=generation_job
        )
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
            if generation_job is not None and generation_job.attempt_token:
                for index in fixed:
                    await _complete_stage_after_external_success(
                        job_id=generation_job.id,
                        slide_index=index,
                        stage="render",
                        attempt_token=generation_job.attempt_token,
                        png=pngs[index],
                    )
        except Exception:
            traceback.print_exc()
        finally:
            vision_elapsed = time.monotonic() - vision_started

    # Observability: one structured summary (counts/timing/model only — no secrets).
    placeholder = _placeholder_png()
    blank_renders = sum(1 for p in pngs if p == placeholder)
    from utils.llm_provider import get_llm_provider, get_model

    export_started = time.monotonic()
    attempt_token = generation_job.attempt_token if generation_job is not None else None
    try:
        if generation_job is not None:
            if not attempt_token:
                raise AuthoredStageError(
                    slide_index=0,
                    stage="persist",
                    code="authored_attempt_lost",
                )
            await begin_publish(
                sql_session,
                job_id=generation_job.id,
                attempt_token=attempt_token,
            )
        out_path = _build_authored_export(request, presentation_id, pngs, title)
    except Exception as exc:
        await sql_session.rollback()
        if isinstance(exc, AuthoredStageError):
            raise
        error = AuthoredStageError(
            slide_index=max(0, len(pngs) - 1),
            stage="persist",
            code="authored_slide_persist_failed",
            cause=exc,
        )
        if generation_job is not None and attempt_token:
            await fail_stage(
                job_id=generation_job.id,
                slide_index=error.slide_index,
                stage="persist",
                attempt_token=attempt_token,
                error=error,
                request_id=generation_job.request_id,
            )
        raise error from exc
    export_elapsed = time.monotonic() - export_started

    persist_started = time.monotonic()
    images_ready = False
    try:
        image_refs = _save_slide_pngs(presentation_id, pngs)
        images_ready = True

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
            lifecycle_status=PRESENTATION_LIFECYCLE_PUBLISHED,
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
                content={
                    "__authored__": True,
                    "image": image_refs[i],
                    "role": roles[i],
                },
                html_content=htmls[i],
                properties={"image": image_refs[i]},
            )
            for i in range(len(htmls))
        ]
        sql_session.add(presentation)
        sql_session.add_all(slides)
        if generation_job is not None and attempt_token:
            response = PresentationPathAndEditPath(
                presentation_id=presentation_id,
                path=out_path,
                edit_path=f"/presentation?id={presentation_id}",
            )
            async_status = await sql_session.get(
                AsyncPresentationGenerationTaskModel,
                generation_job.task_id,
            )
            if async_status is None:
                raise RuntimeError("presentation_generation_task_missing")
            async_status.status = "completed"
            async_status.message = "Presentation generation completed"
            async_status.data = response.model_dump(mode="json")
            async_status.error = None
            async_status.updated_at = datetime.now(timezone.utc)
            sql_session.add(async_status)
            await complete_publish(
                sql_session,
                job_id=generation_job.id,
                attempt_token=attempt_token,
            )
        await sql_session.commit()
    except Exception as exc:
        await sql_session.rollback()
        if isinstance(exc, AuthoredStageError):
            # In particular, preserve authored_attempt_lost so a stale worker
            # cannot relabel lease loss as a retryable slide failure or mutate
            # the newer owner's durable status in the outer handler.
            raise
        # A failed image copy can leave only a prefix of the deck in the public
        # image tree.  It is not referenced by the DB yet, so removing this exact
        # presentation directory is safe.  Once all images are ready, retain them
        # across an ambiguous DB commit outcome for reconciliation/retry.
        if not images_ready:
            try:
                _remove_partial_slide_pngs(presentation_id)
            except OSError:
                LOGGER.warning(
                    "Unable to remove partial authored slide images for %s",
                    presentation_id,
                    exc_info=True,
                )
        error = AuthoredStageError(
            slide_index=max(0, len(pngs) - 1),
            stage="persist",
            code="authored_slide_persist_failed",
            cause=exc,
        )
        if generation_job is not None and attempt_token:
            await fail_stage(
                job_id=generation_job.id,
                slide_index=error.slide_index,
                stage="persist",
                attempt_token=attempt_token,
                error=error,
                request_id=generation_job.request_id,
            )
        raise error from exc
    persist_elapsed = time.monotonic() - persist_started
    if generation_job is not None:
        await _cleanup_published_checkpoints(generation_job.id)
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
