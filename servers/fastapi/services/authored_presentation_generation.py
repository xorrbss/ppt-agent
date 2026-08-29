from __future__ import annotations

# The publication admission path performs one local existence check.
# ruff: noqa: ASYNC240

import asyncio
import inspect
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
    mark_checkpoint_artifacts_sealed,
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
from services.presentation_export_attempt_service import sha256_canonical_json
from services.authored_vnext.semantic.editable_scene import build_editable_hybrid_scene
from services.image_generation_service import ImageGenerationService
from services.authored_source_images import (
    collect_source_image_candidates,
    plan_slide_source_images,
)
from services.authored_composition_policy import (
    blocking_safety_issues,
    build_authored_capacity_plan,
    capacity_violations,
    ensure_authored_semantic_roles,
    repair_authored_layout_alignment,
    resolve_composition_profile,
)
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from utils.authored_illustrations import validate_visible_source_images
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
from utils.llm_calls.author_vision_qa import (
    AuthoredVqaResidualDomIssuesError,
    revise_authored_deck,
)
from utils.slide_capture import (
    _placeholder_png,
    ensure_browser_metrics,
    find_chrome,
    get_cached_browser_metrics,
    render_html_to_png_with_metrics,
)
from services.authored_evidence_ledger import digest_html, digest_png
from services.authored_quality_receipt_v2 import (
    create_quality_receipt_v2,
    evaluate_capacity_browser_metrics,
    quality_receipt_manifest_path,
)
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    verify_browser_source_readback,
)
from services.source_content.readback import (
    SourceContentReadbackError,
    verify_source_verbatim_readback,
)


LOGGER = logging.getLogger(__name__)


async def _apply_bounded_layout_alignment_repairs(
    *,
    htmls: list[str],
    pngs: list[bytes],
    roles: list[str],
    capacity_plan,
    profile_id: str,
    metrics_override: dict[int, object],
) -> list[int]:
    """Apply one content-preserving alignment normalization per affected slide."""

    if profile_id != "ordinary":
        return []
    repaired_indices: list[int] = []
    for index, (html, role) in enumerate(zip(htmls, roles, strict=True)):
        repaired = repair_authored_layout_alignment(html, role=role)
        if repaired == html:
            continue
        canonical_html = ensure_authored_semantic_roles(ensure_authored_qids(repaired))
        capture = await render_html_to_png_with_metrics(canonical_html)
        if capture.png == _placeholder_png() or capture.browser_metrics is None:
            raise RuntimeError("bounded layout alignment browser evidence unavailable")
        violations = capacity_violations(
            canonical_html, capture.browser_metrics, capacity_plan.at(index)
        )
        if violations:
            raise RuntimeError(
                "bounded layout alignment repair failed capacity validation: "
                + ",".join(violations)
            )
        htmls[index], pngs[index] = canonical_html, capture.png
        metrics_override[index] = capture.browser_metrics
        repaired_indices.append(index)
    return repaired_indices


def build_authored_quality_gate_receipt(
    *,
    htmls: list[str],
    profile_id: str,
    gate_failures: list[dict[str, object]] | tuple[dict[str, object], ...] = (),
    repaired_slide_indices: list[int] | tuple[int, ...] = (),
) -> dict[str, object]:
    """Build immutable publication evidence for the final authored HTML.

    Gate evidence and residual issue state intentionally live in separate fields:
    ``status`` describes the gate result, while ``residualIssues`` records what
    remained after the bounded repair attempt.  The HTML digest binds the receipt
    to the exact bytes handed to publication.
    """
    residual = [dict(item) for item in gate_failures if isinstance(item, dict)]
    return {
        "schemaVersion": "authored-quality-gate-receipt/v1",
        "profileId": profile_id,
        "slideCount": len(htmls),
        "htmlDigest": "sha256:" + sha256_canonical_json(htmls),
        "status": "residual_issues" if residual else "passed",
        "repairedSlideIndices": sorted({int(index) for index in repaired_slide_indices}),
        "residualIssueCount": len(residual),
        "residualIssues": residual,
    }


