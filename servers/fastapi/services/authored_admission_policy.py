"""Shared vocabulary for authored admission and its evidence consumers."""

from __future__ import annotations

from utils.get_env_programme import get_authored_admission_gate_mode


POLICY_REVISION = "authored-admission/v1"
SOURCE_READBACK_FAILED = "source_content_browser_readback_failed"
RENDERER_POLICY_FAILED = "authored_slide_author_failed"
SOURCE_VISIBILITY_REASON = "source_readback:visibility"
VERBATIM_FLAG_ONLY = "verbatim_flag_only"
SOURCE_BLOCK_ATTRIBUTE = "data-source-block-id"
QID_ATTRIBUTE = "data-qid"
SEMANTIC_ROLE_ATTRIBUTE = "data-semantic-role"
CONSUMED_GLOBAL_ATTRIBUTES = frozenset(
    {
        SOURCE_BLOCK_ATTRIBUTE,
        QID_ATTRIBUTE,
        SEMANTIC_ROLE_ATTRIBUTE,
        "data-qid-ignore",
        "data-allow-overlap",
        "data-header-part",
        "data-header-band",
    }
)
PRODUCERS = frozenset(
    {
        "author",
        "author_batch",
        "illustration",
        "author_rescue",
        "gate_repair",
        "vqa_repair",
        "structural_patch",
        "terminal_flag_only",
        "render",
        "publication",
    }
)


def admission_gate_mode() -> str:
    return get_authored_admission_gate_mode()


CAPTURE_MISSING = "authored_admission_capture_missing"
CONTEXT_MISMATCH = "authored_admission_context_mismatch"
FLAG_ONLY_INELIGIBLE = "authored_admission_flag_only_ineligible"
HTML_DIGEST_MISMATCH = "authored_admission_html_digest_mismatch"
INTERNAL_ERROR = "authored_admission_internal_error"
MANIFEST_DIGEST_MISMATCH = "authored_admission_manifest_digest_mismatch"
METRICS_DIGEST_MISMATCH = "authored_admission_metrics_digest_mismatch"
PERSISTENCE_CLASS_INVALID = "authored_admission_persistence_class_invalid"
PNG_DIGEST_MISMATCH = "authored_admission_png_digest_mismatch"
PRODUCER_INVALID = "authored_admission_producer_invalid"
PROJECTION_DIGEST_MISMATCH = "authored_admission_projection_digest_mismatch"
RECEIPT_MISSING = "authored_admission_receipt_missing"
RECEIPT_REJECTED = "authored_admission_receipt_rejected"
REPRESENTATION_INVALID = "authored_admission_representation_invalid"
UNCODED_ERROR = "authored_admission_uncoded_error"
