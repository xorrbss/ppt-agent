"""Raster redesign pages through the REAL SVG export path, offline.

publish (prepare_raster_page_assets) -> export assets (build_raster_page_export_assets)
-> svg_export_operation.render_and_export_svgs -> svg_export_assets.prepare_export_assets
-> validate_export_placement -> isolated worker execute_svg_export (materialize_export_assets,
convert_pptx / browser PDF). Only the artifact store, the worker object cache and the
isolated-process launcher are replaced by in-memory equivalents; everything between is
production code.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace

import pytest

import test_raster_redesign_layout_assets as assets_test
from services import svg_export_operation
from services.document_canvas import document_canvas_for_orientation
from services.isolated_worker_contracts import ArtifactRef as WorkerRef
from services.source_content.raster_contracts import RasterApprovalPin
from services.source_content.raster_svg_layout import render_raster_page_svg
from services.svg_asset_persistence import SvgPublicationContext
from services import raster_source_assets as assets
from utils.slide_capture import find_chrome


class ExportArtifacts(assets_test.MemoryArtifacts):
    """In-memory artifact authority that also serves the export operation."""

    async def register_consumer_ref(self, session, reference, *, created_by_operation_id, **_):
        return self.put(self.blobs[reference.artifact_id], reference.kind, reference.media_type)

    async def stream_ref_to_object_store(self, reference, cache):
        cache.blobs[reference.artifact_id] = self.blobs[reference.artifact_id]

    async def register_ref(self, session, reference):
        return reference


class WorkerStore:
    """The isolated worker's content-addressed store, backed by the same bytes."""

    def __init__(self, artifacts: ExportArtifacts, tmp_path):
        self.artifacts, self.tmp_path = artifacts, tmp_path
        self.blobs = {}

    async def maintain(self):
        return None

    def maintenance_batch(self):
        return nullcontext()

    def read(self, ref):
        payload = self.blobs[ref.artifact_id]
        assert hashlib.sha256(payload).hexdigest() == ref.sha256
        return payload

    def verified_path(self, ref):
        path = self.tmp_path / f"{ref.artifact_id}.bin"
        path.write_bytes(self.read(ref))
        return path

    def put(self, payload, *, source, operation_id, kind, media_type):
        public = self.artifacts.put(payload, kind, media_type)
        ref = WorkerRef.from_wire(public.model_dump(mode="json", by_alias=True))
        self.blobs[ref.artifact_id] = payload
        return ref

    def put_path(self, path, **kwargs):
        return self.put(path.read_bytes(), **kwargs)


def install_export_fakes(monkeypatch, artifacts: ExportArtifacts, tmp_path):
    from workers.public_svg_export_worker import execute_svg_export

    store = WorkerStore(artifacts, tmp_path)

    async def launch(claim, *, input_artifact, deadline_seconds=None):
        job = SimpleNamespace(input_artifact=input_artifact, operation_id=claim.operation_id,
                              deadline_epoch_seconds=time.time() + 600)
        output = await execute_svg_export(job, store)
        return SimpleNamespace(ok=True, error_code=None, output_artifact=output.artifact.to_wire(), role="export",
                               resource_metrics={}, execution_evidence=dict(output.evidence))

    async def no_grant(*_args, **_kwargs):
        return None

    monkeypatch.setattr(svg_export_operation, "configured_public_artifact_service", lambda: artifacts)
    monkeypatch.setattr(svg_export_operation, "configured_worker_artifact_cache", lambda: store)
    monkeypatch.setattr(svg_export_operation, "execute_isolated_export_operation", launch)
    monkeypatch.setattr(svg_export_operation, "load_public_export_deadline_grant", no_grant)
    return store


@asynccontextmanager
async def _session():
    yield SimpleNamespace(commit=_noop)


async def _noop():
    return None


