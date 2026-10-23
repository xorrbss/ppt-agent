"""Classify a terminal deck-generation failure code into a user-facing class.

Durable operations record ~60 distinct machine codes, and the public error
registry collapses almost all of them into one generic message. A user who
watched a deck stop needs to know which of a handful of situations they are
in, because the right next step differs: resuming the remaining pages,
starting over with a different document, or waiting for a limit to reset.

This module is the single definition of that mapping. Codes are classified by
what the code that raises them means (see the raising sites under
``services/``), not by their names alone. A code with no confident meaning is
``unknown``; ``None`` stays ``None``.
"""

from __future__ import annotations

from typing import get_args

from models.workspace import GenerationFailureClass

GENERATION_FAILURE_CLASSES: tuple[GenerationFailureClass, ...] = get_args(
    GenerationFailureClass
)

# Terminal failure states of a ``presentation.generate`` operation that carry a
# failure class on the timeline. ``cancelled`` is the user's own decision.
GENERATION_FAILURE_STATES = frozenset(
    {"failed", "dead_letter", "deadline_exceeded", "expired"}
)

_PROVIDER_UNSTABLE = frozenset(
    {
        "public_provider_call_timeout",
        "public_provider_timeout",
        "public_provider_overload",
        "public_provider_result_unknown",
        "public_provider_bridge_exception",
        "public_provider_response_incomplete",
        "public_provider_response_failed",
        "public_provider_postresponse_failed",
        "public_provider_empty_output",
        "public_provider_stale_attempt",
        # The operation's planned call/retry allowance ran out because calls
        # kept failing; partial recovery re-admits exactly these two codes.
        "public_provider_request_limit_exceeded",
        "public_provider_retry_limit_exceeded",
        "svg_provider_attempt_uncertain",
        # The streamed terminal receipt does not match the received output, so
        # the provider result itself is uncertain (svg author driver).
        "svg_provider_terminal_output_mismatch",
    }
)

_QUALITY_NOT_PASSED = frozenset(
    {
        "svg_source_quality_failed",
        "svg_validation_failed",
        "svg_repair_budget_exhausted",
        "svg_source_native_table_group_invalid",
        # Rendered element ids do not bind to the layout receipt: an output
        # validation failure (svg_source_layout), recoverable by remaking pages.
        "svg_source_receipt_binding_mismatch",
        # Renderer-policy rejection or token-capped output of the author stage.
        "authored_slide_author_failed",
        # Renderer-policy rejection of the illustrated slide.
        "authored_slide_illustrate_failed",
        # Final capacity gate after the bounded repair cycle could not fit the
        # slide content.
        "authored_slide_capacity_exceeded",
        "authored_semantic_validation_failed",
        "authored_vqa_residual_dom_issues",
        "composer_render_plan_invalid",
        "composer_render_qid_invalid",
        "composer_blocking_issues_remaining",
    }
)

_SERVICE_INTERRUPTED = frozenset(
    {
        "operation_worker_crashed",
        "operation_lease_lost",
        "operation_recovery_required",
        "workspace_operation_lease_lost",
        "workspace_operation_internal_error",
        "presentation_generation_failed",
        "presentation_generation_job_missing",
        "presentation_generation_job_not_linked",
        "authored_checkpoint_evidence_corrupt",
        # The stored header-design checkpoint no longer matches its owner.
        "svg_header_design_checkpoint_conflict",
        # The source pipeline ran without a tenant context; the timeline offers
        # a verified retry once the handoff is repaired.
        "raster_tenant_required",
    }
)

_SOURCE_UNPROCESSABLE = frozenset(
    {
        # Locked source-verbatim content cannot fit the slide envelope.
        "authored_source_verbatim_capacity_exceeded",
        # The svg render driver treats both as a source-plan failure (author
        # stage): the source's text/image structure could not be placed.
        "svg_source_classification_missing",
        "svg_source_image_unknown_slot",
        "svg_required_source_image_missing",
        "composer_source_table_structure_mismatch",
    }
)

