"""Keep system page numbers and derivatives coherent after structural edits."""
from copy import deepcopy
from sqlalchemy import select
from models.sql.slide import SlideModel
from services.authored_canvas import presentation_canvas_contract
from services.authored_presentation_artifacts import _save_slide_pngs
from services.svg_contract import finalize_svg
from services.svg_page_publication import classify_svg_page_quality, set_svg_page
from services.svg_renderer import render_svg_with_metrics


class SourcePublicationBindingRejected(ValueError):
    code = "svg_source_publication_binding_invalid"
    retryable = False

    def __init__(self):
        super().__init__(self.code)


def _preserve_source_pages(presentation, slides, canvas, *, source_manifest=None):
    """A preservation receipt forbids implicit numbering and page mutations."""
    marker = presentation.theme.get("svg", {}).get("sourcePreservation")
    receipts = [(slide.content or {}).get("svgReceipt", {}).get("source_preservation")
                for slide in slides]
    if marker is None and not any(receipt is not None for receipt in receipts):
        return False
    if (not isinstance(marker, dict)
            or marker.get("schemaVersion") != "svg-source-preservation/v1"
            or marker.get("pageCount") != len(slides)):
        raise SourcePublicationBindingRejected()
    from services.svg_contract import source_digest
    if (source_manifest is not None
            and getattr(source_manifest, "manifest_digest", None) != marker.get("manifestDigest")):
        raise SourcePublicationBindingRejected()

    page_ids = set()
    for index, (slide, receipt) in enumerate(zip(slides, receipts, strict=True)):
        expected = {
            "contract_version": "svg-source-preservation/v1",
            "manifest_digest": marker.get("manifestDigest"),
            "source_page_index": index,
            "page_count": len(slides),
            "svg_digest": source_digest(slide.svg_content or ""),
            "canvas": {"width": canvas.width_px, "height": canvas.height_px},
            "rendering_verified": True,
        }
        if (not slide.is_svg() or slide.index != index or not isinstance(receipt, dict)
                or any(receipt.get(key) != value for key, value in expected.items())):
            raise SourcePublicationBindingRejected()
        page_id = receipt.get("source_page_id")
        checks = receipt.get("checks")
        if (not isinstance(page_id, str) or not page_id or page_id in page_ids
                or (checks is not None and (not isinstance(checks, dict)
                    or any(checks.get(key) is not True for key in (
                        "source_ids_exact", "text_exact", "table_structure_exact", "image_occurrences_exact"))))
                or (checks is None and source_manifest is None)):
            raise SourcePublicationBindingRejected()
        if source_manifest is not None:
            from services.svg_source_layout import verify_source_receipt

            try:
                verify_source_receipt(slide.svg_content or "", receipt, source_manifest, index)
            except (ValueError, TypeError, KeyError) as error:
                raise SourcePublicationBindingRejected() from error
        page_ids.add(page_id)
    return True


def _raster_pin(slide):
    """Any raster page's approval pin, strictly bound to this slide's current
    position and the analysis it was approved from. None for a non-raster page."""
    from services.source_content.raster_contracts import RasterApprovalPin, approval_digest_matches
    receipt = (slide.content or {}).get("svgReceipt") or {}
    value = receipt.get("rasterApprovalPin")
    if value is None:
        return None
    pin = RasterApprovalPin.model_validate(value)
    page = receipt.get("rasterSourcePageNumber")
    # The source page (provenance) is bound to the asset envelope's offered page,
    # not to the slide's current deck position, which a reorder may change.
    envelope = (slide.content or {}).get("svgAssetManifest") or {}
    source_index = envelope.get("sourcePageIndex", slide.index) if isinstance(envelope, dict) else slide.index
    if (not approval_digest_matches(pin) or page != source_index + 1
            or receipt.get("rasterAnalysisDigest") != pin.analysis_digest):
        raise ValueError("raster_publication_page_binding_invalid")
    return pin


def _raster_preserve_pin(slide):
    pin = _raster_pin(slide)
    receipt = (slide.content or {}).get("svgReceipt") or {}
    return pin if pin is not None and receipt.get("rasterSourcePageNumber") in pin.full_page_preserve_pages else None


