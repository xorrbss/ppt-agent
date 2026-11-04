"""Authorized, transient raster materialization for canonical slot SVGs.

Callers supply purpose-specific derivatives, never model-preview fallbacks.
The canonical source and immutable manifest are not modified or persisted here.
"""

from __future__ import annotations

import base64
import io
import xml.etree.ElementTree as ET

from PIL import Image

from services.svg_asset_manifest import PlacementManifest, RasterDerivative, SourceManifest
from services.svg_contract import validate_svg
from utils.hashing import sha256_hex


async def resolve_slide_svg_preview(
    slide, *, session, owner_id, tenant_id: str, width: int, height: int, service=None
) -> str:
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
    if (
        not isinstance(envelope, dict)
        or envelope.get("schemaVersion") != "svg-slide-assets/v1"
        or envelope.get("canonicalVersion") != "svg-static/v2"
    ):
        raise ValueError("svg_asset_reader_version_unsupported")

    source = await load_source_manifest_pointer(
        session=session, service=service, pointer=envelope.get("sourceManifest"), owner_id=owner_id, tenant_id=tenant_id
    )
    placement = await load_placement_manifest_pointer(
        session=session,
        service=service,
        pointer=envelope.get("screenPlacement"),
        owner_id=owner_id,
        tenant_id=tenant_id,
    )
    placement.validate_source(source, page_index=slide.index)
    if (
        placement.purpose != "screen"
        or placement.slide_revision != envelope.get("slideRevision")
        or placement.canonical_svg_digest != source_digest(svg)
    ):
        raise ValueError("svg_asset_preview_revision_mismatch")
    derivatives = {p.slot_id: p.derivative for p in placement.placements}
    if len(derivatives) != len(placement.placements):
        raise ValueError("svg_asset_duplicate_placement")
    return await resolve_svg_for_render(
        svg,
        manifest=source,
        derivatives=derivatives,
        session=session,
        owner_id=owner_id,
        tenant_id=tenant_id,
        width=width,
        height=height,
        service=service,
        placement_manifest=placement,
        page_index=slide.index,
        system_furniture=(slide.content or {}).get("svgReceipt", {}).get("system_furniture"),
    )


