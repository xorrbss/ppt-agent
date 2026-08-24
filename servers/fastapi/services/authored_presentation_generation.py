from __future__ import annotations

# The publication admission path performs one local existence check.
# ruff: noqa: ASYNC240

import asyncio
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
from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from services.authored_deck_plans import resolve_deck_plans
from services.authored_header_contract_service import (
    page_number_mode_for,
    resolve_header_contract,
)
from services.authored_presentation_pipeline import _author_illustrate_render_pipeline
from services.authored_presentation_publication import publish_authored_presentation
from services.authored_semantic_candidate import compile_semantic_components
from models.authored_vnext.deck import ValidatedSlideSpecV2
from services.authored_vnext.generation.ai_text_zones import AITextZoneContext
from services.authored_vnext.generation.legacy_bridge import _generate_snapshot
from services.authored_vnext.generation.legacy_pin import generation_repository_for_owner
from services.authored_vnext.generation.pipeline import _seal_deck
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.semantic.editable_scene import build_editable_hybrid_scene
from services.image_generation_service import ImageGenerationService
from services.authored_source_images import collect_source_image_candidates
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from utils.authored_dom_quality import ensure_authored_qids
from utils.asset_directory_utils import get_images_directory
from utils.authored_styles import resolve_authored_style, validate_exact_reconstruction_style
from utils.llm_calls.author_deck import (
    plan_deck_roles,
    prepare_authored_deck,
)
from utils.llm_calls.author_slide import (
    EXACT_RECONSTRUCTION,
    STYLE_REFERENCE,
    apply_style_defaults,
    normalize_reference_mode,
    authored_semantic_dual_output_mode,
)
from utils.llm_calls.author_vision_qa import revise_authored_deck
from utils.slide_capture import _placeholder_png, ensure_browser_metrics, find_chrome


LOGGER = logging.getLogger(__name__)