def _raster_redesign_pin(slide):
    """A redesign page's own footer (raster_redesign_layout.py, PAGE_NUMBER_ID)
    is already baked in at the correct page number by generation -- unlike a
    reconstruct page, which has no footer of its own and still needs
    finalize_svg's generic stamp. Re-running finalize_svg on a redesign page
    would strip that footer and re-stamp it with the deck's generic
    styleSnapshot tokens (wrong font/colour for this style), and then
    re-measuring the resulting materialized SVG through the ordinary
    (non-authorized) preview path is exactly what produced the dead-lettered
    svg_contract_size_limit publish failure: refresh_svg_page_numbers must
    never send a redesign page through that generic path.
    """
    pin = _raster_pin(slide)
    receipt = (slide.content or {}).get("svgReceipt") or {}
    return pin if pin is not None and receipt.get("rasterSourcePageNumber") in pin.automatic_redesign_pages else None


async def read_svg_slide_assets(session, presentation, slide):
    """Read v2 asset bindings using the current authenticated principal."""
    metadata = (slide.content or {}).get("svgAssetManifest")
    if metadata is None:
        return None
    from services.artifact_store import configured_public_artifact_service
    from services.svg_asset_persistence import load_source_manifest_pointer, load_placement_manifest_pointer
    from services.svg_asset_resolver import resolve_svg_for_render
    from services.svg_contract import source_digest
    from utils.owner_context import get_current_owner_id, get_current_tenant_id

    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if owner is None or not tenant or owner != presentation.owner_id or slide.owner_id != owner:
        raise ValueError("svg_asset_principal_required")
    if (not isinstance(metadata, dict) or metadata.get("schemaVersion") != "svg-slide-assets/v1"
            or metadata.get("canonicalVersion") != "svg-static/v2"):
        raise ValueError("svg_asset_revision_metadata_invalid")
    service = configured_public_artifact_service()
    source = await load_source_manifest_pointer(session, service, pointer=metadata.get("sourceManifest"),
                                                owner_id=owner, tenant_id=tenant)
    placement = await load_placement_manifest_pointer(session, service, pointer=metadata.get("screenPlacement"),
                                                      owner_id=owner, tenant_id=tenant)
    if ((source.presentation_id != presentation.id and not await _verified_inherited_slide_source(session, presentation, source, metadata.get("sourceManifest"), owner, tenant)) or placement.purpose != "screen"
            or placement.slide_revision != metadata.get("slideRevision")
            or placement.canonical_svg_digest != source_digest(slide.svg_content)):
        raise ValueError("svg_asset_revision_binding_invalid")
    placement.validate_source(source, page_index=metadata.get("sourcePageIndex", slide.index))
    canvas = presentation_canvas_contract(presentation.theme)
    preserve_pin = _raster_preserve_pin(slide)
    if preserve_pin is not None:
        from services.source_content.raster_reconstruction_store import admit_approved_raster_analysis
        from services.source_content.raster_svg_layout import render_raster_page_svg
        analysis = await admit_approved_raster_analysis(
            session, operation_id=preserve_pin.operation_id, owner_id=owner,
            tenant_id=tenant, pin=preserve_pin, artifact_service=service,
        )
        expected = render_raster_page_svg(
            analysis, slide.index + 1,
            style_id=presentation.theme.get("style") or "screenshot-blue",
            approved_region_ids=preserve_pin.approved_region_ids,
        )
        if slide.svg_content != expected:
            raise ValueError("raster_publication_source_geometry_changed")
    derivatives = {item.slot_id: item.derivative for item in placement.placements}
    materialized = await resolve_svg_for_render(slide.svg_content, manifest=source,
        derivatives=derivatives, session=session, owner_id=owner, tenant_id=tenant,
        width=canvas.width_px, height=canvas.height_px, service=service,
        page_index=metadata.get("sourcePageIndex", slide.index), placement_manifest=placement,
        allow_image_only=preserve_pin is not None)
    return source, placement, materialized


