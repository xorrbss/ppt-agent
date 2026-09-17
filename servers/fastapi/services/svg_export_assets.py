"""Versioned SVG export transport, using only authenticated artifact authority."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from services.svg_asset_manifest import ArtifactPointer, PlacementManifest, SourceManifest
from services.svg_contract import source_digest, validate_svg

SOURCE_KIND = "source-manifest"
PLACEMENT_KIND = "placement-manifest"
SOURCE_MIME = "application/json"
PLACEMENT_MIME = "application/json"


def validate_export_placement(svg, source, placement, *, page_index, slide_revision,
                              presentation_id, export_format, width, height):
    if str(source.presentation_id) != str(presentation_id):
        raise ValueError("svg_export_manifest_presentation_mismatch")
    if placement.purpose not in {"export", f"export-{export_format}"}:
        raise ValueError("svg_export_placement_purpose_invalid")
    if placement.slide_revision != slide_revision or placement.canonical_svg_digest != source_digest(svg):
        raise ValueError("svg_export_placement_revision_mismatch")
    placement.validate_source(source, page_index=page_index)
    page = next(p for p in source.offered_pages if p.page_index == page_index)
    slots = {s.slot_id: s for s in source.slots}
    validate_svg(svg, width=width, height=height, source_manifest={
        slot: {"required": slots[slot].policy.required, "allowOmit": slots[slot].policy.allow_omit}
        for slot in page.slot_ids
    })
    images = _positioned_images(svg)
    if len(images) != len(placement.placements):
        raise ValueError("svg_export_placement_count_mismatch")
    used = set()
    for (node, tx, ty), item in zip(images, placement.placements):
        if item.slot_id in used or node.get("href") != item.slot_id:
            raise ValueError("svg_export_placement_slot_mismatch")
        used.add(item.slot_id)
        geometry = {key: float(node.get(key, "0")) for key in ("x", "y", "width", "height")}
        geometry["x"] += tx
        geometry["y"] += ty
        if any(value != getattr(item, key) for key, value in geometry.items()):
            raise ValueError("svg_export_placement_geometry_mismatch")
        # A transformed/cropped image needs a geometry-aware recipe. Until that
        # transport is supported, reject it instead of silently changing pixels.
        if item.crop is not None or item.transform != (1, 0, 0, 1, 0, 0):
            raise ValueError("svg_export_placement_transform_unsupported")
        expected = "xMidYMid slice" if item.fit.fit == "cover" else "xMidYMid meet"
        if node.get("preserveAspectRatio", "xMidYMid meet") != expected:
            raise ValueError("svg_export_placement_fit_mismatch")
    return {item.slot_id: item.derivative for item in placement.placements}


def _positioned_images(svg):
    """Measure the contract's nested translations without changing SVG pixels."""
    import re
    from services.svg_contract import _numbers
    images = []
    def visit(node, tx=0., ty=0.):
        transform = node.get("transform")
        if transform:
            match = re.fullmatch(r"translate\(([^()]+)\)", transform)
            if match is None:
                raise ValueError("svg_export_placement_transform_unsupported")
            values = _numbers(match.group(1))
            if len(values) not in {1, 2}:
                raise ValueError("svg_export_placement_transform_unsupported")
            tx += values[0]
            ty += values[1] if len(values) == 2 else 0.
        if node.tag.rsplit("}", 1)[-1] == "image":
            images.append((node, tx, ty))
        for child in node:
            visit(child, tx, ty)
    visit(ET.fromstring(svg))
    return images


async def _load_original_bytes(slot, *, session, owner_id, tenant_id):
    import asyncio
    import hashlib
    from sqlalchemy import select
    from models.sql.image_asset import ImageAsset
    from services.assets import AssetGateway, canonicalize_legacy_image_asset
    from utils.asset_directory_utils import get_images_directory
    from utils.owner_context import (set_current_owner_id, reset_current_owner_id,
                                    set_current_tenant_id, reset_current_tenant_id)
    row = (await session.execute(select(ImageAsset).where(
        ImageAsset.id == slot.source_asset_id, ImageAsset.owner_id == owner_id))).scalar_one_or_none()
    if row is None:
        raise ValueError("svg_export_source_asset_not_found")
    owner_token = set_current_owner_id(owner_id)
    tenant_token = set_current_tenant_id(tenant_id)
    try:
        binding = await canonicalize_legacy_image_asset(session, image=row,
                                                       image_root=get_images_directory())
        gateway = AssetGateway()
        confirmed, path = await gateway.resolve_for_processing(session,
            asset_id=binding.asset_id, kind=binding.kind)
        if confirmed != binding or binding.sha256.removeprefix("sha256:") != slot.source_sha256:
            raise ValueError("svg_export_source_asset_binding_mismatch")
        def read():
            with path.open("rb") as stream:
                return stream.read(32 * 1024 * 1024 + 1)
        payload = await asyncio.to_thread(read)
        if (len(payload) > 32 * 1024 * 1024 or len(payload) != binding.size_bytes
                or hashlib.sha256(payload).hexdigest() != slot.source_sha256):
            raise ValueError("svg_export_source_asset_digest_mismatch")
        if await gateway.bind(session, asset_id=binding.asset_id, kind=binding.kind) != binding:
            raise ValueError("svg_export_source_asset_changed")
        return payload
    finally:
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


