"""Owner-scoped raster review checkpoints on existing workspace authority.

Callers own the surrounding transaction and commit. Review operations are created
by the workspace admission layer in awaiting_approval state; this module never
creates runnable operations or fabricates native source-content manifests.
"""
from __future__ import annotations

import json
import uuid
import asyncio
import hashlib
import io
import re
from datetime import datetime, timezone
from zipfile import ZipFile

from PIL import Image

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.public_artifact import ArtifactRef, PublicArtifact
from models.sql.workspace import WorkspaceArtifact, WorkspaceConversation, WorkspaceOperation
from services.artifact_store import DurableArtifactService, configured_public_artifact_service, require_artifact_key_guard
from services.source_content.raster_contracts import RasterAnalysis, RasterApprovalPin, RasterBox, RasterRegion, require_analysis_digest, seal_approval, approval_digest_matches


class RasterReviewError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def reserve_raster_operation_position(session: AsyncSession, *, conversation_id: uuid.UUID, owner_id: uuid.UUID) -> int:
    conversation = await session.scalar(select(WorkspaceConversation).where(
        WorkspaceConversation.id == conversation_id,
        WorkspaceConversation.owner_id == owner_id,
        WorkspaceConversation.owner_scope == str(owner_id),
    ).with_for_update().execution_options(populate_existing=True))
    if conversation is None:
        raise RasterReviewError("raster_conversation_not_found")
    position = conversation.next_position
    conversation.next_position += 1
    conversation.version += 1
    conversation.updated_at = datetime.now(timezone.utc)
    session.add(conversation)
    await session.flush()
    return position


def _source_crop(analysis: RasterAnalysis, region: RasterRegion, pixel_box: tuple[int, int, int, int], source_bytes: bytes) -> tuple[RasterBox, bytes, str, str, tuple[int, int]]:
    if not region.source_media_part or not region.source_occurrence_id:
        raise RasterReviewError("raster_crop_source_missing")
    x, y, width, height = pixel_box
    if any(type(value) is not int for value in pixel_box) or x < 0 or y < 0 or width <= 0 or height <= 0:
        raise RasterReviewError("raster_pixel_bbox_invalid")
    with ZipFile(io.BytesIO(source_bytes)) as archive:
        media_bytes = archive.read(region.source_media_part)
    with Image.open(io.BytesIO(media_bytes)) as original:
        original.load()
        if x + width > original.width or y + height > original.height:
            raise RasterReviewError("raster_pixel_bbox_outside_source")
        pixels = original.convert("RGBA").crop((x, y, x + width, y + height))
        output = io.BytesIO()
        pixels.save(output, format="PNG")
        bbox = RasterBox(x=x * analysis.source.canvas_width_emu / original.width,
                         y=y * analysis.source.canvas_height_emu / original.height,
                         width=width * analysis.source.canvas_width_emu / original.width,
                         height=height * analysis.source.canvas_height_emu / original.height)
        return bbox, output.getvalue(), hashlib.sha256(pixels.tobytes()).hexdigest(), hashlib.sha256(media_bytes).hexdigest(), original.size


async def derive_raster_crop_corrections(session: AsyncSession, *, operation_id: uuid.UUID, owner_id: uuid.UUID, tenant_id: str, analysis: RasterAnalysis, pixel_boxes: dict[str, tuple[int, int, int, int]], artifact_service: DurableArtifactService | None = None) -> dict[str, RasterRegion]:
    """Create immutable corrected crops; callers persist the next analysis revision."""
    if not pixel_boxes:
        return {}
    service = artifact_service or configured_public_artifact_service()
    operation = await _operation(session, operation_id=operation_id, owner_id=owner_id, tenant_id=tenant_id)
    if operation.state != "awaiting_approval":
        raise RasterReviewError("raster_review_version_conflict")
    current = _current_result(operation)
    if current.get("analysis_digest") != analysis.digest or current.get("analysis_revision") != analysis.revision:
        raise RasterReviewError("raster_review_analysis_conflict")
    reference = analysis.source.source_artifact_ref
    if reference is None:
        raise RasterReviewError("raster_source_artifact_required")
    await _verify_ref(session, service, reference, owner_id=owner_id, tenant_id=tenant_id)
    source_bytes = await service.read_bytes(session, artifact_id=reference.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=reference.sha256)
    regions = {r.region_id: r for page in analysis.pages for r in page.regions}
    if not pixel_boxes.keys() <= regions.keys():
        raise RasterReviewError("raster_review_region_invalid")
    corrected = {}
    for region_id, pixel_box in pixel_boxes.items():
        region = regions[region_id]
        bbox, png, pixel_digest, media_digest, _ = await asyncio.to_thread(_source_crop, analysis, region, pixel_box, source_bytes)
        crop_ref = await service.persist_bytes(session, owner_id=owner_id, tenant_id=tenant_id, kind="image-derivative", payload=png, media_type="image/png", created_by_operation_id=operation_id, retention_class="published")
        corrected[region_id] = RasterRegion.model_validate(region.model_dump(mode="json") | {
            "bbox": bbox.model_dump(), "source_pixel_bbox": pixel_box,
            "crop_source_sha256": media_digest, "pixel_sha256": pixel_digest,
            "crop_artifact_ref": crop_ref.model_dump(mode="json"), "review_required": True,
        })
    return corrected


