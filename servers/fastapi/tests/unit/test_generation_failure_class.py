"""The single mapping from terminal generation codes to user-facing classes.

The table is every terminal ``presentation.generate`` code observed in the 45
days to 2026-10-22, so a guidance change can be checked against real failures.
"""

from __future__ import annotations

import pytest

from services.generation_failure_class import (
    GENERATION_FAILURE_CLASSES,
    classify_generation_failure,
    generation_operation_failure_class,
)

OBSERVED_45D = {
    "http_error_500": "service_interrupted",
    "svg_quality_recovery_review_required": "quality_not_passed",
    "svg_source_quality_failed": "quality_not_passed",
    "source_content_browser_readback_failed": "quality_not_passed",
    "authored_composition_safety_gate_failed": "quality_not_passed",
    "public_provider_result_unknown": "provider_unstable",
    "svg_validation_failed": "quality_not_passed",
    "svg_partial_recovery_request_snapshot_changed": "service_interrupted",
    "authored_slide_author_failed": "quality_not_passed",
    "public_provider_bridge_exception": "provider_unstable",
    "svg_provider_attempt_uncertain": "provider_unstable",
    "operation_worker_crashed": "service_interrupted",
    "svg_partial_recovery_cached_source_changed": "service_interrupted",
    "authored_checkpoint_evidence_corrupt": "service_interrupted",
    "presentation_generation_failed": "service_interrupted",
    "presentation_generation_job_not_linked": "service_interrupted",
    "public_provider_overload": "provider_unstable",
    "public_provider_retry_limit_exceeded": "provider_unstable",
    "svg_common_header_representative_pages_ineligible": "quality_not_passed",
    "svg_quality_recovery_review_unavailable": "quality_not_passed",
    "svg_source_classification_missing": "source_unprocessable",
    "svg_source_native_table_group_invalid": "quality_not_passed",
    "authored_source_verbatim_capacity_exceeded": "source_unprocessable",
    "public_provider_call_timeout": "provider_unstable",
    "public_provider_request_limit_exceeded": "provider_unstable",
    "authored_slide_illustrate_failed": "quality_not_passed",
    "public_deadline_exceeded": "limit_reached",
    "svg_asset_capture_failed": "unknown",
    "svg_common_header_input_capacity_exceeded": "quality_not_passed",
    "svg_common_header_title_source_mismatch": "quality_not_passed",
    "svg_source_receipt_binding_mismatch": "quality_not_passed",
    "svg_repair_budget_exhausted": "quality_not_passed",
    "authored_local_fit_font_floor_unfit": "quality_not_passed",
    "svg_uploaded_design_reference_digest_mismatch": "source_unprocessable",
    "raster_tenant_required": "service_interrupted",
    "retry_amplification_ceiling_exceeded": "limit_reached",
    "authored_local_fit_unverifiable_font_size": "quality_not_passed",
    "authored_quality_receipt_gate_failed": "quality_not_passed",
    "svg_common_header_body_intrusion": "quality_not_passed",
    "svg_common_header_candidates_ineligible": "quality_not_passed",
    "authored_semantic_validation_failed": "quality_not_passed",
    "authored_vqa_residual_dom_issues": "quality_not_passed",
    "svg_common_header_title_overflow": "quality_not_passed",
    "authored_local_fit_post_acceptance_mutation": "quality_not_passed",
    "svg_header_design_checkpoint_conflict": "service_interrupted",
    "xd2s": "unknown",
    "public_operation_fence_conflict": "service_interrupted",
    "composer_render_plan_invalid": "quality_not_passed",
    "svg_provider_terminal_output_mismatch": "provider_unstable",
    "authored_workspace_operation_fence_lost": "service_interrupted",
    "composer_render_qid_invalid": "quality_not_passed",
    "composer_source_table_structure_mismatch": "source_unprocessable",
    "generation_profile_conflict": "unknown",
    "generation_strategy_capability_missing": "unknown",
    "authored_local_fit_original_output_unavailable": "quality_not_passed",
    "authored_slide_capacity_exceeded": "quality_not_passed",
    "svg_source_image_unknown_slot": "source_unprocessable",
    "public_provider_pricing_unavailable": "limit_reached",
    "composer_blocking_issues_remaining": "quality_not_passed",
}


@pytest.mark.parametrize(("code", "expected"), sorted(OBSERVED_45D.items()))
def test_every_observed_code_has_its_class(code: str, expected: str):
    assert classify_generation_failure(code) == expected


@pytest.mark.parametrize(
    "code",
    [
        "public_provider_call_timeout",
        "http_error_500",
        "svg_source_quality_failed",
        "source_content_browser_readback_failed",
        "public_deadline_exceeded",
    ],
)
def test_the_most_frequent_failures_are_never_unknown(code: str):
    assert classify_generation_failure(code) != "unknown"


def test_classes_are_the_wire_enum():
    assert set(GENERATION_FAILURE_CLASSES) == {
        "provider_unstable",
        "quality_not_passed",
        "service_interrupted",
        "source_unprocessable",
        "limit_reached",
        "unknown",
    }
    assert set(OBSERVED_45D.values()) <= set(GENERATION_FAILURE_CLASSES)


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("http_error_503", "service_interrupted"),
        ("http_error_404", "unknown"),
        ("source_content_format_not_supported", "source_unprocessable"),
        ("source_content_verbatim_readback_failed", "quality_not_passed"),
        ("public_provider_pricing_missing_openai_gpt_6", "limit_reached"),
        ("svg_partial_recovery_history_changed", "service_interrupted"),
        ("svg_partial_recovery_budget_unavailable", "unknown"),
    ],
)
def test_families_and_empty_codes(code, expected):
    assert classify_generation_failure(code) == expected


@pytest.mark.parametrize(
    ("kind", "state", "code", "expected"),
    [
        ("presentation.generate", "failed", "public_provider_overload", "provider_unstable"),
        ("presentation.generate", "dead_letter", "http_error_500", "service_interrupted"),
        ("presentation.generate", "deadline_exceeded", "public_deadline_exceeded", "limit_reached"),
        ("presentation.generate", "expired", None, "unknown"),
        ("presentation.generate", "failed", None, "unknown"),
        ("presentation.generate", "cancelled", "public_provider_overload", None),
        ("presentation.generate", "running", None, None),
        ("presentation.generate", "succeeded", None, None),
        ("export.pptx", "failed", "http_error_500", None),
    ],
)
def test_operation_class_is_only_for_terminally_failed_generations(kind, state, code, expected):
    assert generation_operation_failure_class(kind=kind, state=state, error_code=code) == expected
