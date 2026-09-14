from __future__ import annotations

# The publication admission path performs one local existence check.
# ruff: noqa: ASYNC240

import asyncio
import inspect
from hashlib import sha256
import logging
import math
import os
import sys
import time
import traceback
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from functools import partial
from typing import Optional

from sqlalchemy import func, or_, select
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
from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
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
from models.authored_header_contract import AuthoredHeaderContract, default_header_contract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.authored_infographic_plan import AuthoredInfographicPlan
from services.authored_deck_plans import (
    AuthoredDeckPlans,
    derive_illustration_plan,
    resolve_deck_plans,
    resolve_final_outline_infographic_plan,
)
from services.authored_deck_plan_checkpoint import (
    AuthoredDeckPlanCheckpointError,
    canonical_checkpoint_digest,
    resolve_with_authored_deck_plan_checkpoint,
)
from utils.llm_calls.author_slide import TEMPLATE_PAGE_NUMBER_MODE as _TEMPLATE_PAGE_NUMBER_MODE
from utils.slide_capture_deck_scope import set_render_deck_scope
from services.source_content.header_pages import source_pages_are_numbered
from services.authored_vqa_source_integrity import source_vqa_candidate_integrity_passes
from services.authored_admission_producers import source_candidate
from services.authored_admission_capture import (
    capture_once,
    capture_ownership_receipt,
    captured_metrics_or_existing,
)
from services.authored_admission_publication import final_readback
from services.authored_admission_session import admission_scope, configure_admission, admission_shadow_receipt, producer_scope
from services.authored_header_contract_service import (
    page_number_mode_for,
    resolve_header_contract,
)
from services.authored_presentation_pipeline import _author_illustrate_render_pipeline
from services.authored_presentation_publication import publish_authored_presentation
from services.authored_generation_progress_poller import mark_authored_generation_stage
from services.authored_local_fit_evidence import (
    AcceptedLocalFit,
    AuthoredLocalFitRejected,
    accept_unchanged_capture,
    accept_verified_capture,
    cache_accepted_capture,
    receipt_is_preserved_original,
)
from services.authored_local_fit_policy import (
    POLICY_REVISION,
    html_digest,
    local_fit_applies,
    local_fit_policy,
    requested_font_policy_revision,
    slide_budget,
)
from services.authored_canvas import request_canvas_contract
from services.authored_flag_classification import (
    classify_flagged_slides, flag_pages, review_pages_for,
)
from services.authored_local_fit_deadline import (
    remaining_authored_local_fit_seconds,
)
from models.authored_vnext.deck import (
    DeckGenerationSnapshot,
    DeckSceneIR,
    SlideValidationCheckpoint,
    ValidatedSlideSpecV2,
)
from models.authored_vnext.master.slide import (
    ExplicitTitle,
    SlideSpecV2,
    TitleFitResult,
)
from services.authored_vnext.generation.errors import GenerationNotFoundError
from services.authored_vnext.generation.effective_style import (
    materialize_authored_composition,
    resolve_authored_composition,
)
from services.authored_vnext.generation.legacy_pin import generation_repository_for_owner
from services.authored_vnext.generation.repository import async_generation_repository
from services.authored_vnext.generation.internal_authored_composition import (
    build_internal_authored_composition,
)
from services.authored_vnext.generation.pipeline import _seal_deck
from services.authored_vnext.generation.scene_checkpoint import (
    SceneRepairBudgetExhausted,
    claim_scene_repair,
    persist_repaired_scene_envelope,
    persist_scene_envelope,
)
from services.authored_vnext.generation.scene_envelope import (
    AuthoredDeckSceneEnvelope,
    replace_deck_scene_slide,
    seal_deck_scene_envelope,
    seal_slide_scene_envelope,
)
from services.authored_vnext.master.digests import canonical_digest
from services.authored_vnext.master.errors import DeckAlreadyExistsError
from services.presentation_export_attempt_service import sha256_canonical_json
from services.authored_vnext.semantic.editable_scene import build_editable_hybrid_scene
from services.authored_source_images import (
    collect_source_image_candidates,
    plan_slide_source_images,
)
from utils.authored_semantic_role_repair import canonicalize_authored_roles
from services.authored_composition_policy import (
    blocking_safety_issues,
    build_authored_capacity_plan,
    capacity_repair_instructions,
    capacity_violations,
    ensure_authored_semantic_roles,
    project_authored_owned_text,
    reduce_authored_owner_text_capacity,
    reduce_authored_closing_capacity,
    reduce_authored_closing_title_punctuation,
    repair_authored_header_eyebrow_clip,
    repair_authored_layout_alignment,
    repair_authored_owned_text_paint_order,
    resolve_composition_profile,
    semantic_repair_boundary_violations,
)
from services.authored_offcanvas_repair import (
    repair_authored_blank_field_clipping,
    repair_authored_offcanvas_width,
)
from services.authored_generation_preflight import (
    AuthoredSlideArtifact,
    preflight_authored_provider_repair,
)
from services.authored_deck_instruments_stage import attach_deck_receipt_extras
from services.authored_final_metrics import resolve_final_browser_metrics
from services.authored_quality_gate_diagnostics import (
    log_quality_receipt_gate_failure,
    quality_receipt_gate_failure,
)
from services.authored_repair_loop import single_repair_loop_enabled
from services.authored_repair_loop_evidence import (
    vision_evidence_for_flagged,
    visual_review_evidence_only,
)
from services.authored_repair_loop_stage import run_verbatim_repair_loop
from services.authored_repair_round_order import remake_first_flagged_slides
from services.authored_terminal_slide_fallback import (
    MAX_REMAKES_PER_SLIDE,
    replayed_plan_kwargs,
    resolve_terminal_gate_slides,
)
from services.authored_terminal_slide_policy import (
    claim_bounded_repairs,
    declare_unadmitted_gate_repair,
    flagged_slide_absorbs_capacity,
    gate_repair_rejection_is_terminal_slide,
    partition_residual_failures,
    recover_residual_repair_round,
    terminal_fallback_active,
)
from services.authored_decoration_containment import (
    plan_decoration_containment,
    validate_decoration_containment,
)
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from utils.authored_patch_engine import apply_authored_patch
from utils.authored_illustrations import validate_visible_source_images
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
    StructuralRepairBudget,
    _advance_repair_evaluator,
    _measured_structural_issue_qids,
    _structural_compact_dom,
    revise_authored_deck,
)
from services.authored_source_visibility_repair import (
    inspect_slide_source_visibility,
    repair_source_decoration_paint_order,
)
from utils.llm_calls.author_slide_patch import (
    build_structural_repair_scopes,
    protected_qids_for_repair_scopes,
)
from services.authored_generation_observability_probes import (
    _artifact_bytes_metric,
    _blocked_renderer_reference_count,
    _maximum_observed_metric,
    _not_applicable_metric,
    _object_store_latency_metric,
    _observed_metric,
    _runtime_counter_delta,
    _unavailable_metric,
    _worker_handle_count_metric,
    _worker_peak_memory_metric,
)
from utils.llm_calls.critique_slide import CritiqueIssue, SlideCritique
from utils.slide_capture import (
    _placeholder_png,
    ensure_browser_metrics,
    find_chrome,
    get_cached_browser_metrics,
    get_render_runtime_counters,
    render_html_to_png_with_metrics,
)
from services.authored_evidence_ledger import digest_html, digest_png
from services.authored_structural_repair import (
    SlideRepairResult,
    make_source_repair_evaluator,
    source_verbatim_rejection,
    structural_checkpoint_metrics,
    validate_repair_result,
)
from services.authored_generation_timing_receipt import (
    create_generation_timing_receipt,
)
from services import authored_generation_quality_gate as _quality_gate_impl
from services import authored_generation_repair as _repair_impl
from services import authored_generation_repair_inputs as _repair_inputs_impl
from services import authored_generation_scene_persistence as _scene_persistence_impl
from services.authored_quality_receipt_v2 import (
    create_quality_receipt_v2,
    evaluate_capacity_browser_metrics,
    quality_receipt_manifest_path,
)
from services.authored_quality_receipt_v3_pipeline import (
    create_generation_quality_receipt_v3,
)
from services.authored_font_signal import body_font_median_signal
from services.authored_visual_review_evidence import visual_review_not_assessed, visual_review_outcome
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    stamp_source_block_ids,
)


LOGGER = logging.getLogger(__name__)


_PERFORMANCE_STAGE_NAMES = (
    "outline",
    "author",
    "illustration",
    "render",
    "quality",
    "repair",
    "checkpoint",
    "publication",
    "export",
)


def _call_extracted(module, name: str, *args, **kwargs):
    module.bind_runtime(sys.modules[__name__])
    return getattr(module, name)(*args, **kwargs)


def _dom_repair_detail(*args, **kwargs):
    return _call_extracted(_repair_inputs_impl, "_dom_repair_detail", *args, **kwargs)


def _bounded_critique_issues(*args, **kwargs):
    return _call_extracted(_repair_inputs_impl, "_bounded_critique_issues", *args, **kwargs)


def _capacity_gate_initial_critiques(*args, **kwargs):
    return _call_extracted(_repair_inputs_impl, "_capacity_gate_initial_critiques", *args, **kwargs)


def _deterministic_repair_candidate(*args, **kwargs):
    return _call_extracted(_repair_impl, "_deterministic_repair_candidate", *args, **kwargs)


def _repair_authored_intrinsic_grid_overlap(*args, **kwargs):
    return _call_extracted(_repair_impl, "_repair_authored_intrinsic_grid_overlap", *args, **kwargs)


def _admit_vqa_capacity_candidate(*args, **kwargs):
    return _call_extracted(_repair_impl, "_admit_vqa_capacity_candidate", *args, **kwargs)


def _admit_gate_repair_capacity_candidate(*args, **kwargs):
    return _call_extracted(_repair_impl, "_admit_gate_repair_capacity_candidate", *args, **kwargs)


async def _revise_gate_repair_candidates(*args, **kwargs):
    return await _call_extracted(_repair_impl, "_revise_gate_repair_candidates", *args, **kwargs)


async def _revise_source_gate_repair_candidates(*args, **kwargs):
    return await _call_extracted(_repair_impl, "_revise_source_gate_repair_candidates", *args, **kwargs)


async def _bind_bounded_repair_evidence(*args, **kwargs):
    return await _call_extracted(_repair_impl, "_bind_bounded_repair_evidence", *args, **kwargs)


def _source_block_bindings(*args, **kwargs):
    return _call_extracted(_quality_gate_impl, "_source_block_bindings", *args, **kwargs)


def _require_source_verbatim_bindings(*args, **kwargs):
    return _call_extracted(_quality_gate_impl, "_require_source_verbatim_bindings", *args, **kwargs)


def _source_image_block_bindings(*args, **kwargs):
    return _call_extracted(_quality_gate_impl, "_source_image_block_bindings", *args, **kwargs)


async def _final_authored_quality_gate(*args, **kwargs):
    return await _call_extracted(_quality_gate_impl, "_final_authored_quality_gate", *args, **kwargs)


def _persist_or_reuse_identical_semantic_deck(*args, **kwargs):
    return _call_extracted(_scene_persistence_impl, "_persist_or_reuse_identical_semantic_deck", *args, **kwargs)


async def _persist_master_pinned_editable_scene_deck(*args, **kwargs):
    return await _call_extracted(_scene_persistence_impl, "_persist_master_pinned_editable_scene_deck", *args, **kwargs)


def _deck_plan_checkpoint_input(
    *,
    generation_job: PresentationGenerationJob | None,
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    brand: object,
    style: object,
    stage: str,
) -> dict[str, object]:
    """Build a digest-only input contract; the payload itself is never stored."""

    return {
        "schemaVersion": "authored-deck-plan-input/v1",
        "stage": stage,
        "requestSha256": (
            generation_job.request_sha256 if generation_job is not None else "legacy"
        ),
        "outline": outline.model_dump(mode="json"),
        "brand": {
            "topic": getattr(brand, "topic", None),
            "language": getattr(brand, "language", None),
            "primary": getattr(brand, "primary", None),
            "fonts": getattr(brand, "fonts", None),
        },
        "styleId": getattr(style, "id", None),
        "referenceMode": getattr(style, "reference_mode", None),
        "includeTableOfContents": request.include_table_of_contents,
        "hasFixedSlidesMarkdown": bool(request.slides_markdown),
        "instructionsDigest": canonical_checkpoint_digest(
            request.instructions or ""
        ),
    }


def _encode_deck_plans(value: AuthoredDeckPlans) -> dict[str, object]:
    return {
        "schemaVersion": "authored-deck-plans/v1",
        "outline": value.outline.model_dump(mode="json"),
        "roles": list(value.roles),
        "chapterPlan": (
            value.chapter_plan.model_dump(mode="json")
            if value.chapter_plan is not None
            else None
        ),
    }


def _decode_deck_plans(payload: Mapping[str, object]) -> AuthoredDeckPlans:
    if payload.get("schemaVersion") != "authored-deck-plans/v1":
        raise AuthoredDeckPlanCheckpointError(
            "authored_deck_checkpoint_schema_invalid"
        )
    roles = payload.get("roles")
    if not isinstance(roles, list) or not all(isinstance(item, str) for item in roles):
        raise AuthoredDeckPlanCheckpointError(
            "authored_deck_checkpoint_roles_invalid"
        )
    chapter_payload = payload.get("chapterPlan")
    return AuthoredDeckPlans(
        outline=PresentationOutlineModel.model_validate(payload.get("outline")),
        roles=list(roles),
        chapter_plan=(
            AuthoredChapterPlan.model_validate(chapter_payload)
            if isinstance(chapter_payload, dict)
            else None
        ),
    )


def _encode_optional_model(
    schema_version: str, field: str, value: object | None
) -> dict[str, object]:
    return {
        "schemaVersion": schema_version,
        field: (
            value.model_dump(mode="json")
            if value is not None and hasattr(value, "model_dump")
            else None
        ),
    }


