"""Rescue one SVG page that ran out of attempts instead of failing its deck.

The page attempt loop (``collect._page``) raises ``AuthoredStageError`` when a
page exhausts its budget. :func:`rescue_terminal_page` is the one place the SVG
driver decides what publishes in its place. A page's quality never fails the
deck; only a deck class that may not publish a flagged page does
(``ctx.runtime_profile.terminal_policy`` is ``fail``: an exact raster
reconstruction, or a rollout switch turned off). Every other deck walks the
same ladder and publishes the first page that renders, flagged:

a. **keep** the page the payload last rendered and measured, when all of its
   failures are layout failures (``quality/page_failure_kind.py``) and, for a
   source-reflow deck, it is still the verbatim source page -- ``svg:<code>``,
   ``verbatim_flag_only``;
b. **complete-content fallback**: an ordinary deck renders
   :func:`fallback.fallback_page_svg` from the slide's whole outline
   (``author:attempts_exhausted``, ``svg_fallback_page``); a source-reflow deck
   renders :func:`source_fallback.source_fallback_page`, a plain verbatim layout
   of the page's source units that must pass ``verify_source_receipt``
   (``author:source_verbatim_fallback``, ``svg_source_fallback_page``);
c. **failure placeholder**: the slide's title on a plain page
   (``author:placeholder``, ``svg_failure_placeholder``) -- the deck fails only
   when even this cannot render.

A deck under a quality-recovery policy reserves its validated adaptation first
(``collect._page`` returns ``ScreenRecoveryPending``); a page that reaches this
module under that policy records why adaptation did not rescue it and then
takes the ladder like any other page.

Only a page-quality error of the page itself is rescued
(``quality/page_failure_kind.page_error_class``): a lost lease, broken evidence,
an uncertain provider purchase or a provider outage keep failing the deck,
because a substitute page cannot answer them.
"""

from __future__ import annotations

from copy import deepcopy
import logging

from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.quality.flag_classification import FAILURE_PLACEHOLDER_OUTCOME
from services.generation_core.quality.page_failure_kind import (
    PAGE_QUALITY, page_error_class, quality_keeps_content,
)
from services.generation_core.quality.terminal_policy import TerminalPolicy
from services.generation_core.quality.terminal_slide_flag_only import flagged_entry
from services.svg_page_publication import set_svg_page
from utils.hashing import sha256_hex

LOGGER = logging.getLogger(__name__)

#: The authored author-stage placeholder's reason; ``author:`` marks a substitute.
FALLBACK_REASON = "author:attempts_exhausted"
FALLBACK_OUTCOME = "svg_fallback_page"
FLAG_ONLY_OUTCOME = "verbatim_flag_only"
SOURCE_FALLBACK_REASON = "author:source_verbatim_fallback"
SOURCE_FALLBACK_OUTCOME = "svg_source_fallback_page"
PLACEHOLDER_REASON = "author:placeholder"
PLACEHOLDER_OUTCOME = FAILURE_PLACEHOLDER_OUTCOME

#: Payload keys a rescued page keeps from the attempt it replaces: identity for
#: resume, the attempt history the receipt already reports, and why a
#: quality-recovery adaptation did not rescue it.
_IDENTITY_KEYS = ("schema_version", "source_format", "input_digest", "source_manifest_ref",
                  "source_manifest_digest", "attempts", "model_attempt_count", "provider_failures",
                  "initial_quality", "quality_recovery_fallback")


def terminal_policy_of(ctx) -> TerminalPolicy:
    profile = getattr(ctx, "runtime_profile", None)
    return getattr(profile, "terminal_policy", TerminalPolicy.FAIL)


def is_exhausted_page(error: BaseException, index: int) -> bool:
    """Whether ``error`` ends this page on its quality: a final, page-level
    ``AuthoredStageError`` whose code is ``PAGE_QUALITY``."""

    return (isinstance(error, AuthoredStageError) and not error.retryable
            and error.slide_index == index and page_error_class(error.code) == PAGE_QUALITY)