def _finalize_attempt_evidence(
    pipeline,
    htmls: list[str],
    pngs: list[bytes],
    metrics_override: dict[int, object] | None = None,
):
    """Bind final slide bytes to the attempt ledger without browser replay."""

    ledger = getattr(pipeline, "evidence_ledger", None)
    attempt_id = getattr(pipeline, "evidence_attempt_id", None)
    if ledger is None or not attempt_id:
        return None, None, ()
    metrics: list[object] = []
    for index, (html, png) in enumerate(zip(htmls, pngs, strict=True)):
        current = ledger.latest(attempt_id, index)
        if (
            current is not None
            and current.html_digest == digest_html(html)
            and current.png_digest == digest_png(png)
        ):
            evidence = ledger.reuse_unchanged_slide(
                attempt_id=attempt_id,
                slide_index=index,
                html_digest=current.html_digest,
            )
        else:
            # VQA/repair rendering populates this cache in the same capture.
            # Do not recollect here: missing telemetry is retained and blocks
            # the unified gate instead of rendering an unchanged slide again.
            browser_metrics = (
                metrics_override.get(index)
                if metrics_override is not None and index in metrics_override
                else get_cached_browser_metrics(html)
            )
            evidence = ledger.record_slide(
                attempt_id=attempt_id,
                slide_index=index,
                html=html,
                png=png,
                browser_metrics=browser_metrics,
                checkpoint_revision=(
                    current.checkpoint_revision + 1 if current is not None else 0
                ),
            )
        metrics.append(evidence.browser_metrics)
    ledger.seal_attempt(attempt_id)
    return ledger, attempt_id, tuple(metrics)


def _source_block_bindings(request) -> tuple[tuple[tuple[str, str], ...], ...] | None:
    """Project immutable source text into deterministic HTML binding pairs."""

    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None:
        return None
    dump = getattr(manifest, "model_dump", None)
    value = dump(mode="json", by_alias=True) if callable(dump) else manifest
    odump = getattr(outline, "model_dump", None)
    outline_value = odump(mode="json", by_alias=True) if callable(odump) else outline
    units = value.get("units", ()) if isinstance(value, dict) else ()
    slides = outline_value.get("slides", ()) if isinstance(outline_value, dict) else ()
    blocks_by_id: dict[str, dict] = {}
    for unit in units:
        for block in unit.get("blocks", ()) if isinstance(unit, dict) else ():
            if isinstance(block, dict):
                blocks_by_id[str(block.get("sourceBlockId", ""))] = block
    result: list[tuple[tuple[str, str], ...]] = []
    for slide in slides:
        pairs: list[tuple[str, str]] = []
        for block_id in slide.get("readingOrder", ()) if isinstance(slide, dict) else ():
            block = blocks_by_id.get(str(block_id))
            if not block or block.get("kind") != "text":
                continue
            payload = block.get("payload") or {}
            text = payload.get("text") if isinstance(payload, dict) else None
            if isinstance(text, str) and text.strip():
                pairs.append((str(block_id), text))
        result.append(tuple(pairs))
    return tuple(result)


def _source_image_block_bindings(request) -> tuple[tuple[str, ...], ...] | None:
    """Project source image *occurrences* into deterministic DOM bindings."""

    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None:
        return None
    dump = getattr(manifest, "model_dump", None)
    value = dump(mode="json", by_alias=True) if callable(dump) else manifest
    odump = getattr(outline, "model_dump", None)
    outline_value = odump(mode="json", by_alias=True) if callable(odump) else outline
    units = value.get("units", ()) if isinstance(value, dict) else ()
    slides = outline_value.get("slides", ()) if isinstance(outline_value, dict) else ()
    blocks_by_id: dict[str, dict] = {}
    for unit in units:
        for block in unit.get("blocks", ()) if isinstance(unit, dict) else ():
            if isinstance(block, dict):
                blocks_by_id[str(block.get("sourceBlockId", ""))] = block
    result: list[tuple[str, ...]] = []
    for slide in slides:
        ids: list[str] = []
        for block_id in slide.get("readingOrder", ()) if isinstance(slide, dict) else ():
            block = blocks_by_id.get(str(block_id))
            if block and block.get("kind") == "image":
                ids.append(str(block_id))
        result.append(tuple(ids))
    return tuple(result)