async def _verify_crop_corrections(session: AsyncSession, service: DurableArtifactService, analysis: RasterAnalysis, changed: list[RasterRegion], *, owner_id: uuid.UUID, tenant_id: str) -> None:
    if not changed:
        return
    source_ref = analysis.source.source_artifact_ref
    if source_ref is None:
        raise RasterReviewError("raster_source_artifact_required")
    source_bytes = await service.read_bytes(session, artifact_id=source_ref.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=source_ref.sha256)
    for region in changed:
        reference = region.crop_artifact_ref
        if reference is None or region.source_pixel_bbox is None or not region.review_required:
            raise RasterReviewError("raster_corrected_crop_requires_review")
        bbox, png, pixel_digest, media_digest, _ = await asyncio.to_thread(_source_crop, analysis, region, region.source_pixel_bbox, source_bytes)
        await _verify_ref(session, service, reference, owner_id=owner_id, tenant_id=tenant_id)
        actual = await service.read_bytes(session, artifact_id=reference.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=reference.sha256)
        if region.bbox != bbox or region.pixel_sha256 != pixel_digest or region.crop_source_sha256 != media_digest or actual != png:
            raise RasterReviewError("raster_corrected_crop_derivation_mismatch")


def _embedded_image_size(source_bytes: bytes, media_part: str) -> tuple[int, int]:
    with ZipFile(io.BytesIO(source_bytes)) as archive:
        media_bytes = archive.read(media_part)
    with Image.open(io.BytesIO(media_bytes)) as image:
        return image.size


async def derive_full_page_preserve_regions(session: AsyncSession, *, operation_id: uuid.UUID, owner_id: uuid.UUID, tenant_id: str, analysis: RasterAnalysis, page_numbers: tuple[int, ...], artifact_service: DurableArtifactService | None = None) -> dict[int, RasterRegion]:
    """Derive explicit review candidates from whole original page image pixels."""
    if not page_numbers:
        return {}
    service = artifact_service or configured_public_artifact_service()
    operation = await _operation(session, operation_id=operation_id, owner_id=owner_id, tenant_id=tenant_id)
    if operation.state != "awaiting_approval" or _current_result(operation).get("analysis_digest") != analysis.digest:
        raise RasterReviewError("raster_review_analysis_conflict")
    reference = analysis.source.source_artifact_ref
    if reference is None:
        raise RasterReviewError("raster_source_artifact_required")
    await _verify_ref(session, service, reference, owner_id=owner_id, tenant_id=tenant_id)
    source = await service.read_bytes(session, artifact_id=reference.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=reference.sha256)
    result = {}
    for page_number in page_numbers:
        if page_number < 1 or page_number > len(analysis.pages):
            raise RasterReviewError("raster_page_not_found")
        page = analysis.pages[page_number - 1]
        original = next((r for r in page.regions if r.source_media_part and r.source_occurrence_id), None)
        if original is None:
            raise RasterReviewError("raster_crop_source_missing")
        width, height = await asyncio.to_thread(_embedded_image_size, source, original.source_media_part)
        pixel_box = (0, 0, width, height)
        bbox, png, pixel_digest, media_digest, _ = await asyncio.to_thread(_source_crop, analysis, original, pixel_box, source)
        crop_ref = await service.persist_bytes(session, owner_id=owner_id, tenant_id=tenant_id, kind="image-derivative", payload=png, media_type="image/png", created_by_operation_id=operation_id, retention_class="published")
        result[page_number] = RasterRegion(region_id=f"p{page_number}-full-page-preserve", page_number=page_number, role="full_page_preserve", bbox=bbox, z_order=0,
            source_media_part=original.source_media_part, source_occurrence_id=original.source_occurrence_id,
            source_pixel_bbox=pixel_box, crop_source_sha256=media_digest, pixel_sha256=pixel_digest,
            crop_artifact_ref=crop_ref, review_required=True)
    return result


