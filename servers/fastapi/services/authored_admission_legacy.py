"""Legacy shadow-only rescue comparator; enforce delegates only to the gate."""

from __future__ import annotations

from services.authored_admission_policy import SOURCE_READBACK_FAILED
from collections.abc import Sequence
from typing import Any, Mapping
from services.authored_checkpoint_errors import AuthoredStageError
from services.authored_admission_policy import SOURCE_VISIBILITY_REASON, VERBATIM_FLAG_ONLY
from services.authored_terminal_slide_flag_only import flagged_entry
from services.source_content.browser_readback import BrowserSourceReadbackError, log_browser_source_readback_failure


def legacy_rescue_readback(
    *,
    inspector,
    fallback_policy,
    index: int,
    html: str,
    metrics: Mapping[str, Any],
    bindings: Sequence[tuple[str, str]],
    profile: Any,
    reference_mode: str,
    prior_failure: Mapping[str, object] | None = None,
    provider_attempt_count: int = 3,
) -> dict[str, object] | None:
    """Route a rescued source slide through the canonical browser predicate."""

    unit_id = f"author-rescue:{int(index)}"
    block_ids = [str(block_id) for block_id, _text in bindings]
    result = inspector(
        manifest={
            "units": [
                {
                    "sourceUnitId": unit_id,
                    "sourceIndex": 0,
                    "blocks": [
                        {"sourceBlockId": block_id, "kind": "text", "order": order, "payload": {"text": text}}
                        for order, (block_id, text) in enumerate(bindings)
                    ],
                }
            ]
        },
        outline={
            "slides": [
                {
                    "sourceUnitId": unit_id,
                    "sourceIndex": 0,
                    "readingOrder": block_ids,
                }
            ]
        },
        browser_metrics=[metrics],
        htmls=[html],
    )
    hard_failure = bool(
        result.missing_block_ids
        or result.order_violations
        or result.integrity_failures
        or result.telemetry_failures
        or result.diagnostic_error
    )
    fallback = fallback_policy(profile=profile, reference_mode=reference_mode, source_reflow=True)
    if hard_failure or (result.visibility_failures and not fallback):
        summary = log_browser_source_readback_failure(result)
        error = BrowserSourceReadbackError(
            SOURCE_READBACK_FAILED,
            detail=f"author rescue source readback failed; {summary}",
            block_ids=tuple(
                dict.fromkeys(
                    (
                        *result.missing_block_ids,
                        *result.integrity_failures,
                        *result.telemetry_failures,
                        *result.visibility_failures,
                    )
                )
            ),
            slide_indices=(int(index),),
            order_violations=result.order_violations,
        )
        raise AuthoredStageError(
            slide_index=index,
            stage="author",
            code=error.code,
            cause=error,
            retryable=False,
            message=error.detail,
            provider_attempt_count=provider_attempt_count,
            prior_failure=prior_failure,
        ) from error
    if not result.visibility_failures:
        return None
    entry = flagged_entry(
        index=index,
        reason=SOURCE_VISIBILITY_REASON,
        attempted=False,
        outcome=VERBATIM_FLAG_ONLY,
    )
    prior_code = str((prior_failure or {}).get("code") or "").strip()
    entry["reasons"] = [
        SOURCE_VISIBILITY_REASON,
        *([f"author_prior:{prior_code}"[:160]] if prior_code else []),
    ]
    return entry