async def _final_authored_quality_gate(
    *,
    htmls: list[str],
    capacity_plan,
    profile,
    reference_mode: str,
    browser_metrics_by_slide=None,
) -> list[dict[str, object]]:
    """Re-check the exact final HTML before export/publication.

    The first pipeline pass can be followed by VQA or a bounded slide repair.
    This final readback keeps the receipt bound to the bytes that are actually
    exported and prevents a post-gate edit from reintroducing a safety defect.
    Browser metrics are cache hits for unchanged HTML, so shadow semantic mode
    does not launch a second render merely to collect the gate evidence.
    """
    metrics = (
        browser_metrics_by_slide
        if browser_metrics_by_slide is not None
        else await asyncio.gather(*(ensure_browser_metrics(html) for html in htmls))
    )
    residual: list[dict[str, object]] = []
    for index, (html, browser_metrics) in enumerate(zip(htmls, metrics, strict=True)):
        if capacity_plan is not None:
            violations = capacity_violations(
                html, browser_metrics, capacity_plan.at(index)
            )
            if violations:
                raise AuthoredStageError(
                    slide_index=index,
                    stage="render",
                    code="authored_slide_capacity_exceeded",
                    # The first-pass gate already sent this slide through the
                    # one bounded repair cycle. Replaying the same completed
                    # checkpoint cannot change a residual deterministic limit.
                    retryable=False,
                    message=(
                        f"Slide {index + 1} exceeded its frozen capacity envelope: "
                        + ", ".join(violations)
                    ),
                )
        report = inspect_authored_dom(
            html,
            browser_metrics,
            reference_mode=reference_mode,
            strict_structural_off_canvas=(profile.profile_id != "ordinary"),
        )
        if report.telemetry_status != "available":
            raise AuthoredStageError(
                slide_index=index,
                stage="render",
                code="authored_composition_safety_gate_failed",
                retryable=True,
                message=f"Slide {index + 1} failed the composition safety gate (telemetry:{report.telemetry_status})",
            )
        defects = blocking_safety_issues(report)
        if defects:
            residual.append(
                {
                    "slide_index": index,
                    "issues": [issue.to_dict() for issue in defects],
                }
            )
    return residual


