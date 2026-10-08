"""Apply the deck's TerminalPolicy to one SVG page that ran out of attempts.

The page attempt loop (``collect._page``) raises ``AuthoredStageError`` when a
page exhausts its budget. Before this module that error cancelled every
sibling page and failed the deck. :func:`rescue_terminal_page` is the one place
the SVG driver decides otherwise, from ``ctx.runtime_profile.terminal_policy``:

* ``fallback``  -- render :func:`fallback.fallback_page_svg` from the slide's
  outline, flag it ``author:attempts_exhausted`` (the authored placeholder's
  reason, so the flag classification treats it as the substitute it is);
* ``flag_only`` -- keep the page the payload last rendered and measured, flag it
  (source reflow: verbatim text may not be re-authored);
* ``fail``      -- return ``None``; the caller re-raises, as before.

Only the attempt-budget dead end is rescued, as in the authored driver: a lost
lease, an uncertain provider purchase, a provider outage or an asset-pipeline
failure keep failing the deck, because a substitute page cannot answer them.
"""

from __future__ import annotations

import logging

from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.quality.terminal_policy import TerminalPolicy
from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
from services.svg_page_publication import set_svg_page
from utils.hashing import sha256_hex

LOGGER = logging.getLogger(__name__)

#: ``AuthoredStageError.code`` values that mean "this page is out of attempts".
EXHAUSTED_PAGE_CODES = frozenset({
    "svg_validation_failed", "svg_repair_budget_exhausted", "svg_source_quality_failed",
})

#: The authored author-stage placeholder's reason; ``author:`` marks a substitute.
FALLBACK_REASON = "author:attempts_exhausted"
FALLBACK_OUTCOME = "svg_fallback_page"
FLAG_ONLY_OUTCOME = "verbatim_flag_only"

#: Payload keys a rescued page keeps from the attempt it replaces: identity for
#: resume, and the attempt history the receipt already reports.
_IDENTITY_KEYS = ("schema_version", "source_format", "input_digest", "source_manifest_ref",
                  "source_manifest_digest", "attempts", "model_attempt_count", "provider_failures",
                  "initial_quality")


def terminal_policy_of(ctx) -> TerminalPolicy:
    profile = getattr(ctx, "runtime_profile", None)
    return getattr(profile, "terminal_policy", TerminalPolicy.FAIL)


def is_exhausted_page(error: BaseException, index: int) -> bool:
    return (isinstance(error, AuthoredStageError) and not error.retryable
            and error.slide_index == index and error.code in EXHAUSTED_PAGE_CODES)


async def rescue_terminal_page(*, ctx, request, index, content, payload, error, job, checkpoint, write_png):
    """``(payload, png)`` for a rescued page, or ``None`` to keep failing."""

    policy = terminal_policy_of(ctx)
    if policy is TerminalPolicy.FAIL or not is_exhausted_page(error, index):
        return None
    from services.svg_authoring_profile import read_quality_recovery_policy
    if read_quality_recovery_policy(getattr(ctx, "snapshot", {}) or {}):
        # Validated adaptation is reserved by collect in page order and uses its
        # normal author/render callbacks. A terminal placeholder cannot pass it.
        from services.generation_core.drivers.svg.quality_recovery import actual_provider_attempt_count
        prior = payload.get("quality_recovery_fallback") or {}
        payload["quality_recovery_fallback"] = {"status": "blocked",
            "reason": "validated_adaptation_exhausted" if actual_provider_attempt_count(payload) >= 3
                else "validated_candidate_unavailable", "prior_reservation": prior or None}
        await checkpoint(job, index, payload=payload)
        return None
    if policy is TerminalPolicy.FALLBACK:
        from services.svg_authoring_profile import read_authoring_profile

        profile = read_authoring_profile(getattr(ctx, "snapshot", {}) or {})
        if profile and profile["version"] in (6, 7, 8):
            # This candidate's source completeness is mandatory. The generic
            # outline placeholder truncates it and cannot rescue the page.
            return None
    if policy is TerminalPolicy.FLAG_ONLY:
        return await _flag_only(ctx=ctx, index=index, payload=payload, error=error, job=job,
                                checkpoint=checkpoint)
    return await _fallback(ctx=ctx, request=request, index=index, content=content, payload=payload,
                           error=error, job=job, checkpoint=checkpoint, write_png=write_png)


async def _flag_only(*, ctx, index, payload, error, job, checkpoint):
    svg, quality = payload.get("svg_content"), payload.get("quality")
    if not svg or not isinstance(quality, dict) or not payload.get("png_sha256") or (
            payload.get("source_digest") != sha256_hex(svg.encode())):
        return None  # no rendered page to keep: the deck still fails
    if ctx.source_reflow:
        from services.svg_source_layout import verify_source_receipt
        try:
            verify_source_receipt(svg, payload.get("source_preservation"), ctx.source_document, index)
        except ValueError:
            return None  # the kept page must still be the verbatim source page
    payload["terminal"] = flagged_entry(index=index, reason=f"svg:{error.code}", attempted=False,
                                        outcome=FLAG_ONLY_OUTCOME)
    await checkpoint(job, index, payload=payload)
    LOGGER.warning("[svg] terminal page kept flag-only slide=%d code=%s", index + 1, error.code)
    return payload, None


async def _fallback(*, ctx, request, index, content, payload, error, job, checkpoint, write_png):
    from services.generation_core.drivers.svg.fallback import fallback_page_svg
    from services.svg_presentation_checkpoint import _required_numeric_claims
    from services.svg_renderer import render_svg_with_metrics

    svg = fallback_page_svg(content=content, role=ctx.roles[index], index=index, count=ctx.count,
                            width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                            style_snapshot=ctx.snapshot)
    try:
        async with ctx.render_semaphore:
            rendered = await render_svg_with_metrics(
                svg, width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                instructions=request.instructions, required_text=_required_numeric_claims(content),
                **ctx.physical_kwargs)
    except ValueError:
        LOGGER.warning("[svg] fallback page render failed slide=%d", index + 1, exc_info=True)
        return None
    rescued = {key: payload[key] for key in _IDENTITY_KEYS if key in payload}
    fields = {}
    set_svg_page(fields, svg_content=svg, content={"svgReceipt": rescued}, quality=rendered.quality)
    rescued = fields["content"]["svgReceipt"]
    rescued.update(svg_content=fields["svg_content"], **write_png(rendered.png),
                   terminal=flagged_entry(index=index, reason=FALLBACK_REASON, attempted=False,
                                          outcome=FALLBACK_OUTCOME),
                   terminal_error=error.code)
    await checkpoint(job, index, payload=rescued)
    LOGGER.warning("[svg] terminal page replaced by fallback slide=%d code=%s", index + 1, error.code)
    return rescued, rendered.png


__all__ = [
    "EXHAUSTED_PAGE_CODES", "FALLBACK_OUTCOME", "FALLBACK_REASON", "FLAG_ONLY_OUTCOME",
    "is_exhausted_page", "rescue_terminal_page", "terminal_policy_of",
]
