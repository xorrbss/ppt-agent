"""Wire-level checks for the "auto_redesign" analysis mode.

These exercise only the pieces that do not depend on agent A's
``analyze_redesign_pptx`` / raster_contracts additions landing: the public
request models this task owns. Routing/behavioral coverage for the
operation handler and svg_presentation_service lives in
test_raster_analysis_operation_handler_modes.py and
test_raster_redesign_generation.py, which do depend on that landing.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from api.v1.ppt.endpoints.raster_reconstruction import AnalyzeRasterRequest
from models.generate_presentation_request import (
    GeneratePresentationRequest, RasterApprovalPinInput,
)


def test_analyze_raster_request_defaults_to_auto_redesign():
    request = AnalyzeRasterRequest(
        reference_snapshot_id="11111111-1111-1111-1111-111111111111",
        presentation_id="22222222-2222-2222-2222-222222222222",
    )
    assert request.analysis_mode == "auto_redesign"
    assert request.orientation == "portrait"


@pytest.mark.parametrize("mode", ["auto_preserve", "reconstruct", "auto_redesign"])
def test_analyze_raster_request_accepts_every_mode(mode):
    request = AnalyzeRasterRequest(
        reference_snapshot_id="11111111-1111-1111-1111-111111111111",
        presentation_id="22222222-2222-2222-2222-222222222222",
        analysis_mode=mode,
    )
    assert request.analysis_mode == mode


def test_analyze_raster_request_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        AnalyzeRasterRequest(
            reference_snapshot_id="11111111-1111-1111-1111-111111111111",
            presentation_id="22222222-2222-2222-2222-222222222222",
            analysis_mode="bogus_mode",
        )


def _pin_kwargs(**overrides):
    payload = dict(
        operation_id="11111111-1111-1111-1111-111111111111",
        analysis_id="22222222-2222-2222-2222-222222222222",
        analysis_revision=1,
        analysis_digest="a" * 64,
        source_sha256="b" * 64,
        approved_region_ids=("p1-r1",),
        approval_digest="c" * 64,
    )
    payload.update(overrides)
    return payload


def test_raster_approval_pin_input_accepts_automatic_redesign_pages():
    pin = RasterApprovalPinInput(**_pin_kwargs(automatic_redesign_pages=(1, 2, 3)))
    assert pin.automatic_redesign_pages == (1, 2, 3)


def test_raster_approval_pin_input_defaults_automatic_redesign_pages_empty():
    pin = RasterApprovalPinInput(**_pin_kwargs())
    assert pin.automatic_redesign_pages == ()


def test_raster_approval_pin_input_still_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        RasterApprovalPinInput(**_pin_kwargs(totally_unknown_field=True))


def _raster_reconstruction_request_kwargs(**overrides):
    """A minimal, otherwise-valid raster_reconstruction generate request.

    Negative control for the product requirement: an image-only source must
    be exportable as both A4 PPTX and A4 PDF, not pptx only.
    """
    payload = dict(
        content="",
        template="authored",
        authored_style="screenshot-blue",
        strategy="svg",
        page_size="A4",
        orientation="landscape",
        source_mode="raster_reconstruction",
        raster_approval_pin=RasterApprovalPinInput(**_pin_kwargs()),
        reference_snapshot_ids=["33333333-3333-3333-3333-333333333333"],
        include_title_slide=False,
        include_table_of_contents=False,
    )
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("export_as", ["pptx", "pdf"])
def test_raster_reconstruction_accepts_pptx_and_pdf_export(export_as):
    request = GeneratePresentationRequest(
        **_raster_reconstruction_request_kwargs(export_as=export_as)
    )
    assert request.export_as == export_as


def test_raster_reconstruction_rejects_other_export_formats():
    with pytest.raises(ValidationError):
        GeneratePresentationRequest(
            **_raster_reconstruction_request_kwargs(export_as="pptx")
            | {"web_search": True}
        )
