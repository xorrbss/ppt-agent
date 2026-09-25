"""Bind approved raster crops to the existing SVG slot and export contracts.

The review operation owns the analysis pin. A separate active generation or
export operation owns publications; neither authority is inferred from the
other. This module never creates a native source-content manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import uuid
from xml.etree import ElementTree as ET

from PIL import Image

from services.svg_asset_manifest import (
    FitPolicy,
    PlacementManifest,
    SourceManifest,
    SourceSlot,
    SvgPlacement,
    decide_preservation_policy,
)
from services.svg_asset_persistence import (
    SvgPublicationContext,
    load_source_manifest_pointer,
    persist_derivative_if_current,
    persist_manifest_if_current,
)
from services.svg_asset_resolver import resolve_svg_for_render
from services.svg_contract import source_digest, validate_svg
from services.svg_image_derivative import build_model_preview
from services.source_content.raster_contracts import RasterApprovalPin, RasterRegion
from services.source_content.raster_reconstruction_store import admit_approved_raster_analysis
from utils.renderer_isolation import admit_authorized_svg_materialized


_SVG_IMAGE = "{http://www.w3.org/2000/svg}image"


@dataclass(frozen=True)
class RasterPageAssets:
    source_manifest: SourceManifest
    source_pointer: dict
    screen_envelope: dict
    materialized_svg: str
    crop_lineage: tuple[dict, ...]


def raster_slot_id(page_number: int, region_id: str) -> str:
    if page_number < 1 or not region_id:
        raise ValueError("raster_slot_identity_invalid")
    suffix = hashlib.sha256(region_id.encode("utf-8")).hexdigest()[:16]
    return f"source-slot-raster-p{page_number}-{suffix}"


def _validate_preserve_approval_modes(analysis, pin):
    preserve_list = tuple(getattr(pin, "full_page_preserve_pages", ()))
    automatic_list = tuple(getattr(pin, "automatic_preserve_pages", ()))
    preserve_pages = set(preserve_list)
    automatic_pages = set(automatic_list)
    if (len(preserve_pages) != len(preserve_list)
            or len(automatic_pages) != len(automatic_list)
            or not automatic_pages.issubset(preserve_pages)):
        raise ValueError("raster_automatic_preserve_pin_mismatch")
    actual_automatic = set()
    for page in analysis.pages:
        method = getattr(page, "approval_method", "manual_review")
        if method not in {"manual_review", "automatic_preserve", "automatic_redesign"}:
            raise ValueError("raster_automatic_preserve_pin_mismatch")
        if method == "automatic_redesign" and getattr(page, "render_mode", "reconstruct") != "redesign":
            raise ValueError("raster_automatic_preserve_pin_mismatch")
        if method == "automatic_preserve":
            actual_automatic.add(page.page_number)
            if (page.render_mode != "full_page_preserve"
                    or getattr(page, "full_page_preserve_acknowledgements", ())):
                raise ValueError("raster_automatic_preserve_pin_mismatch")
    if actual_automatic != automatic_pages:
        raise ValueError("raster_automatic_preserve_pin_mismatch")
    redesign_list = tuple(getattr(pin, "automatic_redesign_pages", ()))
    actual_redesign = {
        page.page_number for page in analysis.pages
        if getattr(page, "approval_method", "manual_review") == "automatic_redesign"
    }
    if len(set(redesign_list)) != len(redesign_list) or set(redesign_list) != actual_redesign:
        raise ValueError("raster_automatic_redesign_pin_mismatch")


def _approved_crop_regions(analysis, pin, page_number, canonical_svg=None):
    _validate_preserve_approval_modes(analysis, pin)
    pages = [page for page in analysis.pages if page.page_number == page_number]
    if len(pages) != 1:
        raise ValueError("raster_approved_page_missing")
    page = pages[0]
    approved = set(pin.approved_region_ids)
    preserve_pages = set(getattr(pin, "full_page_preserve_pages", ()))
    if page.render_mode == "full_page_preserve":
        region = page.full_page_region
        if (page_number not in preserve_pages or region is None
                or region.role != "full_page_preserve"
                or region.region_id not in approved):
            raise ValueError("raster_full_page_pin_mismatch")
        if any(item.region_id in approved for item in page.regions):
            raise ValueError("raster_full_page_audit_region_selected")
        if region.crop_artifact_ref is None or region.source_pixel_bbox is None:
            raise ValueError("raster_approved_crop_missing")
        return [region]
    if page_number in preserve_pages or page.full_page_region is not None:
        raise ValueError("raster_full_page_pin_mismatch")
    if getattr(page, "render_mode", "reconstruct") == "redesign":
        return _approved_redesign_crop_regions(analysis, page, approved, canonical_svg)
    regions = []
    for region in page.regions:
        if region.region_id not in approved:
            continue
        if region.role == "full_page_preserve":
            raise ValueError("raster_full_page_pin_mismatch")
        if region.role in {"screenshot", "logo", "table"} and region.crop_artifact_ref is None:
            raise ValueError("raster_approved_crop_missing")
        if region.role in {"screenshot", "logo", "table"} and region.crop_artifact_ref is not None:
            regions.append(region)
    return regions


def _approved_redesign_crop_regions(analysis, page, approved, canonical_svg=None):
    """Crop slots for a redesign page: exactly the regions the layout places as images.

    Structured tables (``table_cells``) are redrawn as editable grids and get
    no slot; decoration is omitted; the full-page reference image is never
    offered as a slot. When the canonical SVG uses a dense-group pixel
    fallback, that group's crop replaces its members' own crops.
    """
    from services.source_content.raster_redesign_fallback import available_fallbacks
    from services.source_content.raster_redesign_plan import redesign_crop_regions

    used = set()
    if canonical_svg is not None:
        used = {node.attrib.get("href") for node in ET.fromstring(canonical_svg).iter(_SVG_IMAGE)}
    fallbacks = [fallback for fallback in available_fallbacks(analysis, page)
                 if raster_slot_id(page.page_number, fallback.region.region_id) in used]
    replaced = {member for fallback in fallbacks for member in fallback.member_ids}
    regions = []
    for region in redesign_crop_regions(page):
        if region.region_id in replaced:
            continue
        if region.region_id not in approved:
            raise ValueError("raster_redesign_crop_region_not_approved")
        if region.crop_artifact_ref is None:
            raise ValueError("raster_approved_crop_missing")
        regions.append(region)
    for fallback in fallbacks:
        if any(member not in approved for member in fallback.member_ids):
            raise ValueError("raster_redesign_crop_region_not_approved")
        if getattr(page, "reference_region", None) is None or page.reference_region.crop_artifact_ref is None:
            raise ValueError("raster_group_fallback_source_missing")
        regions.append(fallback.region)
    return regions


def crop_png(payload: bytes, pixel_bbox) -> bytes:
    """Lossless PNG of one pixel box of a source image (dense-group fallback)."""
    x, y, width, height = pixel_bbox
    with Image.open(BytesIO(payload)) as image:
        if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
            raise ValueError("raster_group_fallback_bbox_outside_source")
        buffer = BytesIO()
        image.convert("RGBA").crop((x, y, x + width, y + height)).save(buffer, format="PNG")
        return buffer.getvalue()


async def _region_crop(session, service, *, region, page, owner_id: uuid.UUID, tenant_id: str):
    """A region's committed crop, or a group fallback cut from the page reference image."""
    if region.crop_artifact_ref is not None:  # only a dense-group fallback region has no stored crop
        return await _committed_crop(session, service, region=region, owner_id=owner_id, tenant_id=tenant_id)
    reference, full, _, _, _ = await _committed_crop(
        session, service, region=page.reference_region, owner_id=owner_id, tenant_id=tenant_id,
    )
    payload = crop_png(full, region.source_pixel_bbox)
    width, height, pixel_digest = _source_pixel_digest(payload)
    return reference, payload, width, height, pixel_digest