_LIMIT_REACHED = frozenset(
    {
        "public_deadline_exceeded",
        "public_deadline_insufficient",
        "retry_amplification_ceiling_exceeded",
        "public_provider_actual_cost_limit_exceeded",
        "public_provider_estimated_cost_limit_exceeded",
        "public_provider_usage_limit_paused",
        "public_slide_limit_exceeded",
        "public_owner_active_limit_exceeded",
        "public_owner_active_resource_limit_exceeded",
        "public_tenant_active_limit_exceeded",
        "public_tenant_active_resource_limit_exceeded",
        "public_global_active_limit_exceeded",
        "public_global_active_resource_limit_exceeded",
    }
)

_EXPLICIT: dict[str, GenerationFailureClass] = {
    "internal_defect": "internal_defect",
    "svg_checkpoint_missing": "internal_defect",
    "svg_asset_revision_binding_invalid": "internal_defect",
    "public_generation_transition_invalid": "internal_defect",
    **{code: "provider_unstable" for code in _PROVIDER_UNSTABLE},
    **{code: "quality_not_passed" for code in _QUALITY_NOT_PASSED},
    **{code: "service_interrupted" for code in _SERVICE_INTERRUPTED},
    **{code: "source_unprocessable" for code in _SOURCE_UNPROCESSABLE},
    **{code: "limit_reached" for code in _LIMIT_REACHED},
}

# ``source_content_*`` mixes input rejections with checks of the restyled
# output. These readback/overflow codes fire when the rendered slide failed
# to reproduce the source text (authored admission), so they are quality.
_SOURCE_CONTENT_OUTPUT_CHECKS = frozenset(
    {
        "source_content_browser_readback_failed",
        "source_content_browser_readback_invalid",
        "source_content_verbatim_readback_failed",
        "source_content_verbatim_readback_invalid",
        "source_content_image_readback_failed",
        "source_content_template_overflow",
    }
)

# Ordered (prefix, suffix, class) families whose every member shares one
# meaning at its raising sites. The first match wins.
_FAMILIES: tuple[tuple[str, str, GenerationFailureClass], ...] = (
    ("svg_common_header_", "", "quality_not_passed"),
    ("svg_quality_recovery_review_", "", "quality_not_passed"),
    ("authored_local_fit_", "", "quality_not_passed"),
    ("authored_", "_gate_failed", "quality_not_passed"),
    # The saved inputs a partial recovery reuses changed under it.
    ("svg_partial_recovery_", "_changed", "service_interrupted"),
    ("http_error_5", "", "service_interrupted"),
    ("", "_fence_conflict", "service_interrupted"),
    ("", "_fence_lost", "service_interrupted"),
    ("", "_lease_lost", "service_interrupted"),
    ("source_content_", "", "source_unprocessable"),
    ("svg_uploaded_design_reference_", "", "source_unprocessable"),
    # Without an exact price the cost ceiling cannot be enforced, so the
    # budget guard refuses the call; resuming repeats the same refusal.
    ("public_provider_pricing_", "", "limit_reached"),
)


# Deliberately ``unknown`` (no single meaning at the raising site):
# ``svg_asset_capture_failed`` (any render ValueError while canonical assets are
# bound, whether from the output or the renderer), ``generation_profile_conflict``
# and ``generation_strategy_capability_missing`` (request/profile mismatches
# that neither resuming nor a new document addresses).


def classify_generation_failure(code: str | None) -> GenerationFailureClass | None:
    """Return the failure class of a terminal generation code, or ``None``."""

    if code is None:
        return None
    normalized = code.strip().partition(":")[0]
    if not normalized:
        return None
    explicit = _EXPLICIT.get(normalized)
    if explicit is not None:
        return explicit
    if normalized in _SOURCE_CONTENT_OUTPUT_CHECKS:
        return "quality_not_passed"
    for prefix, suffix, failure_class in _FAMILIES:
        if normalized.startswith(prefix) and normalized.endswith(suffix):
            return failure_class
    return "unknown"


def generation_operation_failure_class(
    *, kind: str | None, state: str | None, error_code: str | None
) -> GenerationFailureClass | None:
    """The class a timeline shows for one operation row.

    Non-null only for a ``presentation.generate`` operation in a terminal
    failure state; a failure that recorded no code is ``unknown``.
    """

    if kind != "presentation.generate" or state not in GENERATION_FAILURE_STATES:
        return None
    return classify_generation_failure(error_code) or "unknown"