async def resolve_fixed_glyph_slide_preview(
    slide, presentation, *, session, owner_id, tenant_id: str, width: int, height: int,
    revision: str | None,
) -> str:
    """Return only a display derivative of a persisted, pinned v3 slide."""
    from models.svg_template_frame import SvgTemplateFrameAdaptation
    from models.svg_template_codec import unpack_adaptation_fonts
    from services.svg_contract import source_digest
    from services.svg_master_furniture import attested_svg_furniture
    from services.svg_templates.catalog import resolve_template_selection
    from services.svg_templates.frame_glyphs import validate_frame_glyph_authority
    from services.svg_templates.generation import template_selection
    from services.svg_renderer import _svg_fragment, svg_preview_html, template_manifest_font_css
    from utils.svg_template_glyph_materialization import materialize_fixed_glyph_svg

    content = slide.content or {}
    receipt = content.get("svgReceipt") or {}
    marker = receipt.get("fixedGlyphPreview")
    if not isinstance(marker, dict) or marker.get("version") != "svg-fixed-glyph-preview/v1":
        raise ValueError("svg_template_glyph_marker_invalid")
    canonical_svg = slide.svg_content or ""
    canonical_digest = source_digest(canonical_svg)
    envelope = content.get("svgAssetManifest")
    expected_revision = (str(envelope.get("slideRevision")) if isinstance(envelope, dict)
                         else canonical_digest)
    if (revision != expected_revision or marker.get("slideRevision") != (
            envelope.get("slideRevision") if isinstance(envelope, dict) else canonical_digest)
            or marker.get("canonicalSvgDigest") != canonical_digest
            or (receipt.get("quality") or {}).get("fixed_glyph_preview", {}).get("pngDigest")
            != marker.get("pngDigest")
            or (receipt.get("quality") or {}).get("png_digest") != marker.get("pngDigest")):
        raise ValueError("svg_template_glyph_revision_mismatch")
    if marker.get("pngDigest") != "sha256:" + str(receipt.get("png_sha256", "")):
        raise ValueError("svg_template_glyph_png_binding_mismatch")
    if isinstance(envelope, dict):
        placement = envelope.get("screenPlacement") or {}
        if marker.get("screenPlacementDigest") != placement.get("sha256"):
            raise ValueError("svg_template_glyph_placement_mismatch")
    snapshot = ((presentation.theme or {}).get("svg") or {}).get("styleSnapshot")
    selection = template_selection(snapshot)
    if (selection is None or selection.get("mode") != "template-redesign"
            or selection.get("template_pin") != marker.get("templatePin")):
        raise ValueError("svg_template_glyph_template_pin_mismatch")
    # A published presentation's frozen snapshot is the same archived-pin
    # authority used by generation recovery. Recheck the owner-scoped version.
    current = await resolve_template_selection(session, pin=selection["template_pin"],
        mode="template-redesign", canvas=selection["canvas"], allow_archived=True)
    from services.svg_templates.selection_material import (
        SELECTION_SCHEMA_V1, ensure_selection_material, material_key, template_adaptations, template_package,
    )
    if selection.get("schema_version") == SELECTION_SCHEMA_V1:
        unchanged = (current.get("package") == selection.get("package")
                     and current.get("adaptations") == selection.get("adaptations"))
    else:
        unchanged = material_key(current) == selection.get("material_key")
    if current.get("version_id") != selection.get("version_id") or not unchanged:
        raise ValueError("svg_template_glyph_frozen_pin_mismatch")
    await ensure_selection_material(session, selection)
    frame_id = marker.get("frameId")
    raw_adaptation = template_adaptations(selection).get(frame_id)
    if not isinstance(raw_adaptation, dict):
        raise ValueError("svg_template_glyph_frame_missing")
    adaptation = SvgTemplateFrameAdaptation.model_validate(unpack_adaptation_fonts(
        raw_adaptation, template_package(selection)))
    frame = adaptation.frame
    authority = frame.fixed_glyph_preview
    if (authority is None or frame.frame_id != frame_id
            or frame.digest != marker.get("frameDigest")
            or adaptation.digest != marker.get("adaptationDigest")
            or authority.authority_digest != marker.get("authorityDigest")
            or authority.materializer_code_digest != marker.get("materializerCodeDigest")
            or frame.font_manifest.digest != marker.get("fontManifestDigest")
            or frame.source_canvas.width_px != width or frame.source_canvas.height_px != height):
        raise ValueError("svg_template_glyph_authority_mismatch")
    validate_frame_glyph_authority(frame)
    furniture = receipt.get("system_furniture")
    if not isinstance(furniture, dict):
        raise ValueError("svg_template_glyph_furniture_missing")
    contract = {node.get("href"): {"required": True} for node in ET.fromstring(canonical_svg).iter()
                if node.tag.rsplit("}", 1)[-1] == "image"
                and node.get("href", "").startswith("source-slot-")}
    attested_svg_furniture(canonical_svg, furniture,
        source_manifest=contract if envelope is not None else None, width=width, height=height)
    binding = furniture.get("generated_template_frame") or {}
    if binding.get("fixed_glyph_preview_digest") != authority.authority_digest:
        raise ValueError("svg_template_glyph_furniture_authority_mismatch")
    resolved = await resolve_slide_svg_preview(slide, session=session, owner_id=owner_id,
        tenant_id=tenant_id, width=width, height=height)
    render_wrapper = svg_preview_html(canonical_svg, width=width, height=height,
        materialized_svg=resolved, font_manifest=frame.font_manifest,
        system_furniture=furniture)
    result = materialize_fixed_glyph_svg(
        canonical_svg, _svg_fragment(render_wrapper).group(1), authority,
        trusted_font_css=template_manifest_font_css(
            frame.font_manifest, _svg_fragment(render_wrapper).group(1)))
    if (result.display_digest != marker.get("screenDisplaySvgDigest")
            or result.render_digest != marker.get("screenRenderSvgDigest")
            or result.projection_digest != marker.get("qidProjectionDigest")
            or result.trusted_font_css_digest != marker.get("trustedFontCssDigest")
            or result.canonical_digest != marker.get("canonicalSvgDigest")):
        raise ValueError("svg_template_glyph_display_digest_mismatch")
    return result.display_svg


def _contract_manifest(manifest: SourceManifest) -> dict[str, dict]:
    return {
        slot.slot_id: {
            "required": slot.policy.required,
            "allowOmit": slot.policy.allow_omit,
        }
        for slot in manifest.slots
        if slot.policy.kind != "not_selected"
    }


