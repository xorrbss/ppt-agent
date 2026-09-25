"""Authenticated analysis and explicit review of screenshot presentation sources."""
from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
from typing import Annotated, Literal
from zipfile import ZipFile

from fastapi import APIRouter, Depends, HTTPException, Response
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sql.presentation import PresentationModel
from models.sql.workspace import WorkspaceConversation, WorkspaceOperation
from services.artifact_store import configured_public_artifact_service
from services.database import get_async_session
from services.references import ReferenceProviderError, admit_reference_snapshot
from services.source_content.raster_contracts import RasterAnalysis, raster_digest, seal_analysis
from services.source_content.raster_reconstruction_store import (
    RasterReviewError, _operation, _retain_live_pointer, approve_raster_analysis,
    load_raster_analysis, persist_raster_analysis, reserve_raster_operation_position,
    derive_raster_crop_corrections, derive_full_page_preserve_regions, raster_review_blockers,
)
from services.workspace_asset_service import resolve_workspace_asset_download
from services.workspace_operation_principal import PRINCIPAL_TENANT_PAYLOAD_KEY
from services.workspace_service import _get_or_create_conversation
from utils.owner_context import get_current_owner_id, get_current_tenant_id


RASTER_RECONSTRUCTION_ROUTER = APIRouter(prefix="/source-content/raster", tags=["Raster source reconstruction"])
_RETRYABLE_ANALYSIS_STATES = frozenset({"failed", "cancelled", "deadline_exceeded", "dead_letter"})


class RasterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeRasterRequest(RasterRequest):
    reference_snapshot_id: uuid.UUID
    presentation_id: uuid.UUID
    orientation: Literal["portrait", "landscape"] = "portrait"
    # ``auto_redesign`` is the product default for image-only sources: it
    # reflows the deck into editable text/tables and only auto-approves when
    # every page cleared analysis with no low-confidence regions. Callers
    # that need the legacy whole-page-as-picture behaviour opt into
    # ``auto_preserve`` explicitly; ``reconstruct`` remains the manual,
    # fully-reviewed path.
    analysis_mode: Literal["auto_preserve", "reconstruct", "auto_redesign"] = "auto_redesign"
    style_id: Literal["screenshot-blue"] = "screenshot-blue"


class RasterReviewVersion(RasterRequest):
    expected_operation_version: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    analysis_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RasterRegionEdit(RasterRequest):
    region_id: str = Field(min_length=1)
    role: Literal["title", "body", "table", "screenshot", "logo", "decoration", "unknown"] | None = None
    text: str | None = Field(default=None, max_length=50000)
    source_pixel_bbox: tuple[StrictInt, StrictInt, StrictInt, StrictInt] | None = None
    reviewed: bool


class RasterPageEdit(RasterRequest):
    page_number: int = Field(ge=1)
    render_mode: Literal["reconstruct", "full_page_preserve"]
    reviewed: bool = False
    acknowledge_image_only: bool = False
    acknowledge_legibility: bool = False


class ReviewRasterRequest(RasterReviewVersion):
    region_edits: list[RasterRegionEdit] = Field(default_factory=list, max_length=20000)
    page_edits: list[RasterPageEdit] = Field(default_factory=list, max_length=2000)
    review_ocr: bool = False
    resolve_unresolved: bool = False


async def _analysis_retry_target(
    session: AsyncSession, existing: WorkspaceOperation, *,
    owner_id: uuid.UUID, tenant_id: str, presentation_id: uuid.UUID,
) -> WorkspaceOperation:
    """Follow the unique retry chain while the caller holds the conversation lock."""
    seen = set()
    while True:
        if existing.id in seen or existing.request_payload.get("tenant_id") != tenant_id:
            raise RasterReviewError("raster_analysis_retry_binding_invalid")
        seen.add(existing.id)
        child = await session.scalar(select(WorkspaceOperation).where(
            WorkspaceOperation.retry_of_operation_id == existing.id,
            WorkspaceOperation.owner_id == owner_id,
            WorkspaceOperation.owner_scope == str(owner_id),
            WorkspaceOperation.presentation_id == presentation_id,
            WorkspaceOperation.kind == "raster.analysis",
        ))
        if child is None:
            return existing
        existing = child