async def rescue_terminal_page(*, ctx, request, index, content, payload, error, job, checkpoint, write_png,
                               read_png=None, presentation_id=None, async_session_maker=None):
    """``(payload, png)`` for a rescued page, or ``None`` to keep failing.

    ``png`` is ``None`` for a source-reflow page, as for every page of such a
    deck: publication reads its image from the checkpoint store.
    """

    policy = terminal_policy_of(ctx)
    if policy is TerminalPolicy.FAIL or not is_exhausted_page(error, index):
        return None
    from services.svg_templates.generation import template_selection, validate_template_publication_page
    selection = template_selection(getattr(ctx, "snapshot", {}) or {})
    template_redesign = bool(selection and selection["mode"] == "template-redesign")
    if template_redesign:
        svg = _last_rendered_svg(payload)
        if svg is None:
            return None
        try:
            validate_template_publication_page(ctx.snapshot, {**payload, "svg_content": svg},
                                               index=index, count=ctx.count, content=content)
        except (ValueError, TypeError, KeyError, IndexError):
            return None
    from services.svg_authoring_profile import read_quality_recovery_policy
    if read_quality_recovery_policy(getattr(ctx, "snapshot", {}) or {}):
        # Evidence only: the validated adaptation (reserved by collect in page
        # order) did not rescue this page, so the ladder below does.
        from services.generation_core.drivers.svg.quality_recovery import actual_provider_attempt_count
        prior = payload.get("quality_recovery_fallback") or {}
        payload["quality_recovery_fallback"] = {"status": "blocked",
            "reason": "validated_adaptation_exhausted" if actual_provider_attempt_count(payload) >= 3
                else "validated_candidate_unavailable",
            # Only a spent reservation is evidence worth keeping; an earlier
            # "blocked" marker is the one this replaces.
            "prior_reservation": prior if prior.get("status") == "reserved" else None}
        await checkpoint(job, index, payload=payload)
    kept = await _flag_only(ctx=ctx, index=index, payload=payload, error=error, job=job,
                            checkpoint=checkpoint, read_png=read_png)
    if kept is not None:
        return kept
    if template_redesign:
        # Generic substitutes have no framed body/furniture preservation proof.
        return None
    if ctx.source_reflow:
        rescued = await _source_fallback(ctx=ctx, request=request, presentation_id=presentation_id,
            index=index, content=content, payload=payload, error=error, job=job, checkpoint=checkpoint,
            write_png=write_png, async_session_maker=async_session_maker)
    else:
        rescued = await _fallback(ctx=ctx, request=request, index=index, content=content, payload=payload,
                                  error=error, job=job, checkpoint=checkpoint, write_png=write_png)
    if rescued is not None:
        return rescued
    return await _placeholder(ctx=ctx, request=request, index=index, content=content, payload=payload,
                              error=error, job=job, checkpoint=checkpoint, write_png=write_png)


def _last_rendered_svg(payload):
    """The SVG the payload's ``quality``/``png_sha256`` measured, or ``None``.

    A validation failure clears ``svg_content`` while ``last_valid_svg`` still
    holds the page that was rendered; the render's ``source_digest`` is what
    binds either one to the measurement.
    """

    for svg in (payload.get("svg_content"), payload.get("last_valid_svg")):
        if svg and payload.get("source_digest") == sha256_hex(svg.encode()):
            return svg
    return None


async def _flag_only(*, ctx, index, payload, error, job, checkpoint, read_png):
    svg, quality = _last_rendered_svg(payload), payload.get("quality")
    if svg is None or not isinstance(quality, dict) or not payload.get("png_sha256"):
        return None  # no rendered page to keep
    if not quality_keeps_content(quality):
        return None  # the kept page would lose content: the fallback holds all of it
    png = None
    if ctx.source_reflow:
        from services.svg_source_layout import verify_source_receipt
        try:
            verify_source_receipt(svg, payload.get("source_preservation"), ctx.source_document, index)
        except (ValueError, TypeError, KeyError):
            return None  # the kept page must still be the verbatim source page
    else:
        png = read_png(payload) if read_png is not None else None
        if png is None or sha256_hex(png) != payload["png_sha256"]:
            return None
    payload["svg_content"] = svg
    payload["terminal"] = flagged_entry(index=index, reason=f"svg:{error.code}", attempted=False,
                                        outcome=FLAG_ONLY_OUTCOME)
    _bind_recovery_evidence(ctx, payload)
    await checkpoint(job, index, payload=payload)
    LOGGER.warning("[svg] terminal page kept flag-only slide=%d code=%s", index + 1, error.code)
    return payload, png


def _rescued_identity(payload):
    return {key: deepcopy(payload[key]) for key in _IDENTITY_KEYS if key in payload}


def _bind_recovery_evidence(ctx, payload):
    """Bind a rescued page like any rendered page of a quality-recovery deck,
    so a resume reuses it (``collect._page``) instead of calling it stale."""

    binding = getattr(ctx, "quality_recovery_input_binding", None)
    if binding:
        from services.generation_core.drivers.svg.quality_recovery import local_evidence_binding
        payload["quality_recovery_local_evidence"] = local_evidence_binding(binding, payload)