def materialize_svg(
    svg: str,
    *,
    manifest: SourceManifest,
    derivatives: dict,
    payloads: dict[str, bytes],
    width: int,
    height: int,
    placement_manifest: PlacementManifest | None = None,
    page_index: int | None = None,
    allow_image_only: bool = False,
    trusted_furniture_images: dict[str, str] | None = None,
) -> str:
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
    validate_svg(svg, width=width, height=height, source_manifest=contract, allow_image_only=allow_image_only,
                 trusted_furniture_images=trusted_furniture_images)
    root = ET.fromstring(svg)
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] != "image":
            continue
        if node.get("id") in (trusted_furniture_images or {}):
            continue
        slot_id = node.get("href", "")
        if slot_id not in derivatives or slot_id not in payloads:
            raise ValueError("svg_asset_derivative_missing")
        derivative = RasterDerivative.model_validate(derivatives[slot_id])
        payload = payloads[slot_id]
        if len(payload) != derivative.byte_size or sha256_hex(payload) != derivative.sha256:
            raise ValueError("svg_asset_derivative_digest_mismatch")
        with Image.open(io.BytesIO(payload)) as image:
            if (
                image.size != (derivative.width, derivative.height)
                or Image.MIME.get(image.format) != derivative.media_type
            ):
                raise ValueError("svg_asset_derivative_decoder_mismatch")
            image.verify()
        node.set("href", f"data:{derivative.media_type};base64,{base64.b64encode(payload).decode('ascii')}")
    return ET.tostring(root, encoding="unicode")


async def resolve_svg_for_render(
    svg: str,
    *,
    manifest: SourceManifest,
    derivatives: dict,
    session,
    owner_id,
    tenant_id: str,
    width: int,
    height: int,
    service=None,
    placement_manifest: PlacementManifest | None = None,
    page_index: int | None = None,
    allow_image_only: bool = False,
    system_furniture: dict | None = None,
) -> str:
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
    from services.svg_master_furniture import attested_svg_furniture
    furniture = attested_svg_furniture(svg, system_furniture, source_manifest=contract,
                                       width=width, height=height) if system_furniture else {}
    trusted_images = system_furniture.get("images", {}) if furniture else {}
    validate_svg(svg, width=width, height=height, source_manifest=contract, allow_image_only=allow_image_only,
                 trusted_furniture_images=trusted_images)
    used = {node.get("href") for node in ET.fromstring(svg).iter()
            if node.tag.rsplit("}", 1)[-1] == "image" and node.get("id") not in furniture}
    if set(derivatives) != used:
        raise ValueError("svg_asset_derivative_slot_set_mismatch")
    payloads = {}
    for slot_id, metadata in derivatives.items():
        derivative = RasterDerivative.model_validate(metadata)
        reference = await service.resolve_ref(
            session,
            artifact_id=derivative.ref,
            owner_id=owner_id,
            tenant_id=tenant_id,
            expected_digest=derivative.sha256,
        )
        if reference.kind != "image-derivative" or reference.media_type != derivative.media_type:
            raise ValueError("svg_asset_derivative_binding_invalid")
        payloads[slot_id] = await service.read_ref_bytes(reference)
    return materialize_svg(
        svg,
        manifest=manifest,
        derivatives=derivatives,
        payloads=payloads,
        width=width,
        height=height,
        placement_manifest=placement_manifest,
        page_index=page_index,
        allow_image_only=allow_image_only,
        trusted_furniture_images=trusted_images,
    )


async def bind_export_derivatives(
    *, derivatives: dict, session, service, owner_id, tenant_id: str, operation_id
) -> dict:
    """Reuse committed bytes with freshly authorized current-operation refs."""
    bound = {}
    for slot_id, metadata in derivatives.items():
        derivative = RasterDerivative.model_validate(metadata)
        reference = await service.resolve_ref(
            session,
            artifact_id=derivative.ref,
            owner_id=owner_id,
            tenant_id=tenant_id,
            expected_digest=derivative.sha256,
        )
        if reference.kind != "image-derivative" or reference.media_type != derivative.media_type:
            raise ValueError("svg_asset_derivative_binding_invalid")
        bound[slot_id] = await service.register_consumer_ref(session, reference, created_by_operation_id=operation_id)
    return bound
