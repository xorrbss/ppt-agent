from __future__ import annotations

# The publication admission path performs one local existence check.
# ruff: noqa: ASYNC240

import asyncio
import html as html_module
import logging
import os
import time
import traceback
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_and_path import PresentationPathAndEditPath
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation import (
    PRESENTATION_LIFECYCLE_PUBLISHED,
    PRESENTATION_LIFECYCLE_STAGING,
    PresentationModel,
)
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_checkpoint_service import (
    AuthoredStageError,
    fail_stage,
    is_retryable_exception,
)
from services.authored_exact_provenance import build_exact_provenance
from services.authored_exact_source_snapshot import (
    ExactSourceSnapshotError,
    materialize_exact_source_style,
)
from services.authored_presentation_artifacts import (
    _authored_export_path,
    _build_authored_export,
    _cleanup_published_checkpoints,
    _complete_stage_after_external_success,
    authored_title,
    resolve_brand,
)
from services.authored_presentation_contracts import AuthoredGenerationPipelineResult
from services.authored_presentation_pipeline import _author_illustrate_render_pipeline
from services.authored_presentation_publication import publish_authored_presentation
from services.authored_vnext.generation.legacy_bridge import (
    _checkpoint_generated_slides,
    export_legacy_master_owned_deck,
    generate_legacy_master_owned_deck,
)
from services.authored_vnext.generation.ai_text_zones import (
    AITextZoneContext,
)
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from utils.authored_dom_quality import ensure_authored_qids
from utils.authored_styles import resolve_authored_style, validate_exact_reconstruction_style
from utils.llm_calls.author_deck import (
    AuthoredDeckResult,
    plan_deck_roles,
    prepare_authored_deck,
)
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    apply_style_defaults,
    normalize_reference_mode,
)
from utils.llm_calls.author_vision_qa import revise_authored_deck
from utils.slide_capture import _placeholder_png, find_chrome, render_html_to_png


LOGGER = logging.getLogger(__name__)


def _deterministic_style_replay_html(
    content: str,
    *,
    role: str,
    index: int,
    slide_count: int,
    style,
    brand,
) -> str:
    """Render saved semantic content without authoring or deleting any text."""

    background = style.background_color or style.preview_bg or "#F8FAFC"
    accent = style.primary_color or style.preview_accent or brand.primary or "#2563EB"
    palette = tuple(style.preview_palette or ())
    text_color = palette[1] if len(palette) > 1 else "#0F172A"
    font = style.body_font or brand.fonts or "Noto Sans KR"
    heading_font = style.heading_font or font
    escaped = html_module.escape(content or "")
    escaped_role = html_module.escape(role or "CONTENT")
    font_size = 28 if len(content) <= 320 else 20 if len(content) <= 900 else 13
    return (
        '<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><style>'
        f'*{{box-sizing:border-box}}body{{margin:0;width:1280px;height:720px;overflow:hidden;'
        f'background:{background};color:{text_color};font-family:"{font}",sans-serif;'
        'padding:58px 68px 54px;display:flex;flex-direction:column;}'
        f'.accent{{width:64px;height:7px;background:{accent};margin-bottom:22px}}'
        f'.role{{font-family:"{heading_font}",sans-serif;font-size:14px;font-weight:700;'
        f'letter-spacing:.12em;color:{accent};margin-bottom:22px}}'
        f'.semantic{{white-space:pre-wrap;overflow-wrap:anywhere;font-size:{font_size}px;'
        'line-height:1.42;font-weight:500;flex:1;overflow:hidden}'
        '.page{font-size:12px;opacity:.55;text-align:right;margin-top:16px}'
        '</style></head><body><div class="accent"></div>'
        f'<div class="role">{escaped_role}</div>'
        f'<div class="semantic">{escaped}</div>'
        f'<div class="page">{index + 1} / {slide_count}</div>'
        '</body></html>'
    )


