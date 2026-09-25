"""Opt-in generation adapter for the existing fenced artifact service."""
from __future__ import annotations

import base64
from hashlib import sha256
import uuid
import xml.etree.ElementTree as ET

from services.svg_asset_manifest import SourceManifest, PlacementManifest, decide_preservation_policy
from services.svg_asset_persistence import (SvgPublicationContext, persist_derivative_if_current,
    persist_manifest_if_current, load_source_manifest_pointer)
from services.svg_contract import source_digest

def slot_writer_enabled(*, strategy: str | None = None, durable: bool = False) -> bool:
    """New durable SVG requests publish canonical assets without local setup."""
    return strategy == "svg" and durable


async def publication_context(session, job):
    from sqlalchemy import select
    from models.sql.public_admission import PublicOperationAdmission
    from models.sql.workspace import WorkspaceOperation
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    if job is None or job.workspace_operation_id is None:
        raise ValueError("svg_asset_durable_operation_required")
    operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if (operation is None or owner is None or operation.owner_id != owner
            or operation.state != "running" or not operation.attempt_token
            or operation.attempt_token != job.attempt_token
            or operation.fencing_token != getattr(job, "_workspace_fencing_token", None)
            or operation.expected_revision is None):
        raise ValueError("svg_asset_publication_fence_lost")
    # Workers restore owner context, while durable admission owns tenant identity.
    admission = await session.scalar(select(PublicOperationAdmission).where(
        PublicOperationAdmission.operation_id == operation.id,
        PublicOperationAdmission.owner_id == owner,
        PublicOperationAdmission.operation_kind == "generation",
        PublicOperationAdmission.state == "active",
    ))
    if (admission is None or not admission.tenant_id
            or (tenant is not None and tenant != admission.tenant_id)
            or admission.fence != (operation.fencing_token
                if operation.contract_version == "public-operation/v2" else 0)):
        raise ValueError("svg_asset_publication_fence_lost")
    tenant = admission.tenant_id
    return SvgPublicationContext(operation_id=operation.id, owner_id=owner, tenant_id=tenant,
        attempt_number=operation.attempt_number, attempt_token=operation.attempt_token,
        fencing_token=operation.fencing_token), operation.expected_revision + 1


def _source_bytes(candidate):
    if candidate.path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("svg_asset_source_byte_budget_exceeded")
    payload = candidate.path.read_bytes()
    if sha256(payload).hexdigest() != candidate.digest.removeprefix("sha256:"):
        raise ValueError("svg_source_image_digest_mismatch")
    return payload


async def prepare_source_manifest(session, service, *, context, revision, presentation_id,
                                  planned, saved_pointer=None, source_preservation_required=False):
    """Pin one metadata-only document input; retries reuse its artifact refs."""
    if saved_pointer is not None:
        manifest = await load_source_manifest_pointer(session, service, pointer=saved_pointer,
            owner_id=context.owner_id, tenant_id=context.tenant_id)
        if manifest.presentation_id != presentation_id or manifest.source_revision != revision:
            raise ValueError("svg_asset_source_revision_conflict")
        candidates = {slot: candidate for page in planned for slot, candidate in page.items()}
        if {slot.slot_id: slot.source_sha256 for slot in manifest.slots} != {
            slot: candidate.digest.removeprefix("sha256:") for slot, candidate in candidates.items()
        } or {page.page_index: set(page.slot_ids) for page in manifest.offered_pages} != {
            i: set(page) for i, page in enumerate(planned)
        }:
            raise ValueError("svg_asset_source_plan_conflict")
        return manifest, saved_pointer
    from services.svg_image_derivative import build_model_preview
    unique = {slot: candidate for page in planned for slot, candidate in page.items()}
    previews = {}
    # Finish every local decoder/budget check before persisting or purchasing.
    for slot_id, candidate in unique.items():
        previews[slot_id] = build_model_preview(
            _source_bytes(candidate), expected_media_type=candidate.mime_type,
            max_bytes=1536 * 1024, max_edge=4096)
    slots = []
    for slot_id, candidate in unique.items():
        preview = previews[slot_id]
        derivative = await persist_derivative_if_current(session, service, context=context, preview=preview,
            publication_name="svg-model-" + sha256(slot_id.encode()).hexdigest()[:32], revision=revision)
        if derivative is None:
            raise ValueError("svg_asset_publication_fence_lost")
        original_locator = candidate.source_locator or {}
        source_format = original_locator.get("sourceFormat", "image")
        locator = dict(sourceFormat=source_format,
            coordinateSpace=original_locator.get("coordinateSpace") or
                ("document-order" if source_format == "docx" else "page-normalized" if source_format in {"pptx", "pdf"} else "intrinsic"),
            pageIndex=original_locator.get("pageIndex", candidate.source_slide_index),
            objectId=original_locator.get("objectId", original_locator.get("objectLocator")),
            blockPath=original_locator.get("blockPath"))
        try:
            occurrence_id = uuid.UUID(candidate.occurrence_id) if candidate.occurrence_id else None
        except (ValueError, AttributeError):
            occurrence_id = uuid.uuid5(presentation_id, f"source-occurrence:{candidate.occurrence_id}")
        slots.append(dict(slotId=slot_id, sourceAssetId=uuid.UUID(candidate.asset_id),
            occurrenceId=occurrence_id or uuid.uuid5(presentation_id, slot_id),
            sourceSha256=candidate.digest.removeprefix("sha256:"),
            sourceWidth=candidate.width, sourceHeight=candidate.height,
            semanticDescription=candidate.semantic_description or candidate.source_heading or "Source image",
            sourceLocator=locator, extractionKind=original_locator.get("extractionKind", "direct_image" if not candidate.source_locator else "embedded_raster"),
            sourceAppearanceSha256=preview.source_appearance_sha256, preservationClass="supporting",
            policy=decide_preservation_policy(selected=True, preservation_class="supporting",
                                              source_preservation_required=source_preservation_required),
            selectionReason="existing_source_image_plan", modelDerivative=derivative))
    manifest = SourceManifest.seal(dict(presentationId=presentation_id, sourceRevision=revision, slots=slots,
        offeredPages=[dict(pageIndex=i, slotIds=list(page)) for i, page in enumerate(planned)]))
    pointer = await persist_manifest_if_current(session, service, context=context, manifest=manifest,
        publication_name="svg-source-manifest", revision=revision)
    if pointer is None:
        raise ValueError("svg_asset_publication_fence_lost")
    return manifest, pointer.model_dump(mode="json", by_alias=True)