async def _verify_full_page_preservation(session: AsyncSession, service: DurableArtifactService, analysis: RasterAnalysis, prior: RasterAnalysis | None, *, owner_id: uuid.UUID, tenant_id: str) -> None:
    selected = [p for p in analysis.pages if p.full_page_region is not None]
    if not selected:
        return
    source_ref = analysis.source.source_artifact_ref
    if source_ref is None:
        raise RasterReviewError("raster_source_artifact_required")
    source = await service.read_bytes(session, artifact_id=source_ref.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=source_ref.sha256)
    for page in selected:
        region = page.full_page_region
        if region is None or region.crop_artifact_ref is None or not region.source_media_part:
            raise RasterReviewError("raster_preserved_crop_missing")
        if (region.source_media_part, region.source_occurrence_id) not in {(r.source_media_part, r.source_occurrence_id) for r in page.regions}:
            raise RasterReviewError("raster_full_page_source_mismatch")
        width, height = await asyncio.to_thread(_embedded_image_size, source, region.source_media_part)
        if region.region_id != f"p{page.page_number}-full-page-preserve" or region.source_pixel_bbox != (0, 0, width, height) or region.overlaps or region.occluded_by:
            raise RasterReviewError("raster_full_page_geometry_invalid")
        bbox, png, pixels_digest, media_digest, _ = await asyncio.to_thread(_source_crop, analysis, region, region.source_pixel_bbox, source)
        await _verify_ref(session, service, region.crop_artifact_ref, owner_id=owner_id, tenant_id=tenant_id)
        actual = await service.read_bytes(session, artifact_id=region.crop_artifact_ref.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=region.crop_artifact_ref.sha256)
        if region.bbox != bbox or region.pixel_sha256 != pixels_digest or region.crop_source_sha256 != media_digest or actual != png:
            raise RasterReviewError("raster_full_page_derivation_mismatch")
        if page.render_mode == "full_page_preserve":
            if page.approval_method == "automatic_preserve":
                if region.review_required or page.full_page_preserve_acknowledgements:
                    raise RasterReviewError("raster_automatic_preserve_authority_invalid")
            else:
                if prior is None:
                    raise RasterReviewError("raster_page_mode_requires_review_revision")
                previous = prior.pages[page.page_number - 1]
                if previous.render_mode != "full_page_preserve" and (not region.review_required or page.full_page_preserve_acknowledgements):
                    raise RasterReviewError("raster_full_page_separate_confirmation_required")


def _redesign_page_blockers(page) -> list[str]:
    """Redesign pages block only on low-confidence regions and missing pixels."""
    prefix = f"page_{page.page_number}"
    blockers = [f"{prefix}:low_confidence_region:{r.region_id}" for r in page.regions if r.review_required]
    if not page.regions:
        blockers.append(f"page_{page.page_number}:redesign_empty")
    if any(r.role in ("screenshot", "logo", "table") and r.crop_artifact_ref is None for r in page.regions):
        blockers.append(f"page_{page.page_number}:preserved_crop_missing")
    if page.reference_region is None or page.reference_region.crop_artifact_ref is None:
        blockers.append(f"page_{page.page_number}:reference_crop_missing")
    return blockers


def _pages_with(analysis: RasterAnalysis, approval_method: str) -> tuple[int, ...]:
    return tuple(page.page_number for page in analysis.pages if page.approval_method == approval_method)


def _all_regions(page) -> tuple[RasterRegion, ...]:
    extra = (page.full_page_region, page.reference_region)
    return (*page.regions, *(region for region in extra if region is not None))