async def _claim_analysis_retry(session: AsyncSession, existing: WorkspaceOperation, *, owner_id: uuid.UUID) -> None:
    changed = await session.execute(update(WorkspaceOperation).where(
        WorkspaceOperation.id == existing.id,
        WorkspaceOperation.owner_id == owner_id,
        WorkspaceOperation.owner_scope == str(owner_id),
        WorkspaceOperation.version == existing.version,
        WorkspaceOperation.state.in_(_RETRYABLE_ANALYSIS_STATES),
    ).values(version=WorkspaceOperation.version + 1).execution_options(synchronize_session=False))
    if changed.rowcount != 1:
        raise RasterReviewError("raster_analysis_retry_conflict")


def _scope() -> tuple[uuid.UUID, str]:
    owner = get_current_owner_id()
    tenant = get_current_tenant_id()
    if owner is None or not tenant:
        raise HTTPException(401, detail={"code": "raster_authenticated_scope_required"})
    return owner, tenant


def _error(error: Exception) -> HTTPException:
    code = getattr(error, "code", "raster_request_invalid")
    if code.endswith("not_found") or code == "raster_analysis_pointer_missing":
        status = 404
    elif "conflict" in code or "stale" in code or "unresolved" in code:
        status = 409
    else:
        status = 422
    return HTTPException(status, detail={"code": code})


async def _response(session: AsyncSession, operation_id: uuid.UUID, analysis: RasterAnalysis | None = None) -> dict:
    owner, tenant = _scope()
    operation = await _operation(session, operation_id=operation_id, owner_id=owner, tenant_id=tenant)
    if operation.kind == "raster.analysis":
        review_id = (operation.result or {}).get("review_operation_id")
        if operation.state == "succeeded":
            # Older results lost this pointer to the workspace result allowlist.
            # The review ID is deterministically derived by the analysis handler.
            review_operation_id = (
                uuid.UUID(review_id) if review_id else uuid.uuid5(operation.id, "raster-review/v1")
            )
            return await _response(session, review_operation_id)
        return {"analysis": None, "operation_id": str(operation.id), "operation_version": operation.version,
                "status": operation.state, "progress": operation.progress,
                "error_code": operation.error_code, "approval_pin": None, "can_approve": False, "review_blockers": [],
                "reference_snapshot_id": operation.request_payload.get("reference_snapshot_id")}
    if analysis is None:
        analysis = await load_raster_analysis(session, operation_id=operation_id, owner_id=owner, tenant_id=tenant)
    return {
        "analysis": analysis.model_dump(mode="json"),
        "operation_id": str(operation.id),
        "operation_version": operation.version,
        "status": operation.state,
        "approval_pin": (operation.result or {}).get("raster_reconstruction", {}).get("approval_pin"),
        "can_approve": operation.state == "awaiting_approval" and not raster_review_blockers(analysis),
        "review_blockers": list(raster_review_blockers(analysis)),
        # Non-blocking suggestions (never gate approval; see raster_review_blockers).
        "advisory_count": sum(1 for issue in analysis.unresolved if issue.startswith("advisory:")),
        "reference_snapshot_id": operation.request_payload.get("reference_snapshot_id"),
        "can_auto_migrate": (
            operation.state == "awaiting_approval"
            and analysis.revision == 1
            and not (operation.result or {}).get("raster_reconstruction", {}).get("approval_pin")
            and operation.request_payload.get("analysis_mode", "reconstruct") == "reconstruct"
            and all(page.render_mode == "reconstruct" and page.full_page_region is None for page in analysis.pages)
        ),
    }


