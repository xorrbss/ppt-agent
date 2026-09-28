"""Admission and deterministic staging for ``source_content_restyle``."""

from __future__ import annotations

import inspect
import re
import uuid
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from models.authored_vnext.deck.operations import DeckGenerationSnapshot
from models.authored_vnext.master.contract import DeckMasterContract, Rect
from services.authored_vnext.generation.scene_compiler import (
    compatibility_composition_digest,
)
from services.source_content.allocator import (
    ContentAllocator,
    PlacementCompositionPin,
    SourceContentPlacementPlan,
    WritableLayout,
    WritableSlot,
    canonical_digest,
)
from services.source_content.contracts import (
    SourceContentManifest,
    SourceContentManifestPin,
)
from services.source_content.errors import SourceContentError
from services.source_content.staging_projection import (
    _source_error as _source_error,
    _style_tokens as _style_tokens,
    _synthetic_style_master as _synthetic_style_master,
    _master_context as _master_context,
    _utc_now as _utc_now,
    _compatibility_text_for_slide as _compatibility_text_for_slide,
    _compatibility_body_slot_id as _compatibility_body_slot_id,
    _deck_snapshot as _deck_snapshot,
)
from services.source_content.materializer import (
    MaterializedSourceContentScene,
    SourceContentSceneMaterializer,
    SourceContentStyleTokens,
)
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


@dataclass(frozen=True, slots=True)
class SourceContentGenerationStaging:
    placement_plan: SourceContentPlacementPlan
    materialized_scene: MaterializedSourceContentScene
    deck_snapshot: DeckGenerationSnapshot
    master_contract: DeckMasterContract




def _validate_design_instruction(instructions: str | None) -> None:
    if instructions and _INSTRUCTION_CONFLICT.search(instructions):
        raise _source_error("source_content_instruction_conflict")


async def _admit_raster_reconstruction_request(
    request: Any,
    *,
    sql_session: Any,
    owner_id: str | None,
    tenant_id: str | None,
) -> None:
    """Bind the approved analysis to one owner-admitted immutable PPTX snapshot."""

    from services.document_canvas import canvas_digest, document_canvas_for_orientation
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
        or analysis.target_canvas.orientation != getattr(request, "orientation", None)
    ):
        raise _source_error("raster_source_binding_mismatch", status_code=409)
    if raster_review_blockers(analysis):
        raise _source_error("raster_review_unresolved", status_code=409)
    document_canvas = document_canvas_for_orientation(request.orientation)
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
















def _fallback_layout(canvas: Rect) -> WritableLayout:
    gap = max(12.0, canvas.height * 0.025)
    title_height = max(68.0, canvas.height * 0.14)
    body_top = canvas.y + title_height + gap
    body_height = max(1.0, canvas.bottom - body_top)
    return WritableLayout(
        layout_role="title-content",
        layout_id="layout:source-content-standard",
        slots=(
            WritableSlot(
                slot_id="slot:source-content-title",
                bounds=Rect(
                    x=canvas.x,
                    y=canvas.y,
                    width=canvas.width,
                    height=title_height,
                ),
                accepted_kinds=("text",),
                semantic_roles=("title", "heading", "section"),
                capacity_units=2,
            ),
            WritableSlot(
                slot_id="slot:source-content-body",
                bounds=Rect(
                    x=canvas.x,
                    y=body_top,
                    width=canvas.width,
                    height=body_height,
                ),
                capacity_units=12,
            ),
        ),
    )