def raster_review_blockers(analysis: RasterAnalysis) -> tuple[str, ...]:
    """Return effective approval blockers while retaining original audit issues."""
    preserved = {p.page_number for p in analysis.pages if p.render_mode == "full_page_preserve"}
    # A redesign page's audit issues are mirrored by region confidence flags.
    redesigned = {p.page_number for p in analysis.pages if p.render_mode == "redesign"}
    blockers = []
    for issue in analysis.unresolved:
        if issue.startswith("advisory:"):
            continue  # suggestions for optional review never block approval
        match = re.match(r"^page_(\d+):", issue)
        if match and int(match.group(1)) in preserved | redesigned:
            continue
        if len(preserved) == len(analysis.pages) and issue == "local_ocr_unavailable":
            continue
        blockers.append(issue)
    for page in analysis.pages:
        if page.render_mode == "redesign":
            blockers.extend(_redesign_page_blockers(page))
            continue
        if page.render_mode == "full_page_preserve":
            region = page.full_page_region
            if region is None or region.review_required or (
                page.approval_method == "automatic_preserve"
                and page.full_page_preserve_acknowledgements
            ) or (
                page.approval_method == "manual_review"
                and set(page.full_page_preserve_acknowledgements) != {"image_only", "legibility"}
            ):
                blockers.append(f"page_{page.page_number}:full_page_confirmation_required")
            if region is None or region.crop_artifact_ref is None:
                blockers.append(f"page_{page.page_number}:full_page_crop_missing")
            continue
        if not page.regions or any(r.review_required or r.role == "unknown" for r in page.regions):
            blockers.append(f"page_{page.page_number}:region_review_required")
        if any(o.review_required for o in page.ocr_inventory) or any(r.ocr and r.ocr.review_required for r in page.regions):
            blockers.append(f"page_{page.page_number}:ocr_review_required")
        if any(r.role in ("screenshot", "logo", "table") and r.crop_artifact_ref is None for r in page.regions):
            blockers.append(f"page_{page.page_number}:preserved_crop_missing")
    return tuple(blockers)


async def _operation(session: AsyncSession, *, operation_id: uuid.UUID, owner_id: uuid.UUID, tenant_id: str) -> WorkspaceOperation:
    row = await session.scalar(select(WorkspaceOperation).where(
        WorkspaceOperation.id == operation_id,
        WorkspaceOperation.owner_id == owner_id,
        WorkspaceOperation.owner_scope == str(owner_id),
    ).with_for_update().execution_options(populate_existing=True))
    if row is None or row.request_payload.get("tenant_id") != tenant_id or not tenant_id:
        raise RasterReviewError("raster_review_not_found")
    return row


def _current_result(operation: WorkspaceOperation) -> dict:
    value = (operation.result or {}).get("raster_reconstruction")
    if not isinstance(value, dict):
        raise RasterReviewError("raster_analysis_not_found")
    return value


async def _verify_ref(session: AsyncSession, service: DurableArtifactService, reference: ArtifactRef, *, owner_id: uuid.UUID, tenant_id: str) -> None:
    if reference.owner_id != owner_id or reference.tenant_id != tenant_id:
        raise RasterReviewError("raster_artifact_scope_mismatch")
    await require_artifact_key_guard(session, reference)
    resolved = await service.resolve_ref(session, artifact_id=reference.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=reference.sha256)
    if resolved.model_dump(mode="json") != reference.model_dump(mode="json"):
        raise RasterReviewError("raster_artifact_binding_mismatch")


async def _retain_live_pointer(session: AsyncSession, *, operation: WorkspaceOperation, reference: ArtifactRef, revision: int, kind: str) -> None:
    existing = await session.scalar(select(WorkspaceArtifact).where(
        WorkspaceArtifact.operation_id == operation.id,
        WorkspaceArtifact.owner_id == operation.owner_id,
        WorkspaceArtifact.public_artifact_id == reference.artifact_id,
        WorkspaceArtifact.status == "ready",
        WorkspaceArtifact.deleted_at.is_(None),
    ))
    if existing is not None:
        return
    retained = await session.execute(update(PublicArtifact).where(
        PublicArtifact.id == reference.artifact_id,
        PublicArtifact.owner_id == reference.owner_id,
        PublicArtifact.tenant_id == reference.tenant_id,
        PublicArtifact.sha256 == reference.sha256,
        PublicArtifact.lifecycle_state == "committed",
    ).values(refcount=PublicArtifact.refcount + 1, lifecycle_revision=PublicArtifact.lifecycle_revision + 1))
    if retained.rowcount != 1:
        raise RasterReviewError("raster_artifact_not_committed")
    session.add(WorkspaceArtifact(
        owner_id=operation.owner_id, operation_id=operation.id,
        presentation_id=operation.presentation_id, public_artifact_id=reference.artifact_id,
        kind=kind, status="ready", storage_key=reference.storage_key,
        sha256=reference.sha256, size_bytes=reference.size_bytes, source_revision=revision,
    ))
    await session.flush()