async def build_slide_export_assets(slide, *, service, session, claim, tenant_id, export_format, canvas):
    """Publish export-purpose recipes from original assets under the live fence."""
    import asyncio
    from services.svg_asset_manifest import PlacementManifest, SvgPlacement
    from services.svg_asset_persistence import (load_source_manifest_pointer, SvgPublicationContext,
        persist_derivative_if_current, persist_manifest_if_current)
    from services.svg_image_derivative import build_purpose_derivative
    from services.svg_renderer import SVG_RENDERER_VERSION
    envelope = (slide.content or {}).get("svgAssetManifest")
    if envelope is None:
        return None
    if (not isinstance(envelope, dict) or envelope.get("schemaVersion") != "svg-slide-assets/v1"
            or envelope.get("canonicalVersion") != "svg-static/v2"):
        raise ValueError("svg_export_asset_reader_version_unsupported")
    source = await load_source_manifest_pointer(session, service,
        pointer=envelope.get("sourceManifest"), owner_id=claim.owner_id, tenant_id=tenant_id)
    if str(source.presentation_id) != str(claim.presentation_id):
        raise ValueError("svg_export_manifest_presentation_mismatch")
    revision = envelope.get("slideRevision")
    if type(revision) is not int or revision < 0:
        raise ValueError("svg_export_slide_revision_invalid")
    page_index = envelope.get("sourcePageIndex", slide.index)
    page = next((p for p in source.offered_pages if p.page_index == page_index), None)
    if page is None:
        raise ValueError("svg_export_source_page_missing")
    context = SvgPublicationContext(operation_id=claim.operation_id, owner_id=claim.owner_id,
        tenant_id=tenant_id, attempt_number=claim.attempt_number, attempt_token=claim.attempt_token,
        fencing_token=claim.fencing_token, operation_kind="export")
    slots = {slot.slot_id: slot for slot in source.slots}
    validate_svg(slide.svg_content, width=canvas["widthPx"], height=canvas["heightPx"],
        source_manifest={s: {"required": slots[s].policy.required, "allowOmit": slots[s].policy.allow_omit}
                         for s in page.slot_ids})
    placements = []
    for index, (node, tx, ty) in enumerate(_positioned_images(slide.svg_content)):
        slot_id = node.get("href", "")
        if slot_id not in page.slot_ids:
            raise ValueError("svg_export_slot_not_offered")
        slot = slots[slot_id]
        payload = await _load_original_bytes(slot, session=session, owner_id=claim.owner_id, tenant_id=tenant_id)
        preview = await asyncio.to_thread(build_purpose_derivative, payload, purpose="export",
            width_css_px=float(node.get("width", "0")), height_css_px=float(node.get("height", "0")),
            fit=slot.export_policy.fit, semantic_kind="unknown")
        if (preview.source_sha256 != slot.source_sha256
                or preview.source_appearance_sha256 != slot.source_appearance_sha256):
            raise ValueError("svg_export_source_appearance_mismatch")
        derivative = await persist_derivative_if_current(session, service, context=context,
            preview=preview, publication_name=f"svg-exp-{slide.id.hex[:16]}-{index}", revision=revision)
        if derivative is None:
            raise ValueError("svg_export_asset_fence_lost")
        placements.append(SvgPlacement(slot_id=slot_id, occurrence_id=slot.occurrence_id,
            x=float(node.get("x", "0")) + tx, y=float(node.get("y", "0")) + ty,
            width=float(node.get("width", "0")), height=float(node.get("height", "0")),
            fit=slot.export_policy, derivative=derivative))
    manifest = PlacementManifest.seal(dict(canonicalSvgDigest=source_digest(slide.svg_content),
        sourceManifestDigest=source.digest, slideRevision=revision, purpose=f"export-{export_format}",
        placements=[p.model_dump(mode="json", by_alias=True) for p in placements], rendererVersion=SVG_RENDERER_VERSION))
    manifest.validate_source(source, page_index=page_index)
    validate_export_placement(slide.svg_content, source, manifest, page_index=page_index,
        slide_revision=revision, presentation_id=claim.presentation_id, export_format=export_format,
        width=canvas["widthPx"], height=canvas["heightPx"])
    pointer = await persist_manifest_if_current(session, service, context=context, manifest=manifest,
        publication_name=f"svg-exp-{slide.id.hex[:16]}-manifest", revision=revision)
    if pointer is None:
        raise ValueError("svg_export_asset_fence_lost")
    return {"sourceManifest": envelope["sourceManifest"],
            "placementManifest": pointer.model_dump(mode="json", by_alias=True),
            "pageIndex": page_index, "slideRevision": revision}


