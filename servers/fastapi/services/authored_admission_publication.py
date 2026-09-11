"""Publication adapter for the central gate and the legacy shadow comparator."""

from __future__ import annotations

from services.authored_admission_policy import INTERNAL_ERROR, SOURCE_READBACK_FAILED

from services.authored_admission_session import current_session
from services.authored_checkpoint_errors import AuthoredStageError
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    BrowserSourceReadbackResult,
    inspect_browser_source_readback,
    verify_browser_source_readback,
    log_browser_source_readback_failure,
)
from services.source_content.readback import SourceContentReadbackError, verify_source_verbatim_readback
from services.authored_terminal_slide_flag_only import source_visibility_flag_entries


def _legacy_final_readback(
    *,
    request,
    htmls,
    source_reflow,
    composition_profile,
    terminal_fallback_enabled,
    evidence_ledger,
    final_browser_metrics,
    flagged_slides,
    _merge_flagged_slide_entries,
):
    source_readback_receipt = None
    source_readback_payload = None
    readback_retryable = True
    if source_reflow and composition_profile.text_mode == "verbatim":
        try:
            readback_retryable = not terminal_fallback_enabled
            source_readback_receipt = verify_source_verbatim_readback(
                manifest=request._source_content_manifest,
                outline=request._source_document_outline,
                htmls=htmls,
            )
            if evidence_ledger is not None:
                browser_source_receipt = (
                    inspect_browser_source_readback if terminal_fallback_enabled else verify_browser_source_readback
                )(
                    manifest=request._source_content_manifest,
                    outline=request._source_document_outline,
                    browser_metrics=final_browser_metrics,
                    htmls=htmls,
                )
                if terminal_fallback_enabled:
                    visibility_entries, unflagged_visibility = source_visibility_flag_entries(
                        outline=request._source_document_outline,
                        block_ids=browser_source_receipt.visibility_failures,
                        flagged_indices=[entry["index"] for entry in flagged_slides],
                    )
                    hard_failure = bool(
                        browser_source_receipt.missing_block_ids
                        or browser_source_receipt.order_violations
                        or browser_source_receipt.integrity_failures
                        or browser_source_receipt.telemetry_failures
                        or browser_source_receipt.diagnostic_error
                    )
                    readback_retryable = bool(
                        browser_source_receipt.telemetry_failures or browser_source_receipt.diagnostic_error
                    )
                    if hard_failure or unflagged_visibility:
                        failure_summary = log_browser_source_readback_failure(browser_source_receipt)
                        raise BrowserSourceReadbackError(
                            SOURCE_READBACK_FAILED,
                            detail=f"browser source text/geometry/visibility contract failed; {failure_summary}",
                            block_ids=tuple(
                                dict.fromkeys(
                                    (
                                        *browser_source_receipt.missing_block_ids,
                                        *browser_source_receipt.visibility_failures,
                                    )
                                )
                            ),
                            slide_indices=browser_source_receipt.failed_slide_indices,
                            order_violations=browser_source_receipt.order_violations,
                        )
                    flagged_slides = _merge_flagged_slide_entries((*flagged_slides, *visibility_entries))
                    object.__setattr__(request, "_authored_flagged_slides", flagged_slides)
                source_readback_receipt = browser_source_receipt
            source_readback_payload = source_readback_receipt.as_dict()
            if terminal_fallback_enabled and evidence_ledger is not None:
                source_readback_payload.update(
                    integrityVerified=True,
                    flaggedVisibilityFailures=list(browser_source_receipt.visibility_failures),
                )
            request._authored_source_readback_receipt = source_readback_payload
        except (SourceContentReadbackError, BrowserSourceReadbackError) as error:
            raise AuthoredStageError(
                slide_index=error.slide_indices[0] if error.slide_indices else 0,
                stage="render",
                code=error.code,
                cause=error,
                retryable=readback_retryable,
                message=error.detail,
            ) from error

    return source_readback_receipt, source_readback_payload, flagged_slides