async def _provider_stage_observability_metrics(
    session: AsyncSession,
    operation_id: uuid.UUID,
) -> dict[str, dict[str, object]]:
    admission = await session.scalar(
        select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == operation_id
        )
    )
    if admission is None:
        return {
            name: _unavailable_metric("public_admission_not_found")
            for name in (
                "queuePosition",
                "stageRetryCalls",
                "stageEstimatedCost",
                "stageActualCost",
            )
        }
    calls = list(
        (
            await session.execute(
                select(PublicProviderLogicalCall).where(
                    PublicProviderLogicalCall.admission_id == admission.id
                )
            )
        ).scalars()
    )
    queue_ahead = int(
        await session.scalar(
            select(func.count())
            .select_from(PublicOperationAdmission)
            .where(PublicOperationAdmission.id != admission.id)
            .where(PublicOperationAdmission.policy_version == admission.policy_version)
            .where(PublicOperationAdmission.tenant_id == admission.tenant_id)
            .where(PublicOperationAdmission.operation_kind == admission.operation_kind)
            .where(PublicOperationAdmission.queued_at < admission.queued_at)
            .where(
                or_(
                    PublicOperationAdmission.activated_at.is_(None),
                    PublicOperationAdmission.activated_at > admission.queued_at,
                )
            )
        )
        or 0
    )
    if not calls:
        return {
            "queuePosition": _observed_metric(queue_ahead + 1),
            **{
            name: _not_applicable_metric("provider_not_used")
            for name in (
                "stageRetryCalls",
                "stageEstimatedCost",
                "stageActualCost",
            )
            },
        }
    retry_by_stage: dict[str, int] = {}
    estimated_by_stage: dict[str, int] = {}
    actual_by_stage: dict[str, int] = {}
    actual_available = True
    for call in calls:
        stage = str(call.stage).strip().lower()
        retry_by_stage.setdefault(stage, 0)
        estimated_by_stage.setdefault(stage, 0)
        actual_by_stage.setdefault(stage, 0)
        if call.retry_class != "initial":
            retry_by_stage[stage] += 1
        estimated_by_stage[stage] += max(0, int(call.estimated_cost_microunits))
        if call.actual_cost_microunits is None:
            actual_available = False
        else:
            actual_by_stage[stage] += max(0, int(call.actual_cost_microunits))
    return {
        "queuePosition": _observed_metric(queue_ahead + 1),
        "stageRetryCalls": _observed_metric(retry_by_stage),
        "stageEstimatedCost": _observed_metric(estimated_by_stage),
        "stageActualCost": (
            _observed_metric(actual_by_stage)
            if actual_available
            else _unavailable_metric("provider_actual_cost_not_reported")
        ),
    }


async def _record_deck_stage_latency(
    generation_job: PresentationGenerationJob | None,
    *,
    stage: str,
    elapsed_seconds: float,
) -> None:
    """Persist one content-free deck-stage latency receipt."""

    if stage not in _PERFORMANCE_STAGE_NAMES:
        raise ValueError("invalid_authored_performance_stage")
    with provider_usage_scope(
        job_id=generation_job.id if generation_job is not None else None,
        operation="authored_generation",
        stage=stage,
        attempt_number=(generation_job.attempt_number if generation_job else 0),
    ):
        await record_local_stage(latency_ms=max(0.0, elapsed_seconds * 1000))


def _outline_stage_started_at(request: object) -> float:
    """Use the operation boundary so receipt timing includes upstream outline LLMs."""

    started_at = getattr(request, "_authored_outline_started_at", None)
    if isinstance(started_at, (int, float)) and not isinstance(started_at, bool):
        return float(started_at)
    return time.monotonic()


def _gate_failure_signature(
    htmls: list[str], failure: dict[str, object]
) -> tuple[int, str, tuple[str, ...]] | None:
    """Return the deterministic slide/digest/issues identity of one gate failure."""

    slide_index = failure.get("slide_index")
    if not isinstance(slide_index, int) or not (0 <= slide_index < len(htmls)):
        return None
    messages = tuple(
        sorted(
            str(issue.get("message") or "").strip()
            for issue in failure.get("issues", ())
            if isinstance(issue, dict) and str(issue.get("message") or "").strip()
        )
    )
    return slide_index, digest_html(htmls[slide_index]), messages


def _use_single_slide_header_fast_path(
    request: GeneratePresentationRequest,
    outline: PresentationOutlineModel,
    style: object,
) -> bool:
    """Whether the deterministic header can replace a deck-level model call."""

    return (
        len(outline.slides) == 1
        and not request.instructions
        and not request._source_content_authored_reflow
        and normalize_reference_mode(
            getattr(style, "reference_mode", STYLE_REFERENCE)
        )
        != EXACT_RECONSTRUCTION
    )


def _callable_accepts_keyword(callable_object, keyword: str) -> bool:
    """Return whether a runtime adapter can receive one named argument."""

    parameters = inspect.signature(callable_object).parameters
    return keyword in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


#: A critique carries at most this many issues (``SlideCritique.issues``).  The
#: deterministic gate can report more -- one per offending element -- and
#: handing the model a longer list raised a validation error that killed the
#: whole deck instead of repairing the slide.
_MAX_CRITIQUE_ISSUES = 8

#: ``CritiqueIssue.detail`` accepts this many characters, and the element names
#: are what makes the instruction actionable, so the boilerplate is what gives
#: way first.
_MAX_CRITIQUE_DETAIL = 200


async def _fit_content_revision(
    html: str,
    *,
    slide_index: int,
    policy_revision: str | None = None,
    canvas: object | None = None,
) -> AcceptedLocalFit:
    """Run a provider content correction through the same local-fit boundary.

    The provider result starts a new author revision.  A changed fitter result
    therefore receives its own one-shot, fit-disabled reconstruction instead
    of inheriting the receipt for the pre-VQA HTML.
    """

    original_digest = html_digest(html)
    remaining_operation_seconds = remaining_authored_local_fit_seconds(cap=120)
    if remaining_operation_seconds <= 0:
        raise AuthoredLocalFitRejected("deadline_exceeded")
    budget = slide_budget(60, remaining_operation_seconds)
    started_at = time.monotonic()
    slide_deadline = started_at + budget["total_seconds"]
    first_deadline = min(
        slide_deadline, started_at + budget["first_seconds"]
    )
    canvas_width_px = int(getattr(canvas, "width_px", 1280))
    canvas_height_px = int(getattr(canvas, "height_px", 720))
    render_kwargs = (
        {}
        if (canvas_width_px, canvas_height_px) == (1280, 720)
        else {"width_px": canvas_width_px, "height_px": canvas_height_px}
    )
    render_capture = partial(
        render_html_to_png_with_metrics,
        **render_kwargs,
    )
    policy = local_fit_policy(
        author_revision=original_digest,
        original_html_digest=original_digest,
        export_width_pt=canvas_width_px * 0.75,
        export_height_pt=canvas_height_px * 0.75,
        viewport_width=canvas_width_px,
        viewport_height=canvas_height_px,
        slide_index=slide_index,
        policy_revision=policy_revision or POLICY_REVISION,
    )
    first_remaining = max(0.001, first_deadline - time.monotonic())
    async with asyncio.timeout(first_remaining):
        fitted = await capture_once(
            html, index=slide_index, capture=render_capture,
            timeout=max(1, min(60, int(first_remaining))),
            fit=True,
            fit_policy=policy,
        )
    prepared_original_html = fitted.prepared_html
    if prepared_original_html is None:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    policy = fitted.prepared_fit_policy or policy
    report = fitted.fit_report or {}
    changed = bool(report.get("changed")) or bool(
        fitted.fitted_html and fitted.fitted_html != prepared_original_html
    )
    if not changed:
        return accept_unchanged_capture(
            fitted,
            original_html=prepared_original_html,
            policy=policy,
        )
    verify_queued_at = time.monotonic()
    verify_started_at = time.monotonic()
    verify_deadline = min(
        slide_deadline,
        verify_queued_at + budget["verify_seconds"],
    )
    verify_remaining = max(0.001, verify_deadline - time.monotonic())
    async with asyncio.timeout(verify_remaining):
        verified = await capture_once(
            fitted.fitted_html, index=slide_index, capture=render_capture,
            timeout=max(1, min(60, int(verify_remaining))),
            fit=False,
            fit_policy={
                **dict(policy),
                "baseline": dict(report.get("baseline") or {}),
            },
            fresh=True,
        )
    verify_finished_at = time.monotonic()
    if verified.prepared_html is None:
        raise AuthoredLocalFitRejected("reconstruction_mismatch")
    policy = verified.prepared_fit_policy or policy
    return accept_verified_capture(
        fitted,
        verified,
        prepared_html=verified.prepared_html,
        original_html=prepared_original_html,
        policy=policy,
        verify_queued_at=verify_queued_at,
        verify_started_at=verify_started_at,
        verify_finished_at=verify_finished_at,
    )


async def _apply_bounded_header_eyebrow_fallback(
    *,
    html: str,
    browser_metrics: object,
    capacity,
    header_contract: AuthoredHeaderContract | None,
    role: str,
    slide_index: int | None = None,
    allow_immutable_checkpoint_geometry: bool = False,
) -> tuple[str, bytes, object] | None:
    """Apply and verify the single contract-scoped eyebrow clip fallback."""

    if capacity_violations(html, browser_metrics, capacity) != (
        "browser_text_not_visible:header.eyebrow",
    ):
        return None
    patched = repair_authored_header_eyebrow_clip(
        html,
        browser_metrics,
        header_contract,
        role,
        allow_immutable_checkpoint_geometry=allow_immutable_checkpoint_geometry,
    )
    if patched == html:
        return None
    bound_htmls, bound_pngs, bound_metrics = await _bind_bounded_repair_evidence(
        [patched], [slide_index]
    )
    if capacity_violations(bound_htmls[0], bound_metrics[0], capacity):
        return None
    return bound_htmls[0], bound_pngs[0], bound_metrics[0]


async def _apply_bounded_closing_capacity_reducer(
    *,
    html: str,
    browser_metrics: object,
    capacity,
    profile_id: str,
    slide_index: int | None = None,
) -> tuple[str, bytes, object] | None:
    """Reduce and Chromium-verify one ordinary CLOSING capacity residual."""

    reduced = reduce_authored_closing_capacity(
        html,
        browser_metrics,
        capacity,
        profile_id=profile_id,
    )
    if reduced == html:
        return None
    bound_htmls, bound_pngs, bound_metrics = await _bind_bounded_repair_evidence(
        [reduced], [slide_index]
    )
    if capacity_violations(bound_htmls[0], bound_metrics[0], capacity):
        return None
    return bound_htmls[0], bound_pngs[0], bound_metrics[0]


async def _apply_bounded_closing_title_reducer(
    *,
    html: str,
    browser_metrics: object,
    capacity,
    profile_id: str,
    slide_index: int | None = None,
) -> tuple[str, bytes, object] | None:
    reduced = reduce_authored_closing_title_punctuation(
        html,
        browser_metrics,
        capacity,
        profile_id=profile_id,
    )
    if reduced == html:
        return None
    bound_htmls, bound_pngs, bound_metrics = await _bind_bounded_repair_evidence(
        [reduced], [slide_index]
    )
    if capacity_violations(bound_htmls[0], bound_metrics[0], capacity):
        return None
    return bound_htmls[0], bound_pngs[0], bound_metrics[0]


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
        bound_htmls, bound_pngs, bound_metrics = await _bind_bounded_repair_evidence(
            [repaired], [index]
        )
        violations = capacity_violations(
            bound_htmls[0], bound_metrics[0], capacity_plan.at(index)
        )
        if violations:
            raise RuntimeError(
                "bounded layout alignment repair failed capacity validation: "
                + ",".join(violations)
            )
        htmls[index], pngs[index] = bound_htmls[0], bound_pngs[0]
        metrics_override[index] = bound_metrics[0]
        repaired_indices.append(index)
    return repaired_indices


async def _apply_final_checkpoint_header_eyebrow_fallback(
    *,
    pipeline,
    htmls: list[str],
    pngs: list[bytes],
    capacity_plan,
    header_contract: AuthoredHeaderContract | None,
    roles: list[str],
    metrics_override: dict[int, object],
    already_attempted: bool,
) -> tuple[bool, int | None]:
    """Try the strict local fallback once when the first gate missed recovery.

    The candidate metrics must be retained for the exact final HTML.  A caller
    that already entered the fallback after provider repair cannot enter again,
    even when its post-capture gate failed, so one deck attempt performs at most
    one deterministic eyebrow recapture.
    """

    if already_attempted:
        return True, None
    ledger = getattr(pipeline, "evidence_ledger", None)
    attempt_id = getattr(pipeline, "evidence_attempt_id", None)
    if ledger is None or not attempt_id:
        return False, None
    for index, html in enumerate(htmls):
        latest = ledger.latest(attempt_id, index)
        if (
            latest is None
            or latest.html != html
            or latest.browser_metrics is None
            or capacity_violations(
                html, latest.browser_metrics, capacity_plan.at(index)
            )
            != ("browser_text_not_visible:header.eyebrow",)
        ):
            continue
        fallback = await _apply_bounded_header_eyebrow_fallback(
            html=html,
            browser_metrics=latest.browser_metrics,
            capacity=capacity_plan.at(index),
            header_contract=header_contract,
            role=roles[index],
            slide_index=index,
            allow_immutable_checkpoint_geometry=(
                latest.capture_policy == "hydrated"
            ),
        )
        if fallback is None:
            return True, None
        htmls[index], pngs[index], metrics_override[index] = fallback
        return True, index
    return False, None


async def _apply_final_checkpoint_closing_capacity_reducer(
    *,
    pipeline,
    htmls: list[str],
    pngs: list[bytes],
    capacity_plan,
    profile_id: str,
    metrics_override: dict[int, object],
) -> list[int]:
    """Reduce final CLOSING-only capacity residuals from exact checkpoint bytes."""

    if profile_id != "ordinary":
        return []
    ledger = getattr(pipeline, "evidence_ledger", None)
    attempt_id = getattr(pipeline, "evidence_attempt_id", None)
    repaired: list[int] = []
    for index, html in enumerate(htmls):
        capacity = capacity_plan.at(index)
        if capacity.role.strip().upper() != "CLOSING":
            continue
        metrics = metrics_override.get(index)
        if metrics is None and ledger is not None and attempt_id:
            latest = ledger.latest(attempt_id, index)
            if latest is not None and latest.html == html:
                metrics = latest.browser_metrics
        if metrics is None:
            continue
        reduced = await _apply_bounded_closing_capacity_reducer(
            html=html,
            browser_metrics=metrics,
            capacity=capacity,
            profile_id=profile_id,
            slide_index=index,
        )
        if reduced is None:
            continue
        htmls[index], pngs[index], metrics_override[index] = reduced
        repaired.append(index)
    return repaired


