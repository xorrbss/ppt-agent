"""Dense-group pixel fallback through the real raster asset publication path.

Runs prepare_raster_page_assets and build_raster_page_export_assets on a
sealed RasterAnalysis whose page needs the fallback. Artifact storage and the
DB publication fence are replaced by an in-memory artifact service and
persistence fakes that keep the real validation (digests, recipes, manifest
revalidation, derivative binding); everything else is production code.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import uuid

import pytest
from PIL import Image, ImageDraw

import test_raster_redesign_layout_fixtures as fx
from models.sql.public_artifact import ArtifactRef, artifact_storage_key
from services import raster_source_assets as assets
from services.svg_asset_manifest import ArtifactPointer, RasterDerivative, SourceManifest, canonical_digest
from services.svg_asset_persistence import SvgPublicationContext
from services.source_content.raster_contracts import RasterApprovalPin, seal_analysis
from services.source_content.raster_svg_layout import raster_page_render_advisories, render_raster_page_svg

OWNER, TENANT, OPERATION = uuid.uuid4(), "tenant", uuid.uuid4()
SHA = "a" * 64


class MemoryArtifacts:
    def __init__(self):
        self.refs, self.blobs = {}, {}

    def put(self, payload: bytes, kind: str, media_type: str) -> ArtifactRef:
        digest = hashlib.sha256(payload).hexdigest()
        ref = ArtifactRef(
            artifact_id=uuid.uuid4(), owner_id=OWNER, tenant_id=TENANT, kind=kind, sha256=digest,
            size_bytes=len(payload), media_type=media_type, created_by_operation_id=OPERATION,
            retention_class="checkpoint",
            storage_key=artifact_storage_key(owner_id=OWNER, tenant_id=TENANT, digest=digest),
        )
        self.refs[ref.artifact_id], self.blobs[ref.artifact_id] = ref, payload
        return ref

    async def persist_bytes(self, session, *, owner_id, tenant_id, kind, payload, media_type, **_):
        assert (owner_id, tenant_id) == (OWNER, TENANT)
        return self.put(payload, kind, media_type)

    async def resolve_ref(self, session, *, artifact_id, owner_id, tenant_id, expected_digest=None):
        ref = self.refs[artifact_id]
        assert (ref.owner_id, ref.tenant_id) == (owner_id, tenant_id)
        assert expected_digest in (None, ref.sha256)
        return ref

    async def read_bytes(self, session, *, artifact_id, owner_id, tenant_id, expected_digest=None):
        await self.resolve_ref(session, artifact_id=artifact_id, owner_id=owner_id, tenant_id=tenant_id,
                               expected_digest=expected_digest)
        return self.blobs[artifact_id]

    async def read_ref_bytes(self, reference):
        return self.blobs[reference.artifact_id]


def _png(size, marks=()):
    image = Image.new("RGB", size, (250, 251, 253))
    draw = ImageDraw.Draw(image)
    for index, (x, y, w, h) in enumerate(marks):
        draw.rectangle((x, y, x + w, y + h), fill=(20 + index * 20, 60, 160))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _rgba_digest(payload):
    with Image.open(io.BytesIO(payload)) as image:
        return hashlib.sha256(image.convert("RGBA").tobytes()).hexdigest()


def _dense_page_analysis(store: MemoryArtifacts, *, brand: bool = False, noise: bool = False):
    long = "\n".join(f"{index}. 조밀한 카드 본문 문장이 계속 이어집니다" for index in range(9))
    boxes = []
    regions = [dict(region_id="p1-title", role="title", box=(40, 40, 700, 70), text="조밀한 그룹 페이지")]
    for index in range(8):
        box = (30 + (index % 4) * 405, 150 + (index // 4) * 380, 380, 360)
        boxes.append(box)
        regions.append(dict(region_id=f"p1-card{index}", role="body", box=box, text=f"카드 {index}\n{long}",
                            group_id=f"g{index}", heading_line_count=1))
    regions.append(dict(region_id="p1-foot", role="body", box=(40, 905, 600, 25), text="DevAX 기반 제안서 01",
                        group_id="footer"))
    full = _png((1672, 941), boxes)
    if noise:  # incompressible pixels: the materialized page exceeds the 2 MB canonical limit
        import os

        buffer = io.BytesIO()
        Image.frombytes("RGB", (1672, 941), os.urandom(1672 * 941 * 3)).save(buffer, format="PNG")
        full = buffer.getvalue()
    reference = store.put(full, "image-derivative", "image/png")
    brand_region = None
    if brand:  # a header logo: last in reading order, first in the SVG DOM
        logo = _png((200, 60), [(10, 10, 50, 30)])
        brand_ref = store.put(logo, "image-derivative", "image/png")
        brand_region = dict(
            region_id="p1-brand", page_number=1, role="logo", z_order=0, review_required=False,
            bbox=dict(x=1300 * fx.EMU, y=30 * fx.EMU, width=200 * fx.EMU, height=60 * fx.EMU),
            source_pixel_bbox=(1300, 30, 200, 60), crop_artifact_ref=brand_ref.model_dump(mode="json"),
            source_occurrence_id="slide-1-image-1", crop_source_sha256=hashlib.sha256(full).hexdigest(),
            pixel_sha256=_rgba_digest(logo), reading_order=len(regions),
        )
    extra_regions = [brand_region] if brand_region else []
    if brand:  # a standalone capture after the cards: in the SVG DOM it follows the group crops
        shot = _png((120, 70), [(5, 5, 40, 20)])
        if noise:  # a large, incompressible screen capture (same aspect as its source box)
            import os

            buffer = io.BytesIO()
            Image.frombytes("RGB", (1200, 700), os.urandom(1200 * 700 * 3)).save(buffer, format="PNG")
            shot = buffer.getvalue()
        shot_ref = store.put(shot, "image-derivative", "image/png")
        extra_regions.append(dict(
            region_id="p1-late-shot", page_number=1, role="screenshot", z_order=0, review_required=False,
            bbox=dict(x=1480 * fx.EMU, y=860 * fx.EMU, width=120 * fx.EMU, height=70 * fx.EMU),
            source_pixel_bbox=(1480, 860, 120, 70), crop_artifact_ref=shot_ref.model_dump(mode="json"),
            source_occurrence_id="slide-1-image-1", crop_source_sha256=hashlib.sha256(full).hexdigest(),
            pixel_sha256=_rgba_digest(shot), reading_order=len(regions) + 1,
        ))
    inventory, sealed_regions = [], []
    for order, spec in enumerate(regions):
        x, y, w, h = spec["box"]
        bbox = dict(x=x * fx.EMU, y=y * fx.EMU, width=w * fx.EMU, height=h * fx.EMU)
        indices = []
        for token in spec["text"].split():
            indices.append(len(inventory))
            inventory.append(dict(text=token, bbox=bbox, engine="fixture", source_sha256=SHA, review_required=False))
        sealed_regions.append(dict(
            region_id=spec["region_id"], page_number=1, role=spec["role"], bbox=bbox, z_order=0,
            text=spec["text"], reading_order=order, review_required=False, source_pixel_bbox=(x, y, w, h),
            ocr_token_indices=tuple(indices), group_id=spec.get("group_id"),
            heading_line_count=spec.get("heading_line_count", 0),
        ))
    reference_region = dict(
        region_id="p1-source-reference", page_number=1, role="source_reference",
        bbox=dict(x=0, y=0, width=fx.SOURCE_W, height=fx.SOURCE_H), z_order=0, review_required=False,
        source_pixel_bbox=(0, 0, 1672, 941), crop_artifact_ref=reference.model_dump(mode="json"),
        source_occurrence_id="slide-1-image-1", source_media_part="ppt/media/image1.png",
        crop_source_sha256=hashlib.sha256(full).hexdigest(), pixel_sha256=_rgba_digest(full),
    )
    return seal_analysis(dict(
        source=dict(source_sha256=SHA, page_count=1, canvas_width_emu=fx.SOURCE_W, canvas_height_emu=fx.SOURCE_H),
        target_canvas=dict(orientation="landscape", width_units=1188, height_units=840),
        pages=[dict(page_number=1, regions=sealed_regions + extra_regions,
                    ocr_inventory=inventory, render_mode="redesign",
                    approval_method="automatic_redesign", reference_region=reference_region)],
    ))


def _install_fakes(monkeypatch, store, analysis):
    async def admit(session, **_):
        return analysis

    async def persist_derivative(session, service, *, context, preview, publication_name, revision):
        # Same binding checks as persist_derivative_if_current, without the DB fence.
        assert hashlib.sha256(preview.payload).hexdigest() == preview.sha256
        assert canonical_digest(preview.recipe) == preview.recipe_digest
        ref = store.put(preview.payload, "image-derivative", preview.media_type)
        return RasterDerivative(ref=ref.artifact_id, sha256=ref.sha256, width=preview.width, height=preview.height,
                                media_type=preview.media_type, byte_size=len(preview.payload),
                                recipe_digest=preview.recipe_digest)

    async def persist_manifest(session, service, *, context, manifest, publication_name, revision):
        manifest = type(manifest).model_validate(manifest.model_dump(mode="json", by_alias=True))
        derivatives = ([slot.model_derivative for slot in manifest.slots] if isinstance(manifest, SourceManifest)
                       else [placement.derivative for placement in manifest.placements])
        for derivative in derivatives:
            ref = store.refs[derivative.ref]
            assert (ref.kind, ref.media_type, ref.size_bytes) == ("image-derivative", derivative.media_type,
                                                                   derivative.byte_size)
        payload = json.dumps(manifest.model_dump(mode="json", by_alias=True), sort_keys=True).encode("utf-8")
        ref = store.put(payload, "source-manifest" if isinstance(manifest, SourceManifest) else "placement-manifest",
                        "application/json")
        return ArtifactPointer(ref=ref.artifact_id, sha256=ref.sha256)

    async def load_manifest(session, service, *, pointer, owner_id, tenant_id):
        return SourceManifest.model_validate_json(store.blobs[uuid.UUID(str(pointer["ref"]))])

    monkeypatch.setattr(assets, "admit_approved_raster_analysis", admit)
    monkeypatch.setattr(assets, "persist_derivative_if_current", persist_derivative)
    monkeypatch.setattr(assets, "persist_manifest_if_current", persist_manifest)
    monkeypatch.setattr(assets, "load_source_manifest_pointer", load_manifest)


def test_dense_group_fallback_publishes_and_exports_through_the_real_asset_path(monkeypatch):
    store = MemoryArtifacts()
    analysis = _dense_page_analysis(store)
    _install_fakes(monkeypatch, store, analysis)
    approved = tuple(r.region_id for r in analysis.pages[0].regions)
    svg = render_raster_page_svg(analysis, 1, approved_region_ids=approved)
    advisories = raster_page_render_advisories(analysis, 1, svg)
    assert advisories and all(a["code"] == "dense_group_pixel_fallback" for a in advisories)
    pin = RasterApprovalPin(
        operation_id=OPERATION, analysis_id=analysis.analysis_id, analysis_revision=analysis.revision,
        analysis_digest=analysis.digest, source_sha256=SHA, approved_region_ids=approved,
        automatic_redesign_pages=(1,),
    )
    context = SvgPublicationContext(operation_id=OPERATION, owner_id=OWNER, tenant_id=TENANT, attempt_number=1,
                                    attempt_token="t", fencing_token=1)
    presentation = uuid.uuid4()
    prepared = asyncio.run(assets.prepare_raster_page_assets(
        None, store, approval_pin=pin, owner_id=OWNER, tenant_id=TENANT, publication_context=context,
        presentation_id=presentation, source_revision=1, slide_revision=1, page_number=1, output_page_index=0,
        canonical_svg=svg, width=1188, height=840,
    ))
    slots = {slot.slot_id: slot for slot in prepared.source_manifest.slots}
    assert len(slots) == len(advisories)
    reference = analysis.pages[0].reference_region
    for advisory, lineage in zip(advisories, prepared.crop_lineage, strict=True):
        slot = slots[lineage["slotId"]]
        _, _, w, h = lineage["sourcePixelBbox"]
        assert (slot.source_width, slot.source_height) == (w, h)
        assert slot.source_asset_id == reference.crop_artifact_ref.artifact_id
        assert lineage["regionId"] == advisory["regionId"]
        assert lineage["sourceOccurrenceId"] == reference.source_occurrence_id
    exported = asyncio.run(assets.build_raster_page_export_assets(
        None, store, approval_pin=pin, owner_id=OWNER, tenant_id=TENANT, publication_context=context,
        presentation_id=presentation, source_pointer=prepared.source_pointer, slide_revision=1, page_number=1,
        output_page_index=0, canonical_svg=svg, width=1188, height=840, export_format="pptx",
    ))
    assert exported["pageIndex"] == 0 and exported["fullPagePreserve"] is False


def test_fallback_without_reference_crop_fails_loudly(monkeypatch):
    store = MemoryArtifacts()
    analysis = _dense_page_analysis(store)
    page = analysis.pages[0].model_copy(update={"reference_region": analysis.pages[0].reference_region.model_copy(
        update={"crop_artifact_ref": None})})
    broken = analysis.model_copy(update={"pages": (page,)})
    approved = tuple(r.region_id for r in page.regions)
    svg = render_raster_page_svg(analysis, 1, approved_region_ids=approved)
    pin = type("Pin", (), dict(approved_region_ids=approved, full_page_preserve_pages=(),
                               automatic_preserve_pages=(), automatic_redesign_pages=(1,)))()
    with pytest.raises(ValueError, match="raster_group_fallback_source_missing"):
        assets._approved_crop_regions(broken, pin, 1, svg)