@RASTER_RECONSTRUCTION_ROUTER.post("/analysis")
async def analyze_raster(
    request: AnalyzeRasterRequest, session: Annotated[AsyncSession, Depends(get_async_session)],
):
    from services.workspace_operation_runner import wake_workspace_operation_runner

    owner, tenant = _scope()
    try:
        presentation = await session.scalar(select(PresentationModel).where(
            PresentationModel.id == request.presentation_id,
            PresentationModel.owner_id == owner,
            PresentationModel.deleted_at.is_(None),
        ))
        if presentation is None:
            raise HTTPException(404, detail={"code": "raster_presentation_not_found"})
        snapshot = await admit_reference_snapshot(session, snapshot_id=request.reference_snapshot_id)
        if len(snapshot.asset_bindings) != 1:
            raise RasterReviewError("raster_single_source_required")
        asset, source_path = await resolve_workspace_asset_download(
            session, asset_id=snapshot.asset_bindings[0].asset_id,
        )
        if source_path.stat().st_size > 128 * 1024 * 1024:
            raise RasterReviewError("raster_source_too_large")
        source_bytes = await asyncio.to_thread(source_path.read_bytes)
        source_sha = hashlib.sha256(source_bytes).hexdigest()
        if not asset.sha256 or asset.sha256 != source_sha:
            raise RasterReviewError("raster_source_digest_mismatch")
        conversation = await _get_or_create_conversation(session, presentation.id)
        await session.scalar(select(WorkspaceConversation).where(
            WorkspaceConversation.id == conversation.id,
            WorkspaceConversation.owner_id == owner,
            WorkspaceConversation.owner_scope == str(owner),
        ).with_for_update())
        request_payload = request.model_dump(mode="json") | {"tenant_id": tenant}
        idempotency_hash = raster_digest(
            request_payload | {"owner_id": str(owner), "source_sha256": source_sha},
        )
        existing = await session.scalar(select(WorkspaceOperation).where(
            WorkspaceOperation.owner_id == owner,
            WorkspaceOperation.owner_scope == str(owner),
            WorkspaceOperation.presentation_id == presentation.id,
            WorkspaceOperation.kind == "raster.analysis",
            WorkspaceOperation.idempotency_key_hash == idempotency_hash,
        ))
        retry_of_operation_id = None
        if existing is not None:
            existing = await _analysis_retry_target(
                session, existing, owner_id=owner, tenant_id=tenant, presentation_id=presentation.id,
            )
            if existing.state not in _RETRYABLE_ANALYSIS_STATES:
                existing_id = existing.id
                await session.commit()
                return await _response(session, existing_id)
            await _claim_analysis_retry(session, existing, owner_id=owner)
            retry_of_operation_id = existing.id
            idempotency_hash = raster_digest(
                {"raster_request_key": idempotency_hash, "retry_of_operation_id": str(existing.id)},
            )
        anchor_position = await reserve_raster_operation_position(
            session, conversation_id=conversation.id, owner_id=owner,
        )
        operation_id = uuid.uuid4()
        operation = WorkspaceOperation(
            id=operation_id, owner_id=owner, owner_scope=str(owner),
            retry_of_operation_id=retry_of_operation_id,
            conversation_id=conversation.id, presentation_id=presentation.id,
            anchor_position=anchor_position,
            kind="raster.analysis", state="queued",
            idempotency_key_hash=idempotency_hash,
            request_hash=raster_digest(request_payload), request_payload=request_payload,
            version=1,
        )
        session.add(operation)
        await session.flush()
        service = configured_public_artifact_service()
        source_ref = await service.persist_bytes(
            session, owner_id=owner, tenant_id=tenant, kind="presentation-pptx", payload=source_bytes,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            created_by_operation_id=operation_id, retention_class="published",
        )
        operation.request_payload = request_payload | {
            "source_artifact_ref": source_ref.model_dump(mode="json"), "source_sha256": source_sha,
        }
        operation.request_hash = raster_digest(operation.request_payload)
        operation.request_payload = operation.request_payload | {PRINCIPAL_TENANT_PAYLOAD_KEY: tenant}  # not hashed
        session.add(operation)
        await _retain_live_pointer(session, operation=operation, reference=source_ref, revision=1, kind="raster.source")
        await session.commit()
        wake_workspace_operation_runner()
        return await _response(session, operation_id)
    except (RasterReviewError, ReferenceProviderError, ValueError) as exc:
        await session.rollback()
        raise _error(exc) from exc


@RASTER_RECONSTRUCTION_ROUTER.get("/analysis/{operation_id}")
async def get_raster_analysis(
    operation_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_async_session)],
):
    try:
        return await _response(session, operation_id)
    except (RasterReviewError, ValueError) as exc:
        raise _error(exc) from exc


@RASTER_RECONSTRUCTION_ROUTER.get("/analysis/{operation_id}/pages/{page_number}")
async def get_raster_page(
    operation_id: uuid.UUID, page_number: int, session: Annotated[AsyncSession, Depends(get_async_session)],
):
    owner, tenant = _scope()
    try:
        analysis = await load_raster_analysis(session, operation_id=operation_id, owner_id=owner, tenant_id=tenant)
        if page_number < 1 or page_number > len(analysis.pages):
            raise HTTPException(404, detail={"code": "raster_page_not_found"})
        page = analysis.pages[page_number - 1]
        media_part = next((r.source_media_part for r in page.regions if r.source_media_part), None)
        reference = analysis.source.source_artifact_ref
        pixel_size = None
        if media_part and reference:
            source = await configured_public_artifact_service().read_bytes(
                session, artifact_id=reference.artifact_id, owner_id=owner, tenant_id=tenant,
                expected_digest=reference.sha256,
            )
            width, height = await asyncio.to_thread(_source_pixel_size, source, media_part)
            pixel_size = {"width": width, "height": height}
        return {
            "page": page.model_dump(mode="json"),
            "source_canvas": analysis.source.model_dump(mode="json"),
            "source_pixel_size": pixel_size,
            "preview_url": f"/api/v1/ppt/source-content/raster/analysis/{operation_id}/pages/{page_number}/preview",
        }
    except (RasterReviewError, ValueError) as exc:
        raise _error(exc) from exc


