"""Static allow-listed workspace error definitions."""

from services.workspace_asset_error_definitions import ASSET_ERROR_DEFINITIONS
from services.workspace_error_types import WorkspaceErrorDefinition, definition


# Keep this registry metadata-only.  Messages are intentionally useful to a
# user but reveal no payload, provider response, storage path, or stack detail.
WORKSPACE_ERROR_REGISTRY: dict[str, WorkspaceErrorDefinition] = {
    item.code: item
    for item in (
        definition(
            "chat_first_feature_disabled",
            "Workspace operations are not enabled.",
            status_code=503,
        ),
        definition(
            "chat_first_runtime_unsupported",
            "This database runtime does not support workspace operations.",
            status_code=501,
        ),
        definition(
            "chat_first_runtime_profile_required",
            "Workspace writes require an explicit runtime profile.",
            status_code=503,
        ),
        definition(
            "chat_first_runtime_profile_invalid",
            "The configured workspace runtime profile is invalid.",
            status_code=503,
        ),
        definition(
            "chat_first_runtime_profile_mismatch",
            "The configured workspace runtime profile does not match the database.",
            status_code=503,
        ),
        definition(
            "chat_first_kill_switch_enabled",
            "Workspace operations are temporarily unavailable.",
            retryable=True,
            status_code=503,
        ),
        definition(
            "chat_first_cohort_excluded",
            "Workspace operations are not enabled for this presentation.",
            status_code=503,
        ),
        definition(
            "chat_first_presentation_scope_required",
            "A presentation is required to evaluate workspace rollout.",
            status_code=503,
        ),
        definition(
            "request_validation_error",
            "The workspace request is invalid.",
            status_code=422,
        ),
        definition(
            "workspace_request_failed",
            "The workspace request could not be completed.",
            status_code=400,
        ),
        definition(
            "internal_server_error",
            "The workspace request could not be completed.",
            retryable=True,
            status_code=500,
        ),
        definition(
            "workspace_route_not_found",
            "The workspace endpoint was not found.",
            status_code=404,
        ),
        definition(
            "presentation_not_found",
            "The presentation was not found.",
            status_code=404,
        ),
        definition(
            "presentation_deleted",
            "The presentation was deleted and cannot be changed.",
            status_code=409,
        ),
        definition(
            "account_deletion_in_progress",
            "This account is being deleted. Workspace changes are unavailable.",
            status_code=409,
        ),
        definition(
            "workspace_mutation_admission_conflict",
            "The presentation is temporarily busy. Try again.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "workspace_operation_not_found",
            "The workspace operation was not found.",
            status_code=404,
        ),
        definition(
            "workspace_asset_not_found",
            "The attachment was not found.",
            status_code=404,
        ),
        definition(
            "workspace_export_not_found",
            "The exported file was not found.",
            status_code=404,
        ),
        definition(
            "workspace_export_expired",
            "The exported file expired. Export it again.",
            status_code=410,
        ),
        definition(
            "workspace_export_operation_invalid",
            "The export operation no longer matches this presentation.",
            status_code=409,
        ),
        definition(
            "workspace_export_not_ready",
            "The exported file is not ready yet.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "workspace_export_missing",
            "The exported file is no longer available. Export it again.",
            status_code=410,
        ),
        definition(
            "workspace_artifact_path_invalid",
            "The exported file was not found.",
            status_code=404,
        ),
        definition(
            "asset_processing_timeout",
            "An attachment did not finish processing in time.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "workspace_asset_not_ready",
            "An attachment is still being processed.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "workspace_asset_failed",
            "An attachment could not be processed.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "workspace_asset_quarantined",
            "An attachment did not pass the safety check.",
            status_code=409,
        ),
        definition(
            "workspace_asset_expired",
            "An attachment expired and must be uploaded again.",
            status_code=409,
        ),
        definition(
            "workspace_asset_deleted",
            "An attachment was deleted and must be uploaded again.",
            status_code=409,
        ),
        definition(
            "operation_state_conflict",
            "This action is no longer available.",
            status_code=409,
        ),
        definition(
            "operation_not_retryable",
            "This operation cannot be retried. Submit a new command instead.",
            status_code=409,
        ),
        definition(
            "workspace_undo_not_supported",
            "Undo is not available for this operation.",
            status_code=409,
        ),
        definition(
            "revision_conflict",
            "The presentation changed in another session. Reload and try again.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "stale_presentation_revision",
            "The presentation changed in another session. Reload and try again.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "event_cursor_expired",
            "Updates expired; reload the workspace timeline.",
            retryable=True,
            status_code=410,
        ),
        definition(
            "operation_recovery_required",
            "The operation is being recovered after an interruption.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "operation_dead_lettered",
            "The operation could not complete. Retry it from this conversation.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "approval_expired",
            "This approval request expired. Submit the command again to continue.",
            status_code=409,
        ),
        definition(
            "unsupported_operation_type",
            "This workspace operation is not supported.",
            status_code=422,
        ),
        definition(
            "idempotency_key_payload_conflict",
            "This idempotency key was already used for a different request.",
            status_code=409,
        ),
        definition(
            "idempotency_record_incomplete",
            "The earlier request is still being recovered. Try again.",
            retryable=True,
            status_code=409,
        ),
        definition(
            "invalid_idempotency_key",
            "Provide a valid idempotency key.",
            status_code=400,
        ),
        definition(
            "workspace_expected_revision_required",
            "Reload the presentation and submit its current revision.",
            retryable=True,
            status_code=409,
        ),
        definition("initial_operation_state_conflict", "The requested operation state is invalid.", status_code=400),
        definition("invalid_initial_operation_state", "The requested operation state is invalid.", status_code=400),
        definition(
            "bootstrap_concurrency_conflict",
            "The workspace changed concurrently. Try again.",
            retryable=True,
            status_code=409,
        ),
        definition("invalid_event_cursor", "The event cursor is invalid.", status_code=400),
        *ASSET_ERROR_DEFINITIONS,
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
    )
}