def _source_pixel_digest(payload: bytes) -> tuple[int, int, str]:
    with Image.open(BytesIO(payload)) as image:
        if image.format != "PNG":
            raise ValueError("raster_crop_png_required")
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, hashlib.sha256(rgba.tobytes()).hexdigest()


async def _committed_crop(session, service, *, region: RasterRegion, owner_id: uuid.UUID, tenant_id: str):
    offered = region.crop_artifact_ref
    if offered is None or offered.owner_id != owner_id or offered.tenant_id != tenant_id:
        raise ValueError("raster_crop_owner_mismatch")
    reference = await service.resolve_ref(
        session, artifact_id=offered.artifact_id, owner_id=owner_id,
        tenant_id=tenant_id, expected_digest=offered.sha256,
    )
    if (
        reference.sha256 != offered.sha256
        or reference.size_bytes != offered.size_bytes
        or reference.kind != offered.kind
        or reference.media_type != offered.media_type
        or reference.created_by_operation_id != offered.created_by_operation_id
    ):
        raise ValueError("raster_crop_reference_mismatch")
    if reference.media_type != "image/png":
        raise ValueError("raster_crop_png_required")
    payload = await service.read_bytes(
        session, artifact_id=reference.artifact_id, owner_id=owner_id,
        tenant_id=tenant_id, expected_digest=reference.sha256,
    )
    if hashlib.sha256(payload).hexdigest() != reference.sha256 or len(payload) != reference.size_bytes:
        raise ValueError("raster_crop_digest_mismatch")
    width, height, pixel_digest = _source_pixel_digest(payload)
    if region.role == "full_page_preserve":
        if (region.source_pixel_bbox != (0, 0, width, height)
                or not region.source_media_part
                or not region.source_occurrence_id
                or not region.crop_source_sha256):
            raise ValueError("raster_full_page_source_geometry_invalid")
    if region.pixel_sha256 is not None and region.pixel_sha256 != pixel_digest:
        raise ValueError("raster_crop_pixel_digest_mismatch")
    return reference, payload, width, height, pixel_digest