def _layouts_and_canvas(
    master_contract: Any | None,
    composition: Any | None = None,
) -> tuple[list[WritableLayout], Rect]:
    if master_contract is None:
        canvas = Rect(x=0, y=0, width=1280, height=720)
        safe = Rect(x=60, y=60, width=1160, height=600)
        return [_fallback_layout(safe)], canvas
    canvas_contract = master_contract.canvas
    canvas = Rect(
        x=0,
        y=0,
        width=float(canvas_contract.width_px),
        height=float(canvas_contract.height_px),
    )
    template = getattr(composition, "authored_template", None)
    resolved = getattr(template, "resolved_reference_layout", None)
    if resolved is not None:
        layouts: list[WritableLayout] = []
        for record in resolved.layouts:
            archetype = str(record.get("archetype") or "content")
            retained_layout_id = str(record.get("id") or archetype)
            if not retained_layout_id.startswith("layout:"):
                retained_layout_id = f"layout:{retained_layout_id}"
            if archetype in {"cover", "section", "closing"}:
                source_bounds = canvas
            else:
                source_bounds = Rect(
                    x=max(24.0, canvas.width * 0.025),
                    y=max(48.0, canvas.height * 0.075),
                    width=canvas.width - max(48.0, canvas.width * 0.05),
                    height=canvas.height - max(102.0, canvas.height * 0.15),
                )
            layouts.append(
                WritableLayout(
                    layout_role=archetype,
                    layout_id=retained_layout_id,
                    slots=(
                        WritableSlot(
                            slot_id=f"slot:template-{archetype}-source",
                            bounds=source_bounds,
                            accepted_kinds=("text", "table", "chart", "image", "shape"),
                            capacity_units=1000,
                        ),
                    ),
                    placement_strategy="sourceGeometry",
                )
            )
        if layouts:
            return layouts, canvas
    standard = master_contract.layouts.standard
    if not standard.slots:
        return [_fallback_layout(canvas_contract.safe_area)], canvas
    slots = []
    for raw in standard.slots:
        role = str(raw.role)
        slots.append(
            WritableSlot(
                slot_id=raw.id,
                bounds=raw.bounds,
                accepted_kinds=("text",)
                if role == "title"
                else (
                    "text",
                    "table",
                    "chart",
                    "image",
                ),
                semantic_roles=("title", "heading", "section") if role == "title" else (),
                capacity_units=2 if role == "title" else 12,
            )
        )
    return [
        WritableLayout(
            layout_role="title-content",
            layout_id=standard.id,
            slots=tuple(slots),
        )
    ], canvas


def _composition_pin(
    *,
    request: Any,
    style: Any,
    style_tokens: SourceContentStyleTokens,
    layouts: list[WritableLayout],
) -> PlacementCompositionPin:
    style_tokens_digest = canonical_digest(style_tokens.model_dump(mode="json", by_alias=True))
    style_identity = {
        "id": style.id,
        "name": style.name,
        "description": style.description,
        "styleTokenDigest": style_tokens_digest,
    }
    master_pin = getattr(request, "_authored_master_pin", None)
    geometry_digest = canonical_digest([layout.model_dump(mode="json", by_alias=True) for layout in layouts])
    return PlacementCompositionPin(
        authored_style_id=style.id,
        # Coverage compares this directly to the tokens actually present in Scene.
        authored_style_digest=style_tokens_digest,
        authored_style_pin_digest=canonical_digest(style_identity),
        company_standard_master_id=(master_pin.master_id if master_pin is not None else None),
        company_standard_revision=(master_pin.master_revision if master_pin is not None else None),
        master_digest=(master_pin.master_digest if master_pin is not None else None),
        master_geometry_digest=geometry_digest,
    )


