"""Admission and reflow preparation for ``source_content_restyle``."""

from __future__ import annotations

import re
import uuid
from types import SimpleNamespace
from typing import Any

from services.source_content.contracts import (
    SourceContentManifest,
    SourceContentManifestPin,
)
from services.source_content.errors import SourceContentError, source_error as _source_error
from services.source_content.svg_source_binding import bind_svg_source_input
from services.source_content.outline import (
    SourceDocumentOutlineBuilder,
    build_authored_text_image_slides,
)
from services.authored_composition_policy import resolve_composition_profile
from utils.authored_empty_media import source_reflow_empty_media_instruction

SOURCE_MODE = "source_content_restyle"
RASTER_SOURCE_MODE = "raster_reconstruction"
_INSTRUCTION_CONFLICT = re.compile(
    r"(?:요약|축약|번역|의역|재작성|문구\s*수정|내용\s*추가|사실\s*추가|"
    r"summari[sz]e|translate|rewrite|paraphrase|add\s+(?:content|facts?))",
    re.IGNORECASE,
)


def _validate_design_instruction(instructions: str | None) -> None:
    if instructions and _INSTRUCTION_CONFLICT.search(instructions):
        raise _source_error("source_content_instruction_conflict")


def _validate_raster_design_mode(request: Any, analysis: Any) -> None:
    """Preserved pixels cannot receive an explicitly selected replacement design."""
    explicit_reference = any(getattr(request, name, None) is not None for name in (
        "master_pin", "svg_design_reference_pin", "svg_design_reference_snapshot_id"))
    selected_style = getattr(request, "authored_style", None)
    replacement_design = explicit_reference or selected_style not in {None, "default", "screenshot-blue"}
    if replacement_design and any(page.render_mode != "redesign" for page in analysis.pages):
        raise SourceContentError("raster_preservation_design_requires_redesign", status_code=422,
            detail={"message": "Preserved raster pages cannot apply a replacement style or master. "
                               "Select redesign explicitly for each page to apply the selected design."})


async def _admit_raster_reconstruction_request(
    request: Any,
    *,
    sql_session: Any,
    owner_id: str | None,
    tenant_id: str | None,
) -> None:
    """Bind the approved analysis to one owner-admitted immutable PPTX snapshot."""

    from services.document_canvas import canvas_digest
    from services.svg_canvas import selected_svg_canvas
    from services.source_content.raster_target import raster_target_document_canvas
    from services.references.contracts import ReferenceProviderError
    from services.references.service import admit_reference_snapshots
    from services.source_content.raster_contracts import RasterApprovalPin
    from services.source_content.raster_reconstruction_store import (
        RasterReviewError,
        admit_approved_raster_analysis,
        raster_review_blockers,
    )
    from utils.owner_context import get_current_tenant_id

    if owner_id is None:
        raise _source_error("source_content_owner_required", status_code=401)
    tenant_id = tenant_id or get_current_tenant_id()
    if not tenant_id:
        raise _source_error("raster_tenant_required", status_code=401)
    pin = getattr(request, "raster_approval_pin", None)
    if pin is None:
        raise _source_error("raster_approval_pin_required", status_code=400)
    try:
        pin = RasterApprovalPin.model_validate(
            pin.model_dump(mode="json", by_alias=False) if hasattr(pin, "model_dump") else pin
        )
    except (TypeError, ValueError) as error:
        raise _source_error("raster_approval_pin_invalid", status_code=400) from error
    snapshot_ids = tuple(getattr(request, "reference_snapshot_ids", ()) or ())
    if len(snapshot_ids) != 1:
        raise _source_error("raster_single_source_snapshot_required", status_code=400)
    try:
        snapshot_id = uuid.UUID(str(snapshot_ids[0]))
        snapshots = await admit_reference_snapshots(sql_session, snapshot_ids=[snapshot_id])
        analysis = await admit_approved_raster_analysis(
            sql_session,
            operation_id=pin.operation_id,
            owner_id=uuid.UUID(str(owner_id)),
            tenant_id=tenant_id,
            pin=pin,
        )
    except (ReferenceProviderError, RasterReviewError, TypeError, ValueError) as error:
        raise _source_error("raster_source_or_approval_invalid", status_code=409) from error
    snapshot = snapshots[0]
    if (
        snapshot.provider_id != "uploaded_document"
        or len(snapshot.asset_bindings) != 1
        or snapshot.asset_bindings[0].sha256 != analysis.source.source_sha256
        or pin.source_sha256 != analysis.source.source_sha256
        or raster_target_document_canvas(analysis.target_canvas) != selected_svg_canvas(request)
    ):
        raise _source_error("raster_source_binding_mismatch", status_code=409)
    if raster_review_blockers(analysis):
        raise _source_error("raster_review_unresolved", status_code=409)
    _validate_raster_design_mode(request, analysis)
    document_canvas = raster_target_document_canvas(analysis.target_canvas)
    canvas_payload = document_canvas.model_dump(mode="json", by_alias=True)
    binding = {
        "version": "raster-reconstruction-input/v1",
        "ownerId": str(owner_id),
        "referenceSnapshotId": str(snapshot_id),
        "sourceDigest": analysis.source.source_sha256,
        "analysisId": str(analysis.analysis_id),
        "analysisDigest": analysis.digest,
        "approvalDigest": pin.approval_digest,
        "canvasDigest": canvas_digest(canvas_payload),
        "documentCanvas": canvas_payload,
        "pageIds": [f"raster-page-{page.page_number}" for page in analysis.pages],
    }
    frozen_binding = getattr(request, "_svg_source_binding", None)
    if frozen_binding is not None and frozen_binding != binding:
        raise _source_error("raster_source_binding_mismatch", status_code=409)
    request._svg_source_binding = binding
    request._reference_snapshots = snapshots
    request._raster_approval_pin = pin
    request._raster_analysis = analysis
    request._raster_reflow = True
    request._raster_reconstruction = True
    request._source_content_svg_reflow = False
    request._source_document_outline = SimpleNamespace(source_slide_count=len(analysis.pages))
    request._document_canvas = document_canvas
    request._authored_canvas = document_canvas.canvas_contract()
    request.n_slides = len(analysis.pages)


