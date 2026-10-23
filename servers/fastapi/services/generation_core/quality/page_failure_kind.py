"""Which measured page failures lose content and which only hurt its layout.

A page that ran out of attempts is rescued according to what its last render
got wrong (``generation_core/drivers/svg/terminal.py``). A **layout** failure
-- overlapping or undersized text, a writing-style symbol, an unused decorative
icon -- leaves every word of the slide on the page; that page is kept and
flagged. A **content** failure -- required source text, a source image, a
requested table or chart that is not on the page, or text a reader cannot see
-- means the page lost something, so it is replaced by a complete-content
fallback page instead.

Every ``quality.failures[*].kind`` the producers emit is listed below:
``services/svg_renderer.py``, ``services/document_canvas_generation.py``
(physical readability), ``generation_core/drivers/svg/render.py``,
``.../svg/embed_assets.py``, ``.../svg/native_requirements.py``,
``.../svg/source_classification_association.py``,
``.../svg/source_paint_occlusion.py``, ``.../svg/timeline_ink_guard.py`` and
``services/svg_common_header_export_evidence.py``. A kind this module does not
know is **content**: the conservative side, because the fallback page drops
nothing while a kept page might have.

The second half classifies the ``AuthoredStageError.code`` a page raises, which
decides *whether* a page is rescued at all: ``INTEGRITY`` (the evidence is not
what it claims, or the operation lost its authority -- the deck fails),
``PROVIDER`` (the provider outcome is unknown or failed -- today's retry and
partial-recovery path), ``PAGE_QUALITY`` (how good one page is -- the rescue
ladder replaces or flags the page). An unknown code is page quality: the ladder
publishes the page's content verbatim, so nothing is lost.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

CONTENT = "content"
LAYOUT = "layout"

#: The page is missing, or cannot be shown to carry, part of the slide's content.
CONTENT_FAILURE_KINDS = frozenset({
    "required_text_missing",
    "source_image_omitted",
    "native_requested_table_missing",
    "native_requested_chart_missing",
    "native_requested_chart_unsupported",
    "source_native_chart_unprojectable",
    "source_classification_unproven",
    # Text that exists in the markup but that a reader cannot see.
    "text_off_canvas",
    "text_hidden",
    "source_text_paint_occluded",
    "source_text_paint_unproven",
    # Unmeasured: visibility is unknown, so completeness is unproven.
    "measurement_incomplete",
    "physical_readability_unmeasured",
    "physical_font_unmeasured",
    "text_density_unmeasured",
    "validation_failed",
})

#: Every word is on the page; only its arrangement, size or style is defective.
LAYOUT_FAILURE_KINDS = frozenset({
    "text_overlap",
    "text_clipped",
    "small_body_text",
    "font_unresolved",
    "physical_font_too_small",
    "dense_content",
    "writing_policy_visible_symbols",
    "timeline_caption_bar_ink_overlap",
    "asset_icon_omitted",
    "provider_repair_unavailable",
    "svg_common_header_measurement_unavailable",
    "svg_common_header_title_overflow",
    "svg_common_header_duplicate",
    "svg_common_header_body_intrusion",
    "svg_common_header_digest_mismatch",
    "svg_common_header_text_hidden",
    "svg_common_header_binding_invalid",
    "svg_common_header_font_mismatch",
    "svg_common_header_overflow",
    "svg_common_header_text_overlap",
})


def page_failure_kind(kind: object) -> str:
    """``LAYOUT`` for a known layout/style kind, ``CONTENT`` for everything else."""

    return LAYOUT if isinstance(kind, str) and kind in LAYOUT_FAILURE_KINDS else CONTENT


def content_failure_kinds(failures: Iterable[object]) -> tuple[str, ...]:
    """The content kinds among ``failures``; a malformed entry counts as unknown."""

    found: list[str] = []
    for failure in failures:
        kind = failure.get("kind") if isinstance(failure, Mapping) else None
        if page_failure_kind(kind) == CONTENT:
            found.append(kind if isinstance(kind, str) else "unknown")
    return tuple(found)


def quality_keeps_content(quality: object) -> bool:
    """Whether a measured page may be kept: every failure is a layout failure.

    A page that did not pass but names no failure is unexplained, and so
    content (the fallback keeps everything; the kept page might not).
    """

    if not isinstance(quality, Mapping):
        return False
    failures = quality.get("failures")
    if failures is None:
        failures = []
    if not isinstance(failures, list):
        return False
    if not failures:
        return quality.get("passed") is True
    return not content_failure_kinds(failures)


INTEGRITY = "integrity"
PROVIDER = "provider"
PAGE_QUALITY = "page_quality"

#: The provider's outcome is unknown or failed. Read after the integrity
#: markers, so an expired provider-checkpoint *authority* stays integrity.
_PROVIDER_PREFIXES = ("public_provider_", "svg_provider_")
_PROVIDER_MARKERS = ("_result_unknown", "_outcome_unknown", "_attempt_uncertain")

#: Code families whose subject is an approved, frozen or bound identity rather
#: than a page's look: a substitute page cannot answer them.
_INTEGRITY_PREFIXES = (
    "svg_cached_author_render_",  # zero-provider recovery bound to an approved manifest
    "svg_cache_remeasurement_",  # cache-only remeasurement bound to witnessed bytes
    "svg_partial_recovery_",  # partial recovery verifies pages against a frozen manifest
    "svg_quality_recovery_",  # recovery plan, runtime binding, authority and budget
    "svg_validated_recovery_",  # the validated adaptation reservation's binding
    "svg_timeout_repurchase_", "svg_recovery_",  # append-only repurchase lineage and fences
    "svg_stage_",  # partition protocol and its operation binding
    "svg_header_design_",  # deck-level common-header design operation state
    "authored_attempt_", "authored_workspace_operation_",  # lease and fence
)
_INTEGRITY_MARKERS = (
    "authority", "lease", "fence", "cancel", "_lost", "_changed", "lineage", "_forbidden",
    "_conflict", "_unbound", "_contract_invalid", "_plan_", "_snapshot_",
    "_binding_invalid",  # every ``*_binding_invalid`` names server-bound evidence
)

#: Integrity codes no marker names. Each is raised beside the page's own bytes:
#: by the source manifest every page shares (a substitute page would hit it
#: again, and a deck of placeholders would hide it), by a server-written
#: identity the model cannot author, or by the renderer's own evidence.
_INTEGRITY_CODES = frozenset({
    # ``svg_source_layout.source_pages``: the sealed source manifest itself.
    "svg_source_manifest_digest_mismatch", "svg_source_unsupported_inventory",
    "svg_source_page_count_mismatch", "svg_source_picture_bullet_unsupported",
    "svg_source_page_order_mismatch", "svg_source_image_only_page_not_supported",
    "svg_source_duplicate_block_id", "svg_source_block_digest_mismatch",
    "svg_source_font_policy_required",
    # ``verify_source_receipt``'s identity half: manifest, source, page id,
    # index, count, page digest and SVG digest are server-written.
    "svg_source_receipt_input_mismatch", "svg_source_page_index_invalid", "svg_page_index_invalid",
    # The server composes the header layer over the authored body and checks
    # its own envelope; the model cannot author either side of it.
    "svg_source_header_composition_invalid",
    # Numbers a chart carries must equal the source's.
    "svg_source_chart_numeric_mismatch", "svg_source_chart_export_numeric_mismatch",
    # The renderer's glyph-fallback receipt is not bound to the measured SVG.
    "svg_template_fallback_binding_invalid",
    # One code for artifact-store I/O, a lost publication fence and a slide
    # revision conflict (``svg_generation_assets.screen_placement``).
    "svg_asset_screen_derivative_failed",
    "svg_master_furniture_image_digest_mismatch", "svg_native_objects_page_count_mismatch",
    "svg_checkpoint_missing", "svg_asset_offer_page_out_of_range",
    # The local source layout returned an escalation outside its contract.
    "svg_source_local_escalation_invalid",
})

#: Page-quality codes a marker above would misread as integrity.
_PAGE_QUALITY_CODES = frozenset({
    # The authored body uses ids of the frozen header layer: a forged page.
    "svg_source_header_body_ownership_conflict",
    # A cached known page re-measured below its gate.
    "svg_partial_recovery_cached_quality_failed",
})


def page_error_class(code: object) -> str:
    """``INTEGRITY``, ``PROVIDER`` or ``PAGE_QUALITY`` for one error code.

    ``code`` may carry a ``:detail`` suffix (``ValueError`` messages do); only
    the part before it is read. A missing or non-string code is integrity:
    nothing about it shows that it is about a page's quality.
    """

    if not isinstance(code, str) or not code:
        return INTEGRITY
    code = code.split(":", 1)[0]
    if code in _PAGE_QUALITY_CODES:
        return PAGE_QUALITY
    if (code in _INTEGRITY_CODES or code.startswith(_INTEGRITY_PREFIXES)
            or any(marker in code for marker in _INTEGRITY_MARKERS)):
        return INTEGRITY
    if code.startswith(_PROVIDER_PREFIXES) or any(marker in code for marker in _PROVIDER_MARKERS):
        return PROVIDER
    return PAGE_QUALITY


def error_class(error: BaseException) -> str:
    """:func:`page_error_class` of an exception raised by a page's work.

    Cancellation is integrity. Provider outcomes, admission refusals, the
    runtime's budget/backpressure controls and timeouts are provider unless
    their code names an integrity marker. An ``AuthoredStageError`` is
    classified by its code; any other exception is integrity, because no
    page-quality producer raises one.
    """

    import asyncio

    from services.authored_checkpoint_errors import AuthoredStageError
    from services.provider_runtime_primitives import CancellationRequested, RuntimeControlError
    from services.public_operation_admission_models import PublicAdmissionRejected
    from utils.provider_overload import ProviderOutcomeError

    if isinstance(error, (asyncio.CancelledError, CancellationRequested)):
        return INTEGRITY
    if isinstance(error, AuthoredStageError):
        return page_error_class(error.code)
    if isinstance(error, (ProviderOutcomeError, PublicAdmissionRejected)):
        return INTEGRITY if page_error_class(error.code) == INTEGRITY else PROVIDER
    if isinstance(error, (RuntimeControlError, TimeoutError)):
        return PROVIDER
    return INTEGRITY


def absorbable_page_error(error: BaseException) -> bool:
    """Whether optional page work may end in what the page already had.

    Page-quality and provider failures may; integrity and cancellation may not,
    and neither may a deferral (a provider quota pause): the operation queue
    resumes it, so it is never absorbed in place.
    """

    from services.workspace_operation_handler_registry import WorkspaceOperationDeferred

    return (not isinstance(error, WorkspaceOperationDeferred)
            and error_class(error) in {PAGE_QUALITY, PROVIDER})


__all__ = [
    "CONTENT", "CONTENT_FAILURE_KINDS", "INTEGRITY", "LAYOUT", "LAYOUT_FAILURE_KINDS", "PAGE_QUALITY",
    "PROVIDER", "absorbable_page_error", "content_failure_kinds", "error_class", "page_error_class",
    "page_failure_kind", "quality_keeps_content",
]