async def _checkpoint_source_reflow_vqa_repairs(
    generation_job: PresentationGenerationJob | None,
    error: AuthoredVqaResidualDomIssuesError,
) -> None:
    """Persist successful local VQA patches before source-reflow fails closed."""

    if generation_job is None or not generation_job.attempt_token:
        return
    for index in error.fixed:
        await _complete_stage_after_external_success(
            job_id=generation_job.id,
            slide_index=index,
            stage="illustrate",
            attempt_token=generation_job.attempt_token,
            html=error.htmls[index],
        )
        await _complete_stage_after_external_success(
            job_id=generation_job.id,
            slide_index=index,
            stage="render",
            attempt_token=generation_job.attempt_token,
            html=error.htmls[index],
            png=error.pngs[index],
            # An unavailable probe is still persisted as an explicit empty
            # evidence object; the unified gate will block publication while
            # restart recovery keeps the repaired HTML/PNG pair.
            browser_metrics=get_cached_browser_metrics(error.htmls[index]) or {},
        )


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
    browser_metrics_by_slide=None,
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
    telemetry = (
        tuple(browser_metrics_by_slide)
        if browser_metrics_by_slide is not None
        else await asyncio.gather(*(ensure_browser_metrics(html) for html in htmls))
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
    if request._source_content_authored_reflow:
        job_kwargs.update(
            source_images_locked_to_source_slide=True,
            require_all_source_images=True,
            disable_generated_illustrations=True,
        )
    composition_profile = request._authored_composition_profile
    if composition_profile is None:
        composition_profile = resolve_composition_profile(request)
        request._authored_composition_profile = composition_profile
    source_reflow = bool(request._source_content_authored_reflow)
    pipeline_kwargs = {
        "header_contract": header_contract,
        "page_number_mode": page_number_mode_for(header_contract),
        "chapter_plan": chapter_plan,
        "illustration_plan": illustration_plan,
        "instructions": request.instructions,
        **job_kwargs,
    }
    # Keep narrow legacy/test doubles source-compatible while the product
    # pipeline consumes the frozen composition profile.
    if "composition_profile" in inspect.signature(
        _author_illustrate_render_pipeline
    ).parameters:
        pipeline_kwargs["composition_profile"] = composition_profile
    if (
        source_reflow
        and composition_profile.text_mode == "verbatim"
        and "source_block_bindings"
        in inspect.signature(_author_illustrate_render_pipeline).parameters
    ):
        pipeline_kwargs["source_block_bindings"] = _source_block_bindings(request)
    if (
        source_reflow
        and "source_image_block_bindings"
        in inspect.signature(_author_illustrate_render_pipeline).parameters
    ):
        pipeline_kwargs["source_image_block_bindings"] = _source_image_block_bindings(request)
    pipeline = await _author_illustrate_render_pipeline(
        outline,
        brand,
        style,
        **pipeline_kwargs,
    )
    authored_deck = pipeline.deck
    # Persist stable edit targets for future local quality-review patches. Attribute
    # insertion is deterministic and has no visual effect on the already-rendered PNG.
    semantic_mode = authored_semantic_dual_output_mode()
    reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )
    if reference_mode != EXACT_RECONSTRUCTION:
        htmls = [
            ensure_authored_semantic_roles(ensure_authored_qids(html))
            for html in authored_deck.htmls
        ]
    elif semantic_mode == "declared":
        htmls = list(authored_deck.htmls)
    else:
        htmls = [ensure_authored_qids(html) for html in authored_deck.htmls]
    pngs = pipeline.pngs

    fixed_count = 0
    vision_elapsed = 0.0
    revised_metrics_by_slide: dict[int, object] = {}
    contents = [s.content for s in outline.slides]
    source_reflow = bool(request._source_content_authored_reflow)
    capacity_plan = build_authored_capacity_plan(contents, roles, composition_profile)
    offered_for_review = plan_slide_source_images(
        contents,
        source_images,
        source_slide_locked=source_reflow,
    )
    capacity_briefs = [
        capacity_plan.at(index)
        .with_source_images(
            len(offered_for_review[index]) if offered_for_review is not None else 0
        )
        .prompt_brief(composition_profile)
        for index in range(len(contents))
    ]
    # A strict pipeline may return slide-scoped gate failures instead of
    # aborting the whole deck. Repair only those slides, once, and preserve the
    # original absolute indices for numbering/chapter prompts. Older pipeline
    # result doubles do not expose failures, so this hook is intentionally a
    # no-op for them.
    gate_failures_raw = getattr(pipeline, "quality_gate_failures", ())
    gate_failures = [
        dict(item)
        for item in gate_failures_raw
        if isinstance(item, dict) and isinstance(item.get("slide_index"), int)
    ]
    repaired_gate_indices: list[int] = []
    if gate_failures:
        flagged = sorted({int(item["slide_index"]) for item in gate_failures})
        repair_kwargs: dict[str, object] = {
            "slide_indices": flagged,
            "total_slides": len(htmls),
            "max_cycles": 1,
            "style": style,
            "return_details": True,
            "local_patch_only": source_reflow,
            "fail_on_residual_dom_issues": True,
            "capacity_briefs": [capacity_briefs[index] for index in flagged],
        }
        if chapter_plan is not None:
            repair_kwargs["chapter_prefixes"] = [
                chapter_plan.eyebrow_prefix_at(index) for index in flagged
            ]
        if illustration_plan is not None:
            repair_kwargs["illustration_slots"] = [
                illustration_plan.allows(index) for index in flagged
            ]
        try:
            repaired = await revise_authored_deck(
                [htmls[index] for index in flagged],
                [pngs[index] for index in flagged],
                [contents[index] for index in flagged],
                [roles[index] for index in flagged],
                brand,
                authored_deck.design_system,
                **repair_kwargs,
            )
            revised_htmls, revised_pngs, fixed = repaired[:3]
            for local_index, absolute_index in enumerate(flagged):
                htmls[absolute_index] = revised_htmls[local_index]
                pngs[absolute_index] = revised_pngs[local_index]
                revised_metrics_by_slide[absolute_index] = get_cached_browser_metrics(
                    revised_htmls[local_index]
                )
            repaired_gate_indices = [flagged[index] for index in fixed]
        except Exception as error:
            LOGGER.warning(
                "[authored] bounded quality-gate repair failed slides=%s error=%s",
                flagged,
                type(error).__name__,
            )
            # A failed or inconclusive bounded repair must not publish the
            # unsafe first-pass deck. The retry boundary can restart the whole
            # operation, while successful repairs remain slide-scoped above.
            raise AuthoredStageError(
                slide_index=flagged[0],
                stage="render",
                code="authored_composition_safety_gate_failed",
                cause=error,
                retryable=True,
                message="제한된 슬라이드 품질 게이트 복구 후에도 안전성 검증을 완료하지 못했습니다.",
            ) from error
    quality_gate_receipt = None
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
                if source_reflow:
                    vision_qa_kwargs["fail_on_residual_dom_issues"] = True
                revise_kwargs: dict[str, object] = {
                    "max_cycles": composition_profile.max_repair_cycles,
                    "style": style,
                    "return_details": source_reflow,
                    "local_patch_only": source_reflow,
                    "capacity_briefs": capacity_briefs,
                    **vision_qa_kwargs,
                }
                accepted_revise_kwargs = inspect.signature(
                    revise_authored_deck
                ).parameters
                revise_kwargs = {
                    key: value
                    for key, value in revise_kwargs.items()
                    if key in accepted_revise_kwargs
                }
                revised = await revise_authored_deck(
                    htmls,
                    pngs,
                    contents,
                    roles,
                    brand,
                    authored_deck.design_system,
                    **revise_kwargs,
                )
                htmls, pngs, fixed = revised[:3]
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
            except Exception as error:
                traceback.print_exc()
                if bool(request._source_content_authored_reflow):
                    if isinstance(error, AuthoredVqaResidualDomIssuesError):
                        await _checkpoint_source_reflow_vqa_repairs(
                            generation_job,
                            error,
                        )
                    raise AuthoredStageError(
                        slide_index=0,
                        stage="render",
                        code="authored_vqa_residual_dom_issues",
                        cause=error,
                        retryable=True,
                    ) from error
            finally:
                vision_elapsed = time.monotonic() - vision_started
                await record_local_stage(latency_ms=vision_elapsed * 1000)

    aligned_indices = await _apply_bounded_layout_alignment_repairs(
        htmls=htmls,
        pngs=pngs,
        roles=roles,
        capacity_plan=capacity_plan,
        profile_id=composition_profile.profile_id,
        metrics_override=revised_metrics_by_slide,
    )
    if aligned_indices:
        repaired_gate_indices = sorted({*repaired_gate_indices, *aligned_indices})
        LOGGER.info(
            "[authored] bounded layout alignment repair slides=%s",
            ",".join(str(index + 1) for index in aligned_indices),
        )

    evidence_ledger, evidence_attempt_id, retained_final_metrics = (
        _finalize_attempt_evidence(
            pipeline, htmls, pngs, metrics_override=revised_metrics_by_slide
        )
    )

    # VQA is allowed to rewrite CSS after the pipeline's first source-image
    # visibility seal. Re-run the strict occurrence visibility contract over
    # every final HTML immediately before export so a repair cannot shrink,
    # hide, clip, or move an admitted source image out of the viewport.
    if source_reflow:
        try:
            final_source_images = plan_slide_source_images(
                contents,
                source_images,
                source_slide_locked=True,
            )
            if final_source_images is None:
                raise ValueError("authored_source_image_plan_unavailable")
            final_metrics = (
                retained_final_metrics
                if evidence_ledger is not None
                else await asyncio.gather(
                    *(ensure_browser_metrics(html) for html in htmls)
                )
            )
            for index, (html, offered, metrics) in enumerate(
                zip(htmls, final_source_images, final_metrics, strict=True)
            ):
                try:
                    validate_visible_source_images(html, offered, metrics)
                except ValueError as error:
                    raise ValueError(
                        f"source_slide_{index + 1}:{error}"
                    ) from error
        except Exception as error:
            raise AuthoredStageError(
                slide_index=0,
                stage="render",
                code="authored_source_image_visibility_mismatch",
                cause=error,
                retryable=True,
            ) from error

    # VQA/source-image sealing can rewrite HTML after the initial gate pass;
    # re-check the exact final bytes immediately before export.  A residual
    # safety defect is still fail-closed; the receipt's residual state is for
    # non-blocking quality findings, not a way around the safety gate.
    residual_gate_failures: list[dict[str, object]] = []
    if composition_profile.strict_dom_gate and reference_mode != EXACT_RECONSTRUCTION:
        residual_gate_failures = await _final_authored_quality_gate(
            htmls=htmls,
            capacity_plan=capacity_plan,
            profile=composition_profile,
            reference_mode=reference_mode,
            browser_metrics_by_slide=(
                retained_final_metrics if evidence_ledger is not None else None
            ),
        )
        if residual_gate_failures:
            first = residual_gate_failures[0]
            raise AuthoredStageError(
                slide_index=int(first.get("slide_index", 0)),
                stage="render",
                code="authored_composition_safety_gate_failed",
                retryable=True,
                message="One or more slides still fail the composition safety gate after bounded repair",
            )

    source_readback_receipt = None
    if source_reflow and composition_profile.text_mode == "verbatim":
        try:
            source_readback_receipt = verify_source_verbatim_readback(
                manifest=request._source_content_manifest,
                outline=request._source_document_outline,
                htmls=htmls,
            )
            if evidence_ledger is not None:
                browser_source_receipt = verify_browser_source_readback(
                    manifest=request._source_content_manifest,
                    outline=request._source_document_outline,
                    browser_metrics=retained_final_metrics,
                    htmls=htmls,
                )
                if not browser_source_receipt.passed:
                    raise BrowserSourceReadbackError(
                        "source_content_browser_readback_failed",
                        detail="browser-observed source block contract failed",
                        block_ids=tuple(browser_source_receipt.missing_block_ids),
                        slide_indices=tuple(
                            range(browser_source_receipt.checked_slide_count)
                        ),
                    )
                source_readback_receipt = browser_source_receipt
            request._authored_source_readback_receipt = source_readback_receipt.as_dict()
        except (SourceContentReadbackError, BrowserSourceReadbackError) as error:
            raise AuthoredStageError(
                slide_index=error.slide_indices[0] if error.slide_indices else 0,
                stage="render",
                code=error.code,
                cause=error,
                retryable=True,
                message=error.detail,
            ) from error

    quality_gate_receipt = build_authored_quality_gate_receipt(
        htmls=htmls,
        profile_id=composition_profile.profile_id,
        gate_failures=residual_gate_failures,
        repaired_slide_indices=repaired_gate_indices,
    )
    if source_readback_receipt is not None:
        quality_gate_receipt["sourceReadback"] = source_readback_receipt.as_dict()

    if evidence_ledger is not None and evidence_attempt_id is not None:
        capacity_checks: list[dict[str, object]] = []
        for index, metrics in enumerate(retained_final_metrics or ()):
            expected = contents[index] if index < len(contents) else None
            if isinstance(expected, str):
                lines = [line.strip() for line in expected.splitlines() if line.strip()]
                title_text = lines[0].lstrip("# ") if lines and lines[0].startswith("#") else None
                body_text = " ".join(lines[1:] if title_text else lines)
            else:
                title_text = body_text = None
            capacity_checks.append(
                evaluate_capacity_browser_metrics(
                    metrics,
                    capacity_plan.at(index) if capacity_plan is not None else {},
                    # Ordinary authored slides may rewrite the outline heading.
                    # Require a browser-observed title role, while source reflow
                    # keeps exact text binding through its dedicated readback.
                    expected_title=title_text if source_reflow else None,
                    # Ordinary authored slides may condense and restructure the
                    # outline. Browser role presence is still mandatory here;
                    # exact source text is enforced separately by source readback.
                    expected_body=(body_text or None) if source_reflow else None,
                )
            )
        if capacity_checks:
            names = ("text", "title", "body", "font", "qid", "image")
            capacity_evidence = {
                "browserObserved": all(item.get("browserObserved") is True for item in capacity_checks),
                "checks": {name: all(bool(item.get("checks", {}).get(name)) for item in capacity_checks) for name in names},
                "violations": [v for item in capacity_checks for v in item.get("violations", [])],
            }
        else:
            capacity_evidence = {"browserObserved": False, "checks": {name: False for name in ("text", "title", "body", "font", "qid", "image")}}
        source_evidence = (
            source_readback_receipt.as_dict()
            if source_readback_receipt is not None
            else {"required": False, "status": "not_applicable"}
        )
        safety_evidence = {
            "status": "passed" if not residual_gate_failures else "blocked",
            "residuals": residual_gate_failures,
        }
        v2 = create_quality_receipt_v2(
            evidence_ledger,
            attempt_id=evidence_attempt_id,
            revision=evidence_ledger.revision(evidence_attempt_id),
            ledger_digest=evidence_ledger.ledger_digest(evidence_attempt_id),
            deck_html_digest=evidence_ledger.deck_html_digest(evidence_attempt_id),
            evidence={
                "safety": safety_evidence,
                "capacity": capacity_evidence,
                "sourceReadback": source_evidence,
                "visualReview": {"status": "passed" if request.vision_qa else "not_applicable"},
                "performance": {"status": "not_assessed"},
            },
            require_visual_review=False,
            require_performance=False,
            # The slide evidence ledger intentionally remains attempt-local;
            # the receipt itself must survive worker restart and checkpoint
            # cleanup.  A job-scoped durable manifest gives retries one stable
            # idempotency boundary without exposing the raw lease token.
            storage_path=(
                quality_receipt_manifest_path(generation_job.id)
                if generation_job is not None
                else None
            ),
        )
        quality_gate_receipt = {
            **quality_gate_receipt,
            **v2.to_dict(),
            "profileId": composition_profile.profile_id,
            "slideCount": len(htmls),
            "htmlDigest": "sha256:" + sha256_canonical_json(htmls),
            "status": "passed" if v2.gate_passed else "blocked",
            "residualIssues": residual_gate_failures,
            "residualIssueCount": len(residual_gate_failures),
        }
        if not v2.gate_passed:
            raise AuthoredStageError(
                slide_index=0,
                stage="render",
                code="authored_quality_receipt_gate_failed",
                retryable=True,
                message="QualityReceipt v2 unified gate blocked publication",
            )

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
            browser_metrics_by_slide=retained_final_metrics,
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
        quality_gate_receipt=quality_gate_receipt,
    )
    if generation_job is not None:
        # Seal the evidence directory only after publication and the durable
        # QualityReceipt v2 gate have succeeded.  The sweeper may then reclaim
        # sealed artifacts according to TTL/byte budget; failed or in-flight
        # attempts remain protected for crash recovery.
        mark_checkpoint_artifacts_sealed(generation_job.id)
        await _cleanup_published_checkpoints(generation_job.id)
    LOGGER.info(
        "[authored] done id=%s slides=%d profile=%s provider=%s model=%s vision_qa=%s "
        "vqa_fixed=%d blank_renders=%d total=%.1fs author=%.1fs "
        "illustrations=%.1fs render=%.1fs pipeline=%.1fs vision=%.1fs "
        "persist=%.1fs export=%.1fs",
        presentation_id,
        len(htmls),
        composition_profile.profile_id,
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