async def export_pages(analysis, artifacts, svgs, *, export_format, owner, tenant, operation):
    """Publish each page's assets, then run the real export operation."""
    approved = tuple(r.region_id for p in analysis.pages for r in p.regions)
    pin = RasterApprovalPin(
        operation_id=operation, analysis_id=analysis.analysis_id, analysis_revision=analysis.revision,
        analysis_digest=analysis.digest, source_sha256=analysis.source.source_sha256, approved_region_ids=approved,
        automatic_redesign_pages=tuple(p.page_number for p in analysis.pages
                                       if p.approval_method == "automatic_redesign"),
        full_page_preserve_pages=tuple(p.page_number for p in analysis.pages if p.render_mode == "full_page_preserve"),
        automatic_preserve_pages=tuple(p.page_number for p in analysis.pages
                                       if p.approval_method == "automatic_preserve"),
    )
    context = SvgPublicationContext(operation_id=operation, owner_id=owner, tenant_id=tenant, attempt_number=1,
                                    attempt_token="t", fencing_token=1)
    presentation = uuid.uuid4()
    bindings = []
    for index, (number, svg) in enumerate(svgs):
        prepared = await assets.prepare_raster_page_assets(
            None, artifacts, approval_pin=pin, owner_id=owner, tenant_id=tenant, publication_context=context,
            presentation_id=presentation, source_revision=1, slide_revision=1, page_number=number,
            output_page_index=index, canonical_svg=svg, width=1188, height=840,
        )
        bindings.append(await assets.build_raster_page_export_assets(
            None, artifacts, approval_pin=pin, owner_id=owner, tenant_id=tenant, publication_context=context,
            presentation_id=presentation, source_pointer=prepared.source_pointer, slide_revision=1,
            page_number=number, output_page_index=index, canonical_svg=svg, width=1188, height=840,
            export_format=export_format,
        ))
    claim = SimpleNamespace(owner_id=owner, kind=f"export.{export_format}", operation_id=operation,
                            expected_revision=1, presentation_id=presentation)
    canvas = document_canvas_for_orientation("landscape").model_dump(mode="json", by_alias=True)
    result = await svg_export_operation.render_and_export_svgs(
        claim, tenant_id=tenant, svgs=[svg for _, svg in svgs], notes=[""] * len(svgs),
        export_format=export_format, canvas=canvas, session_factory=_session, asset_bindings=bindings,
    )
    # The operation re-identifies the worker output; its bytes are found by digest.
    payload = next(artifacts.blobs[ref.artifact_id] for ref in artifacts.refs.values()
                   if ref.sha256 == result.artifact.sha256)
    return result, payload


@pytest.mark.parametrize("export_format", ["pptx", pytest.param("pdf", marks=pytest.mark.skipif(
    find_chrome() is None, reason="headless Chrome is required"))])
def test_dense_fallback_page_exports_through_the_real_export_operation(monkeypatch, tmp_path, export_format):
    artifacts = ExportArtifacts()
    analysis = assets_test._dense_page_analysis(artifacts, brand=True)
    assets_test._install_fakes(monkeypatch, artifacts, analysis)
    install_export_fakes(monkeypatch, artifacts, tmp_path)
    approved = tuple(r.region_id for r in analysis.pages[0].regions)
    svg = render_raster_page_svg(analysis, 1, approved_region_ids=approved)
    result, payload = asyncio.run(export_pages(
        analysis, artifacts, [(1, svg)], export_format=export_format,
        owner=assets_test.OWNER, tenant=assets_test.TENANT, operation=assets_test.OPERATION,
    ))
    assert result.artifact.kind == f"presentation-{export_format}"
    if export_format == "pptx":
        import io

        from pptx import Presentation

        deck = Presentation(io.BytesIO(payload))
        pictures = [s for s in deck.slides[0].shapes if s.shape_type == 13]
        assert len(pictures) == svg.count("<image ")
    else:
        assert payload.startswith(b"%PDF")


def test_placements_follow_canonical_svg_dom_order():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
           '<image x="1" y="1" width="10" height="10" href="source-slot-b" preserveAspectRatio="xMidYMid meet"/>'
           '<image x="20" y="1" width="10" height="10" href="source-slot-a" preserveAspectRatio="xMidYMid meet"/>'
           '<text x="1" y="50" font-family="Noto Sans KR" font-size="14" fill="#000000">t</text></svg>')
    boxes = assets._canonical_boxes(svg, width=100, height=100, expected_slots={"source-slot-a", "source-slot-b"})
    assert list(boxes) == ["source-slot-b", "source-slot-a"]