def validate_full_page_preserve_svg_structure(svg: str, slot_id: str) -> None:
    """Require the one approved source image to remain visible and unobstructed."""
    root = ET.fromstring(svg)
    if root.tag.rsplit("}", 1)[-1] != "svg" or set(root.attrib) != {"width", "height", "viewBox"}:
        raise ValueError("raster_full_page_svg_structure_invalid")
    children = list(root)
    if not children or children[-1].tag.rsplit("}", 1)[-1] != "image":
        raise ValueError("raster_full_page_svg_structure_invalid")
    allowed_background = {"fill", "height", "width", "x", "y", "stroke", "stroke-width"}
    for node in children[:-1]:
        if (node.tag.rsplit("}", 1)[-1] != "rect" or list(node)
                or not set(node.attrib).issubset(allowed_background)):
            raise ValueError("raster_full_page_svg_overlay_invalid")
    image = children[-1]
    if (list(image)
            or set(image.attrib) != {"href", "x", "y", "width", "height", "preserveAspectRatio"}
            or image.attrib.get("href") != slot_id
            or image.attrib.get("preserveAspectRatio") != "xMidYMid meet"):
        raise ValueError("raster_full_page_svg_image_invalid")


def _canonical_boxes(
    svg: str, *, width: int, height: int, expected_slots: set[str], preserve_only: bool = False,
) -> dict[str, tuple[float, float, float, float]]:
    contract = {slot: {"required": True, "allowOmit": False} for slot in expected_slots}
    validate_svg(svg, width=width, height=height, source_manifest=contract, allow_image_only=preserve_only)
    root = ET.fromstring(svg)
    if preserve_only:
        if len(expected_slots) != 1:
            raise ValueError("raster_full_page_svg_not_single_image")
        validate_full_page_preserve_svg_structure(svg, next(iter(expected_slots)))
    boxes = {}
    for node in root.iter(_SVG_IMAGE):
        slot = node.attrib.get("href")
        if slot not in expected_slots or slot in boxes:
            raise ValueError("raster_canonical_slot_mismatch")
        if node.attrib.get("preserveAspectRatio") != "xMidYMid meet":
            raise ValueError("raster_crop_contain_required")
        boxes[slot] = tuple(float(node.attrib[key]) for key in ("x", "y", "width", "height"))
    if set(boxes) != expected_slots:
        raise ValueError("raster_canonical_slot_mismatch")
    return boxes


