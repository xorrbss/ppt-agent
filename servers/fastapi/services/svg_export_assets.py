"""Versioned SVG export transport, using only authenticated artifact authority."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from services.svg_asset_manifest import ArtifactPointer, PlacementManifest, SourceManifest
from services.svg_contract import source_digest, validate_svg
from utils.hashing import sha256_hex

SOURCE_KIND = "source-manifest"
PLACEMENT_KIND = "placement-manifest"
SOURCE_MIME = "application/json"
PLACEMENT_MIME = "application/json"


import hashlib
import json
from pydantic import BaseModel, ConfigDict
from uuid import UUID
from typing import Literal


class _ExportSourceLineage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["svg-export-source-lineage/v1"] = "svg-export-source-lineage/v1"
    source_presentation_id: UUID
    target_presentation_id: UUID
    operation_id: UUID
    owner_id: UUID
    tenant_id: str
    attempt_number: int
    fencing_token: int
    source_manifest_digest: str
    placement_manifest_digest: str
    canonical_svg_digest: str
    page_index: int
    slide_revision: int


def _check_export_source_lineage(lineage, source, placement, *, presentation_id, svg, page_index, operation_id=None, owner_id=None, tenant_id=None, attempt_number=None, fencing_token=None):
    if lineage is None:
        return False
    proof = _ExportSourceLineage.model_validate(lineage)
    if proof.source_presentation_id != source.presentation_id or str(proof.target_presentation_id) != str(presentation_id) or proof.source_presentation_id == proof.target_presentation_id or proof.source_manifest_digest != source.digest or proof.placement_manifest_digest != placement.digest or proof.canonical_svg_digest != source_digest(svg) or proof.page_index != page_index or proof.slide_revision != placement.slide_revision or proof.attempt_number < 1 or proof.fencing_token < 1 or not proof.tenant_id or (operation_id is not None and proof.operation_id != operation_id) or (owner_id is not None and proof.owner_id != owner_id) or (tenant_id is not None and proof.tenant_id != tenant_id) or (attempt_number is not None and proof.attempt_number != attempt_number) or (fencing_token is not None and proof.fencing_token != fencing_token):
        raise ValueError("svg_export_source_lineage_invalid")
    return True


def _attest_furniture(svg, *, source_preservation, system_furniture, source_manifest, width, height):
    from services.svg_master_furniture import attested_source_furniture, attested_svg_furniture
    if source_preservation:
        nodes = attested_source_furniture(svg, source_preservation)
        receipt = source_preservation.get("system_furniture", {})
    elif system_furniture:
        nodes = attested_svg_furniture(svg, system_furniture, source_manifest=source_manifest, width=width, height=height)
        receipt = system_furniture
    else:
        nodes, receipt = {}, {}
    return nodes, receipt.get("images", {}) if nodes else {}


def validate_export_placement(svg, source, placement, *, page_index, slide_revision,
                              presentation_id, export_format, width, height, allow_image_only=False,
                              source_preservation=None, system_furniture=None, source_lineage=None):
    if str(source.presentation_id) != str(presentation_id) and not _check_export_source_lineage(source_lineage, source, placement, presentation_id=presentation_id, svg=svg, page_index=page_index):
        raise ValueError("svg_export_manifest_presentation_mismatch")
    if placement.purpose not in {"export", f"export-{export_format}"}:
        raise ValueError("svg_export_placement_purpose_invalid")
    if placement.slide_revision != slide_revision or placement.canonical_svg_digest != source_digest(svg):
        raise ValueError("svg_export_placement_revision_mismatch")
    placement.validate_source(source, page_index=page_index)
    page = next(p for p in source.offered_pages if p.page_index == page_index)
    slots = {s.slot_id: s for s in source.slots}
    contract = {slot: {"required": slots[slot].policy.required, "allowOmit": slots[slot].policy.allow_omit}
                for slot in page.slot_ids}
    furniture, trusted_images = _attest_furniture(svg, source_preservation=source_preservation,
        system_furniture=system_furniture, source_manifest=contract, width=width, height=height)
    if allow_image_only:
        verified_full_page_preserve_export(
            {"fullPagePreserve": True}, canonical_svg=svg, source_contract={
                slot: {"required": slots[slot].policy.required, "allowOmit": slots[slot].policy.allow_omit}
                for slot in page.slot_ids
            }
        )
    validate_svg(svg, width=width, height=height, source_manifest={
        slot: {"required": slots[slot].policy.required, "allowOmit": slots[slot].policy.allow_omit}
        for slot in page.slot_ids
    }, allow_image_only=allow_image_only, trusted_furniture_images=trusted_images)
    images = _positioned_images(svg, excluded_ids=furniture)
    if len(images) != len(placement.placements):
        raise ValueError("svg_export_placement_count_mismatch")
    used = set()
    for (node, tx, ty), item in zip(images, placement.placements, strict=False):
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


def _positioned_images(svg, *, excluded_ids=()):
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
        if node.tag.rsplit("}", 1)[-1] == "image" and node.get("id") not in excluded_ids:
            images.append((node, tx, ty))
        for child in node:
            visit(child, tx, ty)
    visit(ET.fromstring(svg))
    return images


async def _load_sealed_source_bytes(
    slot, *, session, row, owner_id, presentation_id, page_index, receipt
):
    """Read a source-preserving image only from its sealed source manifest."""
    from sqlalchemy import select
    from models.sql.source_content import SourceContentManifestRecord
    from services.source_content.manifest_store import (
        _manifest_from_record,
        load_source_content_manifest_asset,
    )
    from services.source_images.registry import source_image_occurrence_asset_id
    from services.svg_source_layout import source_pages

    if not isinstance(receipt, dict):
        raise ValueError("svg_export_source_receipt_invalid")
    manifest_digest = receipt.get("manifest_digest")
    source_file_digest = receipt.get("source_digest")
    image_bindings = [
        binding for binding in receipt.get("bindings", [])
        if isinstance(binding, dict)
        and binding.get("kind") == "image"
        and binding.get("slot_id") == slot.slot_id
    ]
    expected_asset_digest = "sha256:" + slot.source_sha256
    if (
        receipt.get("source_page_index") != page_index
        or not isinstance(manifest_digest, str)
        or not isinstance(source_file_digest, str)
        or len(image_bindings) != 1
        or image_bindings[0].get("asset_digest") != expected_asset_digest
    ):
        raise ValueError("svg_export_source_asset_receipt_mismatch")

    owner_scope = str(owner_id) if owner_id is not None else "anonymous"
    owner_predicate = (
        SourceContentManifestRecord.owner_id == owner_id
        if owner_id is not None
        else SourceContentManifestRecord.owner_id.is_(None)
    )
    records = list((await session.scalars(
        select(SourceContentManifestRecord)
        .where(
            SourceContentManifestRecord.owner_scope == owner_scope,
            owner_predicate,
            SourceContentManifestRecord.manifest_digest == manifest_digest,
            SourceContentManifestRecord.source_digest == source_file_digest,
        )
    )).unique().all())
    if len(records) != 1:
        raise ValueError("svg_export_source_manifest_binding_invalid")
    manifest = _manifest_from_record(records[0], owner_id=owner_id)
    pages = source_pages(manifest, chart_contract=receipt.get("source_chart_contract"))
    if not 0 <= page_index < len(pages):
        raise ValueError("svg_export_source_page_missing")
    page = pages[page_index]
    source_id = image_bindings[0].get("source_id")
    block = next(
        (item for item in page.blocks if item.source_block_id == source_id),
        None,
    )
    asset_id = block.payload.get("assetId") if block is not None else None
    block_digest = block.payload.get("contentDigest") if block is not None else None
    if (
        receipt.get("source_page_id") != page.source_unit_id
        or block is None
        or block.kind != "image"
        or not isinstance(asset_id, str)
        or block_digest != expected_asset_digest
        or source_image_occurrence_asset_id(
            presentation_id, block.source_block_id, slot.source_sha256
        ) != row.id
        or row.id != slot.source_asset_id
    ):
        raise ValueError("svg_export_source_occurrence_binding_invalid")
    extras = row.extras if isinstance(row.extras, dict) else {}
    if (
        extras.get("presentation_id") != str(presentation_id)
        or extras.get("source_occurrence_id") != block.source_block_id
        or extras.get("source_slide_index") != page_index
        or extras.get("digest") != slot.source_sha256
    ):
        raise ValueError("svg_export_source_occurrence_binding_invalid")

    sealed = await load_source_content_manifest_asset(
        session,
        owner_id=owner_id,
        manifest_id=manifest.manifest_id,
        asset_id=asset_id,
    )
    payload = bytes(sealed.payload)
    if (
        sealed.content_digest != expected_asset_digest
        or len(payload) > 32 * 1024 * 1024
        or sha256_hex(payload) != slot.source_sha256
    ):
        raise ValueError("svg_export_source_asset_digest_mismatch")
    return payload


async def _load_original_bytes(
    slot, *, session, owner_id, tenant_id, presentation_id=None,
    page_index=None, source_preservation=None,
):
    import asyncio
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
        if source_preservation is not None:
            if presentation_id is None or type(page_index) is not int:
                raise ValueError("svg_export_source_context_invalid")
            return await _load_sealed_source_bytes(
                slot,
                session=session,
                row=row,
                owner_id=owner_id,
                presentation_id=presentation_id,
                page_index=page_index,
                receipt=source_preservation,
            )
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
                or sha256_hex(payload) != slot.source_sha256):
            raise ValueError("svg_export_source_asset_digest_mismatch")
        if await gateway.bind(session, asset_id=binding.asset_id, kind=binding.kind) != binding:
            raise ValueError("svg_export_source_asset_changed")
        return payload
    finally:
        reset_current_tenant_id(tenant_token)
        reset_current_owner_id(owner_token)


async def build_slide_export_assets(slide, *, service, session, claim, tenant_id, export_format, canvas,
                                   _source_verifier=None):
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
    inherited_source = str(source.presentation_id) != str(claim.presentation_id)
    if inherited_source:
        from models.sql.presentation import PresentationModel
        from services.svg_document_mutations import _verified_inherited_slide_source
        presentation = await session.get(PresentationModel, claim.presentation_id)
        verify_source = _source_verifier or _verified_inherited_slide_source
        if presentation is None or presentation.owner_id != claim.owner_id or not await verify_source(session, presentation, source, envelope["sourceManifest"], claim.owner_id, tenant_id):
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
    from services.document_canvas import parse_canvas_metadata
    resolved_canvas = parse_canvas_metadata(canvas)
    raster_receipt = (slide.content or {}).get("svgReceipt")
    if isinstance(raster_receipt, dict) and raster_receipt.get("rasterApprovalPin") is not None:
        from services.raster_source_assets import build_raster_page_export_assets
        from services.source_content.raster_contracts import RasterApprovalPin

        source_page_number = raster_receipt.get("rasterSourcePageNumber")
        if source_page_number != page_index + 1:
            raise ValueError("raster_export_source_page_mismatch")
        return await build_raster_page_export_assets(
            session, service,
            approval_pin=RasterApprovalPin.model_validate(raster_receipt["rasterApprovalPin"]),
            owner_id=claim.owner_id, tenant_id=tenant_id,
            publication_context=context, presentation_id=source.presentation_id,
            source_pointer=envelope["sourceManifest"], slide_revision=revision,
            page_number=source_page_number, output_page_index=page_index,
            canonical_svg=slide.svg_content,
            width=resolved_canvas.width_px, height=resolved_canvas.height_px,
            export_format=export_format,
            **({"system_furniture": raster_receipt["system_furniture"]} if raster_receipt.get("system_furniture") else {}),
        )
    svg_receipt = (slide.content or {}).get("svgReceipt")
    source_preservation = (
        svg_receipt.get("source_preservation")
        if isinstance(svg_receipt, dict)
        else None
    )
    system_furniture = svg_receipt.get("system_furniture") if isinstance(svg_receipt, dict) else None
    furniture, trusted_images = _attest_furniture(slide.svg_content, source_preservation=source_preservation,
        system_furniture=system_furniture, source_manifest={s: {"required": slots[s].policy.required,
            "allowOmit": slots[s].policy.allow_omit} for s in page.slot_ids},
        width=resolved_canvas.width_px, height=resolved_canvas.height_px)
    validate_svg(slide.svg_content, width=resolved_canvas.width_px, height=resolved_canvas.height_px,
        source_manifest={s: {"required": slots[s].policy.required, "allowOmit": slots[s].policy.allow_omit}
                         for s in page.slot_ids}, trusted_furniture_images=trusted_images)
    placements = []
    for index, (node, tx, ty) in enumerate(_positioned_images(slide.svg_content, excluded_ids=furniture)):
        slot_id = node.get("href", "")
        if slot_id not in page.slot_ids:
            raise ValueError("svg_export_slot_not_offered")
        slot = slots[slot_id]
        payload = await _load_original_bytes(
            slot,
            session=session,
            owner_id=claim.owner_id,
            tenant_id=tenant_id,
            presentation_id=claim.presentation_id,
            page_index=page_index,
            source_preservation=source_preservation,
        )
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
    manifest = PlacementManifest.seal(
    dict(
        canonicalSvgDigest=source_digest(
            slide.svg_content),
            sourceManifestDigest=source.digest,
            slideRevision=revision,
            purpose=f"export-{export_format}",
            placements=[
                p.model_dump(
                    mode="json",
                    by_alias=True) for p in placements],
                     rendererVersion=SVG_RENDERER_VERSION))
    manifest.validate_source(source, page_index=page_index)
    source_lineage = _ExportSourceLineage(source_presentation_id=source.presentation_id, target_presentation_id=claim.presentation_id, operation_id=claim.operation_id, owner_id=claim.owner_id, tenant_id=tenant_id, attempt_number=claim.attempt_number, fencing_token=claim.fencing_token, source_manifest_digest=source.digest, placement_manifest_digest=manifest.digest, canonical_svg_digest=source_digest(slide.svg_content), page_index=page_index, slide_revision=revision).model_dump(mode="json") if inherited_source else None
    validate_export_placement(slide.svg_content, source, manifest, page_index=page_index,
        slide_revision=revision, presentation_id=claim.presentation_id, export_format=export_format,
        width=resolved_canvas.width_px, height=resolved_canvas.height_px, source_preservation=source_preservation,
        system_furniture=system_furniture, source_lineage=source_lineage)
    pointer = await persist_manifest_if_current(session, service, context=context, manifest=manifest,
        publication_name=f"svg-exp-{slide.id.hex[:16]}-manifest", revision=revision)
    if pointer is None:
        raise ValueError("svg_export_asset_fence_lost")
    binding = {"sourceManifest": envelope["sourceManifest"], "placementManifest": pointer.model_dump(mode="json", by_alias=True), "pageIndex": page_index, "slideRevision": revision}
    if source_lineage is not None:
        from services.svg_asset_persistence import _persist_if_current
        lineage_pointer = await _persist_if_current(session, service, context=context, publication_name=f"svg-exp-{slide.id.hex[:16]}-source-lineage", revision=revision, kind="svg-export-source-lineage", payload=json.dumps(source_lineage, sort_keys=True, separators=(",", ":")).encode(), media_type="application/json")
        if lineage_pointer is None:
            return None
        binding["sourceLineage"] = lineage_pointer.model_dump(mode="json", by_alias=True)
    return binding


async def prepare_export_assets(binding, *, svg, service, session, claim, tenant_id,
                                export_format, width, height, source_preservation=None):
    base_keys = {"sourceManifest", "placementManifest", "pageIndex", "slideRevision"}
    if not base_keys <= set(binding) or set(binding) - base_keys - {"fullPagePreserve", "systemFurniture", "sourceLineage"} or (
        "fullPagePreserve" in binding and type(binding["fullPagePreserve"]) is not bool
    ):
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
    source_lineage = None
    lineage_ref = None
    if "sourceLineage" in binding:
        from services.svg_asset_persistence import _load_manifest_pointer
        lineage_pointer = ArtifactPointer.model_validate(binding["sourceLineage"])
        proof = await _load_manifest_pointer(session, service, pointer=lineage_pointer, owner_id=claim.owner_id, tenant_id=tenant_id, kind="svg-export-source-lineage", model=_ExportSourceLineage)
        source_lineage = proof.model_dump(mode="json")
        _check_export_source_lineage(source_lineage, source, placement, presentation_id=claim.presentation_id, svg=svg, page_index=binding["pageIndex"], operation_id=claim.operation_id, owner_id=claim.owner_id, tenant_id=tenant_id, attempt_number=claim.attempt_number, fencing_token=claim.fencing_token)
        resolved = await service.resolve_ref(session, artifact_id=lineage_pointer.ref, owner_id=claim.owner_id, tenant_id=tenant_id, expected_digest=lineage_pointer.sha256)
        lineage_ref = await service.register_consumer_ref(session, resolved, created_by_operation_id=claim.operation_id)
    derivatives = validate_export_placement(
    svg,
    source,
    placement,
    page_index=binding["pageIndex"],
    slide_revision=binding["slideRevision"],
    presentation_id=claim.presentation_id,
    export_format=export_format,
    width=width,
    height=height,
     allow_image_only=binding.get("fullPagePreserve") is True, source_preservation=source_preservation,
     system_furniture=binding.get("systemFurniture"), source_lineage=source_lineage)
    from services.svg_asset_resolver import bind_export_derivatives
    from services.public_export_operation import _worker_ref
    derivative_refs = await bind_export_derivatives(derivatives=derivatives, session=session,
        service=service, owner_id=claim.owner_id, tenant_id=tenant_id, operation_id=claim.operation_id)
    wire = {"sourceManifestRef": _worker_ref(refs[0]).to_wire(),
            "placementManifestRef": _worker_ref(refs[1]).to_wire(),
            "sourceManifestDigest": source.digest, "placementManifestDigest": placement.digest,
            "pageIndex": binding["pageIndex"], "slideRevision": binding["slideRevision"],
            "derivatives": {slot: _worker_ref(ref).to_wire() for slot, ref in derivative_refs.items()}}
    if binding.get("fullPagePreserve") is True:
        wire["fullPagePreserve"] = True
    if binding.get("systemFurniture"):
        wire["systemFurniture"] = binding["systemFurniture"]
    if lineage_ref is not None:
        wire["sourceLineageRef"] = _worker_ref(lineage_ref).to_wire()
        wire["sourceLineageDigest"] = lineage_pointer.sha256
    return wire, [*refs, *derivative_refs.values(), *([lineage_ref] if lineage_ref is not None else [])]


def materialize_export_assets(binding, *, svg, job, store, presentation_id,
                              export_format, width, height, source_preservation=None):
    from services.isolated_worker_contracts import ArtifactRef
    from services.svg_asset_resolver import materialize_svg
    binding_keys = {"sourceManifestRef", "placementManifestRef", "sourceManifestDigest",
                    "placementManifestDigest", "pageIndex", "slideRevision", "derivatives"}
    if not binding_keys <= set(binding) or set(binding) - binding_keys - {"fullPagePreserve", "systemFurniture", "sourceLineageRef", "sourceLineageDigest"} or (
        "fullPagePreserve" in binding and type(binding["fullPagePreserve"]) is not bool
    ):
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
    source_lineage = None
    if ("sourceLineageRef" in binding) != ("sourceLineageDigest" in binding):
        raise ValueError("svg_export_source_lineage_invalid")
    if "sourceLineageRef" in binding:
        lineage_artifact, lineage_bytes = read(binding["sourceLineageRef"], "svg-export-source-lineage", "application/json")
        if binding["sourceLineageDigest"] not in {hashlib.sha256(lineage_bytes).hexdigest(), "sha256:" + hashlib.sha256(lineage_bytes).hexdigest()}:
            raise ValueError("svg_export_source_lineage_invalid")
        source_lineage = _ExportSourceLineage.model_validate_json(lineage_bytes).model_dump(mode="json")
        _check_export_source_lineage(source_lineage, source, placement, presentation_id=presentation_id, svg=svg, page_index=binding["pageIndex"], operation_id=job.operation_id, owner_id=job.input_artifact.owner_id, tenant_id=job.input_artifact.tenant_id, fencing_token=job.fencing_token)
    derivatives = validate_export_placement(
    svg,
    source,
    placement,
    page_index=binding["pageIndex"],
    slide_revision=binding["slideRevision"],
    presentation_id=presentation_id,
    export_format=export_format,
    width=width,
    height=height,
     allow_image_only=binding.get("fullPagePreserve") is True, source_preservation=source_preservation,
     system_furniture=binding.get("systemFurniture"), source_lineage=source_lineage)
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
    page = next(p for p in source.offered_pages if p.page_index == binding["pageIndex"])
    slots = {s.slot_id: s for s in source.slots}
    contract = {s: {"required": slots[s].policy.required, "allowOmit": slots[s].policy.allow_omit}
                for s in page.slot_ids}
    resolved = materialize_svg(
    svg,
    manifest=source,
    placement_manifest=placement,
    page_index=binding["pageIndex"],
    derivatives=derivatives,
    payloads=payloads,
    width=width,
    height=height,
    allow_image_only=verified_full_page_preserve_export(
        binding,
        canonical_svg=svg,
         source_contract=contract),
    trusted_furniture_images=((source_preservation or {}).get("system_furniture") or binding.get("systemFurniture") or {}).get("images", {}))
    return resolved, contract

def verified_full_page_preserve_export(binding, *, canonical_svg: str, source_contract: dict) -> bool:
    """Accept image-only export only from a sealed, singleton raster asset binding."""
    if not isinstance(binding, dict) or binding.get("fullPagePreserve") is not True:
        return False
    if len(source_contract) != 1:
        raise ValueError("svg_export_full_page_preserve_binding_invalid")
    slot_id, policy = next(iter(source_contract.items()))
    if policy.get("required") is not True or policy.get("allowOmit") is not False:
        raise ValueError("svg_export_full_page_preserve_binding_invalid")
    from services.raster_source_assets import validate_full_page_preserve_svg_structure
    validate_full_page_preserve_svg_structure(canonical_svg, slot_id)
    return True
