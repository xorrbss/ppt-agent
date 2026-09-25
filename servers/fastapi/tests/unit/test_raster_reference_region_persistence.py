"""Regression coverage for the reference_region crop-persist bug (D, 28/28 pages).

``analyze_redesign_pptx`` (raster_redesign.py) always sets
``page.reference_region`` and stores its PNG in
``draft.crop_png_by_region[reference_region.region_id]``, but the handler's
crop-persist loop only walked ``page.regions``/``full_page_region`` and never
attached a ``crop_artifact_ref`` to ``reference_region``. That left
``reference_region.crop_artifact_ref`` null on every redesign page, so
``raster_review_blockers`` (raster_reconstruction_store.py) added
``page_N:reference_crop_missing`` for every page and approval could never
auto-fire for auto_redesign.

These tests exercise ``_persist_page_crops`` directly (fast, no DB) and prove
both ends of the regression: the unpersisted page blocks, the persisted one
does not.
"""
from __future__ import annotations

import uuid

import pytest

from services.raster_analysis_operation_handler import _persist_page_crops
from services.source_content.raster_contracts import (
    RasterAnalysis, RasterBox, RasterPage, RasterRegion, RasterSourceIdentity,
    RasterTargetCanvas, seal_analysis,
)
from services.source_content.raster_reconstruction_store import raster_review_blockers
from models.sql.public_artifact import ArtifactRef, artifact_storage_key


OWNER_ID = uuid.uuid4()
TENANT_ID = "tenant-1"
REVIEW_ID = uuid.uuid4()


def _body_region() -> RasterRegion:
    return RasterRegion(
        region_id="p1-r1", page_number=1, role="body",
        bbox=RasterBox(x=0, y=0, width=100, height=50), z_order=0,
        text="hello", review_required=False,
    )


def _reference_region(*, with_crop: bool) -> RasterRegion:
    payload = dict(
        region_id="p1-source-reference", page_number=1, role="source_reference",
        bbox=RasterBox(x=0, y=0, width=1000, height=500), z_order=0,
        source_occurrence_id="slide-1-image-1", source_media_part="ppt/media/image1.png",
        source_pixel_bbox=(0, 0, 1000, 500),
        crop_source_sha256="a" * 64, pixel_sha256="b" * 64,
        review_required=False,
    )
    if with_crop:
        payload["crop_artifact_ref"] = ArtifactRef(
            artifact_id=uuid.uuid4(), owner_id=OWNER_ID, tenant_id=TENANT_ID,
            kind="image-derivative", sha256="c" * 64, size_bytes=10,
            storage_key=artifact_storage_key(owner_id=OWNER_ID, tenant_id=TENANT_ID, digest="c" * 64),
            media_type="image/png",
            retention_class="published", created_by_operation_id=REVIEW_ID,
        )
    return RasterRegion(**payload)


def _analysis(reference_region: RasterRegion) -> RasterAnalysis:
    page = RasterPage(
        page_number=1, regions=(_body_region(),), render_mode="redesign",
        approval_method="automatic_redesign", reference_region=reference_region,
    )
    return seal_analysis(RasterAnalysis(
        source=RasterSourceIdentity(
            source_sha256="d" * 64, page_count=1,
            canvas_width_emu=1000, canvas_height_emu=500,
        ),
        target_canvas=RasterTargetCanvas(orientation="landscape", width_units=1188, height_units=840),
        pages=(page,),
    ))


def test_unpersisted_reference_region_blocks_approval():
    """Reproduces the bug: crop_artifact_ref never attached -> blocked."""
    analysis = _analysis(_reference_region(with_crop=False))
    blockers = raster_review_blockers(analysis)
    assert any(b.startswith("page_1:reference_crop_missing") for b in blockers)


class _FakeService:
    def __init__(self):
        self.persisted: list[dict] = []

    async def persist_bytes(
        self, session, *, owner_id, tenant_id, kind, payload, media_type,
        created_by_operation_id, retention_class,
    ):
        self.persisted.append({"owner_id": owner_id, "tenant_id": tenant_id, "kind": kind, "payload": payload})
        return ArtifactRef(
            artifact_id=uuid.uuid4(), owner_id=owner_id, tenant_id=tenant_id,
            kind=kind, sha256="e" * 64, size_bytes=len(payload),
            storage_key=artifact_storage_key(owner_id=owner_id, tenant_id=tenant_id, digest="e" * 64),
            media_type=media_type,
            retention_class=retention_class, created_by_operation_id=created_by_operation_id,
        )


class _FakeDraft:
    def __init__(self, analysis, crop_png_by_region):
        self.analysis = analysis
        self.crop_png_by_region = crop_png_by_region


@pytest.mark.anyio
async def test_persist_page_crops_attaches_reference_region_crop():
    analysis = _analysis(_reference_region(with_crop=False))
    page = analysis.pages[0]
    draft = _FakeDraft(analysis, {page.reference_region.region_id: b"png-bytes"})
    service = _FakeService()

    persisted_page = await _persist_page_crops(
        page, draft, service=service, session=object(),
        owner_id=OWNER_ID, tenant_id=TENANT_ID, review_id=REVIEW_ID,
    )

    assert persisted_page.reference_region.crop_artifact_ref is not None
    assert any(call["kind"] == "image-derivative" and call["payload"] == b"png-bytes" for call in service.persisted)
    # regions/full_page_region untouched (no crop offered for the text region).
    assert persisted_page.regions[0].crop_artifact_ref is None


@pytest.mark.anyio
async def test_persist_page_crops_raises_when_reference_crop_bytes_missing():
    analysis = _analysis(_reference_region(with_crop=False))
    page = analysis.pages[0]
    draft = _FakeDraft(analysis, {})  # crop bytes absent -> must fail, not silently skip
    service = _FakeService()

    with pytest.raises(Exception) as error:
        await _persist_page_crops(
            page, draft, service=service, session=object(),
            owner_id=OWNER_ID, tenant_id=TENANT_ID, review_id=REVIEW_ID,
        )
    assert "raster_reference_crop_missing" in str(error.value)


def test_persisted_reference_region_clears_the_blocker_and_allows_auto_approval():
    """End state after the fix: no blockers, so the handler's
    ``analysis_mode == "auto_redesign" and not raster_review_blockers(analysis)``
    auto-approval condition is satisfied (mirrors raster_analysis_operation_handler.execute)."""
    analysis = _analysis(_reference_region(with_crop=True))
    blockers = raster_review_blockers(analysis)
    assert blockers == ()
    auto_approved = not raster_review_blockers(analysis)
    assert auto_approved is True
