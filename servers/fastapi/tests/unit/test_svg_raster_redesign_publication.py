"""Regression coverage for the dead-lettered publish D hit on real 28-page runs.

publish_presentation_revision -> refresh_svg_page_numbers used to run every
raster redesign page through finalize_svg + the generic asset rebind/measure
pipeline (svg_document_mutations.py, the same non-authorized render_svg_with_
metrics path the generation-gate fix already closed). A redesign page's
footer (raster_redesign_layout.py, PAGE_NUMBER_ID) is already baked in at
generation time, so finalize_svg both corrupts its styling and re-measures a
multi-crop materialized SVG through the 2MB canonical limit -- exactly the
svg_contract_size_limit dead-letter. _raster_redesign_pin now makes
refresh_svg_page_numbers skip that pipeline entirely for an unreordered
redesign page.
"""
from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import svg_document_mutations as mutations
from services.source_content.raster_contracts import RasterApprovalPin, seal_approval
from services.svg_contract import source_digest
from services.svg_export_page_evidence import svg_authoring, svg_geometry_font
from services.svg_renderer import SVG_RENDERER_VERSION
from services.svg_contract import SVG_CONTRACT_VERSION


def _pin(**overrides):
    payload = dict(
        operation_id=uuid.uuid4(), analysis_id=uuid.uuid4(), analysis_revision=1,
        analysis_digest="a" * 64, source_sha256="b" * 64,
        approved_region_ids=("p1-r1", "p1-r2"),
        automatic_redesign_pages=(1,),
    )
    payload.update(overrides)
    return seal_approval(RasterApprovalPin(**payload))


def _redesign_slide(index: int, pin: RasterApprovalPin, *, page_number: int, svg: str | None = None):
    svg = svg or f'<svg xmlns="http://www.w3.org/2000/svg"><text x="0" y="10">page {index}</text></svg>'
    return SimpleNamespace(
        index=index, svg_content=svg, is_svg=lambda: True,
        content={"svgReceipt": {
            "rasterApprovalPin": pin.model_dump(mode="json"),
            "rasterAnalysisDigest": pin.analysis_digest,
            "rasterSourcePageNumber": page_number,
            "quality": {"source_digest": source_digest(svg)},
        }},
        properties={},
    )


def _presentation(slide_count: int):
    return SimpleNamespace(
        id="presentation", is_svg=lambda: True,
        theme={"svg": {"styleSnapshot": {"tokens": {}}}},
    )


def _session(slides):
    return SimpleNamespace(flush=AsyncMock(), scalars=AsyncMock(return_value=slides), add=lambda _row: None)


def _forbidden(name):
    def raise_if_called(*args, **kwargs):
        raise AssertionError(f"{name} must not run for an unreordered redesign page")
    return raise_if_called


def test_redesign_page_skips_finalize_and_measurement_even_when_oversized(monkeypatch):
    """The exact D scenario: a multi-crop redesign page whose materialized SVG
    would exceed the 2MB canonical limit if it were ever measured. It must
    never reach finalize_svg, read_svg_slide_assets or render_svg_with_metrics."""
    pin = _pin()
    slide = _redesign_slide(0, pin, page_number=1)
    original_svg = slide.svg_content
    session = _session([slide])
    canvas = SimpleNamespace(width_px=1188, height_px=840)
    monkeypatch.setattr(mutations, "presentation_canvas_contract", lambda _theme: canvas)
    monkeypatch.setattr(mutations, "finalize_svg", _forbidden("finalize_svg"))
    monkeypatch.setattr(
        mutations, "read_svg_slide_assets", AsyncMock(side_effect=_forbidden("read_svg_slide_assets")))
    monkeypatch.setattr(
        mutations, "render_svg_with_metrics", AsyncMock(side_effect=_forbidden("render_svg_with_metrics")))

    asyncio.run(mutations.refresh_svg_page_numbers(session, _presentation(1)))

    # Untouched: no finalize, no re-render, no size limit ever evaluated.
    assert slide.svg_content == original_svg