async def admit_source_content_request(
    request: Any,
    *,
    sql_session: Any,
    owner_id: str | None,
    tenant_id: str | None = None,
) -> SourceContentManifest | None:
    """Resolve and freeze the immutable owner-scoped manifest for generation."""

    source_mode = getattr(request, "source_mode", None)
    if source_mode not in {SOURCE_MODE, RASTER_SOURCE_MODE}:
        return None
    raster_source = source_mode == RASTER_SOURCE_MODE
    if raster_source:
        await _admit_raster_reconstruction_request(
            request,
            sql_session=sql_session,
            owner_id=owner_id,
            tenant_id=tenant_id,
        )
        return None
    svg_source = getattr(request, "strategy", None) == "svg"
    if svg_source:
        from services.svg_generation_admission import require_svg_creation_admission

        require_svg_creation_admission(request)
    if owner_id is None:
        raise _source_error("source_content_owner_required", status_code=401)
    pin = getattr(request, "source_content_manifest_pin", None)
    if pin is None:
        raise _source_error("source_content_manifest_invalid", status_code=400)
    _validate_design_instruction(getattr(request, "instructions", None))
    if getattr(request, "web_search", False) or getattr(request, "vision_qa", False):
        raise _source_error("source_content_generation_option_conflict", status_code=400)
    if getattr(request, "export_as", None) != "pptx":
        raise _source_error("source_content_format_not_supported", status_code=415)

    from services.source_content.manifest_store import (
        admit_source_content_manifest_pin,
    )

    manifest = await admit_source_content_manifest_pin(
        sql_session,
        owner_id=owner_id,
        manifest_id=pin.manifest_id,
        digest=pin.digest,
    )
    if manifest.source_type != "pptx":
        raise _source_error("source_content_format_not_supported", status_code=415)
    snapshots = {str(value) for value in getattr(request, "reference_snapshot_ids", ())}
    if snapshots and manifest.reference_snapshot_id not in snapshots:
        raise _source_error("source_content_manifest_invalid", status_code=409)
    if svg_source and snapshots and snapshots != {str(manifest.reference_snapshot_id)}:
        raise _source_error("source_content_manifest_invalid", status_code=409)
    request._source_content_manifest = manifest
    if svg_source:
        bind_svg_source_input(request, manifest=manifest, pin=pin, owner_id=owner_id)
    # Freeze a lossless page outline at admission.  It is an identity/read
    # order projection; payloads remain sourced from the immutable manifest.
    try:
        request._source_document_outline = SourceDocumentOutlineBuilder().build(
            manifest=manifest,
            manifest_pin=SourceContentManifestPin(
                manifest_id=pin.manifest_id,
                digest=pin.digest,
            ),
            include_tables=svg_source,
        )
    except ValueError as error:
        code = str(error)
        if not code.startswith("source_content_"):
            code = "source_content_manifest_invalid"
        raise _source_error(code, status_code=409) from error
    if svg_source:
        request._source_content_svg_reflow = True
        request.n_slides = int(request._source_document_outline.source_slide_count)
    return manifest


