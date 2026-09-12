"""One ordered admission of the current authored HTML representation.

This service consumes existing captured metrics; it never adds a Chromium pass.
Token expansion attaches to the static step when placeholder authoring is enabled.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any, Callable, Mapping

from services.authored_admission_context import AdmissionContext, canonical_json, digest_bytes, digest_html
from services.authored_admission_policy import (
    RENDERER_POLICY_FAILED,
    SOURCE_READBACK_FAILED,
    AuthoredConfigurationError,
)
from services.authored_admission_receipt import AdmissionReceipt
from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    BrowserSourceReadbackResult,
    inspect_browser_source_readback,
    stamp_source_image_block_ids,
)
from services.source_content.text_stamping import stamp_source_blocks
from utils.llm_calls.author_slide import validate_authored_slide_html
from utils.renderer_isolation import MAX_HTML_BYTES, RendererPolicyError, duplicate_attribute_counts
from services.authored_rollout_profile import current_rollout_profile


class _RawAttributes(HTMLParser):
    """Preserve the renderer's duplicate-attribute predicate before rewriting."""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        duplicates = duplicate_attribute_counts(attrs)
        if duplicates:
            raise RendererPolicyError(f"duplicate HTML attribute: {duplicates[0][0]}")

    handle_startendtag = handle_starttag


def raw_diagnostics_step(html: str) -> None:
    if not isinstance(html, str):
        raise RendererPolicyError("authored HTML must be a string")
    if not html or len(html.encode("utf-8")) > MAX_HTML_BYTES:
        raise RendererPolicyError("authored HTML source size limit exceeded")
    if "\x00" in html:
        raise RendererPolicyError("authored HTML contains NUL")
    if "__METADATA_ENDPOINT_DO_NOT_RESOLVE__" in html:
        raise RendererPolicyError("reserved metadata endpoint marker is not renderable")
    if current_rollout_profile().html_normalization == "tree":
        from utils.authored_html_tree.raw_diagnostics import diagnose_raw_authored_html

        diagnostics = diagnose_raw_authored_html(html)
        if diagnostics.violations:
            error = RendererPolicyError("authored HTML raw diagnostics failed")
            error.code = diagnostics.violations[0].code
            raise error
    else:
        parser = _RawAttributes(convert_charrefs=True)
        parser.feed(html)
        parser.close()


@dataclass(frozen=True, slots=True)
class Admission:
    html: str
    state: str
    verdict: str
    code: str
    predicate: str
    block_ids: tuple[str, ...]
    receipt: AdmissionReceipt
    readback: BrowserSourceReadbackResult | None = None
    normalizations: tuple[dict[str, Any], ...] = ()
    retryable: bool = False

    @property
    def digests(self) -> dict[str, str | None]:
        return {
            "html": self.receipt.html_digest,
            "metrics": self.receipt.metrics_digest,
            "png": self.receipt.png_digest,
            "context": self.receipt.context.identity_digest,
        }

    def require(self) -> Admission:
        """Raise a rejection that still carries what this admission decided.

        This is the single boundary where a verdict becomes an exception, so it
        is the only place the recovery layers can learn two things the
        ``Admission`` already holds. ``retryable`` is the gate's own
        classification -- a rendered-integrity or visibility rejection is
        deterministic, only telemetry is transient -- and without it the
        operation retry policy falls back to ``is_retryable_exception``, whose
        uncoded tail answers ``True``, so a content rejection buys a full deck
        retry. ``rejected_html`` is the candidate that was rejected, which is
        how the author-stage deferral and the per-slide rescue tell "the
        provider answered and the deck refused the answer" (the dead end a
        third, simplified call answers) from "nothing came back" (the
        operation's own retry). Both lanes already key on these names, so
        forwarding them here keeps the recovery generic instead of teaching
        each lane about this rejection source.
        """

        if self.verdict != "rejected":
            return self
        error = BrowserSourceReadbackError(
            self.code,
            detail=self.predicate,
            block_ids=self.block_ids,
            slide_indices=(self.receipt.context.slide_index,),
        )
        error.predicate = self.predicate
        error.retryable = self.retryable
        if isinstance(self.html, str) and self.html:
            error.rejected_html = self.html
        raise error


@dataclass(frozen=True, slots=True)
class TextStepResult:
    html: str
    state: str = "expanded"
    normalizations: tuple[dict[str, Any], ...] = ()


def bind_current_source(context: AdmissionContext, html: str) -> TextStepResult:
    """T5 replaces this hook; all producers retain the same admission order."""
    if context.source_reflow:
        html = stamp_source_blocks(html, context.bindings).html
        if context.image_bindings and not context.allow_illustration_slots:
            html = stamp_source_image_block_ids(html, context.image_bindings)
    return TextStepResult(html)


def _selected_text_step(context: AdmissionContext, html: str) -> TextStepResult:
    if placeholder_authoring_enabled():
        from services.source_content.placeholder.text_step import select_text_step

        return select_text_step(context, html)
    return bind_current_source(context, html)


text_step: Callable[[AdmissionContext, str], TextStepResult] = _selected_text_step


def placeholder_authoring_enabled() -> bool:
    return current_rollout_profile().placeholder_authoring