async def final_readback(*, pngs, generation_job, **kwargs):
    session = current_session()
    if session is None or not session.contexts:
        return _legacy_final_readback(**kwargs)
    htmls, metrics = kwargs["htmls"], kwargs["final_browser_metrics"]
    flagged = {
        entry["index"]: tuple(entry.get("reasons") or (entry.get("reason", ""),)) for entry in kwargs["flagged_slides"]
    }
    try:
        admissions = [
            session.admit(
                session.context(index, captured=True, flags=flagged.get(index, ())),
                html,
                metrics=metrics[index],
                png=pngs[index],
            )
            for index, html in enumerate(htmls)
        ]
    except Exception:
        if session.mode == "enforce":
            raise
        # Instrumentation cannot become a new failure in the shadow rollout.
        session.shadow.record(
            producer="publication",
            existing_verdict="unobserved",
            existing_code="",
            gate_verdict="error",
            gate_code=INTERNAL_ERROR,
        )
        return _legacy_final_readback(**kwargs)
    if session.mode == "shadow":
        try:
            result = _legacy_final_readback(**kwargs)
        except (AuthoredStageError, BrowserSourceReadbackError) as error:
            index = getattr(error, "slide_index", 0)
            for i, admission in enumerate(admissions):
                session.shadow.record(
                    producer="terminal_flag_only" if i in flagged else "publication",
                    existing_verdict="rejected" if i == index else "unobserved",
                    existing_code=error.code if i == index else "",
                    gate_verdict=admission.verdict,
                    gate_code=admission.code,
                )
            raise
        for i, admission in enumerate(admissions):
            session.shadow.record(
                producer="terminal_flag_only" if i in flagged else "publication",
                existing_verdict="flag_only" if i in flagged else "admitted",
                existing_code="",
                gate_verdict=admission.verdict,
                gate_code=admission.code,
            )
        return result
    for admission in admissions:
        if admission.verdict == "rejected":
            raise AuthoredStageError(
                slide_index=admission.receipt.context.slide_index,
                stage="render",
                code=admission.code,
                retryable=admission.retryable,
                message=admission.predicate,
            )
    if generation_job is not None:
        from services.authored_presentation_artifacts import _complete_stage_after_external_success
        from services.authored_checkpoint_html import checkpoint_stage_html

        for index, admission in enumerate(admissions):
            saved = await _complete_stage_after_external_success(
                job_id=generation_job.id,
                slide_index=index,
                stage="render",
                attempt_token=generation_job.attempt_token,
                fencing_token=getattr(generation_job, "_workspace_fencing_token", None),
                html=htmls[index],
                png=pngs[index],
                browser_metrics=metrics[index],
                admission_receipt=admission.receipt,
            )
            if checkpoint_stage_html(saved, publishable=True) != htmls[index]:
                raise BrowserSourceReadbackError(SOURCE_READBACK_FAILED, detail="publishable checkpoint unavailable")
    if not kwargs["source_reflow"]:
        return None, None, kwargs["flagged_slides"]
    results = [admission.readback for admission in admissions]
    if any(result is None for result in results):
        raise BrowserSourceReadbackError(SOURCE_READBACK_FAILED, detail="captured admission readback missing")
    combined = BrowserSourceReadbackResult(
        checked_slide_count=sum(result.checked_slide_count for result in results),
        checked_block_count=sum(result.checked_block_count for result in results),
        evidence=tuple(item for result in results for item in result.evidence),
        visibility_failures=tuple(item for result in results for item in result.visibility_failures),
        binding_deviations=sum(result.binding_deviations for result in results),
        failed_slide_indices=tuple(i for i, result in enumerate(results) if not result.passed),
    )
    payload = combined.as_dict()
    if kwargs["terminal_fallback_enabled"]:
        payload.update(integrityVerified=True, flaggedVisibilityFailures=list(combined.visibility_failures))
    entries, _ = source_visibility_flag_entries(
        outline=kwargs["request"]._source_document_outline,
        block_ids=combined.visibility_failures,
        flagged_indices=list(flagged),
    )
    flagged_slides = kwargs["_merge_flagged_slide_entries"]((*kwargs["flagged_slides"], *entries))
    kwargs["request"]._authored_source_readback_receipt = payload
    return combined, payload, flagged_slides
