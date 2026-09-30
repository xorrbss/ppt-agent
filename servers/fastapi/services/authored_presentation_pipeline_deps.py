from __future__ import annotations

import asyncio
import inspect
import logging
import os
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from functools import partial
from typing import Any

from models.authored_chapter_plan import AuthoredChapterPlan
from models.authored_header_contract import AuthoredHeaderContract
from models.authored_illustration_plan import AuthoredIllustrationPlan
from models.authored_infographic_plan import AuthoredInfographicPlan
from models.presentation_outline_model import PresentationOutlineModel
from models.sql.presentation_generation_job import PresentationGenerationJob
from services.authored_checkpoint_state import provider_attempt_count_in_epoch
from services.authored_author_stage_deferral import (
    DeferredAuthorBudgetExhausted,
    author_stage_failure as _author_stage_failure,
    defer_rescuable_author_failures,
    deferred_author_budget_error,
)
from services.authored_author_stage_rescue import (
    rescue_author_stage,
    source_reflow_rescue_readback,
)
from services.authored_checkpoint_service import (
    AuthoredStageError,
    fail_stage,
    get_checkpoint,
    initialize_checkpoints,
    is_retryable_exception,
    reserve_provider_attempt,
    should_inject_failure,
    start_stage,
)
from services.authored_checkpoint_artifacts import read_valid_evidence_artifact, read_valid_render_artifact
from services.authored_checkpoint_html import checkpoint_stage_html
from services.authored_structural_repair import validate_structural_checkpoint
from services.authored_presentation_artifacts import _complete_stage_after_external_success
from services.authored_illustration_stage import illustrate_slide
from services.authored_infographic_assets import build_infographic_asset_context, deck_has_picture_source
from services.authored_quality_loop_stage import apply_slide_quality_loop
from services.authored_presentation_contracts import AuthoredGenerationPipelineResult, SlideStageResult
from services.authored_evidence_ledger import DeckEvidenceLedger
from services.image_generation_service import ImageGenerationService
from services.authored_source_images import plan_slide_source_images
from services.authored_measured_gates import measured_font_floor_px, measured_gate_issues
from services.authored_composition_policy import (
    AuthoredCapacityPlan,
    CompositionProfile,
    blocking_safety_issues,
    capacity_violations,
    capacity_overflow,
)
from services.authored_quality_receipt_v2 import public_attempt_id
from services.authored_local_fit_admission import (
    admit_local_fit_capture,
    local_fit_failure_reason,
    preserve_original_capture,
)
from services.authored_local_fit_evidence import (
    AuthoredLocalFitRejected,
    cache_accepted_capture,
    receipt_is_preserved_original,
    reusable_checkpoint_receipt,
)
from services.authored_local_fit_policy import (
    FONT_FLOOR_PT,
    POLICY_REVISION,
    html_digest,
    local_fit_policy,
    pt_per_css_px,
    required_font_px,
    slide_budget,
)
from services.authored_local_fit_deadline import (
    remaining_authored_local_fit_seconds,
)
from utils.authored_semantic_role_repair import canonicalize_authored_roles, plan_slide_role
from services.authored_fit_envelope import reseal_scene_envelope_after_fit
from services.authored_vnext.generation.scene_checkpoint import persist_scene_envelope, read_scene_checkpoint_state
from services.authored_vnext.generation.scene_envelope import (
    AuthoredSlideSceneEnvelope,
    seal_deck_scene_envelope,
    seal_slide_scene_envelope,
)
from services.provider_usage_instrumentation import provider_usage_scope, record_local_stage
from services.authored_generation_performance import authored_provider_concurrency
from services.authored_slide_worker_pool import run_slide_worker_pool
from services.authored_batch_policy import new_authored_batch_policy_state
from services.authored_tail_streaming import (
    AuthoredTailStreamCoordinator,
    run_authored_recovery_pass,
    run_streamed_slide_tail,
)
from services.authored_preauthor_scan import report_unresolved_slides, scan_preauthor_checkpoints
from services.authored_slide_ledger import assert_authored_ledger_disjoint, batch_recorder
from services.authored_style_anchor_stage import (
    accepts_style_anchor, anchor_blocking_defects, anchor_render_step,
    resolve_direct_lane_anchor, resolve_style_anchor,
)
from services.authored_wave_scheduler import AuthoredWaveSchedule, run_initial_authoring
from services.source_images.models import SourceImageCandidate
from services.source_content.browser_readback import BrowserSourceReadbackError
from services.source_content.header_pages import header_absent_pages
from services.source_content.text_stamping import stamp_source_blocks
from utils.asset_directory_utils import get_images_directory
from utils.authored_illustrations import (
    enforce_strict_source_image_size_floor, validate_bound_source_images, validate_visible_source_images,
)
from utils.authored_font_policy import enforce_authored_font_floor
from utils.authored_measured_type_clamp import merge_measured_type_clamp_receipt, render_with_measured_type_clamp
from utils.authored_dom_quality import ensure_authored_qids, inspect_authored_dom
from services.generation_core.authoring.author_deck import (
    author_correction_from_stored_failure,
    AuthoredBatchFailure,
    AuthoredDeckResult,
    AuthoredPlannedBatchResult,
    author_planned_batch,
    author_planned_slide,
    plan_deck_roles,
    prepare_authored_deck,
)
from services.generation_core.authoring.author_slide import (
    EXACT_RECONSTRUCTION,
    NO_PAGE_NUMBER_MODE,
    STYLE_REFERENCE,
    Brand,
    is_fallback_slide_html,
    normalize_reference_mode,
    validate_authored_slide_html,
)
from utils.renderer_isolation import RendererPolicyError
from utils.get_env_programme import is_authored_in_render_fit_enabled, is_authored_tail_streaming_enabled
from utils.llm_calls.author_prompt_cache_key import deck_prompt_cache_key, set_prompt_cache_key
from utils.slide_capture_deck_scope import set_render_deck_scope
from utils.llm_provider import get_llm_provider
from utils.slide_capture import (
    _placeholder_png,
    ensure_browser_metrics,
    get_cached_browser_metrics,
    render_html_to_png,
    render_html_to_png_with_metrics,
)


__all__ = [name for name in globals() if not name.startswith("__")]