async def _fallback(*, ctx, request, index, content, payload, error, job, checkpoint, write_png):
    from services.generation_core.drivers.svg.fallback import fallback_page_svg
    from services.svg_presentation_checkpoint import _required_numeric_claims
    from services.svg_renderer import render_svg_with_metrics

    try:
        header_pages = getattr(ctx, "header_pages", None)
        svg = fallback_page_svg(content=content, role=ctx.roles[index], index=index, count=ctx.count,
                                width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                                style_snapshot=ctx.snapshot,
                                header_page=header_pages[index] if header_pages else None)
        async with ctx.render_semaphore:
            rendered = await render_svg_with_metrics(
                svg, width=ctx.canvas.width_px, height=ctx.canvas.height_px,
                instructions=request.instructions, required_text=_required_numeric_claims(content),
                **ctx.physical_kwargs)
    except ValueError:
        LOGGER.warning("[svg] fallback page render failed slide=%d", index + 1, exc_info=True)
        return None
    return await _publish_substitute(ctx=ctx, svg=svg, rendered=rendered, index=index, payload=payload, error=error,
        job=job, checkpoint=checkpoint, write_png=write_png, reason=FALLBACK_REASON,
        outcome=FALLBACK_OUTCOME, png_returned=True)


async def _publish_substitute(*, ctx, svg, rendered, index, payload, error, job, checkpoint, write_png,
                              reason, outcome, png_returned):
    try:
        fields = {}
        set_svg_page(fields, svg_content=svg, content={"svgReceipt": _rescued_identity(payload)},
                     quality=rendered.quality)
    except ValueError:
        LOGGER.warning("[svg] substitute page measurement unbound slide=%d", index + 1, exc_info=True)
        return None
    rescued = fields["content"]["svgReceipt"]
    rescued.update(svg_content=fields["svg_content"], source_digest=sha256_hex(fields["svg_content"].encode()),
                   **write_png(rendered.png),
                   terminal=flagged_entry(index=index, reason=reason, attempted=False, outcome=outcome),
                   terminal_error=error.code)
    _bind_recovery_evidence(ctx, rescued)
    await checkpoint(job, index, payload=rescued)
    LOGGER.warning("[svg] terminal page replaced slide=%d outcome=%s code=%s", index + 1, outcome, error.code)
    return rescued, (rendered.png if png_returned else None)


def _source_fallback_floor(ctx, request):
    from services.document_canvas_generation import source_restyle_font_floor

    floor = source_restyle_font_floor(request.instructions, getattr(request, "_document_canvas", None))
    if ctx.snapshot.get("readability_policy") is not None:
        from services.document_canvas_generation import svg_quality_font_floor

        floor = max(floor, svg_quality_font_floor(ctx.snapshot["readability_policy"]))
    return floor


