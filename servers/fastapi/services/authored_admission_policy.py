"""Shared vocabulary for authored admission and its evidence consumers."""

from __future__ import annotations

import hashlib
import json
from typing import Any


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
#: The three representations of slide HTML, in the order they may occur.
HTML_REPRESENTATIONS = ("token", "expanded", "captured")
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
POLICY_REVISION_MISMATCH = "authored_admission_policy_revision_mismatch"
ROLLOUT_PROFILE_INVALID = "authored_rollout_profile_invalid"
ROLLOUT_PROFILE_MISMATCH = "authored_rollout_profile_mismatch"


def _canonical(value: Any) -> Any:
    """Order-independent rendering of the policy surface, sets included."""
    if isinstance(value, (set, frozenset)):
        return sorted(str(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def _content_digest() -> str:
    """Digest the markup policy and this module's vocabulary, once, at import.

    A receipt that claims a policy revision has to mean the policy that judged
    it.  The two contents that decide an admission's verdict are the renderer's
    markup allowlists and the vocabulary below; digesting them is what turns
    ``policy_revision`` from a hand-maintained constant into a value that moves
    when the policy does.  Module *contents* are digested, not module source
    text, so the revision is stable under comments, formatting and a frozen
    (source-less) PyInstaller build, and moves on every policy change.
    """
    from utils.renderer_markup_policy import (
        ALLOWED_TAGS,
        DROP_TAGS,
        GLOBAL_ATTRIBUTES,
        SERVER_STAMPED_ATTRIBUTES,
        TAG_ATTRIBUTES,
        VOID_TAGS,
    )

    surface = {
        "allowed_tags": ALLOWED_TAGS,
        "consumed_global_attributes": CONSUMED_GLOBAL_ATTRIBUTES,
        "drop_tags": DROP_TAGS,
        "global_attributes": GLOBAL_ATTRIBUTES,
        "html_representations": HTML_REPRESENTATIONS,
        "producers": PRODUCERS,
        "server_stamped_attributes": SERVER_STAMPED_ATTRIBUTES,
        "tag_attributes": TAG_ATTRIBUTES,
        "vocabulary": sorted(
            value
            for name, value in sorted(globals().items())
            if name.isupper() and isinstance(value, str) and name != "POLICY_REVISION_BASE"
        ),
        "void_tags": VOID_TAGS,
    }
    payload = json.dumps(_canonical(surface), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


#: Identifies the admission policy itself. The rollout profile a deck ran under
#: is appended by :func:`policy_revision_for`, so two decks authored under
#: different switches never share one revision.
POLICY_REVISION_BASE = "authored-admission/v2+" + _content_digest()


def policy_revision_for(rollout_digest: str) -> str:
    """The revision a receipt records: policy contents plus rollout profile."""
    return f"{POLICY_REVISION_BASE}+{rollout_digest[:16]}"