async def prepare_export_assets(binding, *, svg, service, session, claim, tenant_id,
                                export_format, width, height):
    if set(binding) != {"sourceManifest", "placementManifest", "pageIndex", "slideRevision"}:
        raise ValueError("svg_export_asset_binding_invalid")
    refs = []
    manifests = []
    from services.svg_asset_persistence import load_source_manifest_pointer, load_placement_manifest_pointer
    for name, kind, mime, loader in (
        ("sourceManifest", SOURCE_KIND, SOURCE_MIME, load_source_manifest_pointer),
        ("placementManifest", PLACEMENT_KIND, PLACEMENT_MIME, load_placement_manifest_pointer),
    ):
        pointer = ArtifactPointer.model_validate(binding[name])
        ref = await service.resolve_ref(session, artifact_id=pointer.ref, owner_id=claim.owner_id,
                                        tenant_id=tenant_id, expected_digest=pointer.sha256)
        if ref.kind != kind or ref.media_type != mime:
            raise ValueError("svg_export_manifest_artifact_invalid")
        manifests.append(await loader(session, service, pointer=pointer,
            owner_id=claim.owner_id, tenant_id=tenant_id))
        refs.append(await service.register_consumer_ref(session, ref, created_by_operation_id=claim.operation_id))
    source, placement = manifests
    derivatives = validate_export_placement(svg, source, placement,
        page_index=binding["pageIndex"], slide_revision=binding["slideRevision"],
        presentation_id=claim.presentation_id, export_format=export_format, width=width, height=height)
    from services.svg_asset_resolver import bind_export_derivatives
    from services.public_export_operation import _worker_ref
    derivative_refs = await bind_export_derivatives(derivatives=derivatives, session=session,
        service=service, owner_id=claim.owner_id, tenant_id=tenant_id, operation_id=claim.operation_id)
    wire = {"sourceManifestRef": _worker_ref(refs[0]).to_wire(),
            "placementManifestRef": _worker_ref(refs[1]).to_wire(),
            "sourceManifestDigest": source.digest, "placementManifestDigest": placement.digest,
            "pageIndex": binding["pageIndex"], "slideRevision": binding["slideRevision"],
            "derivatives": {slot: _worker_ref(ref).to_wire() for slot, ref in derivative_refs.items()}}
    return wire, [*refs, *derivative_refs.values()]


def materialize_export_assets(binding, *, svg, job, store, presentation_id,
                              export_format, width, height):
    from services.isolated_worker_contracts import ArtifactRef
    from services.svg_asset_resolver import materialize_svg
    if set(binding) != {"sourceManifestRef", "placementManifestRef", "sourceManifestDigest",
                        "placementManifestDigest", "pageIndex", "slideRevision", "derivatives"}:
        raise ValueError("svg_export_asset_transport_invalid")

    def read(wire, kind, mime):
        ref = ArtifactRef.from_wire(wire)
        if (ref.owner_id != job.input_artifact.owner_id or ref.tenant_id != job.input_artifact.tenant_id
                or ref.created_by_operation_id != job.operation_id or ref.kind != kind or ref.media_type != mime):
            raise ValueError("svg_export_asset_operation_binding_invalid")
        return ref, store.read(ref)

    _, source_bytes = read(binding["sourceManifestRef"], SOURCE_KIND, SOURCE_MIME)
    _, placement_bytes = read(binding["placementManifestRef"], PLACEMENT_KIND, PLACEMENT_MIME)
    source = SourceManifest.model_validate_json(source_bytes)
    placement = PlacementManifest.model_validate_json(placement_bytes)
    if source.digest != binding["sourceManifestDigest"] or placement.digest != binding["placementManifestDigest"]:
        raise ValueError("svg_export_manifest_digest_mismatch")
    derivatives = validate_export_placement(svg, source, placement,
        page_index=binding["pageIndex"], slide_revision=binding["slideRevision"], presentation_id=presentation_id,
        export_format=export_format, width=width, height=height)
    if set(binding["derivatives"]) != set(derivatives):
        raise ValueError("svg_export_derivative_slot_mismatch")
    if (sum(d.byte_size for d in derivatives.values()) > 64 * 1024 * 1024
            or sum(d.width * d.height for d in derivatives.values()) > 64_000_000
            or any(d.byte_size > 32 * 1024 * 1024 or max(d.width, d.height) > 16384 for d in derivatives.values())):
        raise ValueError("svg_export_derivative_page_budget_exceeded")
    payloads = {}
    for slot, derivative in derivatives.items():
        ref, payload = read(binding["derivatives"][slot], "image-derivative", derivative.media_type)
        if ref.sha256 != derivative.sha256 or ref.size_bytes != derivative.byte_size:
            raise ValueError("svg_export_derivative_metadata_mismatch")
        payloads[slot] = payload
    resolved = materialize_svg(svg, manifest=source, placement_manifest=placement,
        page_index=binding["pageIndex"], derivatives=derivatives, payloads=payloads, width=width, height=height)
    page = next(p for p in source.offered_pages if p.page_index == binding["pageIndex"])
    slots = {s.slot_id: s for s in source.slots}
    contract = {s: {"required": slots[s].policy.required, "allowOmit": slots[s].policy.allow_omit}
                for s in page.slot_ids}
    return resolved, contract
