"""Static allow-listed workspace error definitions."""

from services.workspace_error_types import definition

CORE_ERROR_DEFINITIONS = (
        definition(
            "svg_source_export_format_unsupported",
            "Source-preserving SVG decks can currently only be exported as PPTX. Choose PPTX.",
            status_code=422,
        ),
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
)