async def _deterministic_style_replay_pipeline(
    outline: PresentationOutlineModel,
    *,
    roles: list[str],
    style,
    brand,
    generation_job: PresentationGenerationJob | None,
) -> AuthoredGenerationPipelineResult:
    started = time.monotonic()
    htmls = [
        _deterministic_style_replay_html(
            slide.content,
            role=roles[index],
            index=index,
            slide_count=len(outline.slides),
            style=style,
            brand=brand,
        )
        for index, slide in enumerate(outline.slides)
    ]
    render_started = time.monotonic()
    pngs = list(await asyncio.gather(*(render_html_to_png(html) for html in htmls)))
    render_work = time.monotonic() - render_started
    await _checkpoint_generated_slides(
        generation_job=generation_job,
        layout_ids=roles,
        htmls=htmls,
        pngs=pngs,
    )
    return AuthoredGenerationPipelineResult(
        deck=AuthoredDeckResult(
            htmls=htmls,
            design_system="authored-design-replay/v1",
        ),
        pngs=pngs,
        elapsed=time.monotonic() - started,
        author_work=0.0,
        illustration_work=0.0,
        render_work=render_work,
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
    style = request._authored_style_snapshot or resolve_authored_style(
        request.authored_style
    )
    roles = plan_deck_roles(outline)
    exact_snapshot_id = request._authored_exact_snapshot_id
    if exact_snapshot_id is not None:
        if normalize_reference_mode(style.reference_mode) != EXACT_RECONSTRUCTION:
            raise AuthoredStageError(
                slide_index=0,
                stage="author",
                code="authored_exact_source_snapshot_invalid",
                retryable=False,
                message="Exact reconstruction source snapshot is invalid",
            )
        try:
            style = await materialize_exact_source_style(
                style,
                exact_snapshot_id,
                expected_slide_count=len(outline.slides),
            )
        except ExactSourceSnapshotError as error:
            raise AuthoredStageError(
                slide_index=0,
                stage="author",
                code="authored_exact_source_snapshot_invalid",
                cause=error,
                retryable=False,
                message="Exact reconstruction source snapshot is unavailable",
            ) from error
    validate_exact_reconstruction_style(
        style,
        slide_count=len(outline.slides),
        roles=roles,
    )
    exact_provenance = None
    if normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    ) == EXACT_RECONSTRUCTION:
        exact_provenance, _ = build_exact_provenance(
            style,
            source_snapshot_id=exact_snapshot_id,
        )
    brand = apply_style_defaults(resolve_brand(request, outline, language), style)

    # Render needs a headless Chrome. Without it every slide silently degrades to a
    # blank placeholder (resilience by design) ??so warn loudly to surface deploy
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

    # Chat-first admission creates a hidden shell in the same transaction as
    # the durable operation/job.  It is the only non-published row this writer
    # may reuse; unrelated staging rows remain hard conflicts.
    admitted_shell = (
        existing is not None
        and existing.lifecycle_status == PRESENTATION_LIFECYCLE_STAGING
        and generation_job is not None
        and generation_job.workspace_operation_id is not None
        and generation_job.presentation_id == presentation_id
    )
    if existing is not None and not admitted_shell:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_slide_persist_failed",
            retryable=False,
            message="A conflicting presentation record prevented final publication",
        )

    # Release the admission read transaction before any long-running external
    # work or independently durable artifact intent.  At this point the caller

    # has no authored publication mutations; the generation endpoint commits
    # its outline/status checkpoints before entering this service.  Keeping a
    # SQLite read transaction open here would otherwise block the independent
    # artifact-intent writer at finalization.
    await sql_session.commit()

    master_generation = None
    if request._authored_master_resolution is not None:
        owner_id = request._authored_master_resolution.owner_id
        source_attachments = tuple(
            snapshot.normalized_payload_json
            for snapshot in request._reference_snapshots
        )
        ai_text_context = AITextZoneContext(
            prompt=request.content,
            instructions=request.instructions or "",
            materials=tuple(
                request.slides_markdown
                or [slide.content for slide in outline.slides]
            ),
            attachments=source_attachments,
        )
        try:
            master_generation = await generate_legacy_master_owned_deck(
                owner_id=owner_id,
                presentation_id=presentation_id,
                outline=outline,
                resolved=request._authored_master_resolution,
                authored_style=style if request.authored_style is not None else None,
                composition_pin=request._authored_composition_pin,
                deterministic_replay=request._authored_design_replay,
                ai_text_context=ai_text_context,
                generation_job=generation_job,
                source_semantic_snapshot=request._authored_source_semantic_snapshot,
            )
        except Exception as error:
            if isinstance(error, AuthoredStageError):
                raise
            LOGGER.exception(
                "Master-owned authored generation failed for %s",
                presentation_id,
            )
            raise AuthoredStageError(
                slide_index=0,
                stage="author",
                code=getattr(error, "code", "authored_vnext_generation_failed"),
                retryable=is_retryable_exception(error),
                cause=error,
            ) from error
        composition_pin = request._authored_composition_pin
        deck_composition = master_generation.result.deck.composition
        if (
            composition_pin is None
            or master_generation.composition_pin != composition_pin
            or deck_composition != composition_pin
        ):
            raise AuthoredStageError(
                slide_index=0,
                stage="author",
                code="authored_composition_digest_mismatch",
                retryable=False,
                message=(
                    "생성 결과의 회사표준/저작 스타일 구성이 요청 시 고정된 구성과 "
                    "일치하지 않습니다."
                ),
            )
        composition_pin.require_digest(
            deck_composition.digest,
            consumer="generation-preview-checkpoint",
        )
        roles = [
            item.slide.layout_id for item in master_generation.result.deck.slides
        ]
        # The compiler-owned immutable deck scene is the only preview/PNG source.
        # Company furniture and authored style are already materialized in its
        # effective contract; no post-render overlay or template-only fallback is
        # permitted after this point.
        pipeline = master_generation.pipeline
    elif request._authored_design_replay:
        pipeline = await _deterministic_style_replay_pipeline(
            outline,
            roles=roles,
            style=style,
            brand=brand,
            generation_job=generation_job,
        )
    elif generation_job is None:
        pipeline = await _author_illustrate_render_pipeline(outline, brand, style)
    else:
        pipeline = await _author_illustrate_render_pipeline(
            outline, brand, style, generation_job=generation_job
        )
    authored_deck = pipeline.deck
    # Persist stable edit targets for future local quality-review patches. Attribute
    # insertion is deterministic and has no visual effect on the already-rendered PNG.
    htmls = [ensure_authored_qids(html) for html in authored_deck.htmls]
    pngs = pipeline.pngs

    fixed_count = 0
    vision_elapsed = 0.0
    if (
        getattr(request, "vision_qa", False)
        and master_generation is None
        and not request._authored_design_replay
    ):
        vision_started = time.monotonic()
        with provider_usage_scope(
            job_id=generation_job.id if generation_job is not None else None,
            operation="authored_generation",
            stage="vqa",
            attempt_number=(generation_job.attempt_number if generation_job else 0),
        ):
            try:
                contents = [s.content for s in outline.slides]
                vision_qa_kwargs = {}
                if normalize_reference_mode(
                    getattr(style, "reference_mode", STYLE_REFERENCE)
                ) == EXACT_RECONSTRUCTION:
                    exact_plan = prepare_authored_deck(outline, brand, style)
                    vision_qa_kwargs["reference_images_by_slide"] = (
                        exact_plan.reference_images
                    )
                htmls, pngs, fixed = await revise_authored_deck(
                    htmls,
                    pngs,
                    contents,
                    roles,
                    brand,
                    authored_deck.design_system,
                    max_cycles=1,
                    style=style,
                    **vision_qa_kwargs,
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
                await record_local_stage(latency_ms=vision_elapsed * 1000)

    # Observability: one structured summary (counts/timing/model only ??no secrets).
    placeholder = _placeholder_png()
    blank_renders = sum(1 for p in pngs if p == placeholder)
    from utils.llm_provider import get_llm_provider, get_model

    export_started = time.monotonic()
    attempt_token = generation_job.attempt_token if generation_job is not None else None
    native_export_evidence = None
    try:
        if generation_job is not None:
            if not attempt_token:
                raise AuthoredStageError(
                    slide_index=0,
                    stage="persist",
                    code="authored_attempt_lost",
                )
        if (
            master_generation is not None
            and request.export_as == "pptx"
        ):
            owner_id = request._authored_master_resolution.owner_id
            requested_composition = request._authored_composition_pin
            if requested_composition is None:
                raise AuthoredStageError(
                    slide_index=0,
                    stage="persist",
                    code="authored_composition_checkpoint_missing",
                    retryable=False,
                    message="내보내기에 필요한 회사표준/저작 스타일 구성 정보가 없습니다.",
                )
            requested_composition.require_digest(
                master_generation.composition_pin.digest,
                consumer="native-pptx-export",
            )
            out_path, native_export_evidence = await export_legacy_master_owned_deck(
                owner_id=owner_id,
                generation=master_generation,
                destination=_authored_export_path(
                    request, presentation_id, title
                ),
                reset_failed_job_attempts=bool(
                    generation_job is not None
                    and generation_job.resume_count > 0
                ),
                execution_scope=(
                    f"{generation_job.id}:{generation_job.attempt_number}:"
                    f"{attempt_token}"
                    if generation_job is not None and attempt_token
                    else None
                ),
                operation_fence=max(
                    1,
                    generation_job.attempt_number
                    if generation_job is not None
                    else 1,
                ),
            )
        else:
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
    published_path, persist_elapsed = await publish_authored_presentation(
        request=request,
        presentation_id=presentation_id,
        outline=outline,
        language=language,
        sql_session=sql_session,
        generation_job=generation_job,
        existing=existing,
        title=title,
        brand=brand,
        style=style,
        roles=roles,
        exact_provenance=exact_provenance,
        htmls=htmls,
        pngs=pngs,
        out_path=out_path,
        attempt_token=attempt_token,
        master_generation=master_generation,
        native_export_evidence=native_export_evidence,
    )
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
        path=published_path,
        edit_path=f"/presentation?id={presentation_id}",
    )