async def _source_fallback(*, ctx, request, presentation_id, index, content, payload, error, job,
                           checkpoint, write_png, async_session_maker):
    from services.generation_core.drivers.svg import render
    from services.generation_core.drivers.svg.source_fallback import SOURCE_FALLBACK_VERSION, source_fallback_page
    from services.svg_contract import source_digest
    from services.svg_source_layout import verify_source_receipt

    header_pages = getattr(ctx, "header_pages", None)
    try:
        svg, preservation = source_fallback_page(
            ctx.source_document, index, ctx.snapshot,
            image_slots={candidate.occurrence_id: slot for slot, candidate in ctx.planned[index].items()},
            width=ctx.canvas.width_px, height=ctx.canvas.height_px,
            minimum_font_px=_source_fallback_floor(ctx, request), validated_pages=ctx.source_units,
            header_page=header_pages[index] if header_pages else None)
    except (ValueError, TypeError, KeyError):
        LOGGER.warning("[svg] source fallback page unavailable slide=%d", index + 1, exc_info=True)
        return None
    rescued = _rescued_identity(payload)
    # One local attempt records the deterministic page; the provider attempts
    # before it stay as they were. The ordinary source render measures it.
    rescued.setdefault("attempts", []).append({"kind": "local", "receipt": {
        "source_fallback": SOURCE_FALLBACK_VERSION, "source_preservation": preservation,
        "source_digest": source_digest(svg), "validation_error": None}})
    rescued.update(svg_content=svg, source_preservation={**preservation, "font_profile": ctx.source_font_profile})
    try:
        await render.attempt_render(ctx=ctx, request=request, presentation_id=presentation_id, index=index,
            content=content, payload=rescued, svg=svg, ordinal=1, job=job, checkpoint=checkpoint,
            async_session_maker=async_session_maker, write_png=write_png)
    except AuthoredStageError as rendered_error:
        # A measured failure after the page rendered keeps the rendered page
        # (checked below); anything earlier left no page to publish.
        LOGGER.warning("[svg] source fallback page measured %s slide=%d", rendered_error.code, index + 1)
    except (ValueError, TypeError, KeyError):
        LOGGER.warning("[svg] source fallback page render failed slide=%d", index + 1, exc_info=True)
        return None
    final = rescued.get("svg_content")
    if (not final or rescued.get("source_digest") != sha256_hex(final.encode())
            or not rescued.get("png_sha256") or not isinstance(rescued.get("quality"), dict)):
        return None
    try:
        verify_source_receipt(final, rescued.get("source_preservation"), ctx.source_document, index)
        if rescued["source_preservation"].get("native_tables"):
            # Publication projects these into native tables; prove it here,
            # where a refusal still has the placeholder to fall back to.
            from services.authored_canvas import request_canvas_metadata
            from services.generation_core.drivers.svg.native_publish import canvas_point_per_px
            from services.svg_source_native_tables import source_table_native_objects
            source_table_native_objects(final, rescued["source_preservation"], ctx.source_document, index,
                                        pt_per_px=canvas_point_per_px(request_canvas_metadata(request)))
    except (ValueError, TypeError, KeyError):
        LOGGER.warning("[svg] source fallback page does not bind slide=%d", index + 1, exc_info=True)
        return None
    rescued.update(terminal=flagged_entry(index=index, reason=SOURCE_FALLBACK_REASON, attempted=False,
                                          outcome=SOURCE_FALLBACK_OUTCOME), terminal_error=error.code)
    _bind_recovery_evidence(ctx, rescued)
    await checkpoint(job, index, payload=rescued)
    LOGGER.warning("[svg] terminal page replaced slide=%d outcome=%s code=%s", index + 1,
                   SOURCE_FALLBACK_OUTCOME, error.code)
    return rescued, None


async def _placeholder(*, ctx, request, index, content, payload, error, job, checkpoint, write_png):
    from services.generation_core.drivers.svg.fallback import placeholder_page_svg
    from services.svg_renderer import render_svg_with_metrics

    width, height = ctx.canvas.width_px, ctx.canvas.height_px
    try:
        if ctx.source_reflow:
            from services.generation_core.drivers.svg.source_fallback import source_page_title
            from utils.svg_template_fonts import FONT_FAMILY

            title = source_page_title(ctx.source_units[index]) or str(content or "")
            svg = placeholder_page_svg(title=title, width=width, height=height, font_family=FONT_FAMILY)
            # The same hard minimum every source page renders under.
            render_kwargs = {"instructions": None, "minimum_font_px": _source_fallback_floor(ctx, request),
                             "source_font_profile": ctx.source_font_profile}
        else:
            from services.svg_trusted_fonts import DEFAULT_FONT_FAMILY

            header_pages = getattr(ctx, "header_pages", None)
            svg = placeholder_page_svg(title=str(content or ""), width=width, height=height,
                font_family=DEFAULT_FONT_FAMILY, index=index, count=ctx.count, style_snapshot=ctx.snapshot,
                header_page=header_pages[index] if header_pages else None)
            render_kwargs = {"instructions": request.instructions}
        async with ctx.render_semaphore:
            rendered = await render_svg_with_metrics(svg, width=width, height=height, **render_kwargs,
                                                     **ctx.physical_kwargs)
    except ValueError:
        LOGGER.error("[svg] failure placeholder render failed slide=%d", index + 1, exc_info=True)
        return None
    return await _publish_substitute(ctx=ctx, svg=svg, rendered=rendered, index=index, payload=payload, error=error,
        job=job, checkpoint=checkpoint, write_png=write_png, reason=PLACEHOLDER_REASON,
        outcome=PLACEHOLDER_OUTCOME, png_returned=not ctx.source_reflow)


__all__ = [
    "FALLBACK_OUTCOME", "FALLBACK_REASON", "FLAG_ONLY_OUTCOME",
    "PLACEHOLDER_OUTCOME", "PLACEHOLDER_REASON", "SOURCE_FALLBACK_OUTCOME", "SOURCE_FALLBACK_REASON",
    "is_exhausted_page", "rescue_terminal_page", "terminal_policy_of",
]