def _placement_manifest(*, svg: str, manifest: SourceManifest, derivatives: dict,
                        boxes: dict, slide_revision: int, purpose: str) -> PlacementManifest:
    # Placements follow the canonical SVG's <image> DOM order (the single source
    # of truth the export validator walks), never the manifest's slot order.
    slots = {slot.slot_id: slot for slot in manifest.slots}
    placements = []
    for slot_id, (x, y, width, height) in boxes.items():
        slot = slots[slot_id]
        placements.append(SvgPlacement(
            slot_id=slot.slot_id, occurrence_id=slot.occurrence_id,
            x=x, y=y, width=width, height=height, fit=FitPolicy(),
            derivative=derivatives[slot.slot_id],
        ))
    return PlacementManifest.seal(dict(
        canonicalSvgDigest=source_digest(svg), sourceManifestDigest=manifest.digest,
        slideRevision=slide_revision, purpose=purpose, placements=placements,
        rendererVersion="svg-browser/v1",
    ))


async def prepare_raster_page_assets(
    session, service, *, approval_pin: RasterApprovalPin, owner_id: uuid.UUID,
    tenant_id: str, publication_context: SvgPublicationContext,
    presentation_id: uuid.UUID, source_revision: int, slide_revision: int,
    page_number: int, output_page_index: int, canonical_svg: str,
    width: int, height: int,
) -> RasterPageAssets:
    """Publish a page's source and screen manifests under the generation fence."""
    if (publication_context.owner_id, publication_context.tenant_id) != (owner_id, tenant_id):
        raise ValueError("raster_publication_owner_mismatch")
    analysis = await admit_approved_raster_analysis(
        session, operation_id=approval_pin.operation_id, owner_id=owner_id,
        tenant_id=tenant_id, pin=approval_pin, artifact_service=service,
    )
    if (analysis.target_canvas.width_units, analysis.target_canvas.height_units) != (width, height):
        raise ValueError("raster_target_canvas_mismatch")
    regions = _approved_crop_regions(analysis, approval_pin, page_number, canonical_svg)
    slot_ids = [raster_slot_id(page_number, region.region_id) for region in regions]
    if len(set(slot_ids)) != len(slot_ids):
        raise ValueError("raster_slot_identity_collision")
    boxes = _canonical_boxes(
        canonical_svg, width=width, height=height, expected_slots=set(slot_ids),
        preserve_only=page_number in getattr(approval_pin, "full_page_preserve_pages", ()),
    )
    slots = []
    derivatives = {}
    lineage = []
    for region, slot_id in zip(regions, slot_ids, strict=True):
        reference, payload, image_width, image_height, pixel_digest = await _region_crop(
            session, service, region=region, page=analysis.pages[page_number - 1],
            owner_id=owner_id, tenant_id=tenant_id,
        )
        preview = build_model_preview(
            payload, expected_media_type="image/png", semantic_kind="text",
            max_bytes=32 * 1024 * 1024, max_edge=8192,
        )
        preview_width, preview_height, preview_pixel_digest = _source_pixel_digest(preview.payload)
        if (preview_width, preview_height, preview_pixel_digest) != (image_width, image_height, pixel_digest):
            raise ValueError("raster_crop_preview_pixel_mismatch")
        derivative = await persist_derivative_if_current(
            session, service, context=publication_context, preview=preview,
            publication_name=f"raster-screen-{output_page_index}-{slot_id}",
            revision=slide_revision,
        )
        if derivative is None:
            raise ValueError("raster_publication_fence_lost")
        occurrence = uuid.uuid5(analysis.analysis_id, f"{region.source_occurrence_id or ''}:{region.region_id}")
        slots.append(SourceSlot(
            slot_id=slot_id, source_asset_id=reference.artifact_id,
            source_sha256=reference.sha256, source_width=image_width,
            source_height=image_height, semantic_description=f"Raster source region {region.region_id}",
            occurrence_id=occurrence,
            source_locator=dict(sourceFormat="pptx", pageIndex=page_number - 1,
                                coordinateSpace="intrinsic", objectId=region.region_id),
            extraction_kind="region_render", source_appearance_sha256=pixel_digest,
            preservation_class="critical", importance_uncertain=False,
            policy=decide_preservation_policy(
                selected=True, preservation_class="critical", source_preservation_required=True,
            ),
            selection_reason="Approved raster region", model_derivative=derivative,
            screen_policy=FitPolicy(), export_policy=FitPolicy(),
        ))
        derivatives[slot_id] = derivative
        lineage.append(dict(
            slotId=slot_id, regionId=region.region_id,
            sourceArtifactId=str(reference.artifact_id), sourceSha256=reference.sha256,
            sourceMediaSha256=region.crop_source_sha256,
            sourceOccurrenceId=region.source_occurrence_id,
            sourcePixelBbox=region.source_pixel_bbox,
            sourcePixelSha256=pixel_digest, screenDerivativeSha256=derivative.sha256,
        ))
    manifest = SourceManifest.seal(dict(
        presentationId=presentation_id, sourceRevision=source_revision,
        slots=slots, offeredPages=[dict(pageIndex=output_page_index, slotIds=slot_ids)],
    ))
    source_pointer = await persist_manifest_if_current(
        session, service, context=publication_context, manifest=manifest,
        publication_name=f"raster-source-manifest-{output_page_index}",
        revision=source_revision,
    )
    if source_pointer is None:
        raise ValueError("raster_publication_fence_lost")
    placement = _placement_manifest(
        svg=canonical_svg, manifest=manifest, derivatives=derivatives,
        boxes=boxes, slide_revision=slide_revision, purpose="screen",
    )
    placement_pointer = await persist_manifest_if_current(
        session, service, context=publication_context, manifest=placement,
        publication_name=f"raster-screen-manifest-{output_page_index}",
        revision=slide_revision,
    )
    if placement_pointer is None:
        raise ValueError("raster_publication_fence_lost")
    materialized = await resolve_svg_for_render(
        canonical_svg, manifest=manifest, derivatives=derivatives,
        session=session, owner_id=owner_id, tenant_id=tenant_id,
        width=width, height=height, service=service,
        placement_manifest=placement, page_index=output_page_index,
     allow_image_only=page_number in getattr(approval_pin, "full_page_preserve_pages", ()))
    contract = {slot.slot_id: {"required": True, "allowOmit": False} for slot in slots}
    admit_authorized_svg_materialized(
        canonical_svg, materialized, source_manifest=contract,
        width_px=width, height_px=height,
     allow_image_only=page_number in getattr(approval_pin, "full_page_preserve_pages", ()))
    source_wire = source_pointer.model_dump(mode="json", by_alias=True)
    envelope = dict(
        schemaVersion="svg-slide-assets/v1", canonicalVersion="svg-static/v2",
        sourceManifest=source_wire,
        screenPlacement=placement_pointer.model_dump(mode="json", by_alias=True),
        sourcePageIndex=output_page_index, slideRevision=slide_revision,
    )
    return RasterPageAssets(
        source_manifest=manifest, source_pointer=source_wire,
        screen_envelope=envelope, materialized_svg=materialized,
        crop_lineage=tuple(lineage),
    )