@RASTER_RECONSTRUCTION_ROUTER.patch("/analysis/{operation_id}")
async def review_raster_analysis(
    operation_id: uuid.UUID, request: ReviewRasterRequest,
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    owner, tenant = _scope()
    try:
        analysis = await load_raster_analysis(session, operation_id=operation_id, owner_id=owner, tenant_id=tenant)
        if analysis.digest != request.analysis_digest or analysis.revision != request.expected_revision:
            raise RasterReviewError("raster_review_analysis_conflict")
        edits = {edit.region_id: edit for edit in request.region_edits}
        known = {r.region_id for p in analysis.pages for r in p.regions}
        if len(edits) != len(request.region_edits) or not edits.keys() <= known:
            raise RasterReviewError("raster_review_region_invalid")
        page_edits = {edit.page_number: edit for edit in request.page_edits}
        if len(page_edits) != len(request.page_edits) or any(number > len(analysis.pages) for number in page_edits):
            raise RasterReviewError("raster_review_page_invalid")
        operation = await _operation(session, operation_id=operation_id, owner_id=owner, tenant_id=tenant)
        if operation.version != request.expected_operation_version:
            raise RasterReviewError("raster_review_version_conflict")
        pixel_boxes = {
            edit.region_id: edit.source_pixel_bbox
            for edit in request.region_edits if edit.source_pixel_bbox is not None
        }
        corrected = await derive_raster_crop_corrections(
            session, operation_id=operation_id, owner_id=owner, tenant_id=tenant,
            analysis=analysis, pixel_boxes=pixel_boxes,
        )
        preserve_pages = tuple(
            number for number, edit in page_edits.items()
            if edit.render_mode == "full_page_preserve"
            and analysis.pages[number - 1].render_mode != "full_page_preserve"
        )
        preserved = await derive_full_page_preserve_regions(
            session, operation_id=operation_id, owner_id=owner, tenant_id=tenant,
            analysis=analysis, page_numbers=preserve_pages,
        )
        pages = []
        for page in analysis.pages:
            regions = []
            for region in page.regions:
                if region.region_id in corrected:
                    region = corrected[region.region_id]
                edit = edits.get(region.region_id)
                changes = {}
                if edit is not None:
                    changes["review_required"] = not edit.reviewed
                    if edit.role is not None:
                        changes["role"] = edit.role
                    if "text" in edit.model_fields_set:
                        changes["text"] = edit.text
                    if edit.role == "decoration":
                        changes["text"] = None
                    if region.region_id in corrected:
                        changes["review_required"] = True
                if request.review_ocr and region.ocr is not None:
                    changes["ocr"] = region.ocr.model_copy(update={"review_required": False})
                regions.append(region.model_copy(update=changes))
            inventory = (
                tuple(o.model_copy(update={"review_required": False}) for o in page.ocr_inventory)
                if request.review_ocr else page.ocr_inventory
            )
            page_changes = {"regions": tuple(regions), "ocr_inventory": inventory}
            page_edit = page_edits.get(page.page_number)
            if page_edit is not None:
                page_changes["render_mode"] = page_edit.render_mode
                page_changes["full_page_preserve_acknowledgements"] = ()
                if page.page_number in preserved:
                    page_changes["full_page_region"] = preserved[page.page_number]
                elif page_edit.render_mode == "full_page_preserve":
                    if page.full_page_region is None:
                        raise RasterReviewError("raster_full_page_region_required")
                    acknowledged = (
                        page_edit.reviewed and page_edit.acknowledge_image_only and page_edit.acknowledge_legibility
                    )
                    page_changes["full_page_region"] = page.full_page_region.model_copy(
                        update={"review_required": not acknowledged},
                    )
                    if acknowledged:
                        page_changes["full_page_preserve_acknowledgements"] = ("image_only", "legibility")
            pages.append(page.model_copy(update=page_changes))
        analysis = seal_analysis(analysis.model_copy(update={
            "revision": analysis.revision + 1,
            "pages": tuple(pages),
            "unresolved": () if request.resolve_unresolved else analysis.unresolved,
        }))
        await persist_raster_analysis(
            session, operation_id=operation_id, owner_id=owner, tenant_id=tenant,
            analysis=analysis, expected_operation_version=request.expected_operation_version,
        )
        await session.commit()
        return await _response(session, operation_id, analysis)
    except (RasterReviewError, ValueError) as exc:
        await session.rollback()
        raise _error(exc) from exc


@RASTER_RECONSTRUCTION_ROUTER.post("/analysis/{operation_id}/approve")
async def approve_raster(
    operation_id: uuid.UUID, request: RasterReviewVersion,
    session: Annotated[AsyncSession, Depends(get_async_session)],
):
    owner, tenant = _scope()
    try:
        await approve_raster_analysis(
            session, operation_id=operation_id, owner_id=owner, tenant_id=tenant,
            analysis_digest=request.analysis_digest, expected_revision=request.expected_revision,
            expected_operation_version=request.expected_operation_version,
        )
        await session.commit()
        return await _response(session, operation_id)
    except (RasterReviewError, ValueError) as exc:
        await session.rollback()
        raise _error(exc) from exc


def _preview_png(source_bytes: bytes, media_part: str) -> bytes:
    with ZipFile(io.BytesIO(source_bytes)) as archive:
        payload = archive.read(media_part)
    with Image.open(io.BytesIO(payload)) as image:
        image.load()
        result = io.BytesIO()
        image.convert("RGBA").save(result, format="PNG")
        return result.getvalue()


def _source_pixel_size(source_bytes: bytes, media_part: str) -> tuple[int, int]:
    with ZipFile(io.BytesIO(source_bytes)) as archive:
        payload = archive.read(media_part)
    with Image.open(io.BytesIO(payload)) as image:
        return image.size


@RASTER_RECONSTRUCTION_ROUTER.get("/analysis/{operation_id}/pages/{page_number}/preview")
async def preview_raster_page(
    operation_id: uuid.UUID, page_number: int, session: Annotated[AsyncSession, Depends(get_async_session)],
):
    owner, tenant = _scope()
    try:
        analysis = await load_raster_analysis(session, operation_id=operation_id, owner_id=owner, tenant_id=tenant)
        if page_number < 1 or page_number > len(analysis.pages):
            raise HTTPException(404, detail={"code": "raster_page_not_found"})
        media_part = next(
            (r.source_media_part for r in analysis.pages[page_number - 1].regions if r.source_media_part), None,
        )
        reference = analysis.source.source_artifact_ref
        if not media_part or reference is None:
            raise RasterReviewError("raster_preview_not_found")
        source = await configured_public_artifact_service().read_bytes(
            session, artifact_id=reference.artifact_id, owner_id=owner, tenant_id=tenant,
            expected_digest=reference.sha256,
        )
        payload = await asyncio.to_thread(_preview_png, source, media_part)
        return Response(
            payload, media_type="image/png",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    except (RasterReviewError, ValueError) as exc:
        raise _error(exc) from exc


@RASTER_RECONSTRUCTION_ROUTER.get("/analysis/{operation_id}/regions/{region_id}/crop")
async def preview_raster_crop(
    operation_id: uuid.UUID, region_id: str, session: Annotated[AsyncSession, Depends(get_async_session)],
):
    owner, tenant = _scope()
    try:
        analysis = await load_raster_analysis(session, operation_id=operation_id, owner_id=owner, tenant_id=tenant)
        region = next(
            (
                r for p in analysis.pages
                for r in (*p.regions, *((p.full_page_region,) if p.full_page_region else ()))
                if r.region_id == region_id
            ),
            None,
        )
        if region is None or region.crop_artifact_ref is None:
            raise HTTPException(404, detail={"code": "raster_crop_not_found"})
        reference = region.crop_artifact_ref
        payload = await configured_public_artifact_service().read_bytes(
            session, artifact_id=reference.artifact_id, owner_id=owner, tenant_id=tenant,
            expected_digest=reference.sha256,
        )
        return Response(
            payload, media_type="image/png",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )
    except (RasterReviewError, ValueError) as exc:
        raise _error(exc) from exc