def prepare_source_content_svg_reflow(request: Any) -> str:
    """Project page context without handing source authority to the authoring model."""
    if getattr(request, "_raster_reflow", False):
        analysis = getattr(request, "_raster_analysis", None)
        if analysis is None or getattr(request, "_svg_source_binding", None) is None:
            raise _source_error("raster_source_binding_mismatch", status_code=409)
        request.slides_markdown = [
            f"<!-- approved raster source page {page.page_number} -->" for page in analysis.pages
        ]
        request.n_slides = len(analysis.pages)
        request.include_title_slide = False
        request.include_table_of_contents = False
        return str(request._svg_source_binding["referenceSnapshotId"])
    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None or not getattr(request, "_svg_source_binding", None):
        raise _source_error("source_content_manifest_invalid", status_code=409)
    request.slides_markdown = build_authored_text_image_slides(manifest=manifest, outline=outline)
    request.n_slides = outline.source_slide_count
    request.include_title_slide = False
    request.include_table_of_contents = False
    request._source_content_svg_reflow = True
    return str(manifest.reference_snapshot_id)


def prepare_source_content_authored_reflow(request: Any) -> str:
    """Convert the admitted source into the existing authored input contract.

    This is a runtime projection. The durable request keeps its immutable
    manifest pin, and every retry rebuilds the same page-locked outline before
    entering ordinary authored generation.
    """

    manifest = getattr(request, "_source_content_manifest", None)
    outline = getattr(request, "_source_document_outline", None)
    if manifest is None or outline is None:
        raise _source_error("source_content_manifest_invalid", status_code=409)
    try:
        slides = build_authored_text_image_slides(
            manifest=manifest,
            outline=outline,
        )
    except ValueError as error:
        code = str(error)
        if not code.startswith("source_content_"):
            code = "source_content_outline_invalid"
        raise _source_error(code, status_code=409) from error
    text_mode = str(getattr(getattr(request, "content_policy", None), "text", "verbatim") or "verbatim").strip().lower()
    if text_mode == "verbatim":
        contract_instruction = (
            "Treat each page's extracted source text and source images as page-locked "
            "verbatim authority. Recompose within the selected template, but copy every "
            "source text block exactly: either keep the complete block inside one "
            "containing DOM element (nested spans are fine, and a line break only "
            "between whole words, never inside one), or split "
            "it only at its own bullets, numbered items, arrow/pipe steps, tab gaps, or "
            "lines, with each item's complete text inside its own element. Bullet glyphs, "
            "circled numbers, and leading arrows may be drawn separately or omitted; "
            "everything else must match character for character: never split an item "
            "across elements, keep one block's items in their source order, never "
            "append or drop punctuation, and keep blank fields "
            "(______) and page numbers as literal text. A running header or page number "
            "may repeat verbatim; do not repeat any other block. Do not summarize, "
            "paraphrase, translate, omit, or invent content. If a page cannot fit on one "
            "slide, fail closed instead "
            "of changing the source. Place every source image offered for that slide "
            "exactly once. Do not create or reproduce tables, charts, or the source "
            "slide layout. Do not generate replacement illustrations."
        )
    else:
        contract_instruction = (
            "Treat each page's extracted source text and source images as a page-locked "
            "content outline, then re-author it with the selected template and the "
            "ordinary authored composition logic. Keep every key fact and source order, "
            "but concise rephrasing is allowed for a clean fit. Place every source image "
            "offered for that slide exactly once. Do not create or reproduce tables, "
            "charts, or the source slide layout. Do not generate replacement illustrations."
        )
    contract_instruction += source_reflow_empty_media_instruction(outline)
    existing_instruction = str(getattr(request, "instructions", None) or "").strip()
    request.instructions = (
        f"{existing_instruction}\n\n{contract_instruction}" if existing_instruction else contract_instruction
    )
    request.slides_markdown = slides
    request.n_slides = len(slides)
    request.include_title_slide = False
    request.include_table_of_contents = False
    # Source reflow is a visual rewrite, so the first authored render must go
    # through the bounded visual review.  Admission still rejects a caller-
    # supplied visionQa flag above; this internal flag is enabled only after
    # the immutable text/image projection has been admitted.
    request.vision_qa = True
    request._source_content_authored_reflow = True
    request._authored_composition_profile = resolve_composition_profile(request)
    request.source_mode = None
    return str(manifest.reference_snapshot_id)


__all__ = [
    "RASTER_SOURCE_MODE",
    "SOURCE_MODE",
    "SourceContentError",
    "admit_source_content_request",
    "prepare_source_content_authored_reflow",
    "prepare_source_content_svg_reflow",
]
