"""Authorized, transient raster materialization for canonical slot SVGs.

Callers supply purpose-specific derivatives, never model-preview fallbacks.
The canonical source and immutable manifest are not modified or persisted here.
"""
from __future__ import annotations

import base64
import hashlib
import io
import xml.etree.ElementTree as ET

from PIL import Image

from services.svg_asset_manifest import PlacementManifest, RasterDerivative, SourceManifest
from services.svg_contract import validate_svg


async def resolve_slide_svg_preview(slide, *, session, owner_id, tenant_id: str,
                                   width: int, height: int, service=None) -> str:
    """Read committed v2 pointers without returning manifests to the client."""
    from services.svg_asset_persistence import load_source_manifest_pointer, load_placement_manifest_pointer
    from services.svg_contract import source_digest
    if service is None:
        from services.artifact_store import configured_public_artifact_service
        service = configured_public_artifact_service()
    svg = slide.svg_content or ""
    envelope = (slide.content or {}).get("svgAssetManifest")
    if envelope is None:
        validate_svg(svg, width=width, height=height)
        return svg
    if (not isinstance(envelope, dict)
            or envelope.get("schemaVersion") != "svg-slide-assets/v1"
            or envelope.get("canonicalVersion") != "svg-static/v2"):
        raise ValueError("svg_asset_reader_version_unsupported")

    source = await load_source_manifest_pointer(session=session, service=service,
        pointer=envelope.get("sourceManifest"), owner_id=owner_id, tenant_id=tenant_id)
    placement = await load_placement_manifest_pointer(session=session, service=service,
        pointer=envelope.get("screenPlacement"), owner_id=owner_id, tenant_id=tenant_id)
    placement.validate_source(source, page_index=slide.index)
    if (placement.purpose != "screen"
            or placement.slide_revision != envelope.get("slideRevision")
            or placement.canonical_svg_digest != source_digest(svg)):
        raise ValueError("svg_asset_preview_revision_mismatch")
    derivatives = {p.slot_id: p.derivative for p in placement.placements}
    if len(derivatives) != len(placement.placements):
        raise ValueError("svg_asset_duplicate_placement")
    return await resolve_svg_for_render(svg, manifest=source, derivatives=derivatives,
        session=session, owner_id=owner_id, tenant_id=tenant_id,
        width=width, height=height, service=service,
        placement_manifest=placement, page_index=slide.index)


def _contract_manifest(manifest: SourceManifest) -> dict[str, dict]:
    return {
        slot.slot_id: {
            "required": slot.policy.required,
            "allowOmit": slot.policy.allow_omit,
        }
        for slot in manifest.slots
        if slot.policy.kind != "not_selected"
    }


def materialize_svg(svg: str, *, manifest: SourceManifest, derivatives: dict,
                    payloads: dict[str, bytes], width: int, height: int,
                    placement_manifest: PlacementManifest | None = None,
                    page_index: int | None = None, allow_image_only: bool = False) -> str:
    contract = _contract_manifest(manifest)
    if placement_manifest is not None:
        from services.svg_contract import source_digest
        if page_index is None or placement_manifest.canonical_svg_digest != source_digest(svg):
            raise ValueError("svg_asset_placement_source_mismatch")
        placement_manifest.validate_source(manifest, page_index=page_index)
        offered = next(p.slot_ids for p in manifest.offered_pages if p.page_index == page_index)
        contract = {key: value for key, value in contract.items() if key in offered}
        expected = {p.slot_id: p.derivative for p in placement_manifest.placements}
        if {k: RasterDerivative.model_validate(v) for k, v in derivatives.items()} != expected:
            raise ValueError("svg_asset_placement_derivative_mismatch")
    validate_svg(svg, width=width, height=height,
                 source_manifest=contract, allow_image_only=allow_image_only)
    root = ET.fromstring(svg)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "image":
            continue
        slot_id = node.get("href", "")
        if slot_id not in derivatives or slot_id not in payloads:
            raise ValueError("svg_asset_derivative_missing")
        derivative = RasterDerivative.model_validate(derivatives[slot_id])
        payload = payloads[slot_id]
        if len(payload) != derivative.byte_size or hashlib.sha256(payload).hexdigest() != derivative.sha256:
            raise ValueError("svg_asset_derivative_digest_mismatch")
        with Image.open(io.BytesIO(payload)) as image:
            if (image.size != (derivative.width, derivative.height)
                    or Image.MIME.get(image.format) != derivative.media_type):
                raise ValueError("svg_asset_derivative_decoder_mismatch")
            image.verify()
        node.set("href", f"data:{derivative.media_type};base64,{base64.b64encode(payload).decode('ascii')}")
    return ET.tostring(root, encoding="unicode")


async def resolve_svg_for_render(svg: str, *, manifest: SourceManifest, derivatives: dict,
                                 session, owner_id, tenant_id: str, width: int, height: int,
                                 service=None,
                                 placement_manifest: PlacementManifest | None = None,
                                 page_index: int | None = None, allow_image_only: bool = False) -> str:
    """Resolve opaque derivative refs under the current authenticated principal."""
    if service is None:
        from services.artifact_store import configured_public_artifact_service
        service = configured_public_artifact_service()
    contract = _contract_manifest(manifest)
    if placement_manifest is not None:
        if page_index is None:
            raise ValueError("svg_asset_placement_page_required")
        placement_manifest.validate_source(manifest, page_index=page_index)
        page = next((page for page in manifest.offered_pages if page.page_index == page_index), None)
        if page is None:
            raise ValueError("svg_asset_placement_page_not_offered")
        contract = {key: value for key, value in contract.items() if key in page.slot_ids}
    validate_svg(svg, width=width, height=height, source_manifest=contract, allow_image_only=allow_image_only)
    used = {node.get("href") for node in ET.fromstring(svg).iter()
            if node.tag.rsplit("}", 1)[-1] == "image"}
    if set(derivatives) != used:
        raise ValueError("svg_asset_derivative_slot_set_mismatch")
    payloads = {}
    for slot_id, metadata in derivatives.items():
        derivative = RasterDerivative.model_validate(metadata)
        reference = await service.resolve_ref(session, artifact_id=derivative.ref,
            owner_id=owner_id, tenant_id=tenant_id, expected_digest=derivative.sha256)
        if reference.kind != "image-derivative" or reference.media_type != derivative.media_type:
            raise ValueError("svg_asset_derivative_binding_invalid")
        payloads[slot_id] = await service.read_ref_bytes(reference)
    return materialize_svg(svg, manifest=manifest, derivatives=derivatives,
                           payloads=payloads, width=width, height=height,
                           placement_manifest=placement_manifest, page_index=page_index, allow_image_only=allow_image_only)


async def bind_export_derivatives(*, derivatives: dict, session, service,
                                  owner_id, tenant_id: str, operation_id) -> dict:
    """Reuse committed bytes with freshly authorized current-operation refs."""
    bound = {}
    for slot_id, metadata in derivatives.items():
        derivative = RasterDerivative.model_validate(metadata)
        reference = await service.resolve_ref(session, artifact_id=derivative.ref,
            owner_id=owner_id, tenant_id=tenant_id, expected_digest=derivative.sha256)
        if reference.kind != "image-derivative" or reference.media_type != derivative.media_type:
            raise ValueError("svg_asset_derivative_binding_invalid")
        bound[slot_id] = await service.register_consumer_ref(session, reference,
            created_by_operation_id=operation_id)
    return bound