def _merge_flagged_slide_entries(
    entries: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """Merge author-stage and terminal-gate flags for the same slide index.

    A slide can be flagged twice: once by the author-stage rescue (a fresh
    authoring call exhausted its attempts) and again by the terminal gate
    fallback (the remade slide is still terminal). Concatenating both lists
    used to let the second entry silently replace the first once the client
    keys badges by slide, dropping the author-stage reason. Keep the first
    entry's fields (so every existing single-flag receipt is byte-identical)
    and record every distinct reason seen for that index under ``reasons``
    -- only added when there is more than one, so a slide flagged once still
    serializes exactly as it did before this function existed.
    """

    merged: dict[int, dict[str, object]] = {}
    reasons_by_index: dict[int, list[str]] = {}
    order: list[int] = []
    for entry in entries:
        index = int(entry["index"])
        reason = entry.get("reason")
        extra_reasons = entry.get("reasons")
        entry_reasons: list[str] = []
        if isinstance(extra_reasons, Sequence) and not isinstance(extra_reasons, (str, bytes)):
            entry_reasons.extend(item for item in extra_reasons if isinstance(item, str))
        elif isinstance(reason, str):
            entry_reasons.append(reason)
        if index not in merged:
            merged[index] = dict(entry)
            reasons_by_index[index] = list(dict.fromkeys(entry_reasons))
            order.append(index)
            continue
        reasons = reasons_by_index[index]
        reasons.extend(item for item in entry_reasons if item not in reasons)
        if "sourceVisibilityBlockIds" in entry:
            merged[index]["sourceVisibilityBlockIds"] = entry["sourceVisibilityBlockIds"]
    result: list[dict[str, object]] = []
    for index in order:
        entry = merged[index]
        reasons = reasons_by_index[index]
        if len(reasons) > 1:
            entry = {**entry, "reasons": reasons}
        result.append(entry)
    return result


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


def _evaluate_capacity_role_evidence(metrics: object, capacity: object) -> dict[str, object]:
    """Evaluate final semantic owners without rebinding source text to a role.

    Source readback separately proves each immutable block's exact browser text.
    Capacity owns the final composition instead: title/body role presence, caps,
    font, QID, image, and visibility checks over the HTML being published.
    """

    return evaluate_capacity_browser_metrics(metrics, capacity)


def _finalize_attempt_evidence(
    pipeline,
    htmls: list[str],
    pngs: list[bytes],
    metrics_override: dict[int, object] | None = None,
    *, width_px: int = 1280, height_px: int = 720,
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
                else get_cached_browser_metrics(
                    html, width_px=width_px, height_px=height_px
                )
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


async def _checkpoint_source_reflow_vqa_repairs(
    generation_job: PresentationGenerationJob | None,
    error: AuthoredVqaResidualDomIssuesError,
    *,
    absolute_indices: Sequence[int] | None = None,
    persist_scene=None,
) -> None:
    """Persist successful local VQA patches before source-reflow fails closed."""

    if generation_job is None or not generation_job.attempt_token:
        return
    results = getattr(error, "repair_results", ())
    # Legacy `fixed` only means changed: it cannot authorize source checkpoints.
    if not results:
        return
    if absolute_indices is not None:
        expected = tuple(int(index) for index in absolute_indices)
        if len(expected) != len(set(expected)) or set(expected) != {
            result.slide_index for result in results
        }:
            raise ValueError("authored_vqa_checkpoint_index_mapping_invalid")
    seen: set[int] = set()
    for result in results:
        if result.slide_index < 0 or result.slide_index in seen:
            raise ValueError("authored_vqa_checkpoint_index_mapping_invalid")
        seen.add(result.slide_index)
    admitted_results = [
        result for result in results if result.final_status == "admitted" and result.changed
    ]
    for result in admitted_results:
        validate_repair_result(result, expected_slide_index=result.slide_index)
    fencing_token = getattr(generation_job, "_workspace_fencing_token", None)
    for result in admitted_results:
        absolute_index = result.slide_index
        if persist_scene is not None:
            persist_scene(result)
        # The original illustrate/render checkpoint is already complete here.
        # Updating its HTML first while leaving the old render evidence pointer
        # in place creates an intentionally inconsistent tuple, so the following
        # render CAS rejects its own repair as checkpoint corruption. Publish the
        # repaired HTML/PNG/metrics together through the render mutation instead.
        await _complete_stage_after_external_success(
            job_id=generation_job.id,
            slide_index=absolute_index,
            stage="render",
            attempt_token=generation_job.attempt_token,
            fencing_token=fencing_token,
            html=result.html,
            png=result.png,
            browser_metrics=structural_checkpoint_metrics(result),
        )


@admission_scope
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
    stage_timings_ms = {stage: 0 for stage in _PERFORMANCE_STAGE_NAMES}
    timing_recorder = getattr(
        request, "_authored_generation_timing_recorder", None
    )

    def record_stage_timing(stage: str, value_ms: object) -> None:
        value = max(0, round(float(value_ms)))
        stage_timings_ms[stage] = value
        if timing_recorder is not None:
            timing_recorder.record_stage_ms(stage, value)

    def observe_deck_checkpoint(signal: object) -> None:
        if timing_recorder is not None and hasattr(signal, "to_dict"):
            timing_recorder.record_checkpoint_signal(signal.to_dict())

    render_runtime_before = get_render_runtime_counters()
    worker_handle_count_before = _worker_handle_count_metric()

    async def mark_stage(stage: str) -> None:
        object.__setattr__(request, "_authored_timing_stage", stage)
        object.__setattr__(
            request, "_authored_timing_stage_started_at", time.monotonic()
        )
        if (
            generation_job is not None
            and isinstance(generation_job.attempt_token, str)
            and generation_job.attempt_token
        ):
            await mark_authored_generation_stage(
                job_id=generation_job.id,
                attempt_token=generation_job.attempt_token,
                stage=stage,
            )

    canvas = request_canvas_contract(request)
    canvas_width_px = int(canvas.width_px)
    canvas_height_px = int(canvas.height_px)
    request._authored_local_fit_policy_revision = requested_font_policy_revision(
        request.instructions,
        policy_revision=getattr(request, "_authored_local_fit_policy_revision", None) or POLICY_REVISION,
    )
    render_kwargs = (
        {}
        if (canvas_width_px, canvas_height_px) == (1280, 720)
        else {"width_px": canvas_width_px, "height_px": canvas_height_px}
    )
    render_capture = partial(
        render_html_to_png_with_metrics,
        **render_kwargs,
    )
    cached_metrics = partial(
        get_cached_browser_metrics,
        **render_kwargs,
    )
    ensure_metrics = partial(
        ensure_browser_metrics,
        **render_kwargs,
    )
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
    await mark_stage("outline")
    outline_stage_started = _outline_stage_started_at(request)

    async def resolve_current_deck_plans() -> AuthoredDeckPlans:
        return await resolve_deck_plans(
            request, outline, brand, style, roles, resolve_infographic=False
        )

    with provider_usage_scope(
        job_id=generation_job.id if generation_job is not None else None,
        operation="authored_generation",
        stage="deck_plan",
        attempt_number=(generation_job.attempt_number if generation_job else 0),
    ):
        deck_plans, deck_plan_reused = (
            await resolve_with_authored_deck_plan_checkpoint(
                generation_job=generation_job,
                stage="deck_plan",
                input_contract=_deck_plan_checkpoint_input(
                    generation_job=generation_job,
                    request=request,
                    outline=outline,
                    brand=brand,
                    style=style,
                    stage="deck_plan",
                ),
                resolve=resolve_current_deck_plans,
                encode=_encode_deck_plans,
                decode=_decode_deck_plans,
                observe=observe_deck_checkpoint,
            )
        )
    outline = deck_plans.outline
    roles = deck_plans.roles
    chapter_plan: AuthoredChapterPlan | None = deck_plans.chapter_plan

    async def resolve_current_chapter_plan() -> AuthoredChapterPlan | None:
        return chapter_plan

    chapter_payload, chapter_plan_reused = (
        await resolve_with_authored_deck_plan_checkpoint(
            generation_job=generation_job,
            stage="chapter_plan",
            input_contract={
                "schemaVersion": "authored-chapter-plan-input/v1",
                "deckPlanDigest": canonical_checkpoint_digest(
                    _encode_deck_plans(deck_plans)
                ),
            },
            resolve=resolve_current_chapter_plan,
            encode=lambda value: _encode_optional_model(
                "authored-chapter-plan/v1", "chapterPlan", value
            ),
            decode=lambda payload: (
                AuthoredChapterPlan.model_validate(payload.get("chapterPlan"))
                if isinstance(payload.get("chapterPlan"), dict)
                else None
            ),
            observe=observe_deck_checkpoint,
        )
    )
    if chapter_payload != chapter_plan:
        raise AuthoredDeckPlanCheckpointError(
            "authored_chapter_checkpoint_deck_plan_mismatch"
        )
    LOGGER.info(
        "[authored] deck checkpoint reuse: deck=%s chapter=%s",
        deck_plan_reused,
        chapter_plan_reused,
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
    # A plain one-slide request has no cross-slide header decision to make.
    # Reuse the deterministic safe fallback and save a separate provider pass;
    # explicit instructions, source-verbatim work, and exact reconstruction
    # still use the full contract resolver because their chrome is meaningful.
    # Start only after every existing-presentation/admission early exit. The
    # illustration and header calls now read the same final outline in parallel,
    # but no provider task survives an already-published return.
    async def resolve_current_infographic_plan() -> AuthoredInfographicPlan | None:
        return await resolve_final_outline_infographic_plan(
            request, outline, brand, style, roles
        )

    async def resolve_checkpointed_infographic_plan() -> tuple[
        AuthoredInfographicPlan | None, bool
    ]:
        # The checkpoint stage keeps its historical name: the figure plan is
        # what now decides the deck's pictures, and a resumed attempt reads the
        # same stage row it always did.
        with provider_usage_scope(
            job_id=generation_job.id if generation_job is not None else None,
            operation="authored_generation",
            stage="illustration",
            attempt_number=(generation_job.attempt_number if generation_job else 0),
        ):
            return await resolve_with_authored_deck_plan_checkpoint(
                generation_job=generation_job,
                stage="illustration",
                input_contract=_deck_plan_checkpoint_input(
                    generation_job=generation_job,
                    request=request,
                    outline=outline,
                    brand=brand,
                    style=style,
                    stage="illustration",
                ),
                resolve=resolve_current_infographic_plan,
                encode=lambda value: _encode_optional_model(
                    "authored-infographic-plan/v1", "infographicPlan", value
                ),
                decode=lambda payload: (
                    AuthoredInfographicPlan.model_validate(
                        payload.get("infographicPlan")
                    )
                    if isinstance(payload.get("infographicPlan"), dict)
                    else None
                ),
                observe=observe_deck_checkpoint,
            )

    infographic_plan_task = asyncio.create_task(
        resolve_checkpointed_infographic_plan()
    )
    use_single_slide_header_fast_path = _use_single_slide_header_fast_path(
        request, outline, style
    )
    if use_single_slide_header_fast_path:
        async def resolve_current_header_contract() -> AuthoredHeaderContract:
            return default_header_contract(
                primary=getattr(brand, "primary", None) or "#2563EB",
                chapter_breadcrumb=False,
            )

        header_contract, header_reused = (
            await resolve_with_authored_deck_plan_checkpoint(
                generation_job=generation_job,
                stage="header",
                input_contract=_deck_plan_checkpoint_input(
                    generation_job=generation_job,
                    request=request,
                    outline=outline,
                    brand=brand,
                    style=style,
                    stage="header",
                ),
                resolve=resolve_current_header_contract,
                encode=lambda value: _encode_optional_model(
                    "authored-header-contract/v1", "headerContract", value
                ),
                decode=lambda payload: AuthoredHeaderContract.model_validate(
                    payload.get("headerContract")
                ),
                observe=observe_deck_checkpoint,
            )
        )
    else:
        async def resolve_current_header_contract() -> AuthoredHeaderContract:
            return await resolve_header_contract(
                outline,
                brand,
                style,
                instructions=request.instructions,
                chapter_breadcrumb=chapter_plan is not None,
            )

        with provider_usage_scope(
            job_id=generation_job.id if generation_job is not None else None,
            operation="authored_generation",
            stage="header",
            attempt_number=(generation_job.attempt_number if generation_job else 0),
        ):
            header_contract, header_reused = (
                await resolve_with_authored_deck_plan_checkpoint(
                    generation_job=generation_job,
                    stage="header",
                    input_contract=_deck_plan_checkpoint_input(
                        generation_job=generation_job,
                        request=request,
                        outline=outline,
                        brand=brand,
                        style=style,
                        stage="header",
                    ),
                    resolve=resolve_current_header_contract,
                    encode=lambda value: _encode_optional_model(
                        "authored-header-contract/v1", "headerContract", value
                    ),
                    decode=lambda payload: AuthoredHeaderContract.model_validate(
                        payload.get("headerContract")
                    ),
                    observe=observe_deck_checkpoint,
                )
            )
    infographic_plan, infographic_reused = await infographic_plan_task
    # ``roles`` turns the free-composition deck's picture budget on (design G4):
    # without them the call is byte-identical to the pre-programme behaviour.
    illustration_plan: AuthoredIllustrationPlan | None = derive_illustration_plan(
        infographic_plan, roles
    )
    LOGGER.info(
        "[authored] deck checkpoint reuse: header=%s infographic=%s",
        header_reused,
        infographic_reused,
    )
    outline_elapsed = time.monotonic() - outline_stage_started
    record_stage_timing("outline", outline_elapsed * 1000)
    await _record_deck_stage_latency(
        generation_job, stage="outline", elapsed_seconds=outline_elapsed
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
    source_text_bindings = (
        _source_block_bindings(request)
        if source_reflow and composition_profile.text_mode == "verbatim"
        else None
    )
    source_image_bindings = (
        _source_image_block_bindings(request) if source_reflow else None
    )
    if source_reflow and (
        source_text_bindings is None or source_image_bindings is None
    ):
        raise AuthoredStageError(
            slide_index=0,
            stage="author",
            code="source_content_browser_readback_failed",
            retryable=False,
            message="source bindings are unavailable before Chromium render",
        )

    configure_admission(
        request=request, presentation_id=presentation_id, generation_job=generation_job,
        profile=composition_profile, reference_mode=normalize_reference_mode(getattr(style, "reference_mode", STYLE_REFERENCE)),
        slide_count=len(outline.slides),
        bindings=source_text_bindings, image_bindings=source_image_bindings,
    )

    async def validate_source_repair_candidate(
        slide_index: int,
        candidate_html: str,
        candidate_metrics: Mapping[str, object],
    ) -> None:
        """Reject a quality patch before it can publish broken source evidence."""

        if not source_reflow or source_text_bindings is None:
            return
        source_candidate(
            candidate_html, index=slide_index, metrics=candidate_metrics,
            manifest=request._source_content_manifest, outline=request._source_document_outline,
            bindings=source_text_bindings[slide_index],
        )
    pipeline_kwargs = {
        "header_contract": header_contract,
        # A restyle reproduces the source's own page numbers, so the source
        # decides whether this deck is numbered. The contract call reads a
        # content sample and judges the deck's design, which on identical
        # input answered False and instructed every slide to render no page
        # number and no footer -- exactly the source text it then failed on.
        "page_number_mode": (
            _TEMPLATE_PAGE_NUMBER_MODE
            if source_pages_are_numbered(source_text_bindings)
            else page_number_mode_for(header_contract)
        ),
        "chapter_plan": chapter_plan,
        "illustration_plan": illustration_plan,
        "instructions": request.instructions,
        **job_kwargs,
    }
    # Keep narrow legacy/test doubles source-compatible while the product
    # pipeline consumes the frozen composition profile and the figure plan.
    if _callable_accepts_keyword(
        _author_illustrate_render_pipeline, "composition_profile"
    ):
        pipeline_kwargs["composition_profile"] = composition_profile
    if _callable_accepts_keyword(
        _author_illustrate_render_pipeline, "infographic_plan"
    ):
        pipeline_kwargs["infographic_plan"] = infographic_plan
    if _callable_accepts_keyword(
        _author_illustrate_render_pipeline, "local_fit_policy_revision"
    ):
        pipeline_kwargs["local_fit_policy_revision"] = getattr(
            request, "_authored_local_fit_policy_revision", None
        )
    if _callable_accepts_keyword(_author_illustrate_render_pipeline, "canvas"):
        pipeline_kwargs["canvas"] = canvas
    if source_text_bindings is not None:
        pipeline_kwargs["source_block_bindings"] = source_text_bindings
    if source_image_bindings is not None:
        pipeline_kwargs["source_image_block_bindings"] = source_image_bindings
    await mark_stage("author")
    pipeline = await _author_illustrate_render_pipeline(
        outline,
        brand,
        style,
        **pipeline_kwargs,
    )
    record_stage_timing("author", max(0.0, pipeline.author_work) * 1000)
    record_stage_timing(
        "illustration", max(0.0, pipeline.illustration_work) * 1000
    )
    record_stage_timing("render", max(0.0, pipeline.render_work) * 1000)
    record_stage_timing("quality", max(0.0, pipeline.quality_work) * 1000)
    for observed_stage, elapsed in (
        ("author", pipeline.author_work),
        ("illustration", pipeline.illustration_work),
        ("render", pipeline.render_work),
    ):
        await _record_deck_stage_latency(
            generation_job,
            stage=observed_stage,
            elapsed_seconds=max(0.0, elapsed),
        )
    authored_deck = pipeline.deck
    # Persist stable edit targets for future local quality-review patches. Attribute
    # insertion is deterministic and has no visual effect on the already-rendered PNG.
    semantic_mode = authored_semantic_dual_output_mode()
    reference_mode = normalize_reference_mode(
        getattr(style, "reference_mode", STYLE_REFERENCE)
    )
    scene_envelope = getattr(pipeline, "scene_envelope", None)
    if scene_envelope is None:
        # Compatibility for narrow adapters only. The product pipeline always
        # seals before Chromium and supplies this immutable authority.
        if reference_mode != EXACT_RECONSTRUCTION:
            compatibility_htmls = [
                canonicalize_authored_roles(html)
                for html in authored_deck.htmls
            ]
        elif semantic_mode == "declared":
            compatibility_htmls = list(authored_deck.htmls)
        else:
            compatibility_htmls = [
                ensure_authored_qids(html) for html in authored_deck.htmls
            ]
        scene_envelope = seal_deck_scene_envelope(
            seal_slide_scene_envelope(html, slide_index=index)
            for index, html in enumerate(compatibility_htmls)
        )
    htmls = list(scene_envelope.html_projections)
    if source_reflow and composition_profile.text_mode == "verbatim":
        try:
            _require_source_verbatim_bindings(htmls, source_text_bindings)
        except BrowserSourceReadbackError as error:
            raise AuthoredStageError(
                slide_index=(error.slide_indices[0] if error.slide_indices else 0),
                stage="author",
                code=error.code,
                cause=error,
                retryable=False,
                message=error.detail,
            ) from error
    pngs = pipeline.pngs
    local_fit_receipts = [
        dict(item)
        for item in getattr(pipeline, "local_fit_receipts", ())
        if isinstance(item, Mapping) and item
    ]
    if local_fit_applies(
        reference_mode=reference_mode, source_reflow=source_reflow
    ) and len(local_fit_receipts) != len(htmls):
        raise AuthoredStageError(
            slide_index=0,
            stage="render",
            code="authored_local_fit_evidence_incomplete",
            retryable=False,
        )

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
    source_visibility_by_slide = {}
    if source_reflow:
        for index, original_html in enumerate(htmls):
            latest = (
                pipeline.evidence_ledger.latest(pipeline.evidence_attempt_id, index)
                if getattr(pipeline, "evidence_ledger", None) is not None
                and getattr(pipeline, "evidence_attempt_id", None) else None
            )
            original_metrics = getattr(latest, "browser_metrics", None)
            if original_metrics is None:
                original_metrics = cached_metrics(original_html)
            if original_metrics is None:
                continue
            source_issues = inspect_slide_source_visibility(
                manifest=request._source_content_manifest,
                outline=request._source_document_outline,
                slide_index=index, html=original_html, metrics=original_metrics,
            )
            if not source_issues:
                continue
            source_visibility_by_slide[index] = source_issues
            issues = [dict(issue.to_dict(), message=issue.detail) for issue in source_issues]
            failure = next((item for item in gate_failures if item["slide_index"] == index), None)
            if failure is None:
                gate_failures.append({"slide_index": index, "issues": issues})
            else:
                failure["issues"] = [*failure.get("issues", ()), *issues]
    preserved_local_fit_indices: set[int] = set()
    if local_fit_receipts:
        for index, (html, receipt) in enumerate(
            zip(htmls, local_fit_receipts, strict=True)
        ):
            if receipt.get("finalHtmlDigest") != (
                "sha256:" + sha256(html.encode("utf-8")).hexdigest()
            ):
                raise AuthoredStageError(
                    slide_index=index,
                    stage="render",
                    code="authored_local_fit_post_acceptance_mutation",
                    retryable=False,
                )
            if receipt_is_preserved_original(receipt):
                preserved_local_fit_indices.add(index)

    async def prepare_post_acceptance_repair(
        index: int, admitted: tuple[str, bytes, object]
    ) -> tuple[str, bytes, object] | None:
        """Re-accept changed repair output before it replaces a fitted tuple."""

        selected_html, selected_png, selected_metrics = admitted
        if not local_fit_receipts or local_fit_receipts[index].get(
            "finalHtmlDigest"
        ) == html_digest(selected_html):
            return selected_html, selected_png, selected_metrics
        try:
            fitted = await _fit_content_revision(
                selected_html,
                slide_index=index,
                policy_revision=getattr(
                    request, "_authored_local_fit_policy_revision", None
                ),
                canvas=canvas,
            )
        except AuthoredLocalFitRejected:
            return None
        local_fit_receipts[index] = fitted.receipt
        cache_accepted_capture(
            fitted, timeout=60,
            width_px=canvas_width_px, height_px=canvas_height_px,
        )
        return fitted.html, fitted.png, fitted.metrics

    source_repair_fit_receipts: dict[tuple[int, str], dict] = {}

    async def capture_source_repair_candidate(
        candidate_html: str, slide_index: int,
    ) -> tuple[str, bytes, Mapping[str, object]]:
        # Local fit must precede admission. Changing an admitted candidate here
        # would invalidate the source/composition evidence and its saved digest.
        if local_fit_receipts:
            fitted = await _fit_content_revision(
                candidate_html, slide_index=slide_index,
                policy_revision=getattr(request, "_authored_local_fit_policy_revision", None),
                canvas=canvas,
            )
            source_repair_fit_receipts[(slide_index, digest_html(fitted.html))] = fitted.receipt
            cache_accepted_capture(
                fitted, timeout=60,
                width_px=canvas_width_px, height_px=canvas_height_px,
            )
            return fitted.html, fitted.png, fitted.metrics
        canonical = ensure_authored_semantic_roles(ensure_authored_qids(candidate_html))
        capture = await capture_once(canonical, index=slide_index, capture=render_capture)
        if (
            capture.prepared_html is None
            or capture.png == _placeholder_png()
            or capture.browser_metrics is None
        ):
            raise RuntimeError("source repair browser evidence unavailable")
        return capture.prepared_html, capture.png, capture.browser_metrics

    author_provider_attempt_counts = [
        int(value)
        for value in getattr(pipeline, "author_provider_attempt_counts", ())
    ]
    has_author_provider_attempt_counts = (
        len(author_provider_attempt_counts) == len(htmls)
    )
    # Decoration geometry gets the same one-cycle, immutable Scene repair as
    # existing gate failures, even when the text-only generation gate passed.
    containment_plans = {}
    if not source_reflow and reference_mode != EXACT_RECONSTRUCTION:
        for index, original_html in enumerate(htmls):
            if index in preserved_local_fit_indices:
                continue
            latest = (
                pipeline.evidence_ledger.latest(pipeline.evidence_attempt_id, index)
                if getattr(pipeline, "evidence_ledger", None) is not None
                and getattr(pipeline, "evidence_attempt_id", None) else None
            )
            original_metrics = getattr(latest, "browser_metrics", None)
            if original_metrics is None:
                original_metrics = cached_metrics(original_html)
            plan = plan_decoration_containment(
                original_html, original_metrics,
                profile_id=composition_profile.profile_id, reference_mode=reference_mode,
            )
            if plan is None:
                continue
            if plan.candidate == original_html:
                raise AuthoredStageError(
                    slide_index=index, stage="render",
                    code="authored_decoration_containment_unavailable", retryable=False,
                    message="No bounded decoration placement preserves protected content",
                )
            containment_plans[index] = plan
            containment_issue = {
                "category": "clipping", "severity": "high", "qids": list(plan.targets),
                "message": "decoration containment repair required",
            }
            existing_failure = next(
                (failure for failure in reversed(gate_failures) if failure["slide_index"] == index), None
            )
            if existing_failure is not None:
                existing_failure["issues"] = [*existing_failure.get("issues", ()), containment_issue]
            else:
                gate_failures.append({"slide_index": index, "issues": [containment_issue]})
    # Accepted local-fit tuples with a measured whole-page visual warning are
    # deliberately published unchanged. Their page flag is the user-facing
    # recovery path, so they must not enter either automatic remake path.
    if preserved_local_fit_indices:
        gate_failures = [
            item
            for item in gate_failures
            if int(item["slide_index"]) not in preserved_local_fit_indices
        ]

    initial_gate_signatures = {
        signature
        for item in gate_failures
        if (signature := _gate_failure_signature(htmls, item)) is not None
    }
    repaired_gate_indices: list[int] = []
    # D7: one predicate decides for the whole attempt whether a terminal slide
    # may be published flagged, so the claim loop, the bounded repair round and
    # the final gate cannot disagree about this deck.
    terminal_fallback_enabled = terminal_fallback_active(
        profile=composition_profile,
        reference_mode=reference_mode,
        source_reflow=source_reflow,
    )
    predeclared_terminal: list[dict[str, object]] = []
    remake_counts: dict[int, int] = {}
    # Rev-3 2.5: with the knob on, the four post-author repair paths collapse
    # into one bounded loop on the verbatim path. Off, every path below runs as
    # it does today, through the same admission gate.
    single_loop = single_repair_loop_enabled(source_reflow=source_reflow)
    vqa_evidence: dict[int, object] = {}
    await mark_stage("repair")
    repair_started = time.monotonic()
    repair_claims: dict[int, object] = {}
    if gate_failures:
        flagged = sorted({int(item["slide_index"]) for item in gate_failures})
        if generation_job is not None:
            failures_by_index = {
                int(item["slide_index"]): item for item in gate_failures
            }
            # D7: a slide whose single durable repair was already spent by an
            # earlier attempt is declared terminal and dropped from the bounded
            # round (it has no claim, so its original triple stays untouched);
            # it reaches the remake-then-flag fallback below instead of killing
            # the deck. A deck that may not publish flagged keeps today's raise.
            claim_outcome = claim_bounded_repairs(
                flagged,
                claim=lambda index: claim_scene_repair(
                    generation_job.id,
                    scene_envelope.slides[index],
                    error_code="authored_composition_safety_gate_failed",
                    issues=failures_by_index[index].get("issues", ()),
                    max_claims=(MAX_REMAKES_PER_SLIDE if terminal_fallback_enabled else 1),
                ),
                budget_exhausted=SceneRepairBudgetExhausted,
                profile=composition_profile,
                reference_mode=reference_mode,
                source_reflow=source_reflow,
            )
            if claim_outcome.blocked is not None:
                blocked_index, blocked_error = claim_outcome.blocked
                raise AuthoredStageError(
                    slide_index=blocked_index,
                    stage="render",
                    code="authored_composition_safety_gate_failed",
                    cause=blocked_error,
                    retryable=False,
                    message=(
                        "The same Scene digest and quality error repeated "
                        "after the single slide repair"
                        if blocked_error.repeated
                        else "The immutable Scene slide repair budget is exhausted"
                    ),
                ) from blocked_error
            repair_claims.update(claim_outcome.claims)
            predeclared_terminal.extend(claim_outcome.predeclared)
            flagged = claim_outcome.flagged
        if terminal_fallback_enabled and not source_reflow:
            # D7 remake-first: on a deck that may publish a flagged slide, a
            # fresh composition briefed with the gate's own defects beats
            # patching the failing HTML -- bench run 10 measured the patch
            # round at 0 of 6 and the remake at 4 of 8. Everything below then
            # runs only for the slides the remake did not rescue.
            remake_first = await remake_first_flagged_slides(
                flagged=flagged, gate_failures=gate_failures, htmls=htmls,
                pngs=pngs, contents=contents, roles=roles, brand=brand,
                design_system=authored_deck.design_system, style=style,
                capacity_plan=capacity_plan, profile=composition_profile,
                reference_mode=reference_mode, scene_envelope=scene_envelope,
                metrics_by_slide=revised_metrics_by_slide,
                chapter_plan=chapter_plan, illustration_plan=illustration_plan,
                infographic_plan=infographic_plan,
                instructions=request.instructions, generation_job=generation_job,
                prepare_admitted=prepare_post_acceptance_repair,
            )
            scene_envelope = remake_first.scene_envelope
            flagged = remake_first.remaining_flagged
            remake_counts.update(remake_first.remake_counts)
            # H3's deterministic rung and the remake both produce a repaired
            # slide; only the remake spent a provider call and a remake budget.
            repaired_gate_indices = [
                *remake_first.remade_slide_indices,
                *remake_first.text_scaled_slide_indices,
            ]
        repair_kwargs: dict[str, object] = {
            "slide_indices": flagged,
            "total_slides": len(htmls),
            # One durable repair claim owns a bounded two-step correction:
            # the second proposal sees the first patch's freshly measured DOM
            # and can finish multi-defect slides without replaying the whole
            # operation. Each slide remains isolated and runs concurrently.
            "max_cycles": 2,
            "style": style,
            "return_details": True,
            # Gate recovery is never allowed to return a free-form replacement
            # document. It may only use the existing structured QID patch path
            # or the deterministic reducers below.
            "local_patch_only": True,
            "fail_on_residual_dom_issues": not (
                source_reflow and terminal_fallback_enabled
            ),
            "allow_patch_proposal_retry": False,
            "candidate_validator": validate_source_repair_candidate,
            "capacity_briefs": [capacity_briefs[index] for index in flagged],
            "initial_critiques": _capacity_gate_initial_critiques(
                gate_failures,
                flagged,
                roles,
                allow_text_reduction=composition_profile.allow_summarization,
            ),
        }
        # Gate repair owns a separate, durable one-call budget. Reusing the
        # author-stage count here blocked quality repair for every slide whose
        # two author calls had already been spent, even though its repair claim
        # had just been acquired. The claim, not authoring history, is the call
        # authority and remains one-shot across workers and retries.
        provider_recovery_allowed = [
            index in repair_claims for index in flagged
        ]
        repair_kwargs["provider_recovery_allowed"] = provider_recovery_allowed
        repair_kwargs.update(
            replayed_plan_kwargs(
                flagged, chapter_plan=chapter_plan,
                illustration_plan=illustration_plan,
                infographic_plan=infographic_plan,
            )
        )
        frozen_gate_inputs: dict[int, tuple[str, bytes, object | None, object | None]] = {}
        for absolute_index in flagged:
            latest = (
                pipeline.evidence_ledger.latest(
                    pipeline.evidence_attempt_id,
                    absolute_index,
                )
                if getattr(pipeline, "evidence_ledger", None) is not None
                and getattr(pipeline, "evidence_attempt_id", None)
                else None
            )
            original_metrics = getattr(latest, "browser_metrics", None)
            if original_metrics is None:
                original_metrics = cached_metrics(htmls[absolute_index])
            if source_reflow and (
                not isinstance(original_metrics, Mapping)
                or any(
                    isinstance(item, Mapping)
                    and (item.get("source_block_id") or item.get("sourceBlockId"))
                    and "source_clip_bounds" not in item
                    for item in original_metrics.get("elements", ())
                )
            ):
                # Older checkpoints predate exact clipping intersections.
                # Refresh only this failed slide, retaining its exact HTML.
                capture = await capture_once(htmls[absolute_index], index=absolute_index, capture=render_capture)
                if capture.prepared_html != htmls[absolute_index]:
                    raise AuthoredStageError(
                        slide_index=absolute_index, stage="render",
                        code="authored_local_fit_post_acceptance_mutation", retryable=False,
                    )
                if capture.png == _placeholder_png() or capture.browser_metrics is None:
                    raise AuthoredStageError(
                        slide_index=absolute_index, stage="render",
                        code="authored_composition_safety_gate_failed", retryable=True,
                        message="Chromium telemetry unavailable before source repair",
                    )
                original_metrics = capture.browser_metrics
                pngs[absolute_index] = capture.png
            frozen_gate_inputs[absolute_index] = (
                htmls[absolute_index],
                pngs[absolute_index],
                original_metrics,
                getattr(latest, "capture_policy", None),
            )
        structural_results: list[SlideRepairResult] = []
        structural_budgets = {
            index: StructuralRepairBudget(
                max_candidate_captures=3,
                max_provider_transmissions=min(
                    2, int(repair_kwargs.get("max_cycles", 2))
                ),
            )
            for index in flagged
        }
        if source_reflow:
            manifest = request._source_content_manifest
            source_outline = request._source_document_outline
            manifest = manifest.model_dump(mode="json", by_alias=True) if hasattr(manifest, "model_dump") else manifest
            source_outline = source_outline.model_dump(mode="json", by_alias=True) if hasattr(source_outline, "model_dump") else source_outline
            source_units = {
                str(unit.get("sourceUnitId") or unit.get("source_unit_id") or ""): unit
                for unit in manifest.get("units", ())
            }
            evaluators = {}
            for index in flagged:
                source_slide = source_outline["slides"][index]
                source_unit = source_units.get(str(
                    source_slide.get("sourceUnitId") or source_slide.get("source_unit_id") or ""
                ))
                if source_unit is None:
                    raise BrowserSourceReadbackError(
                        "source_content_browser_readback_failed",
                        detail="source unit unavailable during structural repair",
                        slide_indices=(index,),
                    )

                async def composition_validator(candidate_html, metrics, *, absolute_index=index):
                    # A candidate that drops bound source text is rejected here,
                    # never raised: propagating ended the whole deck over one
                    # unusable repair candidate (validation run-14).
                    try:
                        with producer_scope("structural_patch"):
                            await validate_source_repair_candidate(
                                absolute_index, candidate_html, metrics,
                            )
                    except BrowserSourceReadbackError as error:
                        return source_verbatim_rejection(error)
                    capacity_errors = capacity_violations(
                        candidate_html, metrics, capacity_plan.at(absolute_index)
                    )
                    report = inspect_authored_dom(
                        candidate_html, metrics, reference_mode=reference_mode,
                        strict_structural_off_canvas=(composition_profile.profile_id != "ordinary"),
                    )
                    defects = blocking_safety_issues(report)
                    return {
                        "passed": not capacity_errors and not defects and report.telemetry_status == "available",
                        "violations": [
                            *({"category": code, "severity": "high"} for code in capacity_errors),
                            *(issue.to_dict() for issue in defects),
                        ],
                        "telemetryStatus": report.telemetry_status,
                        "contract_violations": [
                            code for code in capacity_errors
                            if any(marker in code for marker in (
                                "missing", "unverified", "mismatch", "duplicate", "invalid",
                            )) or code.startswith(("font:", "supporting_font:", "images:", "browser_images:"))
                        ],
                    }

                measured_dom = _structural_compact_dom(htmls[index], frozen_gate_inputs[index][2])
                original_report = inspect_authored_dom(
                    htmls[index], frozen_gate_inputs[index][2],
                    reference_mode=reference_mode, strict_structural_off_canvas=True,
                )
                repair_issue_qids = tuple(dict.fromkeys(
                    qid
                    for issue in (
                        *blocking_safety_issues(original_report),
                        *source_visibility_by_slide.get(index, ()),
                    )
                    for qid in issue.qids
                ))
                repair_scopes = build_structural_repair_scopes(
                    measured_dom,
                    issue_qids=tuple(dict.fromkeys((
                        *repair_issue_qids,
                        *_measured_structural_issue_qids(
                            frozen_gate_inputs[index][2], issue_qids=repair_issue_qids,
                        ),
                    ))),
                    html=htmls[index],
                )
                protected_qids = protected_qids_for_repair_scopes(
                    measured_dom, repair_scopes,
                )
                evaluators[index] = make_source_repair_evaluator(
                    manifest={"units": [source_unit]}, outline={"slides": [source_slide]},
                    baseline_html=htmls[index], baseline_metrics=frozen_gate_inputs[index][2],
                    composition_validator=composition_validator,
                    context={"slide_index": index, "profile_id": composition_profile.profile_id,
                             "reference_mode": reference_mode, "protected_qids": protected_qids},
                )

            repair_kwargs.pop("candidate_validator", None)
            repair_kwargs.update(
                candidate_evaluators=evaluators,
                structural_results_sink=structural_results,
                structural_candidate_capture=capture_source_repair_candidate,
                initial_browser_metrics=[frozen_gate_inputs[index][2] for index in flagged],
                structural_budgets=structural_budgets,
            )
        deterministic_gate_candidates: dict[int, str] = {}
        for local_index, absolute_index in enumerate(flagged):
            original_html, _png, metrics, capture_policy = frozen_gate_inputs[
                absolute_index
            ]
            if metrics is None:
                # Provider repair cannot compensate for missing Chromium
                # authority.  Recollect on a later operation attempt instead
                # of spending a provider call against an unmeasured slide.
                provider_recovery_allowed[local_index] = False
                raise AuthoredStageError(
                    slide_index=absolute_index,
                    stage="render",
                    code="authored_composition_safety_gate_failed",
                    retryable=True,
                    message="Chromium telemetry is unavailable before repair preflight",
                )
            deterministic = containment_plans[absolute_index].candidate if absolute_index in containment_plans else _deterministic_repair_candidate(
                html=original_html,
                browser_metrics=metrics,
                capacity=capacity_plan.at(absolute_index),
                header_contract=header_contract,
                role=roles[absolute_index],
                profile_id=composition_profile.profile_id,
                allow_immutable_checkpoint_geometry=(capture_policy == "hydrated"),
                repair_intrinsic_grid_overlap=source_reflow,
            )
            deterministic_changed = digest_html(deterministic) != digest_html(original_html)
            preflight = preflight_authored_provider_repair(
                slide_index=absolute_index,
                artifact=AuthoredSlideArtifact(
                    html=original_html,
                    png=_png,
                    browser_metrics=metrics,
                ),
                capacity=capacity_plan.at(absolute_index),
                profile_id=composition_profile.profile_id,
                reference_mode=reference_mode,
            )
            # The exact preflight decision is the provider-call authority. A
            # deterministic or terminal slide may still use the local reducer
            # below, but it cannot consume a provider recovery slot.
            provider_recovery_allowed[local_index] = bool(
                provider_recovery_allowed[local_index]
                and preflight.provider_call_allowed
            )
            if (
                preflight.terminal
                and not deterministic_changed
                and preflight.code != "authored_composition_safety_gate_failed"
            ):
                LOGGER.warning(
                    "[authored] terminal repair preflight slide=%d code=%s violations=%s",
                    absolute_index + 1,
                    preflight.code,
                    list(preflight.violations),
                )
                raise AuthoredStageError(
                    slide_index=absolute_index,
                    stage="render",
                    code=(
                        preflight.code
                        or "authored_deterministic_preflight_failed"
                    ),
                    retryable=False,
                    message=(
                        "deterministic preflight blocked provider repair: "
                        + ", ".join(preflight.violations)
                    ),
                )
            deterministic_gate_candidates[absolute_index] = deterministic
            if deterministic_changed and not source_reflow:
                # A deterministic local candidate needs only the one bound
                # Chromium recapture below, not a provider recovery.
                provider_recovery_allowed[local_index] = False
        if single_loop and getattr(request, "vision_qa", False):
            # Vision review feeds the loop's one prompt; it writes no HTML.
            vqa_evidence = await vision_evidence_for_flagged(
                flagged=flagged, pngs=pngs, contents=contents,
                reference_mode=reference_mode,
                job_id=(generation_job.id if generation_job is not None else None),
                attempt_number=(generation_job.attempt_number if generation_job else 0),
            )
        try:
            with provider_usage_scope(
                job_id=(generation_job.id if generation_job is not None else None),
                operation="authored_generation",
                stage="repair",
                attempt_number=(generation_job.attempt_number if generation_job else 0),
            ):
                if single_loop:
                    revised_htmls, fixed = [], []
                    structural_results.extend(
                        await run_verbatim_repair_loop(
                            flagged=flagged, htmls=htmls, pngs=pngs, roles=roles,
                            contents=contents, brand=brand,
                            design_system=authored_deck.design_system,
                            capacity_briefs=capacity_briefs,
                            frozen_gate_inputs=frozen_gate_inputs,
                            deterministic_by_index=deterministic_gate_candidates,
                            provider_allowed=provider_recovery_allowed,
                            evaluators=evaluators,
                            capture=capture_source_repair_candidate,
                            reference_mode=reference_mode,
                            source_issues_by_index=source_visibility_by_slide,
                            vqa_evidence=vqa_evidence,
                            plans=(chapter_plan, illustration_plan, infographic_plan),
                            page_number_mode=pipeline_kwargs["page_number_mode"],
                            instructions=request.instructions,
                            source_images_by_slide=offered_for_review,
                            generation_job=generation_job,
                        )
                    )
                else:
                    revised_htmls, fixed = await _revise_gate_repair_candidates(
                        flagged=flagged,
                        htmls=htmls,
                        pngs=pngs,
                        contents=contents,
                        roles=roles,
                        deterministic_candidates=deterministic_gate_candidates,
                        brand=brand,
                        design_system=authored_deck.design_system,
                        repair_kwargs=repair_kwargs,
                        terminal_fallback_enabled=terminal_fallback_enabled,
                    )
            # Bind the repaired HTML, pixels, and Chromium readback atomically.
            # All deterministic mutations above form one candidate and consume
            # exactly one recapture for each failed slide.
            if source_reflow:
                results_by_index = {result.slide_index: result for result in structural_results}
                if len(results_by_index) != len(flagged) or set(results_by_index) != set(flagged):
                    raise ValueError("authored_vqa_checkpoint_index_mapping_invalid")
                for index in flagged:
                    result = results_by_index[index]
                    if result.final_status == "admitted":
                        validate_repair_result(result, expected_slide_index=index)
                revised_htmls = [results_by_index[index].html for index in flagged]
                revised_pngs = [results_by_index[index].png for index in flagged]
                revised_metrics = [results_by_index[index].metrics for index in flagged]
            else:
                revised_htmls, revised_pngs, revised_metrics = (
                    await _bind_bounded_repair_evidence(revised_htmls, list(flagged))
                )
            admitted_repairs: list[tuple[int, str, bytes, object]] = []
            for local_index, absolute_index in enumerate(flagged):
                (
                    original_html,
                    original_png,
                    original_metrics,
                    _capture_policy,
                ) = frozen_gate_inputs[absolute_index]
                if source_reflow:
                    result = results_by_index[absolute_index]
                    if result.final_status != "admitted":
                        predeclared_terminal.append(
                            declare_unadmitted_gate_repair(absolute_index)
                        )
                        continue
                    if local_fit_receipts and digest_html(result.html) != digest_html(original_html):
                        receipt = source_repair_fit_receipts.get((absolute_index, digest_html(result.html)))
                        if receipt is None:
                            raise AuthoredStageError(
                                slide_index=absolute_index, stage="render",
                                code="authored_local_fit_post_acceptance_mutation", retryable=False,
                            )
                        local_fit_receipts[absolute_index] = receipt
                    admitted_repairs.append((absolute_index, result.html, result.png, result.metrics))
                    continue
                containment_plan = containment_plans.get(absolute_index)
                if containment_plan is not None and not validate_decoration_containment(
                    containment_plan, original_html, original_metrics,
                    revised_htmls[local_index], revised_metrics[local_index],
                ):
                    raise AuthoredStageError(
                        slide_index=absolute_index, stage="render",
                        code="authored_decoration_containment_failed", retryable=False,
                        message="Fresh decoration repair changed protected geometry or visibility",
                    )
                selected_html, selected_png, selected_metrics, admitted = (
                    _admit_gate_repair_capacity_candidate(
                        slide_index=absolute_index,
                        original_html=original_html,
                        original_png=original_png,
                        original_metrics=original_metrics,
                        candidate_html=revised_htmls[local_index],
                        candidate_png=revised_pngs[local_index],
                        candidate_metrics=revised_metrics[local_index],
                        capacity=capacity_plan.at(absolute_index),
                        terminal_fallback_enabled=terminal_fallback_enabled,
                        allow_primary_title_shortening=(
                            composition_profile.profile_id == "ordinary"
                            and composition_profile.allow_summarization
                            and "data-source-block-id"
                            not in original_html.lower()
                        ),
                    )
                )
                if not admitted or selected_metrics is None:
                    # D7 layout defect only: the admission raises on everything else.
                    predeclared_terminal.append(declare_unadmitted_gate_repair(absolute_index))
                    continue
                prepared = await prepare_post_acceptance_repair(
                    absolute_index,
                    (selected_html, selected_png, selected_metrics),
                )
                if prepared is None:
                    predeclared_terminal.append(
                        declare_unadmitted_gate_repair(absolute_index)
                    )
                    continue
                selected_html, selected_png, selected_metrics = prepared
                admitted_repairs.append(
                    (
                        absolute_index,
                        selected_html,
                        selected_png,
                        selected_metrics,
                    )
                )
            fixed_local_indices = {int(index) for index in fixed}
            fixed_local_indices.update(range(len(flagged)))
            for absolute_index, selected_html, selected_png, selected_metrics in admitted_repairs:
                htmls[absolute_index] = selected_html
                pngs[absolute_index] = selected_png
                revised_metrics_by_slide[absolute_index] = selected_metrics
                scene_envelope = replace_deck_scene_slide(
                    scene_envelope,
                    slide_index=absolute_index,
                    html_projection=selected_html,
                )
                if generation_job is not None:
                    claim = repair_claims.get(absolute_index)
                    if claim is not None:
                        persist_repaired_scene_envelope(
                            generation_job.id,
                            scene_envelope.slides[absolute_index],
                            claim,
                        )
                    if generation_job.attempt_token:
                        await _complete_stage_after_external_success(
                            job_id=generation_job.id,
                            slide_index=absolute_index,
                            stage="render",
                            attempt_token=generation_job.attempt_token,
                            fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
                            html=selected_html,
                            png=selected_png,
                            browser_metrics=(
                                structural_checkpoint_metrics(results_by_index[absolute_index])
                                if source_reflow else selected_metrics
                            ),
                        )
            repaired_gate_indices += [
                flagged[index] for index in sorted(fixed_local_indices)
            ]
        except AuthoredStageError:
            raise
        except Exception as error:
            if source_reflow and isinstance(error, AuthoredVqaResidualDomIssuesError):
                # The round repaired some slides and could not repair the rest.
                # Throwing the whole round away made every attempt of the
                # operation start from the same defects: 3d0b9e72 fixed slide 12
                # and lost that fix with slide 33's overlap. Keeping what worked
                # lets the next attempt begin from the better deck and spend its
                # one repair on what is actually left.
                def persist_partial_repair_scene(result):
                    nonlocal scene_envelope
                    claim = repair_claims.get(result.slide_index)
                    if generation_job is None or claim is None:
                        raise ValueError("source_repair_scene_claim_missing")
                    revised_scene = replace_deck_scene_slide(
                        scene_envelope, slide_index=result.slide_index,
                        html_projection=result.html,
                    )
                    persist_repaired_scene_envelope(
                        generation_job.id, revised_scene.slides[result.slide_index], claim,
                    )
                    scene_envelope = revised_scene

                await _checkpoint_source_reflow_vqa_repairs(
                    generation_job,
                    error,
                    absolute_indices=getattr(
                        error, "checkpoint_absolute_indices", flagged
                    ),
                    persist_scene=persist_partial_repair_scene,
                )
            LOGGER.warning(
                "[authored] bounded quality-gate repair failed slides=%s error=%s",
                flagged,
                type(error).__name__,
            )
            # The durable repair claim was consumed before external work. A
            # failed/inconclusive repair is terminal and cannot replay the deck.
            raise AuthoredStageError(
                slide_index=flagged[0],
                stage="render",
                code="authored_composition_safety_gate_failed",
                cause=error,
                retryable=False,
                message="제한된 슬라이드 품질 게이트 복구 후에도 안전성 검증을 완료하지 못했습니다.",
            ) from error
    quality_gate_receipt = None
    # QUAL-04: the receipt records what VQA did, not whether it was requested.
    visual_review_evidence = visual_review_not_assessed()
    # A replay now produces authored slides, so the caller's own vision-QA choice
    # governs it like any other authored deck.
    if single_loop and getattr(request, "vision_qa", False):
        # Rev-3 2.5: the review already ran, as evidence for the loop's prompt.
        # B9: what the loop admitted is attributed to the loop, from its own
        # results -- never inferred from what the review happened to see.
        visual_review_evidence = visual_review_evidence_only(
            reviewed_indices=sorted(vqa_evidence),
            model_reviewed=sum(1 for value in vqa_evidence.values() if value is not None),
            repair_loop_model_calls=sum(
                int(entry.get("proposal_calls") or 0)
                for result in structural_results
                for entry in result.cycle_history
            ),
            repair_loop_admitted_indices=[
                result.slide_index
                for result in structural_results
                if result.final_status == "admitted"
            ],
        )
    if getattr(request, "vision_qa", False) and not single_loop:
        pre_vqa_htmls = tuple(htmls)
        pre_vqa_pngs = tuple(pngs)
        pre_vqa_metrics_by_slide = {
            index: (revised_metrics_by_slide.get(index) or cached_metrics(pre_vqa_htmls[index]))
            for index in range(len(pre_vqa_htmls))
        }
        fixed, accepted_fixed, vqa_repair_details = [], [], []
        # Reachability of the vision-QA MODEL half, kept apart from the
        # deterministic critique's own findings (QUAL-04).
        vqa_review_stats: dict[str, object] = {}
        vision_started = time.monotonic()
        with provider_usage_scope(
            job_id=generation_job.id if generation_job is not None else None,
            operation="authored_generation",
            stage="vqa",
            attempt_number=(generation_job.attempt_number if generation_job else 0),
        ):
            try:
                vision_qa_kwargs = {}
                if reference_mode == EXACT_RECONSTRUCTION:
                    exact_plan = prepare_authored_deck(outline, brand, style, canvas=canvas)
                    vision_qa_kwargs["reference_images_by_slide"] = (
                        exact_plan.reference_images
                    )
                # Re-authoring a slide must replay the deck-wide decisions
                # made before its first draft: the chapter prefix always, and
                # the illustration/infographic slots unless the reference page
                # dictates them.
                vision_qa_kwargs.update(
                    replayed_plan_kwargs(
                        range(len(htmls)), chapter_plan=chapter_plan,
                        illustration_plan=illustration_plan,
                        infographic_plan=infographic_plan, style=style,
                        brand=brand, instructions=request.instructions,
                        exact_reconstruction=(reference_mode == EXACT_RECONSTRUCTION),
                        with_assets=True,
                    )
                )
                if source_reflow:
                    vision_qa_kwargs["fail_on_residual_dom_issues"] = (
                        not terminal_fallback_enabled
                    )
                revise_kwargs: dict[str, object] = {
                    "instructions": request.instructions,
                    "max_cycles": composition_profile.max_repair_cycles,
                    "style": style,
                    "return_details": source_reflow,
                    "local_patch_only": source_reflow,
                    "capacity_briefs": capacity_briefs,
                    "repair_details_sink": vqa_repair_details,
                    "review_stats_sink": vqa_review_stats,
                    "canvas": canvas,
                    **vision_qa_kwargs,
                }
                vqa_provider_recovery_allowed: list[bool] | None = None
                if local_fit_receipts:
                    # Measured geometry/font findings cannot consume a
                    # provider recovery. Content-only review findings retain
                    # their existing correction policy; an admitted rewrite is
                    # sent through a new local-fit author revision below.
                    vqa_provider_recovery_allowed = [
                        (
                            not has_author_provider_attempt_counts
                            or author_provider_attempt_counts[index] < 2
                        )
                        for index in range(len(htmls))
                    ]
                    revise_kwargs["provider_recovery_allowed"] = (
                        vqa_provider_recovery_allowed
                    )
                    revise_kwargs["provider_recovery_blocked_issue_types"] = (
                        "overflow",
                        "overlap",
                        "misalignment",
                        "empty-space",
                    )
                elif has_author_provider_attempt_counts:
                    vqa_provider_recovery_allowed = [
                        count < 2 for count in author_provider_attempt_counts
                    ]
                    revise_kwargs["provider_recovery_allowed"] = (
                        vqa_provider_recovery_allowed
                    )
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
                if vqa_provider_recovery_allowed is not None:
                    # The live reviser receives this same slide-scoped mask
                    # before provider I/O.  Every allowed VQA call consumes the
                    # only recovery slot; later stages therefore cannot exceed
                    # initial=1 + recovery<=1 for that slide.
                    for index, allowed in enumerate(vqa_provider_recovery_allowed):
                        if allowed:
                            author_provider_attempt_counts[index] = 2
                candidate_htmls, candidate_pngs, fixed = revised[:3]
                candidate_htmls = list(candidate_htmls)
                candidate_pngs = list(candidate_pngs)
                # Some narrow adapters mutate their input lists in place.
                # Restore the frozen pre-VQA deck before admitting candidates
                # slide by slide.
                htmls = list(pre_vqa_htmls)
                pngs = list(pre_vqa_pngs)
                accepted_fixed: list[int] = []
                for index in fixed:
                    candidate_html = canonicalize_authored_roles(
                        candidate_htmls[index]
                    )
                    candidate_fit: AcceptedLocalFit | None = None
                    if local_fit_receipts:
                        try:
                            candidate_fit = await _fit_content_revision(
                                candidate_html,
                                slide_index=index,
                                policy_revision=getattr(
                                    request,
                                    "_authored_local_fit_policy_revision",
                                    None,
                                ),
                                canvas=canvas,
                            )
                        except AuthoredLocalFitRejected as error:
                            LOGGER.warning(
                                "[authored] rejected post-VQA local-fit candidate "
                                "slide=%d reason=%s digest=%s",
                                index + 1,
                                error.reason,
                                digest_html(candidate_html),
                            )
                            continue
                        candidate_html = candidate_fit.html
                        candidate_pngs[index] = candidate_fit.png
                        candidate_metrics = candidate_fit.metrics
                    else:
                        # Measure the exact correction instead of consulting a
                        # potentially stale capture cache.
                        candidate_metrics = await captured_metrics_or_existing(
                            candidate_html, index=index, ensure=ensure_metrics,
                        )
                    if not await source_vqa_candidate_integrity_passes(
                        index, candidate_html, candidate_metrics, validate_source_repair_candidate
                    ):
                        continue
                    (
                        selected_html,
                        selected_png,
                        selected_metrics,
                        admitted,
                    ) = _admit_vqa_capacity_candidate(
                        slide_index=index,
                        original_html=pre_vqa_htmls[index],
                        original_png=pre_vqa_pngs[index],
                        original_metrics=pre_vqa_metrics_by_slide[index],
                        candidate_html=candidate_html,
                        candidate_png=candidate_pngs[index],
                        candidate_metrics=candidate_metrics,
                        capacity=capacity_plan.at(index),
                    )
                    htmls[index] = selected_html
                    pngs[index] = selected_png
                    if not admitted:
                        if selected_metrics is not None:
                            revised_metrics_by_slide[index] = selected_metrics
                        continue
                    if candidate_fit is not None:
                        local_fit_receipts[index] = candidate_fit.receipt
                        cache_accepted_capture(
                            candidate_fit, timeout=60,
                            width_px=canvas_width_px,
                            height_px=canvas_height_px,
                        )
                    accepted_fixed.append(index)
                    scene_envelope = replace_deck_scene_slide(
                        scene_envelope,
                        slide_index=index,
                        html_projection=selected_html,
                    )
                    if selected_metrics is None:
                        raise RuntimeError("authored_vqa_scene_metrics_unavailable")
                    revised_metrics_by_slide[index] = selected_metrics
                    if generation_job is not None:
                        persist_scene_envelope(
                            generation_job.id,
                            scene_envelope.slides[index],
                        )
                    if generation_job is not None and generation_job.attempt_token:
                        await _complete_stage_after_external_success(
                            job_id=generation_job.id,
                            slide_index=index,
                            stage="render",
                            attempt_token=generation_job.attempt_token,
                            fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
                            html=selected_html,
                            png=selected_png,
                            browser_metrics=selected_metrics,
                        )
                if source_reflow and terminal_fallback_enabled:
                    declared_indices = {
                        int(entry["index"])
                        for entry in predeclared_terminal
                        if isinstance(entry.get("index"), int)
                    }
                    for detail in vqa_repair_details:
                        slide_index = detail.get("slide_index")
                        unresolved = (
                            detail.get("attempted") is True
                            and detail.get("succeeded") is not True
                        ) or (
                            "candidate_status" in detail
                            and detail.get("candidate_status") != "admitted"
                        )
                        if (
                            isinstance(slide_index, int)
                            and unresolved
                            and slide_index not in declared_indices
                        ):
                            predeclared_terminal.append(
                                declare_unadmitted_gate_repair(slide_index)
                            )
                            declared_indices.add(slide_index)
                fixed_count = len(accepted_fixed)
                visual_review_evidence = visual_review_outcome(
                    slide_count=len(htmls), fixed=fixed, accepted_fixed=accepted_fixed,
                    repair_details=vqa_repair_details, review_stats=vqa_review_stats)
            except Exception as error:
                traceback.print_exc()
                visual_review_evidence = visual_review_outcome(
                    slide_count=len(htmls), fixed=fixed, accepted_fixed=accepted_fixed,
                    repair_details=vqa_repair_details, review_stats=vqa_review_stats,
                    error=error)
                if bool(request._source_content_authored_reflow):
                    if isinstance(error, AuthoredVqaResidualDomIssuesError):
                        await _checkpoint_source_reflow_vqa_repairs(generation_job, error)
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

    # No deck-wide post-pass may mutate a normal slide. All repair candidates
    # are confined to the initially failed indices and their one claimed budget.

    repair_elapsed = time.monotonic() - repair_started
    record_stage_timing(
        "quality",
        stage_timings_ms["quality"] + max(0.0, vision_elapsed) * 1000,
    )
    await _record_deck_stage_latency(
        generation_job,
        stage="quality",
        elapsed_seconds=max(0.0, pipeline.quality_work + vision_elapsed),
    )
    record_stage_timing("repair", repair_elapsed * 1000)
    await _record_deck_stage_latency(
        generation_job, stage="repair", elapsed_seconds=repair_elapsed
    )

    # D7: a slide that is still terminal after the bounded repair gets one
    # automatic lower-density remake here, before the attempt evidence is
    # sealed; whatever stays terminal is flagged instead of failing the deck.
    terminal_fallback = await resolve_terminal_gate_slides(
        htmls=htmls, pngs=pngs, contents=contents, roles=roles, brand=brand,
        design_system=authored_deck.design_system, style=style,
        capacity_plan=capacity_plan, profile=composition_profile,
        reference_mode=reference_mode, scene_envelope=scene_envelope,
        metrics_by_slide=revised_metrics_by_slide, source_reflow=source_reflow,
        chapter_plan=chapter_plan, illustration_plan=illustration_plan,
        infographic_plan=infographic_plan, instructions=request.instructions,
        generation_job=generation_job, predeclared=predeclared_terminal,
        remake_counts=remake_counts,
        preserved_flagged_indices=tuple(sorted(preserved_local_fit_indices)),
        prepare_admitted=prepare_post_acceptance_repair,
    )
    scene_envelope = terminal_fallback.scene_envelope
    flagged_slides = _merge_flagged_slide_entries(
        (*pipeline.author_flagged_slides, *terminal_fallback.flagged_slides)
    )
    # Decided once, here, and threaded through to ``publish_authored_presentation``
    # (as ``slide_ids``) so each persisted ``SlideModel`` row gets exactly this id.
    # A flagged entry's ``slideId`` then names the row it was generated for
    # directly -- no post-construction lookup, and no risk of the receipt's
    # ``index`` field later addressing a different slide after an in-session
    # reorder renumbers every slide's ``index``.
    slide_ids = [uuid.uuid4() for _ in htmls]
    flagged_slides = [
        (
            {**entry, "slideId": str(slide_ids[entry["index"]])}
            if isinstance(entry.get("index"), int) and 0 <= entry["index"] < len(slide_ids)
            else entry
        )
        for entry in flagged_slides
    ]
    flagged_residual_failures: list[dict[str, object]] = []
    object.__setattr__(request, "_authored_flagged_slides", flagged_slides)
    # F3: the seconds the wave scheduler slept out a provider cooldown, on the
    # same request channel the flagged slides use. The attempt timing receipt
    # records it on the authoring stage as ``cooldownWaitSeconds``.
    object.__setattr__(
        request,
        "_authored_cooldown_wait_seconds",
        float(getattr(pipeline, "cooldown_wait_seconds", 0.0) or 0.0),
    )

    if local_fit_receipts:
        for index, (html, receipt) in enumerate(
            zip(htmls, local_fit_receipts, strict=True)
        ):
            if receipt.get("finalHtmlDigest") != (
                "sha256:" + sha256(html.encode("utf-8")).hexdigest()
            ):
                raise AuthoredStageError(
                    slide_index=index,
                    stage="render",
                    code="authored_local_fit_post_acceptance_mutation",
                    retryable=False,
                )

    review_pages = flag_pages(flagged_slides)
    review_indices = {int(item["index"]) for item in review_pages}
    auto_repaired_indices = {
        *map(int, repaired_gate_indices),
        *(
            index
            for index, receipt in enumerate(local_fit_receipts)
            if receipt.get("changed") is True
        ),
    } - review_indices
    page_summary = {
        "total": len(htmls),
        "normal": max(
            0, len(htmls) - len(auto_repaired_indices) - len(review_indices)
        ),
        "autoRepaired": len(auto_repaired_indices),
        "reviewRequired": len(review_indices),
    }

    await mark_stage("checkpoint")
    checkpoint_started = time.monotonic()
    evidence_ledger, evidence_attempt_id, retained_final_metrics = (
        _finalize_attempt_evidence(
            pipeline, htmls, pngs, metrics_override=revised_metrics_by_slide,
            width_px=canvas_width_px, height_px=canvas_height_px,
        )
    )
    # F4: the metrics of the HTML that is about to be published, and of nothing
    # else. The sealed ledger and the repair rounds are both readbacks here --
    # each is accepted per slide only while it still carries that slide's source
    # digest, so a remade slide can never be gated on its pre-remake capture.
    final_browser_metrics = tuple(
        await resolve_final_browser_metrics(
            htmls,
            retained=(retained_final_metrics if evidence_ledger is not None else None),
            overrides=revised_metrics_by_slide,
            width_px=canvas_width_px,
            height_px=canvas_height_px,
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
                # ``None`` is this planner's word for "source images are not in
                # play for this deck", which is the ordinary state of a source
                # whose pages are text only. There is nothing to place and so
                # nothing to check; treating it as a failure ended a 35-slide
                # deck that had already passed every gate before it.
                final_source_images = [{} for _ in htmls]
            for index, (html, offered, metrics) in enumerate(
                zip(htmls, final_source_images, final_browser_metrics, strict=True)
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
        # M1: the failing slides' fit magnitude travels to the E4 attempt-timing
        # receipt on the same request the timing stage already rides.
        gate_fit_summaries: dict[int, Mapping[str, object]] = {}
        residual_gate_failures = await _final_authored_quality_gate(
            presentation_id=presentation_id,
            htmls=htmls,
            capacity_plan=capacity_plan,
            profile=composition_profile,
            reference_mode=reference_mode,
            browser_metrics_by_slide=list(final_browser_metrics),
            flagged_slide_indices=[int(entry["index"]) for entry in flagged_slides],
            fit_summaries=gate_fit_summaries,
        )
        if gate_fit_summaries:
            object.__setattr__(request, "_authored_gate_fit_summaries", gate_fit_summaries)
        if residual_gate_failures and flagged_slides:
            residual_gate_failures, flagged_residual_failures = (
                partition_residual_failures(residual_gate_failures, flagged_slides)
            )
        if residual_gate_failures:
            first = residual_gate_failures[0]
            repeated_signature = any(
                signature in initial_gate_signatures
                for item in residual_gate_failures
                if (signature := _gate_failure_signature(htmls, item)) is not None
            )
            raise AuthoredStageError(
                slide_index=int(first.get("slide_index", 0)),
                stage="render",
                code="authored_composition_safety_gate_failed",
                # This is the exact post-repair HTML/metrics tuple. Replaying
                # every completed slide cannot change a deterministic residual.
                retryable=False,
                message=(
                    "One or more slides still fail the composition safety gate "
                    "after bounded repair"
                    + (
                        " with the same HTML digest and metrics"
                        if repeated_signature
                        else ""
                    )
                ),
            )

    source_readback_receipt, source_readback_payload, flagged_slides = await final_readback(
        request=request, htmls=htmls, pngs=pngs, generation_job=generation_job,
        source_reflow=source_reflow, composition_profile=composition_profile,
        terminal_fallback_enabled=terminal_fallback_enabled, evidence_ledger=evidence_ledger,
        final_browser_metrics=final_browser_metrics, flagged_slides=flagged_slides,
        _merge_flagged_slide_entries=_merge_flagged_slide_entries,
    )

    # Final here: the readback merged its visibility sub-reasons and the gate's
    # residuals are partitioned, so a flag can be told from a review requirement.
    flagged_slides = classify_flagged_slides(
        flagged_slides, residual_failures=flagged_residual_failures
    )
    object.__setattr__(request, "_authored_flagged_slides", flagged_slides)
    reviewable_pages = review_pages_for(flagged_slides)

    quality_gate_receipt = build_authored_quality_gate_receipt(
        htmls=htmls,
        profile_id=composition_profile.profile_id,
        # A D7-flagged slide is published, so its measured issues are residual
        # evidence in the receipt rather than a blocker.
        gate_failures=[*residual_gate_failures, *flagged_residual_failures],
        repaired_slide_indices=repaired_gate_indices,
    )
    quality_gate_receipt["admissionShadow"] = admission_shadow_receipt()
    # Real Chromium captures and owner-served reuses per slide: the number
    # the live series reports, counted at the one owner every capture site
    # routes through rather than at the gate, which sees only admissions.
    quality_gate_receipt["captureOwnership"] = capture_ownership_receipt()
    quality_gate_receipt["sceneEnvelope"] = scene_envelope.receipt()
    if local_fit_receipts:
        quality_gate_receipt["localFit"] = {
            "policyRevision": local_fit_receipts[0].get("policy_revision"),
            "slides": list(local_fit_receipts),
        }
    if flagged_slides:
        quality_gate_receipt["flaggedSlides"] = flagged_slides
    quality_gate_receipt.update(
        documentStatus="pending",
        documentCompleted=False,
        qualityPass=not review_pages and not residual_gate_failures,
        pageSummary=page_summary,
        reviewPages=reviewable_pages,
    )
    if source_readback_receipt is not None:
        quality_gate_receipt["sourceReadback"] = source_readback_payload
    # H4/H6 evidence: deck instruments and the one vision advisory pass. Both
    # are receipt-only and knob-gated; neither can change what is published.
    await attach_deck_receipt_extras(
        quality_gate_receipt, htmls=htmls, pngs=pngs,
        metrics=list(final_browser_metrics), style=style, brand=brand,
    )
    # Stashed on the request so the attempt-timing receipt (a separate,
    # digest-bound file the bench reads without a DB dependency; see
    # services/presentation_generation_attempt_timing.py) can carry the same
    # H4/H6 evidence the quality-gate receipt above just received.
    object.__setattr__(
        request,
        "_authored_deck_instruments",
        (quality_gate_receipt.get("instruments") or {}).get("deck"),
    )
    object.__setattr__(
        request,
        "_authored_vision_advisory",
        (quality_gate_receipt.get("advisory") or {}).get("vision"),
    )

    quality_receipt_v2 = None
    if evidence_ledger is not None and evidence_attempt_id is not None:
        # A D7-flagged slide is published with its defects on record (see the
        # safety evidence below), so its capacity checks never block the deck.
        flagged_indices = {int(entry["index"]) for entry in flagged_slides}
        # Keyed by slide index, not positional: the gate-failure diagnostic must
        # be able to name WHICH slide carries a capacity violation.
        capacity_by_slide: dict[int, dict[str, object]] = {
            index: _evaluate_capacity_role_evidence(metrics, capacity_plan.at(index) if capacity_plan is not None else {})
            for index, metrics in enumerate(final_browser_metrics) if index not in flagged_indices
        }
        capacity_checks: list[dict[str, object]] = list(capacity_by_slide.values())
        if capacity_checks:
            names = ("text", "title", "body", "font", "qid", "image")
            capacity_evidence = {
                "browserObserved": all(item.get("browserObserved") is True for item in capacity_checks),
                "checks": {name: all(bool(item.get("checks", {}).get(name)) for item in capacity_checks) for name in names},
                "violations": [v for item in capacity_checks for v in item.get("violations", [])],
                "flaggedSlideIndices": sorted(flagged_indices),
                # QUAL-06: receipt-only typographic density signal, never a check.
                "bodyFontMedianPx": body_font_median_signal(final_browser_metrics),
            }
        else:
            capacity_evidence = {"browserObserved": False, "checks": {name: False for name in ("text", "title", "body", "font", "qid", "image")}}
        source_evidence = (
            source_readback_payload
            if source_readback_payload is not None
            else {"required": False, "status": "not_applicable"}
        )
        safety_evidence = {
            "status": "passed" if not residual_gate_failures else "blocked",
            "residuals": residual_gate_failures,
            # D7-accepted terminal slides are recorded, never dropped: they do
            # not block publication but they stay in the sealed evidence.
            "flagged": flagged_slides,
        }
        checkpoint_elapsed = time.monotonic() - checkpoint_started
        record_stage_timing("checkpoint", checkpoint_elapsed * 1000)
        await _record_deck_stage_latency(
            generation_job,
            stage="checkpoint",
            elapsed_seconds=checkpoint_elapsed,
        )
        quality_profile_id = str(
            getattr(composition_profile, "quality_profile_id", "development")
        )
        performance_evidence = {
            # Stage timing is observability, not a performance checker result.
            # Do not mint a quality PASS until a bounded performance policy has
            # its own versioned measurements and thresholds.
            "status": "not_assessed",
            "reasonCode": "performance_checker_evidence_not_persisted",
        }
        quality_receipt_v2 = create_quality_receipt_v2(
            evidence_ledger,
            attempt_id=evidence_attempt_id,
            revision=evidence_ledger.revision(evidence_attempt_id),
            ledger_digest=evidence_ledger.ledger_digest(evidence_attempt_id),
            deck_html_digest=evidence_ledger.deck_html_digest(evidence_attempt_id),
            evidence={
                "safety": safety_evidence,
                "capacity": capacity_evidence,
                "sourceReadback": source_evidence,
                # ``vision_qa`` selects a repair path; it is not a durable
                # visual-checker receipt with versioned input/output evidence.
                "visualReview": {
                    "status": "not_assessed",
                    "reasonCode": "visual_checker_evidence_not_persisted",
                },
                "performance": performance_evidence,
                "nativeCoverage": {"status": "not_assessed"},
                "publication": {"status": "pending"},
            },
            require_visual_review=False,
            require_performance=False,
            # Native OOXML/readback evidence exists only after the separate
            # Hybrid export completes. Keep that pre-export evaluation scope
            # distinct while preserving the caller's requested release policy
            # in the digest-bound receipt consumed by export completion.
            requested_release_profile=quality_profile_id,
            pre_export_evaluation_profile="default",
            scene_envelope=scene_envelope.receipt(),
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
        quality_receipt_v3 = await create_generation_quality_receipt_v3(
            receipt_v2=quality_receipt_v2,
            job_id=(generation_job.id if generation_job is not None else None),
            slide_roles=roles,
            provider_attempt_counts=author_provider_attempt_counts,
            repaired_slide_indices=repaired_gate_indices,
            capacity_evidence=capacity_evidence,
            safety_evidence=safety_evidence,
            browser_metrics=final_browser_metrics,
            visual_review=visual_review_evidence,
            source_manifest=(request._source_content_manifest if source_reflow else None),
            source_readback=(
                source_readback_payload
                if source_readback_payload is not None
                else None
            ),
            # These are the exact completed-deck artifacts, not registry
            # imports: the frozen shared prompt and sealed Scene prove which
            # prompt/semantic authority Chromium actually consumed.
            design_system_prompt=getattr(authored_deck, "design_system", None),
            scene_envelope=scene_envelope.receipt(),
            # The public composition root may install a DB/Object Storage
            # append port on the pipeline.  Its absence is deliberately
            # recorded as a public durability blocker by the V3 adapter.
            persistence_port=getattr(
                request, "_quality_receipt_v3_persistence_port", None
            ),
            durability_profile=getattr(
                request, "_quality_receipt_v3_durability_profile", None
            ),
        )
        quality_pass = not review_pages and not residual_gate_failures
        quality_residuals = [
            *residual_gate_failures,
            *flagged_residual_failures,
        ]
        quality_gate_receipt = {
            **quality_gate_receipt,
            **quality_receipt_v2.to_dict(),
            "profileId": composition_profile.profile_id,
            "slideCount": len(htmls),
            "htmlDigest": "sha256:" + sha256_canonical_json(htmls),
            "status": "passed" if quality_pass else "blocked",
            "qualityPass": quality_pass,
            "documentStatus": "pending",
            "documentCompleted": False,
            "pageSummary": page_summary,
            "reviewPages": reviewable_pages,
            "residualIssues": quality_residuals,
            "residualIssueCount": len(quality_residuals),
            "requestedQualityProfile": quality_profile_id,
            "qualityReceiptV3": {
                **quality_receipt_v3.to_dict(),
                # QUAL-11: no constant release verdict here; the public V3 projection derives it.
                "qualityStatus": ("passed" if quality_pass else "blocked"),
            },
            "releaseEligibility": {
                "status": (
                    "pending"
                    if quality_profile_id == "release"
                    else "not_applicable"
                )
            },
            "requiredPostExportEvidence": (
                ["nativeCoverage", "ooxmlReadback"]
                if quality_profile_id == "release"
                else []
            ),
        }
        if not quality_receipt_v2.gate_passed:
            # One structured line naming the failing check ids and the slides
            # they were measured on: bench run 24 ended here with the code and
            # nothing else, so the cause had to be re-derived from the deck.
            gate_failure = quality_receipt_gate_failure(
                quality_receipt_v2,
                capacity_checks=capacity_by_slide,
                violations=capacity_evidence.get("violations") or (),
            )
            raise AuthoredStageError(
                slide_index=(
                    gate_failure["blockedSlides"][0]
                    if gate_failure["blockedSlides"]
                    else 0
                ),
                stage="render",
                code="authored_quality_receipt_gate_failed",
                # QualityReceipt v2 evaluates the sealed final evidence after
                # the one bounded slide repair. A whole-deck retry is wasteful.
                retryable=False,
                message=log_quality_receipt_gate_failure(gate_failure),
            )

    # Observability: one structured summary (counts/timing/model only ??no secrets).
    placeholder = _placeholder_png()
    blank_renders = sum(1 for p in pngs if p == placeholder)
    from utils.llm_provider import get_llm_provider, get_model

    await mark_stage("export")
    export_started = time.monotonic()
    attempt_token = generation_job.attempt_token if generation_job is not None else None
    export_generation_job_id = generation_job.id if generation_job is not None else None
    export_generation_request_id = (
        generation_job.request_id if generation_job is not None else None
    )
    native_export_evidence = None
    try:
        if generation_job is not None:
            if not attempt_token:
                raise AuthoredStageError(
                    slide_index=0,
                    stage="persist",
                    code="authored_attempt_lost",
                )
        out_path = _build_authored_export(
            request,
            presentation_id,
            pngs,
            title,
            local_fit_receipts=(local_fit_receipts or None),
            canvas=canvas,
        )
        quality_gate_receipt["documentStatus"] = "completed"
        quality_gate_receipt["documentCompleted"] = True
        if generation_job is not None and quality_receipt_v2 is not None:
            # Publication copies these bytes and returns a public download URL,
            # not a filesystem path. Collect before any publication commit so
            # a missing source cannot turn a committed success into a failure.
            artifact_bytes_observation = _artifact_bytes_metric(out_path)
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
        if export_generation_job_id is not None and attempt_token:
            await fail_stage(
                job_id=export_generation_job_id,
                slide_index=error.slide_index,
                stage="persist",
                attempt_token=attempt_token,
                error=error,
                request_id=export_generation_request_id,
                fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
            )
        raise error from exc
    export_elapsed = time.monotonic() - export_started
    record_stage_timing("export", export_elapsed * 1000)
    await _record_deck_stage_latency(
        generation_job, stage="export", elapsed_seconds=export_elapsed
    )
    await mark_stage("publication")
    publication_started = time.monotonic()
    if tuple(htmls) != scene_envelope.html_projections:
        raise AuthoredStageError(
            slide_index=0,
            stage="persist",
            code="authored_scene_envelope_projection_drift",
            retryable=False,
        )
    semantic_components = list(scene_envelope.semantic_components)
    editable_scene = None
    publication_owner_id = existing.owner_id if existing is not None else None
    staging_recovery_context = None
    if (
        generation_job is not None
        and generation_job.workspace_operation_id is not None
        and generation_job.attempt_token
        and getattr(generation_job, "_workspace_fencing_token", None) is not None
    ):
        staging_recovery_context = {
            "presentation_id": presentation_id,
            "job_id": generation_job.id,
            "operation_id": generation_job.workspace_operation_id,
            "attempt_token": generation_job.attempt_token,
            "attempt_number": generation_job.attempt_number,
            "fencing_token": generation_job._workspace_fencing_token,
            "stale_deck_id": f"deck:semantic-{presentation_id.hex}",
        }
    if publication_owner_id is not None:
        # Source-image discovery above may reopen the caller's SQLite read
        # transaction after the admission boundary was released.  The vNext
        # repository below performs creation-only writes through an independent
        # synchronous session; keeping that reader open can block its commit and
        # stall the whole FastAPI event loop at publication.  There are no
        # caller-owned publication mutations yet, and expire_on_commit is false,
        # so close the read transaction before crossing into the independent CAS
        # writer.  The final presentation/publication tuple is still committed
        # atomically by publish_authored_presentation below.
        await sql_session.commit()
        editable_scene = await _persist_master_pinned_editable_scene_deck(
            request=request,
            presentation_id=presentation_id,
            outline=outline,
            owner_id=publication_owner_id,
            style=style,
            htmls=htmls,
            pngs=pngs,
            semantic_components=semantic_components,
            scene_envelope=scene_envelope,
            revision=(existing.revision if existing is not None else 0) + 1,
            browser_metrics_by_slide=final_browser_metrics,
            recovery_context=staging_recovery_context,
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
        infographic_plan=infographic_plan,
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
        slide_ids=slide_ids,
    )
    publication_elapsed = time.monotonic() - publication_started
    record_stage_timing("publication", publication_elapsed * 1000)
    await _record_deck_stage_latency(
        generation_job,
        stage="publication",
        elapsed_seconds=publication_elapsed,
    )
    # Per-slide figure measurement.  Written for every authored deck, job or
    # no job, so the local synchronous benchmark route is measured too.  The
    # variant and the critic score come from the quality loop's own per-slide
    # receipts; a slide the loop did not run for records them as absent rather
    # than as zero.
    from services.authored_slide_quality_receipt import (
        build_slide_quality_records,
        record_slide_quality_receipt,
    )

    loop_receipts = dict(getattr(pipeline, "slide_quality_receipts", {}) or {})
    record_slide_quality_receipt(
        presentation_id,
        build_slide_quality_records(
            slide_count=len(htmls or ()),
            infographic_plan=infographic_plan,
            variants={
                index: (receipt.get("best_of") or {}).get("variant_chosen")
                for index, receipt in loop_receipts.items()
                if isinstance(receipt.get("best_of"), dict)
            },
            critic_scores={
                index: receipt["critic_score"]
                for index, receipt in loop_receipts.items()
                if receipt.get("critic_score") is not None
            },
            normalizations={
                index: receipt["normalizations"]
                for index, receipt in loop_receipts.items()
                if isinstance(receipt.get("normalizations"), list)
            },
        ),
        attempt_number=(
            generation_job.attempt_number if generation_job is not None else None
        ),
    )
    if generation_job is not None and quality_receipt_v2 is not None:
        from services.public_operation_admission import (
            public_operation_observability_metrics,
        )

        public_metrics = (
            await public_operation_observability_metrics(
                sql_session, generation_job.workspace_operation_id
            )
            if generation_job.workspace_operation_id is not None
            else {
                name: _unavailable_metric("public_admission_not_found")
                for name in (
                    "queueWait", "queuePosition", "providerInitialCalls",
                    "providerRetryCalls", "providerLogicalOrdinal",
                    "estimatedTokens", "actualTokens", "estimatedCost", "actualCost",
                )
            }
        )
        stage_provider_metrics = (
            await _provider_stage_observability_metrics(
                sql_session, generation_job.workspace_operation_id
            )
            if generation_job.workspace_operation_id is not None
            else {
                name: _unavailable_metric("public_admission_not_found")
                for name in (
                    "queuePosition",
                    "stageRetryCalls",
                    "stageEstimatedCost",
                    "stageActualCost",
                )
            }
        )
        render_runtime_after = get_render_runtime_counters()
        worker_handle_count_after = _worker_handle_count_metric()
        observability_metrics = {
            **public_metrics,
            **stage_provider_metrics,
            **{
                f"{stage}Duration": _observed_metric(stage_timings_ms[stage])
                for stage in _PERFORMANCE_STAGE_NAMES
            },
            "attemptNumber": _observed_metric(generation_job.attempt_number),
            "fencingOutcome": _observed_metric("publish_committed"),
            "chromeLaunch": _observed_metric(
                _runtime_counter_delta(
                    render_runtime_before, render_runtime_after, "chrome_process_launches"
                )
            ),
            "chromeRestart": _observed_metric(
                _runtime_counter_delta(
                    render_runtime_before, render_runtime_after, "session_restarts"
                )
            ),
            "blockedRequest": _observed_metric(
                _blocked_renderer_reference_count(htmls)
            ),
            # A timing receipt is emitted only after this attempt completed all
            # render and quality stages. Any render timeout aborts the attempt
            # before this point, so the successful attempt's exact count is 0.
            "timeout": _observed_metric(0),
            "crash": _observed_metric(
                _runtime_counter_delta(
                    render_runtime_before, render_runtime_after, "session_restarts"
                )
            ),
            "workerPeakMemory": _worker_peak_memory_metric(),
            "workerPeakHandles": _maximum_observed_metric(
                worker_handle_count_before, worker_handle_count_after
            ),
            "artifactBytes": artifact_bytes_observation,
            "cacheHit": _observed_metric(
                _runtime_counter_delta(
                    render_runtime_before, render_runtime_after,
                    "render_cache_hits", "metrics_cache_hits", "capture_cache_hits",
                )
            ),
            "cacheMiss": _observed_metric(
                _runtime_counter_delta(
                    render_runtime_before, render_runtime_after,
                    "render_cache_misses", "metrics_cache_misses", "capture_cache_misses",
                )
            ),
            "cacheEviction": _observed_metric(
                _runtime_counter_delta(
                    render_runtime_before, render_runtime_after,
                    "render_cache_evictions", "metrics_cache_evictions", "capture_cache_evictions",
                )
            ),
            "objectStoreLatency": _object_store_latency_metric(
                getattr(request, "_object_store_metrics", None),
                getattr(generation_job, "object_store_metrics", None),
                published_path,
            ),
            "finalOutcome": _observed_metric("succeeded"),
        }
        create_generation_timing_receipt(
            job_id=generation_job.id,
            quality_receipt=quality_receipt_v2,
            attempt_number=generation_job.attempt_number,
            stage_timings_ms=stage_timings_ms,
            total_ms=round((time.monotonic() - started) * 1000),
            observability_metrics=observability_metrics,
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
        "vqa_fixed=%d blank_renders=%d total=%.1fs author_sum=%.1fs "
        "illustrations=%.1fs render=%.1fs pipeline=%.1fs vision=%.1fs "
        "persist=%.1fs export=%.1fs publication=%.1fs",
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
        publication_elapsed,
    )
    return PresentationPathAndEditPath(
        presentation_id=presentation_id,
        path=published_path,
        edit_path=f"/presentation?id={presentation_id}",
    )