async def _load_analysis(session: AsyncSession, service: DurableArtifactService, operation: WorkspaceOperation, *, owner_id: uuid.UUID, tenant_id: str, verify_crops: bool = False) -> RasterAnalysis:
    record = _current_result(operation)
    reference = ArtifactRef.model_validate(record.get("analysis_artifact_ref"))
    if reference.kind != "checkpoint" or reference.created_by_operation_id != operation.id:
        raise RasterReviewError("raster_analysis_ref_invalid")
    await _verify_ref(session, service, reference, owner_id=owner_id, tenant_id=tenant_id)
    pointer = await session.scalar(select(WorkspaceArtifact).where(
        WorkspaceArtifact.operation_id == operation.id,
        WorkspaceArtifact.owner_id == owner_id,
        WorkspaceArtifact.public_artifact_id == reference.artifact_id,
        WorkspaceArtifact.status == "ready",
        WorkspaceArtifact.deleted_at.is_(None),
    ))
    if pointer is None:
        raise RasterReviewError("raster_analysis_pointer_missing")
    payload = await service.read_bytes(session, artifact_id=reference.artifact_id, owner_id=owner_id, tenant_id=tenant_id, expected_digest=reference.sha256)
    analysis = RasterAnalysis.model_validate_json(payload)
    require_analysis_digest(analysis)
    source_ref = analysis.source.source_artifact_ref
    if source_ref is None or source_ref.kind != "presentation-pptx" or source_ref.sha256 != analysis.source.source_sha256:
        raise RasterReviewError("raster_source_artifact_required")
    await _verify_ref(session, service, source_ref, owner_id=owner_id, tenant_id=tenant_id)
    if record.get("analysis_digest") != analysis.digest or record.get("analysis_revision") != analysis.revision:
        raise RasterReviewError("raster_analysis_pin_mismatch")
    for page in analysis.pages:
        for region in _all_regions(page):
            if verify_crops and region.crop_artifact_ref is not None:
                await _verify_ref(session, service, region.crop_artifact_ref, owner_id=owner_id, tenant_id=tenant_id)
    return analysis


async def load_raster_analysis(session: AsyncSession, *, operation_id: uuid.UUID, owner_id: uuid.UUID, tenant_id: str, artifact_service: DurableArtifactService | None = None) -> RasterAnalysis:
    service = artifact_service or configured_public_artifact_service()
    operation = await _operation(session, operation_id=operation_id, owner_id=owner_id, tenant_id=tenant_id)
    return await _load_analysis(session, service, operation, owner_id=owner_id, tenant_id=tenant_id)


