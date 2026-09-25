"""Safe public definitions for literal durable operation failures."""

from services.workspace_error_types import definition


_GROUPS = (
    (503, True, "The operation could not finish. Try again.", (
        "authored_batch_remake_preview_incomplete",
        "release_quality_review_post_gate_failed",
    )),
    (422, False, "The operation request is invalid.", (
        "authored_vnext_native_export_mode_required",
        "authored_vnext_regenerate_body_contract_invalid",
        "batch_remake_preview_request_invalid",
        "page_remake_preview_request_invalid",
        "svg_export_source_required",
        "svg_font_embedding_not_supported",
        "svg_release_quality_review_not_supported",
    )),
    (409, False, "The presentation changed. Review it and try again.", (
        "authored_vnext_export_master_changed",
        "authored_vnext_regenerate_master_changed",
        "public_operation_fence_conflict",
        "release_quality_review_checker_changed",
        "release_quality_review_render_identity_changed",
        "release_quality_review_result_drift",
        "release_quality_review_runtime_changed",
    )),
    (409, False, "The presentation is not ready for this operation.", (
        "authored_vnext_export_projection_incomplete",
        "content_integrity_receipt_invalid",
        "generation_strategy_capability_missing",
        "public_export_admission_required",
        "public_export_execution_receipt_invalid",
        "public_export_render_identity_invalid",
        "public_export_render_runtime_identity_invalid",
        "public_generation_publication_missing",
        "public_isolated_authored_html_required",
        "public_operation_deadline_missing",
        "public_phase2_admission_invalid",
        "public_quality_projection_inputs_missing",
        "release_quality_review_admission_required",
        "release_quality_review_authored_html_required",
        "release_quality_review_authority_invalid",
        "release_quality_review_base_invalid",
        "release_quality_review_base_missing",
        "release_quality_review_blocked",
        "release_quality_review_identity_required",
        "release_quality_review_recheck_base_invalid",
        "release_quality_review_required",
        "svg_export_active_admission_required",
        "svg_export_incomplete_presentation",
    )),
    (503, False, "The operation could not finish. Try again later.", (
        "artifact_store_failed",
        "authored_manifest_publication_failed",
        "generation_timing_receipt_persistence_failed",
        "generation_timing_receipt_projection_invalid",
        "public_admission_authority_unavailable",
        "public_operation_authority_unavailable",
        "release_quality_review_failed",
    )),
    (408, False, "The operation deadline has passed.", (
        "public_deadline_exceeded",
    )),
    (409, True, "Another request is saving the same files. Try again.", (
        "artifact_key_busy",
    )),
    (403, False, "This operation does not match the account that started it.", (
        "workspace_operation_principal_conflict",
    )),
)


OPERATION_ERROR_DEFINITIONS = {
    item.code: item
    for status_code, retryable, message, codes in _GROUPS
    for code in codes
    for item in (definition(code, message, retryable=retryable, status_code=status_code),)
}
