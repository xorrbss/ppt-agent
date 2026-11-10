"""Reorder of a multi-crop raster-redesign page: display number follows the deck.

Real publication (prepare_raster_page_assets) with an oversized (>2 MB when
materialized) page, then renumber_redesign_page with the real placement
rebind and Chromium remeasurement, then the real export asset path.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import test_raster_redesign_layout_assets as assets_test
import test_raster_redesign_layout_export_path as export_test
from services import raster_source_assets as assets
from services import svg_asset_persistence, svg_raster_renumber
from services.source_content.raster_contracts import RasterApprovalPin
from services.source_content.raster_svg_layout import render_raster_page_svg
from services.svg_asset_manifest import PlacementManifest, SourceManifest
from services.svg_asset_persistence import SvgPublicationContext
from services.svg_contract import PAGE_NUMBER_ID, source_digest, validate_svg
from services.svg_export_page_evidence import svg_authoring, svg_geometry_font


def test_reorder_of_oversized_redesign_page_renumbers_and_still_exports(monkeypatch, tmp_path):
    store = export_test.ExportArtifacts()
    analysis = assets_test._dense_page_analysis(store, brand=True, noise=True)
    assets_test._install_fakes(monkeypatch, store, analysis)
    export_test.install_export_fakes(monkeypatch, store, tmp_path)
    approved = tuple(r.region_id for r in analysis.pages[0].regions)
    svg = render_raster_page_svg(analysis, 1, approved_region_ids=approved)
    pin = RasterApprovalPin(
        operation_id=assets_test.OPERATION, analysis_id=analysis.analysis_id,
        analysis_revision=analysis.revision, analysis_digest=analysis.digest,
        source_sha256=assets_test.SHA, approved_region_ids=approved, automatic_redesign_pages=(1,),
    )
    context = SvgPublicationContext(operation_id=assets_test.OPERATION, owner_id=assets_test.OWNER,
                                    tenant_id=assets_test.TENANT, attempt_number=1, attempt_token="t",
                                    fencing_token=1)
    presentation_id = uuid.uuid4()
    prepared = asyncio.run(assets.prepare_raster_page_assets(
        None, store, approval_pin=pin, owner_id=assets_test.OWNER, tenant_id=assets_test.TENANT,
        publication_context=context, presentation_id=presentation_id, source_revision=1, slide_revision=1,
        page_number=1, output_page_index=0, canonical_svg=svg, width=1188, height=840,
    ))
    assert len(prepared.materialized_svg.encode()) > 2 * 1024 * 1024  # the oversized case

    slide = SimpleNamespace(index=0, svg_content=svg, content={
        "svgAssetManifest": prepared.screen_envelope,
        "svgReceipt": {"rasterApprovalPin": pin.model_dump(mode="json"), "rasterSourcePageNumber": 1,
                       "rasterAnalysisDigest": pin.analysis_digest, "source_digest": "old",
                       "quality": {"source_digest": source_digest(svg)}},
    })
    presentation = SimpleNamespace(id=presentation_id, owner_id=assets_test.OWNER, revision=4)

    async def load(model, session, service, *, pointer, owner_id, tenant_id):
        return model.model_validate_json(store.blobs[uuid.UUID(str(pointer["ref"]))])

    monkeypatch.setattr(svg_asset_persistence, "load_source_manifest_pointer",
                        lambda *a, **k: load(SourceManifest, *a, **k))
    monkeypatch.setattr(svg_asset_persistence, "load_placement_manifest_pointer",
                        lambda *a, **k: load(PlacementManifest, *a, **k))
    monkeypatch.setattr(svg_raster_renumber, "_fenced_operation",
                        AsyncMock(return_value=(assets_test.OWNER, assets_test.TENANT)))
    monkeypatch.setattr("services.slide_image_sets._save_slide_pngs",
                        lambda *_args: [str(tmp_path / "numbered.png")])

    changed = asyncio.run(svg_raster_renumber.renumber_redesign_page(
        None, presentation, slide, 11, width=1188, height=840, operation_id=uuid.uuid4(),
        attempt_token="a", expected_revision=4, service=store,
    ))
    assert changed
    assert f'<text id="{PAGE_NUMBER_ID}"' in slide.svg_content and ">12</text>" in slide.svg_content
    assert slide.svg_content.replace(">12</text>", ">01</text>") == svg  # only the digits changed
    validate_svg(slide.svg_content, width=1188, height=840,
                 source_manifest={s.slot_id: {"required": True, "allowOmit": False}
                                  for s in prepared.source_manifest.slots})
    envelope = slide.content["svgAssetManifest"]
    assert envelope["sourceManifest"] == prepared.screen_envelope["sourceManifest"]  # crops/slots unchanged
    assert envelope["sourcePageIndex"] == 0 and envelope["slideRevision"] == 5
    receipt = slide.content["svgReceipt"]
    assert receipt["rasterSourcePageNumber"] == 1 and receipt["rasterDisplayPageNumber"] == 12
    assert receipt["quality"]["source_digest"] == source_digest(slide.svg_content)
    assert receipt["quality"]["measurement_complete"] is True
    for checker in (svg_authoring, svg_geometry_font):
        assert "stale" not in checker([slide]).get("reasonCode", "")
        assert "missing" not in checker([slide]).get("reasonCode", "")

    monkeypatch.undo()  # export uses the real resolver/renderer paths again
    assets_test._install_fakes(monkeypatch, store, analysis)
    export_test.install_export_fakes(monkeypatch, store, tmp_path)
    binding = asyncio.run(assets.build_raster_page_export_assets(
        None, store, approval_pin=pin, owner_id=assets_test.OWNER, tenant_id=assets_test.TENANT,
        publication_context=context, presentation_id=presentation_id,
        source_pointer=envelope["sourceManifest"], slide_revision=envelope["slideRevision"], page_number=1,
        output_page_index=envelope["sourcePageIndex"], canonical_svg=slide.svg_content,
        width=1188, height=840, export_format="pptx",
    ))
    claim = SimpleNamespace(owner_id=assets_test.OWNER, kind="export.pptx", operation_id=assets_test.OPERATION,
                            expected_revision=5, presentation_id=presentation_id)
    from services import svg_export_operation
    from services.document_canvas import document_canvas_for_orientation

    result = asyncio.run(svg_export_operation.render_and_export_svgs(
        claim, tenant_id=assets_test.TENANT, svgs=[slide.svg_content], notes=[""], export_format="pptx",
        canvas=document_canvas_for_orientation("landscape").model_dump(mode="json", by_alias=True),
        session_factory=export_test._session, asset_bindings=[binding],
    ))
    assert result.artifact.kind == "presentation-pptx"


def test_relabel_keeps_everything_but_the_digits():
    from services.svg_raster_renumber import display_page_number, relabel_page_number

    svg = (f'<svg><text id="{PAGE_NUMBER_ID}" x="1140" y="815" text-anchor="end">07</text></svg>')
    assert display_page_number(svg) == 7
    assert relabel_page_number(svg, 123) == svg.replace(">07<", ">123<")
    with pytest.raises(ValueError, match="raster_redesign_footer_missing"):
        relabel_page_number("<svg/>", 3)
