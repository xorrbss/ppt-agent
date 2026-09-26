"""Static allow-listed workspace error definitions."""

from services.workspace_error_types import definition

ADDITIONAL_ERROR_DEFINITIONS = (
        definition(
            "authored_cluster_capacity_unavailable",
            "Presentation generation capacity is temporarily unavailable.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "authored_template_request_invalid",
            "The authored template request is invalid.",
            status_code=422,
        ),
        definition(
            "authored_template_generation_job_missing",
            "The template generation job is being recovered. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "authored_template_generation_enqueue_failed",
            "Template generation could not be started. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "authored_template_generation_task_missing",
            "The template generation task is being recovered. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "authored_template_result_missing",
            "The generated presentation is not ready yet. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "invalid_operation_handler_result",
            "The workspace operation returned an invalid result.",
            status_code=500,
        ),
        definition(
            "presentation_already_deleted",
            "The presentation was already deleted.",
            status_code=409,
        ),
        definition(
            "presentation_delete_approval_expired",
            "The deletion approval expired. Submit the command again.",
            status_code=409,
        ),
        definition(
            "presentation_delete_approval_required",
            "Deleting this presentation requires approval.",
            status_code=409,
        ),
        definition(
            "presentation_delete_database_unavailable",
            "The presentation cannot be deleted right now. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "presentation_delete_dependency_conflict",
            "The presentation cannot be deleted while related work is active.",
            status_code=409,
        ),
        definition(
            "presentation_delete_expected_revision_mismatch",
            "The presentation changed before deletion. Reload and try again.",
            status_code=409,
        ),
        definition(
            "presentation_delete_expected_revision_required",
            "Reload the presentation before deleting it.",
            status_code=409,
        ),
        definition(
            "presentation_delete_operation_kind_invalid",
            "The deletion operation is invalid.",
            status_code=422,
        ),
        definition(
            "presentation_delete_owner_mismatch",
            "The presentation could not be found for this workspace.",
            status_code=404,
        ),
        definition(
            "presentation_delete_temporarily_unavailable",
            "The presentation cannot be deleted right now. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "presentation_edit_failed",
            "The presentation edit could not be completed. Try again.",
            retryable=True,
            status_code=500,
        ),
        definition(
            "presentation_export_failed",
            "The presentation export could not be completed. Try again.",
            retryable=True,
            status_code=500,
        ),
        # Without these the export quality gate fell through to the generic
        # "The workspace request could not be completed", which tells the user
        # nothing about a deck that no format can export and no retry can fix.
        definition(
            "quality_receipt_v3_blocked",
            "This deck's quality review has not passed, so it cannot be "
            "exported in any format. Remake the affected pages and try again.",
            status_code=409,
        ),
        definition(
            "quality_receipt_v3_required",
            "This deck has no quality record yet, so it cannot be exported. "
            "Remake it and try again.",
            status_code=409,
        ),
        definition(
            "quality_receipt_v3_digest_invalid",
            "This deck's quality record could not be verified, so it cannot be "
            "exported. Remake it and try again.",
            status_code=409,
        ),
        definition(
            "page_remake_request_invalid",
            "This page remake request is missing its page or preview.",
            status_code=400,
        ),
        definition(
            "page_remake_publish_failed",
            "The new page version could not be applied. Try again.",
            retryable=True,
            status_code=500,
        ),
        definition(
            "presentation_generation_job_missing",
            "The presentation generation job is being recovered. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "presentation_generation_job_not_linked",
            "The presentation generation job is being linked. Try again.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "presentation_generation_task_missing",
            "The presentation generation task could not be recovered.",
            status_code=500,
        ),
        definition(
            "quality_review_failed",
            "The quality review could not be completed. Try again.",
            retryable=True,
            status_code=500,
        ),
        definition(
            "quality_review_result_commit_failed",
            "The quality review result could not be saved. Try again.",
            retryable=True,
            status_code=500,
        ),
        definition(
            "quality_review_read_only_required",
            "Quality review supports analysis-only requests.",
            status_code=422,
        ),
        definition(
            "quality_review_request_invalid",
            "The quality review request is invalid.",
            status_code=422,
        ),
        definition(
            "quality_review_revision_changed",
            "The presentation changed during quality review. Run it again.",
            status_code=409,
        ),
        definition(
            "quality_review_unsupported_presentation",
            "This presentation cannot be reviewed.",
            status_code=422,
        ),
        definition(
            "workspace_command_missing",
            "Provide a workspace command.",
            status_code=422,
        ),
        definition(
            "workspace_export_format_invalid",
            "Choose a supported export format.",
            status_code=422,
        ),
        definition(
            "workspace_export_pptx_mode_invalid",
            "Choose a supported PowerPoint export mode.",
            status_code=422,
        ),
        definition(
            "workspace_export_font_embedding_invalid",
            "Choose a valid font embedding option.",
            status_code=422,
        ),
        definition(
            "workspace_export_font_embedding_requires_hybrid",
            "Font embedding requires Hybrid PowerPoint export.",
            status_code=422,
        ),
        definition("version_not_found", "The selected presentation version no longer exists.", status_code=404),
        definition(
            "version_restore_target_invalid", "Choose a valid presentation version to restore.", status_code=409
        ),
        definition(
            "version_snapshot_invalid", "The selected presentation version cannot be restored.", status_code=409
        ),
        definition(
            "version_restore_failed",
            "The presentation version could not be restored. Try again.",
            retryable=True,
            status_code=500,
        ),
        definition(
            "authored_batch_remake_request_invalid",
            "This batch page remake request is missing its pages or previews.",
            status_code=400,
        ),
        definition(
            "authored_batch_remake_master_pinned_unsupported",
            "Batch page remake is not available while a design master is pinned.",
            status_code=400,
        ),
        definition(
            "authored_batch_remake_all_slides_failed",
            "None of the selected pages could be applied.",
            status_code=409,
        ),
        definition(
            "slide_order_request_invalid",
            "The slide order request is invalid.",
            status_code=422,
        ),
        definition(
            "slide_order_id_set_mismatch",
            "The requested slide order does not match the presentation's current slides. Reload and try again.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "slide_order_duplicate_id",
            "The requested slide order lists the same slide more than once.",
            status_code=422,
        ),
        definition(
            "slide_order_requires_slide",
            "The presentation has no slides to reorder.",
            status_code=409,
        ),
        definition(
            "slide_order_publish_failed",
            "The slide order could not be saved. Try again.",
            retryable=True,
            status_code=500,
        ),
        definition(
            "slide_order_still_running",
            "The slide order is still being saved.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "raster_authenticated_scope_required",
            "This analysis could not be authenticated. Sign in and try again.",
            status_code=422,
        ),
        definition(
            "raster_analysis_mode_invalid",
            "Choose a supported analysis mode.",
            status_code=422,
        ),
        definition(
            "raster_source_binding_invalid",
            "The uploaded file no longer matches this request. Upload it again.",
            status_code=409,
        ),
        definition(
            "raster_review_scope_mismatch",
            "This analysis belongs to a different workspace. Start a new analysis.",
            status_code=409,
        ),
        definition(
            "raster_auto_review_binding_invalid",
            "The analysis mode changed since this review started. Start a new analysis.",
            status_code=409,
        ),
        definition(
            "raster_auto_review_modified",
            "The analysis changed since it was last reviewed. Start a new analysis.",
            status_code=409,
        ),
        definition(
            "raster_source_digest_mismatch",
            "The uploaded file changed during analysis. Upload it again.",
            status_code=422,
        ),
)