async def refresh_svg_page_numbers(session, presentation, *, operation_id=None, attempt_token=None,
                                   expected_revision=None, source_manifest=None):
    if not presentation.is_svg():
        return
    await session.flush()
    slides = list(await session.scalars(select(SlideModel).where(
        SlideModel.presentation == presentation.id).order_by(SlideModel.index, SlideModel.id)))
    if not slides:
        raise ValueError("svg_document_requires_slide")
    canvas = presentation_canvas_contract(presentation.theme)
    if _preserve_source_pages(presentation, slides, canvas,
                              source_manifest=source_manifest):
        return
    snapshot = presentation.theme.get("svg", {}).get("styleSnapshot")
    if not snapshot:
        raise ValueError("svg_style_snapshot_required")
    header_outline = None
    if snapshot.get("common_header") is not None:
        from services.svg_common_header import validate_common_header_outline

        stored = getattr(presentation, "outlines", None)
        header_outline = stored.get("slides") if isinstance(stored, dict) else None
        if not isinstance(header_outline, list) or len(header_outline) != len(slides):
            raise ValueError("svg_common_header_binding_incomplete")
        validate_common_header_outline(snapshot, stored)
    for index, slide in enumerate(slides):
        if not slide.is_svg() or not slide.svg_content:
            raise ValueError("svg_document_mixed_source")
        header_page = None
        if header_outline is not None:
            page = header_outline[index]
            header_page = page["common_header"]
            content = slide.content or {}
            if (slide.index != index or content.get("source") != page["content"]
                    or page.get("title_source_digest") != header_page.get("title_source_digest")):
                raise ValueError("svg_common_header_source_mismatch")
            if header_page.get("applied"):
                measured = ((content.get("svgReceipt") or {}).get("quality") or {}).get("common_header") or {}
                if (measured.get("passed") is not True
                        or any(measured.get(key) != header_page[value] for key, value in
                               (("header_digest", "header_digest"), ("page_digest", "digest"), ("title_digest", "title_digest")))):
                    raise ValueError("svg_common_header_digest_mismatch")
        if _raster_redesign_pin(slide) is not None:
            # Retain the raster footer style and source page. The relabel
            # path remeasures its authorized derivative before publication.
            from services.svg_raster_renumber import renumber_redesign_page

            await renumber_redesign_page(
                session, presentation, slide, index, width=canvas.width_px, height=canvas.height_px,
                operation_id=operation_id, attempt_token=attempt_token, expected_revision=expected_revision)
            slide.index = index
            session.add(slide)
            continue
        assets = await read_svg_slide_assets(session, presentation, slide)
        if _raster_preserve_pin(slide) is not None:
            if assets is None or slide.index != index:
                raise ValueError("raster_publication_page_binding_invalid")
            continue
        source_manifest = None
        if assets is not None:
            from services.svg_asset_resolver import _contract_manifest
            offered = next(page.slot_ids for page in assets[0].offered_pages
                if page.page_index == (slide.content["svgAssetManifest"].get("sourcePageIndex", slide.index)))
            source_manifest = {slot: policy for slot, policy in _contract_manifest(assets[0]).items()
                               if slot in offered}
        final = finalize_svg(slide.svg_content, index=index, n=len(slides), width=canvas.width_px,
                             height=canvas.height_px, style_snapshot=snapshot,
                             source_manifest=source_manifest,
                             **({"header_page": header_page, "allow_existing_common_header": True}
                                if header_page is not None else {}))
        render_source = final
        content = deepcopy(slide.content or {})
        if assets is not None and (final != slide.svg_content or
                content["svgAssetManifest"].get("_rebindRequired") is True or
                content["svgAssetManifest"].get("sourcePageIndex", slide.index) != index):
            content["svgAssetManifest"], render_source = await rebind_svg_assets(
                session, presentation, slide, final, assets, page_index=index,
                operation_id=operation_id, attempt_token=attempt_token,
                expected_revision=expected_revision)
        slide.index = index
        if final != slide.svg_content:
            capture = await render_svg_with_metrics(final, width=canvas.width_px, height=canvas.height_px,
                materialized_svg=render_source if render_source != final else None,
                instructions=presentation.instructions)
            image = _save_slide_pngs(presentation.id, [capture.png], "svg-numbering")[0]
            quality = classify_svg_page_quality(svg_content=final, quality=capture.quality,
                index=index, png_path=image, receipt=content.get("svgReceipt") or {})
            set_svg_page(slide, svg_content=final, content=content, quality=quality, png=capture.png)
            slide.properties = {**(slide.properties or {}), "image": image}
        elif content != slide.content:
            set_svg_page(slide, svg_content=final, content=content)
        session.add(slide)
    presentation.n_slides = len(slides)
    from services.generation_core.quality.page_outcome import review_page_indices
    metadata = dict(presentation.theme.get("svg", {}))
    qualities = [(slide.content.get("svgReceipt") or {}).get("quality", {}) for slide in slides]
    review = review_page_indices(qualities)
    metadata.update(quality=qualities, reviewPages=review,
                    qualityStatus="review_required" if review else "passed")
    presentation.theme = {**presentation.theme, "svg": metadata}
    session.add(presentation)