def rendered_verdict(
    context: AdmissionContext,
    result: BrowserSourceReadbackResult,
) -> tuple[str, str, tuple[str, ...], bool]:
    """The existing terminal rule never turns integrity/telemetry into flags."""
    if result.missing_block_ids or result.order_violations or result.integrity_failures:
        return (
            "rejected",
            "rendered_integrity",
            tuple(
                dict.fromkeys(
                    (
                        *result.missing_block_ids,
                        *result.integrity_failures,
                    )
                )
            ),
            False,
        )
    if result.telemetry_failures or result.diagnostic_error:
        return "rejected", "telemetry", result.telemetry_failures, True
    if result.visibility_failures:
        verdict = "flag_only" if (context.terminal_fallback_eligible and context.already_admitted_flags) else "rejected"
        return verdict, "visibility", result.visibility_failures, False
    return "admitted", "complete", (), False


def admit_slide_html(
    context: AdmissionContext,
    html: str,
    *,
    metrics: Mapping[str, object] | None = None,
    png: bytes | None = None,
) -> Admission:
    """Raw diagnostics → rewrite/policy → binding → limits → captured readback."""
    predicate = "raw_diagnostics"
    normalizations: list[dict] = []
    expanded = html
    readback = None
    verdict, code, block_ids, retryable = "admitted", "", (), False
    try:
        raw_diagnostics_step(html)
        if context.state == "token" and placeholder_authoring_enabled():
            from services.source_content.placeholder.text_step import check_placeholder_context

            check_placeholder_context(context, html)
        predicate = "rewrite_policy"
        expanded = validate_authored_slide_html(
            html,
            allow_illustration_slots=context.allow_illustration_slots,
            finalized=context.finalized,
            normalization_receipt=normalizations,
        )
        predicate = "static_bindings"
        if context.state == "captured":
            if context.source_reflow and stamp_source_blocks(expanded, context.bindings).html != expanded:
                raise BrowserSourceReadbackError(
                    SOURCE_READBACK_FAILED, detail="source text was not sealed before capture"
                )
        else:
            text_result = text_step(context, expanded)
            expanded = text_result.html
            normalizations.extend(text_result.normalizations)
            context = context.for_state(text_result.state)
        predicate = "post_expansion_policy"
        expanded = validate_authored_slide_html(
            expanded,
            allow_illustration_slots=context.allow_illustration_slots,
            finalized=context.finalized,
            normalization_receipt=normalizations,
        )
        if context.state == "captured":
            predicate = "capture_binding"
            # A captured representation cannot be rewritten after the pixels exist.
            if expanded != html:
                raise BrowserSourceReadbackError(SOURCE_READBACK_FAILED, detail=predicate)
            if metrics is None:
                retryable = True
                raise BrowserSourceReadbackError(SOURCE_READBACK_FAILED, detail="capture telemetry is unavailable")
            if context.source_reflow:
                predicate = "rendered_integrity"
                manifest, projection = context.readback_documents()
                readback = inspect_browser_source_readback(
                    manifest=manifest,
                    outline=projection,
                    browser_metrics=[metrics],
                    htmls=[html],
                )
                verdict, predicate, block_ids, retryable = rendered_verdict(context, readback)
                if verdict == "rejected":
                    code = SOURCE_READBACK_FAILED
        if verdict == "admitted":
            predicate = "complete"
    except AuthoredConfigurationError:
        # The run itself has no coherent policy to judge these bytes against --
        # an operator's knob combination, a job pin this build no longer
        # honours -- which is not a fact about the slide's content. Every
        # exception meaning that carries this marker (see its definition in
        # ``authored_admission_policy``), so this check stays correct as new
        # configuration failures are added elsewhere, without listing them
        # here. It must run before ``except ValueError`` below, which would
        # otherwise treat any coded subclass -- this one included -- as a
        # slide-content rejection.
        raise
    except RendererPolicyError as error:
        verdict, code = "rejected", getattr(error, "code", RENDERER_POLICY_FAILED)
    except BrowserSourceReadbackError as error:
        verdict, code, block_ids = "rejected", error.code, tuple(error.block_ids)
    except ValueError as error:
        if hasattr(error, "code"):
            verdict, code = "rejected", error.code
            block_ids = tuple(getattr(error, "block_ids", ()))
        elif str(error) == SOURCE_READBACK_FAILED:
            verdict, code = "rejected", SOURCE_READBACK_FAILED
        else:
            raise
    # Failed captures stay bound to their input bytes for evidence-only persistence.
    if context.state == "captured":
        expanded = html
    receipt = AdmissionReceipt(
        context=context,
        html_digest=digest_html(expanded),
        verdict=verdict,
        code=code,
        predicate=predicate,
        block_ids=block_ids,
        metrics_digest=digest_html(canonical_json(metrics)) if metrics is not None else None,
        png_digest=digest_bytes(png) if png is not None else None,
        capture_count=int(context.state == "captured" and metrics is not None),
        normalizations=tuple(normalizations),
    )
    return Admission(
        expanded,
        context.state,
        verdict,
        code,
        predicate,
        block_ids,
        receipt,
        readback,
        tuple(normalizations),
        retryable,
    )


def reused_admission(admission: Admission) -> Admission:
    return replace(admission, receipt=replace(admission.receipt, capture_reused=True))
