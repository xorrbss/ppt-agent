"""Attachment-specific safe workspace errors."""

from services.workspace_error_types import definition


ASSET_ERROR_DEFINITIONS = (
    definition("workspace_asset_id_required", "Upload the attachment before submitting this request.", status_code=400),
    definition("workspace_asset_hash_invalid", "The attachment metadata is invalid.", status_code=400),
    definition("workspace_asset_hash_mismatch", "The attachment changed and must be uploaded again.", status_code=409),
    definition(
        "workspace_asset_reprocess_required",
        "Reprocess the failed attachment before retrying.",
        retryable=True,
        status_code=409,
    ),
    definition(
        "workspace_asset_reattach_required", "Replace the unavailable attachment before retrying.", status_code=409
    ),
    definition("workspace_asset_media_type_invalid", "The attachment type is not supported.", status_code=400),
    definition(
        "workspace_asset_media_type_mismatch",
        "The attachment content does not match its declared type.",
        status_code=400,
    ),
    definition("workspace_asset_empty", "The attachment is empty.", status_code=400),
    definition(
        "workspace_asset_signature_mismatch",
        "The attachment content does not match its file type.",
        status_code=400,
    ),
    definition(
        "workspace_asset_malware_scan_mode_invalid",
        "Attachment safety scanning is unavailable.",
        retryable=True,
        status_code=503,
    ),
    definition(
        "workspace_asset_malware_scanner_unavailable",
        "Attachment safety scanning is unavailable.",
        retryable=True,
        status_code=503,
    ),
    definition(
        "workspace_asset_malware_scan_failed",
        "The attachment safety check could not be completed.",
        retryable=True,
        status_code=503,
    ),
    definition("workspace_asset_malware_detected", "The attachment did not pass the safety check.", status_code=400),
    definition("workspace_asset_file_type_invalid", "The attachment type is not supported.", status_code=400),
    definition(
        "workspace_asset_storage_invalid", "The attachment could not be stored.", retryable=True, status_code=500
    ),
    definition(
        "workspace_asset_source_changed",
        "The attachment changed while it was being imported.",
        retryable=True,
        status_code=409,
    ),
    definition("workspace_asset_too_large", "The attachment exceeds the upload limit.", status_code=413),
    definition("workspace_asset_upload_invalid", "The attachment could not be uploaded.", status_code=400),
    definition("workspace_conversation_not_found", "The workspace conversation was not found.", status_code=404),
    definition(
        "workspace_asset_version_conflict",
        "The attachment changed concurrently. Reload and try again.",
        retryable=True,
        status_code=409,
    ),
    definition(
        "workspace_asset_transition_invalid",
        "The requested attachment state transition is invalid.",
        status_code=409,
    ),
)