def test_redesign_page_reorder_renumbers_footer_without_generic_pipeline(monkeypatch):
    """A moved redesign page shows its new deck position; its source page
    (provenance) is unchanged; the changed page is remeasured without finalize."""
    pin = _pin()
    footer = ('<svg xmlns="http://www.w3.org/2000/svg" id="svg-raster-redesign" width="1188" height="840" '
              'viewBox="0 0 1188 840"><text x="10" y="40" font-family="Noto Sans KR" font-size="20" '
              'fill="#000000">본문</text><text id="svg-system-page-number" x="1140" y="815" '
              'font-family="Noto Sans KR" font-size="14.00" fill="#536785" text-anchor="end">01</text></svg>')
    slide = _redesign_slide(1, pin, page_number=1, svg=footer)
    slide.content["svgAssetManifest"] = {"sourcePageIndex": 0}
    from services import svg_raster_renumber

    monkeypatch.setattr(svg_raster_renumber, "_rebind_screen_placement",
                        AsyncMock(side_effect=lambda *a, **k: ({**a[4], "rebound": True}, None)))
    canvas = SimpleNamespace(width_px=1188, height_px=840)
    monkeypatch.setattr(mutations, "presentation_canvas_contract", lambda _theme: canvas)
    monkeypatch.setattr(mutations, "finalize_svg", _forbidden("finalize_svg"))
    monkeypatch.setattr(
        mutations, "read_svg_slide_assets", AsyncMock(side_effect=_forbidden("read_svg_slide_assets")))
    capture = AsyncMock(side_effect=lambda svg, **kwargs: SimpleNamespace(png=b"png", quality={
        "source_digest": source_digest(svg), "renderer_version": SVG_RENDERER_VERSION,
        "contract_version": SVG_CONTRACT_VERSION, "measurement_complete": True,
        "geometry_passed": True, "passed": True,
        "trusted_font_materialization": {"worker_materialized": True},
    }))
    monkeypatch.setattr("services.svg_renderer.render_svg_with_metrics", capture)
    monkeypatch.setattr("services.authored_presentation_artifacts._save_slide_pngs", lambda *_args: ["numbered.png"])

    # slide.index 1 but deck position 0 in this one-slide deck; then move to position 2.
    two = _redesign_slide(0, _pin(approved_region_ids=("p2-r1",)), page_number=1, svg=footer)
    two.content["svgAssetManifest"] = {"sourcePageIndex": 0}
    session = _session([two, slide])
    asyncio.run(mutations.refresh_svg_page_numbers(session, _presentation(2)))

    assert slide.index == 1 and ">02</text>" in slide.svg_content
    assert slide.content["svgReceipt"]["rasterSourcePageNumber"] == 1
    assert slide.content["svgReceipt"]["rasterDisplayPageNumber"] == 2
    assert two.svg_content == footer  # already at its position: untouched
    assert slide.content["svgAssetManifest"]["rebound"] is True  # new screen placement, same source page
    assert slide.content["svgAssetManifest"]["sourcePageIndex"] == 0
    capture.assert_awaited_once()
    assert capture.await_args.args[0] == slide.svg_content
    assert slide.properties["image"] == "numbered.png"
    for checker in (svg_authoring, svg_geometry_font):
        assert checker([slide])["status"] == "passed"

def test_raster_redesign_pin_helper_selects_only_redesign_pages():
    redesign_pin = _pin(automatic_redesign_pages=(1,), full_page_preserve_pages=())
    preserve_pin = _pin(automatic_redesign_pages=(), full_page_preserve_pages=(1,))
    redesign_slide = _redesign_slide(0, redesign_pin, page_number=1)
    preserve_slide = _redesign_slide(0, preserve_pin, page_number=1)
    plain_slide = SimpleNamespace(index=0, svg_content="<svg/>", is_svg=lambda: True, content={})

    assert mutations._raster_redesign_pin(redesign_slide) is not None
    assert mutations._raster_redesign_pin(preserve_slide) is None
    assert mutations._raster_redesign_pin(plain_slide) is None
    assert mutations._raster_preserve_pin(redesign_slide) is None
    assert mutations._raster_preserve_pin(preserve_slide) is not None


def test_raster_pin_rejects_tampered_binding():
    pin = _pin()
    slide = _redesign_slide(0, pin, page_number=1)
    tampered = deepcopy(slide.content)
    tampered["svgReceipt"]["rasterAnalysisDigest"] = "c" * 64
    slide.content = tampered
    with pytest.raises(ValueError, match="raster_publication_page_binding_invalid"):
        mutations._raster_pin(slide)