async def _persist_master_pinned_editable_scene_deck(
    *,
    request: GeneratePresentationRequest,
    presentation_id: uuid.UUID,
    outline: PresentationOutlineModel,
    owner_id: uuid.UUID,
    style,
    htmls: list[str],
    pngs: list[bytes],
    semantic_components: list[dict[str, object]],
    revision: int,
):
    """Bridge normal Authored output into the vNext durable CAS deck log.

    The pinned-master planner contributes only its sealed master/composition and
    compatibility slide container. Authored HTML plus browser telemetry remains
    the sole visual/semantic authority for the canonical Scene.
    """

    resolved = request._authored_master_resolution
    if resolved is None:
        return None
    source_semantic_snapshot = request._authored_source_semantic_snapshot
    result, _master, composition = await asyncio.to_thread(
        _generate_snapshot,
        owner_id=owner_id,
        presentation_id=presentation_id,
        outline=outline,
        resolved=resolved,
        authored_style=style,
        composition_pin=request._authored_composition_pin,
        ai_text_context=AITextZoneContext(
            materials=tuple(slide.content for slide in outline.slides)
        ),
        source_semantic_snapshot=(
            source_semantic_snapshot
            if isinstance(source_semantic_snapshot, dict)
            else None
        ),
    )
    base = result.deck
    if revision != 1:
        raise ValueError("normal Authored semantic deck must start at revision 1")
    # Author/render checkpoints survive process restarts, while the DOM metrics
    # cache is intentionally process-local.  Recollect the exact instrumented
    # HTML before Scene compilation so a retry cannot publish every owner as
    # geometry-unavailable merely because its PNG outlived the worker process.
    telemetry = await asyncio.gather(
        *(ensure_browser_metrics(html) for html in htmls)
    )
    if any(metrics is None for metrics in telemetry):
        raise ValueError("editable Hybrid Scene browser telemetry is unavailable")
    deck_id = f"deck:semantic-{presentation_id.hex}"
    scene = build_editable_hybrid_scene(
        presentation_id=str(presentation_id),
        revision=revision,
        slides=None,
        slide_ids=[item.slide.slide_id for item in base.slides],
        deck_id=deck_id,
        htmls=htmls,
        pngs=pngs,
        semantic_drafts=semantic_components,
        composition_digest=composition.digest,
    )
    sealed_slides: list[ValidatedSlideSpecV2] = []
    for item, scene_slide in zip(base.slides, scene.slides, strict=True):
        slide_values = item.slide.model_dump(mode="json", by_alias=False)
        slide_values["revision"] = revision
        slide_values.pop("spec_digest", None)
        digest_payload = item.slide.model_dump(mode="json", by_alias=True)
        digest_payload["revision"] = revision
        digest_payload.pop("specDigest", None)
        slide_values["spec_digest"] = canonical_digest(digest_payload)
        sealed_slides.append(
            ValidatedSlideSpecV2(
                slide=type(item.slide).model_validate(slide_values),
                checkpoint=item.checkpoint.model_copy(
                    update={
                        "source_digest": scene.source_digest,
                        "scene_digest": scene_slide.slide_digest,
                    }
                ),
                scene=scene_slide,
            )
        )
    deck = _seal_deck(
        deck_id=deck_id,
        revision=revision,
        parent=None,
        pin=base.master,
        composition=composition,
        source_digest=scene.source_digest,
        slides=sealed_slides,
        canonical_scene=scene,
    )
    generation_repository_for_owner(owner_id).append_imported_deck(deck)
    return scene


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

    # Every deck-wide decision is taken here, before a single slide is authored,
    # because slides are authored independently and in parallel and cannot see
    # one another. The expanded outline and the plans that describe it come back
    # together so they can never drift apart.
    deck_plans = await resolve_deck_plans(request, outline, brand, style, roles)
    outline = deck_plans.outline
    roles = deck_plans.roles
    chapter_plan: AuthoredChapterPlan | None = deck_plans.chapter_plan
    illustration_plan: AuthoredIllustrationPlan | None = (
        deck_plans.illustration_plan
    )

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
        existing_projection = (
            existing.theme.get("authored_vnext")
            if isinstance(existing.theme, dict)
            else None
        )
        existing_deck_id = (
            existing_projection.get("deckId")
            if isinstance(existing_projection, dict)
            else None
        )
        return PresentationPathAndEditPath(
            presentation_id=presentation_id,
            path=existing_export,
            edit_path=(
                f"/authored-vnext/workbench?authoredDeckId={existing_deck_id}"
                if isinstance(existing_deck_id, str) and existing_deck_id
                else f"/presentation?id={presentation_id}"
            ),
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

    # Authored HTML is the sole user-facing visual generator. A selected company
    # standard remains a sealed constraint/furniture identity, but it must never
    # route new creation through the independent heuristic Scene compiler.
    header_contract: AuthoredHeaderContract | None = await resolve_header_contract(
        outline,
        brand,
        style,
        instructions=request.instructions,
        chapter_breadcrumb=chapter_plan is not None,
    )
    job_kwargs = {} if generation_job is None else {"generation_job": generation_job}
    source_images = await collect_source_image_candidates(
        request, style, presentation_id, sql_session
    )
    if source_images:
        job_kwargs["source_images"] = source_images
    pipeline = await _author_illustrate_render_pipeline(
        outline,
        brand,
        style,
        header_contract=header_contract,
        page_number_mode=page_number_mode_for(header_contract),
        chapter_plan=chapter_plan,
        illustration_plan=illustration_plan,
        instructions=request.instructions,
        **job_kwargs,
    )
    authored_deck = pipeline.deck
    # Persist stable edit targets for future local quality-review patches. Attribute
    # insertion is deterministic and has no visual effect on the already-rendered PNG.
    semantic_mode = authored_semantic_dual_output_mode()
    if semantic_mode == "declared":
        htmls = list(authored_deck.htmls)
    else:
        htmls = [ensure_authored_qids(html) for html in authored_deck.htmls]
    pngs = pipeline.pngs

    fixed_count = 0
    vision_elapsed = 0.0
    # A replay now produces authored slides, so the caller's own vision-QA choice
    # governs it like any other authored deck.
    if getattr(request, "vision_qa", False):
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
                if chapter_plan is not None:
                    # A corrected slide is authored the same way its first draft
                    # was, so it needs the same chapter prefix. Without it the
                    # one slide vision-QA touched is the one whose eyebrow
                    # disagrees with the deck.
                    vision_qa_kwargs["chapter_prefixes"] = [
                        chapter_plan.eyebrow_prefix_at(index)
                        for index in range(len(htmls))
                    ]
                if (
                    normalize_reference_mode(
                        getattr(style, "reference_mode", STYLE_REFERENCE)
                    )
                    != EXACT_RECONSTRUCTION
                ):
                    # Re-authoring a slide must replay the deck-wide decision
                    # made before its first draft.  ``None`` remains distinct
                    # from an empty plan: without a plan the legacy behavior
                    # still lets a slide request an illustration.
                    if illustration_plan is not None:
                        vision_qa_kwargs["illustration_slots"] = [
                            illustration_plan.allows(index)
                            for index in range(len(htmls))
                        ]
                    if getattr(style, "illustration_prompt", None):
                        # One service is shared by this bounded QA pass; the
                        # reviser calls it only for an allowed replacement slot.
                        vision_qa_kwargs["image_service"] = ImageGenerationService(
                            get_images_directory()
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
    semantic_components = compile_semantic_components(htmls)
    editable_scene = None
    publication_owner_id = existing.owner_id if existing is not None else None
    if publication_owner_id is not None:
        editable_scene = await _persist_master_pinned_editable_scene_deck(
            request=request,
            presentation_id=presentation_id,
            outline=outline,
            owner_id=publication_owner_id,
            style=style,
            htmls=htmls,
            pngs=pngs,
            semantic_components=semantic_components,
            revision=(existing.revision if existing is not None else 0) + 1,
        )
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
        chapter_plan=chapter_plan,
        illustration_plan=illustration_plan,
        htmls=htmls,
        semantic_components=semantic_components,
        pngs=pngs,
        out_path=out_path,
        attempt_token=attempt_token,
        master_generation=None,
        canonical_scene=editable_scene,
        native_export_evidence=native_export_evidence,
        header_contract=header_contract,
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