async def author_images(session, service, *, context, manifest, page_index):
    from services.svg_asset_manifest import read_derivative_bytes
    offered = next(page.slot_ids for page in manifest.offered_pages if page.page_index == page_index)
    result = {}
    for slot in manifest.slots:
        if slot.slot_id not in offered:
            continue
        derivative = slot.model_derivative
        payload = await read_derivative_bytes(artifact_service=service, session=session, derivative=derivative,
            owner_id=context.owner_id, tenant_id=context.tenant_id, max_bytes=1536 * 1024, max_pixels=16_777_216)
        result[slot.slot_id] = dict(data_url=f"data:{derivative.media_type};base64," + base64.b64encode(payload).decode(),
            description=slot.semantic_description, required=slot.policy.required, allowOmit=slot.policy.allow_omit)
    return result


async def screen_placement(session, service, *, context, revision, manifest, source_pointer,
                            svg, page_index, candidates, width, height, saved_envelope=None):
    from services.svg_image_derivative import build_purpose_derivative
    from services.svg_asset_resolver import resolve_svg_for_render
    if saved_envelope is not None:
        from services.svg_asset_persistence import load_placement_manifest_pointer
        if (saved_envelope.get("sourceManifest") != source_pointer
                or saved_envelope.get("slideRevision") != revision):
            raise ValueError("svg_asset_screen_revision_conflict")
        placement = await load_placement_manifest_pointer(session, service,
            pointer=saved_envelope.get("screenPlacement"), owner_id=context.owner_id, tenant_id=context.tenant_id)
        if placement.canonical_svg_digest == source_digest(svg):
            materialized = await resolve_svg_for_render(svg, manifest=manifest,
                derivatives={item.slot_id: item.derivative for item in placement.placements}, session=session,
                owner_id=context.owner_id, tenant_id=context.tenant_id, width=width, height=height,
                service=service, placement_manifest=placement, page_index=page_index)
            return saved_envelope, materialized
    slots = {slot.slot_id: slot for slot in manifest.slots}
    placements = []
    placed_slots = set()
    # Parent translations must be accounted for in immutable placement geometry.
    def visit(node, tx=0., ty=0.):
        transform = node.get("transform")
        if transform:
            from services.svg_contract import _numbers
            values = _numbers(transform.removeprefix("translate(").removesuffix(")"))
            tx += values[0]
            ty += values[1] if len(values) > 1 else 0
        if node.tag.rsplit("}", 1)[-1] == "image":
            yield node, tx, ty
        for child in node:
            yield from visit(child, tx, ty)
    for ordinal, (node, tx, ty) in enumerate(visit(ET.fromstring(svg))):
        slot_id = node.get("href")
        if slot_id in placed_slots:
            raise ValueError("svg_asset_duplicate_slot_placement")
        placed_slots.add(slot_id)
        slot, candidate = slots[slot_id], candidates[slot_id]
        preview = build_purpose_derivative(_source_bytes(candidate), purpose="screen",
            width_css_px=float(node.get("width")), height_css_px=float(node.get("height")))
        derivative = await persist_derivative_if_current(session, service, context=context, preview=preview,
            publication_name=f"svg-screen-{page_index}-{ordinal}-{sha256(svg.encode()).hexdigest()[:12]}", revision=revision)
        if derivative is None:
            raise ValueError("svg_asset_publication_fence_lost")
        placements.append(dict(slotId=slot_id, occurrenceId=slot.occurrence_id,
            x=float(node.get("x")) + tx, y=float(node.get("y")) + ty,
            width=float(node.get("width")), height=float(node.get("height")), derivative=derivative))
    placement = PlacementManifest.seal(dict(canonicalSvgDigest=source_digest(svg), sourceManifestDigest=manifest.digest,
        slideRevision=revision, purpose="screen", placements=placements, rendererVersion="svg-browser/v1"))
    placement.validate_source(manifest, page_index=page_index)
    pointer = await persist_manifest_if_current(session, service, context=context, manifest=placement,
        publication_name=f"svg-screen-manifest-{page_index}-{sha256(svg.encode()).hexdigest()[:12]}", revision=revision)
    if pointer is None:
        raise ValueError("svg_asset_publication_fence_lost")
    materialized = await resolve_svg_for_render(svg, manifest=manifest,
        derivatives={item.slot_id: item.derivative for item in placement.placements},
        session=session, owner_id=context.owner_id, tenant_id=context.tenant_id,
        width=width, height=height, service=service, page_index=page_index, placement_manifest=placement)
    envelope = dict(schemaVersion="svg-slide-assets/v1", canonicalVersion="svg-static/v2",
        sourceManifest=source_pointer, screenPlacement=pointer.model_dump(mode="json", by_alias=True),
        sourcePageIndex=page_index, slideRevision=revision)
    return envelope, materialized