async def build_raster_page_export_assets(
    session, service, *, approval_pin: RasterApprovalPin, owner_id: uuid.UUID,
    tenant_id: str, publication_context: SvgPublicationContext,
    presentation_id: uuid.UUID, source_pointer: dict, slide_revision: int,
    page_number: int, output_page_index: int, canonical_svg: str,
    width: int, height: int, export_format: str,
) -> dict:
    """Return the standard export envelope using committed, approved crops."""
    if export_format not in {"pptx", "pdf"}:
        raise ValueError("raster_export_format_invalid")
    if (publication_context.owner_id, publication_context.tenant_id) != (owner_id, tenant_id):
        raise ValueError("raster_publication_owner_mismatch")
    analysis = await admit_approved_raster_analysis(
        session, operation_id=approval_pin.operation_id, owner_id=owner_id,
        tenant_id=tenant_id, pin=approval_pin, artifact_service=service,
    )
    manifest = await load_source_manifest_pointer(
        session, service, pointer=source_pointer, owner_id=owner_id, tenant_id=tenant_id,
    )
    if manifest.presentation_id != presentation_id:
        raise ValueError("raster_source_manifest_presentation_mismatch")
    regions = _approved_crop_regions(analysis, approval_pin, page_number, canonical_svg)
    expected_slots = {raster_slot_id(page_number, region.region_id) for region in regions}
    if {slot.slot_id for slot in manifest.slots} != expected_slots:
        raise ValueError("raster_source_manifest_slot_mismatch")
    boxes = _canonical_boxes(
        canonical_svg, width=width, height=height, expected_slots=expected_slots,
        preserve_only=page_number in getattr(approval_pin, "full_page_preserve_pages", ()),
    )
    derivatives = {}
    for region in regions:
        slot_id = raster_slot_id(page_number, region.region_id)
        reference, payload, image_width, image_height, pixel_digest = await _region_crop(
            session, service, region=region, page=analysis.pages[page_number - 1],
            owner_id=owner_id, tenant_id=tenant_id,
        )
        slot = next(slot for slot in manifest.slots if slot.slot_id == slot_id)
        if (slot.source_asset_id, slot.source_sha256, slot.source_width,
                slot.source_height, slot.source_appearance_sha256) != (
                    reference.artifact_id, reference.sha256, image_width, image_height, pixel_digest,
                ):
            raise ValueError("raster_source_manifest_crop_mismatch")
        preview = build_model_preview(
            payload, expected_media_type="image/png", semantic_kind="text",
            max_bytes=32 * 1024 * 1024, max_edge=8192,
        )
        preview_width, preview_height, preview_pixel_digest = _source_pixel_digest(preview.payload)
        if (preview_width, preview_height, preview_pixel_digest) != (image_width, image_height, pixel_digest):
            raise ValueError("raster_export_pixel_mismatch")
        derivative = await persist_derivative_if_current(
            session, service, context=publication_context, preview=preview,
            publication_name=f"raster-export-{export_format}-{output_page_index}-{slot_id}",
            revision=slide_revision,
        )
        if derivative is None:
            raise ValueError("raster_publication_fence_lost")
        derivatives[slot_id] = derivative
    placement = _placement_manifest(
        svg=canonical_svg, manifest=manifest, derivatives=derivatives,
        boxes=boxes, slide_revision=slide_revision, purpose=f"export-{export_format}",
    )
    pointer = await persist_manifest_if_current(
        session, service, context=publication_context, manifest=placement,
        publication_name=f"raster-export-manifest-{export_format}-{output_page_index}",
        revision=slide_revision,
    )
    if pointer is None:
        raise ValueError("raster_publication_fence_lost")
    return dict(
        sourceManifest=source_pointer,
        placementManifest=pointer.model_dump(mode="json", by_alias=True),
        pageIndex=output_page_index, slideRevision=slide_revision,
        fullPagePreserve=page_number in getattr(approval_pin, "full_page_preserve_pages", ()),
    )