async def _cas_result(session: AsyncSession, operation: WorkspaceOperation, *, expected_version: int, record: dict) -> None:
    result = dict(operation.result or {})
    result["raster_reconstruction"] = record
    changed = await session.execute(update(WorkspaceOperation).where(
        WorkspaceOperation.id == operation.id,
        WorkspaceOperation.owner_id == operation.owner_id,
        WorkspaceOperation.owner_scope == operation.owner_scope,
        WorkspaceOperation.version == expected_version,
        WorkspaceOperation.state == "awaiting_approval",
        WorkspaceOperation.cancel_requested_at.is_(None),
    ).values(result=result, version=expected_version + 1, updated_at=datetime.now(timezone.utc)).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise RasterReviewError("raster_review_version_conflict")


async def persist_raster_analysis(session: AsyncSession, *, operation_id: uuid.UUID, owner_id: uuid.UUID, tenant_id: str, analysis: RasterAnalysis, expected_operation_version: int, artifact_service: DurableArtifactService | None = None) -> RasterAnalysis:
    require_analysis_digest(analysis)
    service = artifact_service or configured_public_artifact_service()
    async with session.begin_nested():
        operation = await _operation(session, operation_id=operation_id, owner_id=owner_id, tenant_id=tenant_id)
        if operation.version != expected_operation_version or operation.state != "awaiting_approval":
            raise RasterReviewError("raster_review_version_conflict")
        if any(page.approval_method == "automatic_preserve" for page in analysis.pages) and operation.request_payload.get("analysis_mode") != "auto_preserve":
            raise RasterReviewError("raster_automatic_preserve_request_required")
        redesigned = any(page.approval_method == "automatic_redesign" for page in analysis.pages)
        if redesigned and operation.request_payload.get("analysis_mode") != "auto_redesign":
            raise RasterReviewError("raster_automatic_redesign_request_required")
        prior = None
        if isinstance((operation.result or {}).get("raster_reconstruction"), dict):
            prior = await _load_analysis(session, service, operation, owner_id=owner_id, tenant_id=tenant_id)
            if analysis.analysis_id != prior.analysis_id or analysis.source != prior.source or analysis.revision != prior.revision + 1:
                raise RasterReviewError("raster_analysis_revision_binding_invalid")
            before = {r.region_id: r for p in prior.pages for r in p.regions}
            after = {r.region_id: r for p in analysis.pages for r in p.regions}
            if before.keys() != after.keys():
                raise RasterReviewError("raster_region_inventory_immutable")
            crop_fields = {"bbox", "source_pixel_bbox", "crop_artifact_ref", "crop_source_sha256", "pixel_sha256"}
            mutable = {"role", "text", "review_required", "ocr", "table_cells", "review_reason"} | crop_fields
            changed_crops = []
            for region_id, region in before.items():
                if region.model_dump(exclude=mutable) != after[region_id].model_dump(exclude=mutable):
                    raise RasterReviewError("raster_crop_lineage_immutable")
                if region.model_dump(include=crop_fields) != after[region_id].model_dump(include=crop_fields):
                    changed_crops.append(after[region_id])
                prior_ocr = region.ocr.model_dump(exclude={"review_required"}) if region.ocr else None
                next_ocr = after[region_id].ocr.model_dump(exclude={"review_required"}) if after[region_id].ocr else None
                if prior_ocr != next_ocr:
                    raise RasterReviewError("raster_ocr_provenance_immutable")
            if [[o.model_dump(exclude={"review_required"}) for o in p.ocr_inventory] for p in prior.pages] != [[o.model_dump(exclude={"review_required"}) for o in p.ocr_inventory] for p in analysis.pages]:
                raise RasterReviewError("raster_ocr_inventory_immutable")
            def lineage(value: RasterAnalysis) -> list:
                return [(p.reference_region, p.unpromoted_ocr_indices) for p in value.pages]

            if lineage(prior) != lineage(analysis):
                raise RasterReviewError("raster_reference_region_immutable")
            await _verify_crop_corrections(session, service, analysis, changed_crops, owner_id=owner_id, tenant_id=tenant_id)
        elif analysis.revision != 1:
            raise RasterReviewError("raster_analysis_initial_revision_invalid")
        await _verify_full_page_preservation(session, service, analysis, prior, owner_id=owner_id, tenant_id=tenant_id)
        source_ref = analysis.source.source_artifact_ref
        if source_ref is None or source_ref.kind != "presentation-pptx" or source_ref.sha256 != analysis.source.source_sha256:
            raise RasterReviewError("raster_source_artifact_required")
        await _verify_ref(session, service, source_ref, owner_id=owner_id, tenant_id=tenant_id)
        await _retain_live_pointer(session, operation=operation, reference=source_ref, revision=analysis.revision, kind="raster.source")
        for page in analysis.pages:
            for region in _all_regions(page):
                if region.crop_artifact_ref is not None:
                    await _verify_ref(session, service, region.crop_artifact_ref, owner_id=owner_id, tenant_id=tenant_id)
                    await _retain_live_pointer(session, operation=operation, reference=region.crop_artifact_ref, revision=analysis.revision, kind="raster.crop")
        payload = json.dumps(analysis.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        reference = await service.persist_bytes(session, owner_id=owner_id, tenant_id=tenant_id, kind="checkpoint", payload=payload, media_type="application/json", created_by_operation_id=operation.id, retention_class="published")
        await _retain_live_pointer(session, operation=operation, reference=reference, revision=analysis.revision, kind="raster.analysis")
        previous_ref = (operation.result or {}).get("raster_reconstruction", {}).get("analysis_artifact_ref")
        await _cas_result(session, operation, expected_version=expected_operation_version, record={"analysis_id": str(analysis.analysis_id), "analysis_revision": analysis.revision, "analysis_digest": analysis.digest, "analysis_artifact_ref": reference.model_dump(mode="json"), "previous_analysis_artifact_ref": previous_ref, "approval_pin": None})
    return analysis


async def approve_raster_analysis(session: AsyncSession, *, operation_id: uuid.UUID, owner_id: uuid.UUID, tenant_id: str, analysis_digest: str, expected_revision: int, expected_operation_version: int, artifact_service: DurableArtifactService | None = None) -> RasterApprovalPin:
    service = artifact_service or configured_public_artifact_service()
    async with session.begin_nested():
        operation = await _operation(session, operation_id=operation_id, owner_id=owner_id, tenant_id=tenant_id)
        if operation.version != expected_operation_version or operation.state != "awaiting_approval":
            raise RasterReviewError("raster_review_version_conflict")
        analysis = await _load_analysis(session, service, operation, owner_id=owner_id, tenant_id=tenant_id, verify_crops=True)
        if analysis.digest != analysis_digest or analysis.revision != expected_revision:
            raise RasterReviewError("raster_review_analysis_conflict")
        regions = tuple(region for page in analysis.pages for region in ((page.full_page_region,) if page.render_mode == "full_page_preserve" else page.regions))
        if raster_review_blockers(analysis):
            raise RasterReviewError("raster_review_unresolved")
        pin = seal_approval(RasterApprovalPin(operation_id=operation.id, analysis_id=analysis.analysis_id, analysis_revision=analysis.revision, analysis_digest=analysis.digest, source_sha256=analysis.source.source_sha256, approved_region_ids=tuple(r.region_id for r in regions), full_page_preserve_pages=tuple(page.page_number for page in analysis.pages if page.render_mode == "full_page_preserve"), automatic_preserve_pages=tuple(page.page_number for page in analysis.pages if page.approval_method == "automatic_preserve"), automatic_redesign_pages=_pages_with(analysis, "automatic_redesign")))
        record = dict(_current_result(operation))
        record["approval_pin"] = pin.model_dump(mode="json")
        await _cas_result(session, operation, expected_version=expected_operation_version, record=record)
    return pin


async def admit_approved_raster_analysis(session: AsyncSession, *, operation_id: uuid.UUID, owner_id: uuid.UUID, tenant_id: str, pin: RasterApprovalPin, artifact_service: DurableArtifactService | None = None) -> RasterAnalysis:
    service = artifact_service or configured_public_artifact_service()
    operation = await _operation(session, operation_id=operation_id, owner_id=owner_id, tenant_id=tenant_id)
    if operation.cancel_requested_at is not None or operation.state not in ("awaiting_approval", "succeeded"):
        raise RasterReviewError("raster_approval_inactive")
    if pin.operation_id != operation.id or not approval_digest_matches(pin):
        raise RasterReviewError("raster_approval_pin_invalid")
    stored_pin = _current_result(operation).get("approval_pin")
    if not isinstance(stored_pin, dict) or RasterApprovalPin.model_validate(stored_pin).model_dump(mode="json") != pin.model_dump(mode="json"):
        raise RasterReviewError("raster_approval_pin_stale")
    analysis = await _load_analysis(session, service, operation, owner_id=owner_id, tenant_id=tenant_id, verify_crops=True)
    if (pin.analysis_id, pin.analysis_revision, pin.analysis_digest, pin.source_sha256) != (analysis.analysis_id, analysis.revision, analysis.digest, analysis.source.source_sha256):
        raise RasterReviewError("raster_approval_analysis_mismatch")
    if pin.automatic_preserve_pages != tuple(page.page_number for page in analysis.pages if page.approval_method == "automatic_preserve"):
        raise RasterReviewError("raster_approval_method_mismatch")
    if pin.automatic_redesign_pages != _pages_with(analysis, "automatic_redesign"):
        raise RasterReviewError("raster_approval_method_mismatch")
    return analysis