def rebound_placement(svg, source, placement, *, revision):
    """Rebind geometry while retaining the authorized immutable image bytes."""
    import xml.etree.ElementTree as ET
    from services.svg_asset_manifest import PlacementManifest
    from services.svg_contract import source_digest, _numbers
    previous = {item.slot_id: item for item in placement.placements}
    result = []
    used = set()
    def visit(node, tx=0., ty=0.):
        if node.get("transform"):
            values = _numbers(node.get("transform").removeprefix("translate(").removesuffix(")"))
            tx += values[0]
            ty += values[1] if len(values) > 1 else 0
        if node.tag.rsplit("}", 1)[-1] == "image":
            slot = node.get("href")
            if slot in used or slot not in previous:
                raise ValueError("svg_asset_revision_slot_invalid")
            used.add(slot)
            item = previous[slot].model_dump(mode="json", by_alias=True)
            item.update(x=float(node.get("x")) + tx, y=float(node.get("y")) + ty,
                        width=float(node.get("width")), height=float(node.get("height")))
            result.append(item)
        for child in node:
            visit(child, tx, ty)
    visit(ET.fromstring(svg))
    return PlacementManifest.seal(dict(canonicalSvgDigest=source_digest(svg),
        sourceManifestDigest=source.digest, slideRevision=revision, purpose="screen",
        placements=result, rendererVersion=placement.renderer_version))


async def materialize_rebound_svg(session, presentation, svg, source, placement, page_index):
    from services.svg_asset_resolver import resolve_svg_for_render
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    canvas = presentation_canvas_contract(presentation.theme)
    return await resolve_svg_for_render(svg, manifest=source,
        derivatives={item.slot_id: item.derivative for item in placement.placements},
        session=session, owner_id=get_current_owner_id(), tenant_id=get_current_tenant_id(),
        width=canvas.width_px, height=canvas.height_px, placement_manifest=placement,
        page_index=page_index)