async def build_source_content_staging(
    *,
    request: Any,
    presentation_id: Any,
    sql_session: Any,
    owner_id: Any | None,
) -> SourceContentGenerationStaging:
    """Allocate and materialize the already-admitted request with no LLM calls."""

    manifest = getattr(request, "_source_content_manifest", None)
    style = getattr(request, "_authored_style_snapshot", None)
    pin = getattr(request, "source_content_manifest_pin", None)
    if manifest is None or style is None or pin is None:
        raise _source_error("source_content_manifest_invalid", status_code=409)
    if owner_id is None:
        raise _source_error("source_content_owner_required", status_code=401)
    from services.source_content.manifest_store import (
        load_source_content_manifest_assets,
    )

    sealed_assets = await load_source_content_manifest_assets(
        sql_session,
        owner_id=owner_id,
        manifest_id=pin.manifest_id,
    )
    tokens = _style_tokens(style)
    master_contract, master_pin, composition = _master_context(
        request=request,
        style=style,
        style_tokens=tokens,
    )
    layouts, canvas = _layouts_and_canvas(master_contract, composition)
    composition_pin = _composition_pin(
        request=request,
        style=style,
        style_tokens=tokens,
        layouts=layouts,
    )
    try:
        plan = ContentAllocator().allocate(
            manifest=manifest,
            manifest_pin=SourceContentManifestPin(
                manifest_id=pin.manifest_id,
                digest=pin.digest,
            ),
            composition_pin=composition_pin,
            layouts=layouts,
            outline=getattr(request, "_source_document_outline", None),
        )
        expected_units = len(manifest.units)
        if len(plan.slides) != expected_units or [slide.source_unit_id for slide in plan.slides] != [
            unit.source_unit_id
            for unit in sorted(
                manifest.units,
                key=lambda item: (item.source_index, item.source_unit_id),
            )
        ]:
            raise _source_error("source_content_source_unit_cardinality_mismatch")
        materialized = SourceContentSceneMaterializer().materialize(
            manifest=manifest,
            plan=plan,
            layouts=layouts,
            style_tokens=tokens,
            canvas_bounds=canvas,
            deck_id=(
                f"deck:semantic-{presentation_id.hex}"
                if hasattr(presentation_id, "hex")
                else f"deck:semantic-{str(presentation_id).lower().replace('-', '')}"
            ),
            sealed_assets=sealed_assets,
            scene_composition_digest=(
                composition.digest
                if composition is not None
                else compatibility_composition_digest(master_pin=master_pin)
            ),
        )
        snapshot = _deck_snapshot(
            manifest=manifest,
            plan=plan,
            materialized=materialized,
            master_contract=master_contract,
            master_pin=master_pin,
            composition=composition,
            layouts=layouts,
        )
    except SourceContentError:
        raise
    except ValueError as error:
        code = str(error)
        if not code.startswith("source_content_"):
            code = "source_content_manifest_invalid"
        raise _source_error(code) from error
    return SourceContentGenerationStaging(
        placement_plan=plan,
        materialized_scene=materialized,
        deck_snapshot=snapshot,
        master_contract=master_contract,
    )


async def generate_source_content_restyle_presentation(
    *,
    request: Any,
    presentation_id: Any,
    async_status: Any,
    export_cookie_header: str | None,
    generation_job: Any,
    progress_reporter: Any,
    sql_session: Any,
    publisher: Callable[..., Awaitable[Any]] | None = None,
) -> Any:
    """Enter the source-content product branch before any generative authoring."""

    from utils.owner_context import get_current_owner_id

    staging = await build_source_content_staging(
        request=request,
        presentation_id=presentation_id,
        sql_session=sql_session,
        owner_id=get_current_owner_id(),
    )
    if publisher is None:
        try:
            from services.source_content.publication import (
                publish_source_content_generation as publisher,
            )
        except ImportError as error:
            raise _source_error("source_content_publication_unavailable", status_code=503) from error
    result = publisher(
        request=request,
        presentation_id=presentation_id,
        manifest=request._source_content_manifest,
        placement_plan=staging.placement_plan,
        materialized_scene=staging.materialized_scene,
        deck_snapshot=staging.deck_snapshot,
        master_contract=staging.master_contract,
        async_status=async_status,
        export_cookie_header=export_cookie_header,
        generation_job=generation_job,
        progress_reporter=progress_reporter,
        sql_session=sql_session,
    )
    return await result if inspect.isawaitable(result) else result


__all__ = [
    "SOURCE_MODE",
    "SourceContentGenerationStaging",
    "admit_source_content_request",
    "build_source_content_staging",
    "generate_source_content_restyle_presentation",
    "prepare_source_content_authored_reflow",
    "prepare_source_content_svg_reflow",
]
