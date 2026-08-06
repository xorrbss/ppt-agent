"""Central, safe public error contract for the chat-first workspace API.

Internal exceptions may carry provider text, paths, prompts, or implementation
details.  Only the stable definitions in this module (or the deliberately
generic fallback) are allowed to cross the workspace HTTP boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from fastapi import HTTPException


_STABLE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


@dataclass(frozen=True)
class WorkspaceErrorDefinition:
    code: str
    safe_message: str
    retryable: bool
    status_code: int


def _definition(
    code: str,
    safe_message: str,
    *,
    retryable: bool = False,
    status_code: int = 400,
) -> WorkspaceErrorDefinition:
    return WorkspaceErrorDefinition(
        code=code,
        safe_message=safe_message,
        retryable=retryable,
        status_code=status_code,
    )


# Keep this registry metadata-only.  Messages are intentionally useful to a
# user but reveal no payload, provider response, storage path, or stack detail.
WORKSPACE_ERROR_REGISTRY: dict[str, WorkspaceErrorDefinition] = {
    item.code: item
    for item in (
        _definition(
            "chat_first_feature_disabled",
            "Workspace operations are not enabled.",
            status_code=503,
        ),
        _definition(
            "chat_first_runtime_unsupported",
            "This database runtime does not support workspace operations.",
            status_code=501,
        ),
        _definition(
            "chat_first_runtime_profile_required",
            "Workspace writes require an explicit runtime profile.",
            status_code=503,
        ),
        _definition(
            "chat_first_runtime_profile_invalid",
            "The configured workspace runtime profile is invalid.",
            status_code=503,
        ),
        _definition(
            "chat_first_runtime_profile_mismatch",
            "The configured workspace runtime profile does not match the database.",
            status_code=503,
        ),
        _definition(
            "chat_first_kill_switch_enabled",
            "Workspace operations are temporarily unavailable.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "chat_first_cohort_excluded",
            "Workspace operations are not enabled for this presentation.",
            status_code=503,
        ),
        _definition(
            "chat_first_presentation_scope_required",
            "A presentation is required to evaluate workspace rollout.",
            status_code=503,
        ),
        _definition(
            "request_validation_error",
            "The workspace request is invalid.",
            status_code=422,
        ),
        _definition(
            "workspace_request_failed",
            "The workspace request could not be completed.",
            status_code=400,
        ),
        _definition(
            "internal_server_error",
            "The workspace request could not be completed.",
            retryable=True,
            status_code=500,
        ),
        _definition(
            "workspace_route_not_found",
            "The workspace endpoint was not found.",
            status_code=404,
        ),
        _definition(
            "presentation_not_found",
            "The presentation was not found.",
            status_code=404,
        ),
        _definition(
            "presentation_deleted",
            "The presentation was deleted and cannot be changed.",
            status_code=409,
        ),
        _definition(
            "account_deletion_in_progress",
            "This account is being deleted. Workspace changes are unavailable.",
            status_code=409,
        ),
        _definition(
            "workspace_mutation_admission_conflict",
            "The presentation is temporarily busy. Try again.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "workspace_operation_not_found",
            "The workspace operation was not found.",
            status_code=404,
        ),
        _definition(
            "workspace_asset_not_found",
            "The attachment was not found.",
            status_code=404,
        ),
        _definition(
            "workspace_export_not_found",
            "The exported file was not found.",
            status_code=404,
        ),
        _definition(
            "workspace_export_expired",
            "The exported file expired. Export it again.",
            status_code=410,
        ),
        _definition(
            "workspace_export_not_ready",
            "The exported file is not ready yet.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "workspace_export_missing",
            "The exported file is no longer available. Export it again.",
            status_code=410,
        ),
        _definition(
            "workspace_artifact_path_invalid",
            "The exported file was not found.",
            status_code=404,
        ),
        _definition(
            "asset_processing_timeout",
            "An attachment did not finish processing in time.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "workspace_asset_not_ready",
            "An attachment is still being processed.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "workspace_asset_failed",
            "An attachment could not be processed.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "workspace_asset_quarantined",
            "An attachment did not pass the safety check.",
            status_code=409,
        ),
        _definition(
            "workspace_asset_expired",
            "An attachment expired and must be uploaded again.",
            status_code=409,
        ),
        _definition(
            "workspace_asset_deleted",
            "An attachment was deleted and must be uploaded again.",
            status_code=409,
        ),
        _definition(
            "operation_state_conflict",
            "This action is no longer available.",
            status_code=409,
        ),
        _definition(
            "operation_not_retryable",
            "This operation cannot be retried. Submit a new command instead.",
            status_code=409,
        ),
        _definition(
            "workspace_undo_not_supported",
            "Undo is not available for this operation.",
            status_code=409,
        ),
        _definition(
            "revision_conflict",
            "The presentation changed in another session. Reload and try again.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "stale_presentation_revision",
            "The presentation changed in another session. Reload and try again.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "event_cursor_expired",
            "Updates expired; reload the workspace timeline.",
            retryable=True,
            status_code=410,
        ),
        _definition(
            "operation_recovery_required",
            "The operation is being recovered after an interruption.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "operation_dead_lettered",
            "The operation could not complete. Retry it from this conversation.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "approval_expired",
            "This approval request expired. Submit the command again to continue.",
            status_code=409,
        ),
        _definition(
            "unsupported_operation_type",
            "This workspace operation is not supported.",
            status_code=422,
        ),
        _definition(
            "idempotency_key_payload_conflict",
            "This idempotency key was already used for a different request.",
            status_code=409,
        ),
        _definition(
            "idempotency_record_incomplete",
            "The earlier request is still being recovered. Try again.",
            retryable=True,
            status_code=409,
        ),
        _definition(
            "invalid_idempotency_key",
            "Provide a valid idempotency key.",
            status_code=400,
        ),
        _definition(
            "workspace_expected_revision_required",
            "Reload the presentation and submit its current revision.",
            retryable=True,
            status_code=409,
        ),
        _definition("initial_operation_state_conflict", "The requested operation state is invalid.", status_code=400),
        _definition("invalid_initial_operation_state", "The requested operation state is invalid.", status_code=400),
        _definition("bootstrap_concurrency_conflict", "The workspace changed concurrently. Try again.", retryable=True, status_code=409),
        _definition("invalid_event_cursor", "The event cursor is invalid.", status_code=400),
        _definition("workspace_asset_id_required", "Upload the attachment before submitting this request.", status_code=400),
        _definition("workspace_asset_hash_invalid", "The attachment metadata is invalid.", status_code=400),
        _definition("workspace_asset_hash_mismatch", "The attachment changed and must be uploaded again.", status_code=409),
        _definition("workspace_asset_reprocess_required", "Reprocess the failed attachment before retrying.", retryable=True, status_code=409),
        _definition("workspace_asset_reattach_required", "Replace the unavailable attachment before retrying.", status_code=409),
        _definition("workspace_asset_media_type_invalid", "The attachment type is not supported.", status_code=400),
        _definition("workspace_asset_media_type_mismatch", "The attachment content does not match its declared type.", status_code=400),
        _definition("workspace_asset_empty", "The attachment is empty.", status_code=400),
        _definition("workspace_asset_signature_mismatch", "The attachment content does not match its file type.", status_code=400),
        _definition("workspace_asset_malware_scan_mode_invalid", "Attachment safety scanning is unavailable.", retryable=True, status_code=503),
        _definition("workspace_asset_malware_scanner_unavailable", "Attachment safety scanning is unavailable.", retryable=True, status_code=503),
        _definition("workspace_asset_malware_scan_failed", "The attachment safety check could not be completed.", retryable=True, status_code=503),
        _definition("workspace_asset_malware_detected", "The attachment did not pass the safety check.", status_code=400),
        _definition("workspace_asset_file_type_invalid", "The attachment type is not supported.", status_code=400),
        _definition("workspace_asset_storage_invalid", "The attachment could not be stored.", retryable=True, status_code=500),
        _definition("workspace_asset_source_changed", "The attachment changed while it was being imported.", retryable=True, status_code=409),
        _definition("workspace_asset_too_large", "The attachment exceeds the upload limit.", status_code=413),
        _definition("workspace_asset_upload_invalid", "The attachment could not be uploaded.", status_code=400),
        _definition("workspace_conversation_not_found", "The workspace conversation was not found.", status_code=404),
        _definition("workspace_asset_version_conflict", "The attachment changed concurrently. Reload and try again.", retryable=True, status_code=409),
        _definition("workspace_asset_transition_invalid", "The requested attachment state transition is invalid.", status_code=409),
        _definition(
            "authored_cluster_capacity_unavailable",
            "Presentation generation capacity is temporarily unavailable.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "authored_template_request_invalid",
            "The authored template request is invalid.",
            status_code=422,
        ),
        _definition(
            "authored_template_generation_job_missing",
            "The template generation job is being recovered. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "authored_template_generation_enqueue_failed",
            "Template generation could not be started. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "authored_template_generation_task_missing",
            "The template generation task is being recovered. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "authored_template_result_missing",
            "The generated presentation is not ready yet. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "invalid_operation_handler_result",
            "The workspace operation returned an invalid result.",
            status_code=500,
        ),
        _definition(
            "presentation_already_deleted",
            "The presentation was already deleted.",
            status_code=409,
        ),
        _definition(
            "presentation_delete_approval_expired",
            "The deletion approval expired. Submit the command again.",
            status_code=409,
        ),
        _definition(
            "presentation_delete_approval_required",
            "Deleting this presentation requires approval.",
            status_code=409,
        ),
        _definition(
            "presentation_delete_database_unavailable",
            "The presentation cannot be deleted right now. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "presentation_delete_dependency_conflict",
            "The presentation cannot be deleted while related work is active.",
            status_code=409,
        ),
        _definition(
            "presentation_delete_expected_revision_mismatch",
            "The presentation changed before deletion. Reload and try again.",
            status_code=409,
        ),
        _definition(
            "presentation_delete_expected_revision_required",
            "Reload the presentation before deleting it.",
            status_code=409,
        ),
        _definition(
            "presentation_delete_operation_kind_invalid",
            "The deletion operation is invalid.",
            status_code=422,
        ),
        _definition(
            "presentation_delete_owner_mismatch",
            "The presentation could not be found for this workspace.",
            status_code=404,
        ),
        _definition(
            "presentation_delete_temporarily_unavailable",
            "The presentation cannot be deleted right now. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "presentation_edit_failed",
            "The presentation edit could not be completed. Try again.",
            retryable=True,
            status_code=500,
        ),
        _definition(
            "presentation_export_failed",
            "The presentation export could not be completed. Try again.",
            retryable=True,
            status_code=500,
        ),
        _definition(
            "page_remake_request_invalid",
            "This page remake request is missing its page or preview.",
            status_code=400,
        ),
        _definition(
            "page_remake_publish_failed",
            "The new page version could not be applied. Try again.",
            retryable=True,
            status_code=500,
        ),
        _definition(
            "presentation_generation_job_missing",
            "The presentation generation job is being recovered. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "presentation_generation_job_not_linked",
            "The presentation generation job is being linked. Try again.",
            retryable=True,
            status_code=503,
        ),
        _definition(
            "presentation_generation_task_missing",
            "The presentation generation task could not be recovered.",
            status_code=500,
        ),
        _definition(
            "quality_review_failed",
            "The quality review could not be completed. Try again.",
            retryable=True,
            status_code=500,
        ),
        _definition(
            "quality_review_result_commit_failed",
            "The quality review result could not be saved. Try again.",
            retryable=True,
            status_code=500,
        ),
        _definition(
            "quality_review_read_only_required",
            "Quality review supports analysis-only requests.",
            status_code=422,
        ),
        _definition(
            "quality_review_request_invalid",
            "The quality review request is invalid.",
            status_code=422,
        ),
        _definition(
            "quality_review_revision_changed",
            "The presentation changed during quality review. Run it again.",
            status_code=409,
        ),
        _definition(
            "quality_review_unsupported_presentation",
            "This presentation cannot be reviewed.",
            status_code=422,
        ),
        _definition(
            "workspace_command_missing",
            "Provide a workspace command.",
            status_code=422,
        ),
        _definition(
            "workspace_export_format_invalid",
            "Choose a supported export format.",
            status_code=422,
        ),
        _definition("version_not_found", "The selected presentation version no longer exists.", status_code=404),
        _definition("version_restore_target_invalid", "Choose a valid presentation version to restore.", status_code=409),
        _definition("version_snapshot_invalid", "The selected presentation version cannot be restored.", status_code=409),
        _definition("version_restore_failed", "The presentation version could not be restored. Try again.", retryable=True, status_code=500),
    )
}


def resolve_workspace_error(
    code: object,
    *,
    status_code: int | None = None,
    retryable: bool | None = None,
) -> WorkspaceErrorDefinition:
    """Return an allow-listed public definition.

    Unknown internal codes never pass through the HTTP boundary: even a
    syntactically stable provider or handler code may reveal implementation
    detail or create an undocumented client dependency.
    """

    normalized = str(code or "").strip().lower()
    if not _STABLE_CODE.fullmatch(normalized):
        normalized = "workspace_request_failed"
    registered = WORKSPACE_ERROR_REGISTRY.get(normalized)
    if registered is None:
        registered = WORKSPACE_ERROR_REGISTRY["workspace_request_failed"]
    return WorkspaceErrorDefinition(
        code=registered.code,
        safe_message=registered.safe_message,
        retryable=registered.retryable if retryable is None else bool(retryable),
        status_code=registered.status_code if status_code is None else status_code,
    )


class WorkspaceAPIException(HTTPException):
    """HTTP exception whose public fields have already been safety-normalized."""

    def __init__(
        self,
        code: object,
        *,
        status_code: int | None = None,
        retryable: bool | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        definition = resolve_workspace_error(
            code,
            status_code=status_code,
            retryable=retryable,
        )
        self.code = definition.code
        self.safe_message = definition.safe_message
        self.retryable = definition.retryable
        super().__init__(
            status_code=definition.status_code,
            detail=definition.code,
            headers=headers,
        )