async def rebind_svg_assets(session, presentation, slide, svg, assets, *, page_index,
                            operation_id, attempt_token, expected_revision):
    """Stage immutable edit manifests under the exact mutation operation lock.

    The caller commits only after publish_presentation_revision succeeds.
    Generation admission fences do not authorize editor operations.
    """
    import json
    from models.sql.workspace import WorkspaceOperation
    from services.artifact_store import configured_public_artifact_service
    from services.svg_asset_manifest import SourceManifest
    from utils.owner_context import get_current_owner_id, get_current_tenant_id
    owner, tenant = get_current_owner_id(), get_current_tenant_id()
    if (not operation_id or not attempt_token or expected_revision is None or not tenant
            or owner != presentation.owner_id):
        raise ValueError("svg_asset_mutation_fence_required")
    operation = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id, WorkspaceOperation.owner_id == owner,
        WorkspaceOperation.presentation_id == presentation.id,
        WorkspaceOperation.state == "running", WorkspaceOperation.attempt_token == attempt_token,
        WorkspaceOperation.expected_revision == expected_revision).with_for_update())
    if operation is None or presentation.revision != expected_revision:
        raise ValueError("svg_asset_mutation_fence_lost")
    source, previous, _ = assets
    source_index = slide.content["svgAssetManifest"].get("sourcePageIndex", slide.index)
    offered = next(page.slot_ids for page in source.offered_pages if page.page_index == source_index)
    revision = expected_revision + 1
    source = SourceManifest.seal(dict(presentationId=presentation.id, sourceRevision=revision,
        slots=[slot.model_dump(mode="json", by_alias=True) for slot in source.slots if slot.slot_id in offered],
        offeredPages=[dict(pageIndex=page_index, slotIds=list(offered))]))
    placement = rebound_placement(svg, source, previous, revision=revision)
    materialized = await materialize_rebound_svg(session, presentation, svg, source, placement, page_index)
    service = configured_public_artifact_service()
    pointers = []
    for manifest, kind in ((source, "source-manifest"), (placement, "placement-manifest")):
        payload = json.dumps(manifest.model_dump(mode="json", by_alias=True), sort_keys=True,
            separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        reference = await service.persist_bytes(session, owner_id=owner, tenant_id=tenant, kind=kind,
            payload=payload, media_type="application/json", created_by_operation_id=operation_id,
            retention_class="published")
        pointers.append(dict(ref=str(reference.artifact_id), sha256=reference.sha256))
    return dict(schemaVersion="svg-slide-assets/v1", canonicalVersion="svg-static/v2",
        sourceManifest=pointers[0], screenPlacement=pointers[1], sourcePageIndex=page_index,
        slideRevision=revision), materialized


async def _verified_inherited_slide_source(session, presentation, source, pointer, owner, tenant):
    """Only a fenced durable recovery job may read its exact immutable ancestor source."""
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from sqlalchemy import select
    from models.sql.presentation_generation_job import PresentationGenerationJob
    from models.sql.workspace import WorkspaceOperation
    from services.svg_generation_assets import _verify_inherited_source_manifest
    from services.svg_timeout_repurchase import TimeoutRepurchaseRejected
    job = await session.scalar(select(PresentationGenerationJob).where(
        PresentationGenerationJob.presentation_id == presentation.id,
    ).order_by(PresentationGenerationJob.created_at.desc()).limit(1))
    if job is None or not job.workspace_operation_id or job.request_payload.get("strategy") != "svg" or not job.request_payload.get("_svg_recovery_lineage"):
        return False
    operation = await session.get(WorkspaceOperation, job.workspace_operation_id)
    if (operation is None or operation.presentation_id != presentation.id or operation.owner_id != presentation.owner_id
        or str(operation.owner_id) != str(owner) or operation.fencing_token < 1
        or operation.attempt_number != job.attempt_number
        or job.state not in {"running", "publishing", "published", "completed", "succeeded"}):
        return False
    if job.state == "published":
        if job.attempt_token is not None or job.lease_expires_at is not None or not job.published_at or not job.last_started_at or not operation.started_at:
            return False
        published_at = job.published_at.replace(tzinfo=timezone.utc) if job.published_at.tzinfo is None else job.published_at
        job_started_at = job.last_started_at.replace(tzinfo=timezone.utc) if job.last_started_at.tzinfo is None else job.last_started_at
        operation_started_at = operation.started_at.replace(tzinfo=timezone.utc) if operation.started_at.tzinfo is None else operation.started_at
        if published_at < job_started_at or published_at < operation_started_at:
            return False
    elif not job.attempt_token or operation.attempt_token != job.attempt_token:
        return False
    if operation.state == "running":
        lease = operation.lease_expires_at
        if lease is None or not operation.attempt_token:
            return False
        if lease.tzinfo is None:
            lease = lease.replace(tzinfo=timezone.utc)
        if type(operation.expected_revision) is not int or lease <= datetime.now(timezone.utc) or presentation.revision not in {operation.expected_revision, operation.expected_revision + 1}:
            return False
    elif operation.state in {"completed", "succeeded"}:
        if operation.result_revision is None or presentation.revision < operation.result_revision or job.state not in {"published", "completed", "succeeded"}:
            return False
    else:
        return False
    try:
        await _verify_inherited_source_manifest(session, source, pointer, context=SimpleNamespace(owner_id=owner, tenant_id=tenant),
                                                presentation_id=presentation.id, operation_id=operation.id)
    except (ValueError, TimeoutRepurchaseRejected):
        return False
    return True
