"""Unit coverage for services/raster_redesign_generation.py.

Verifies the instruction-routing rule: only a page whose analysed
``render_mode`` is "redesign" receives the caller's generation instruction;
"reconstruct" and "full_page_preserve" pages render exactly as before.

Depends on agent B's ``instruction`` parameter on
``render_raster_page_svg`` (services/source_content/raster_svg_layout.py),
already landed, and on RasterAnalysis/RasterPage accepting a "redesign"
render_mode (agent A's raster_contracts.py addition). Skips cleanly until
the contract change lands.
"""
from __future__ import annotations

import pytest

from services.raster_redesign_generation import (
    raster_measure_via_materialized_admission, raster_measurement_inputs,
    raster_render_advisories_for_page, raster_required_text_for_page,
    raster_source_font_profile, render_raster_reconstruction_page_svg,
)


class _FakePin:
    def __init__(self, approved_region_ids, *, full_page_preserve_pages=()):
        self.approved_region_ids = approved_region_ids
        self.full_page_preserve_pages = full_page_preserve_pages


class _FakePage:
    def __init__(self, render_mode, *, page_number=1, regions=()):
        self.render_mode = render_mode
        self.page_number = page_number
        self.regions = regions


class _FakeRegion:
    def __init__(self, region_id, role, text, **overrides):
        self.region_id = region_id
        self.role = role
        self.text = text
        for key, value in overrides.items():
            setattr(self, key, value)


class _FakeAnalysis:
    def __init__(self, pages):
        self.pages = pages


@pytest.fixture(autouse=True)
def stub_render_raster_page_svg(monkeypatch):
    calls = []

    def _fake(analysis, page_number, *, style_id, approved_region_ids, **kwargs):
        calls.append({
            "page_number": page_number, "style_id": style_id,
            "approved_region_ids": tuple(approved_region_ids), **kwargs,
        })
        return "<svg/>"

    monkeypatch.setattr(
        "services.raster_redesign_generation.render_raster_page_svg", _fake,
    )
    return calls


def test_redesign_page_receives_instruction(stub_render_raster_page_svg):
    analysis = _FakeAnalysis([_FakePage("redesign")])
    pin = _FakePin(("p1-r1",))
    render_raster_reconstruction_page_svg(
        analysis, pin, 1, style_id="screenshot-blue", instructions="더 간결하게",
    )
    assert stub_render_raster_page_svg[0]["instruction"] == "더 간결하게"


@pytest.mark.parametrize("render_mode", ["reconstruct", "full_page_preserve"])
def test_non_redesign_pages_never_receive_instruction(stub_render_raster_page_svg, render_mode):
    analysis = _FakeAnalysis([_FakePage(render_mode)])
    pin = _FakePin(("p1-r1",))
    render_raster_reconstruction_page_svg(
        analysis, pin, 1, style_id="screenshot-blue", instructions="더 간결하게",
    )
    assert "instruction" not in stub_render_raster_page_svg[0]


def test_approved_region_ids_and_style_pass_through(stub_render_raster_page_svg):
    analysis = _FakeAnalysis([_FakePage("redesign")])
    pin = _FakePin(("p1-r1", "p1-r2"))
    render_raster_reconstruction_page_svg(
        analysis, pin, 1, style_id="screenshot-blue", instructions=None,
    )
    call = stub_render_raster_page_svg[0]
    assert call["approved_region_ids"] == ("p1-r1", "p1-r2")
    assert call["style_id"] == "screenshot-blue"
    assert call["instruction"] is None


def test_raster_render_advisories_for_page_delegates_and_lists(monkeypatch):
    calls = []

    def _fake_advisories(analysis, page_number, svg):
        calls.append((analysis, page_number, svg))
        return ({"code": "dense_group_pixel_fallback", "groupId": "g1",
                  "regionId": "p1-r9", "replacedRegionIds": ["p1-r1", "p1-r2"]},)

    monkeypatch.setattr(
        "services.raster_redesign_generation.raster_page_render_advisories", _fake_advisories,
    )
    analysis = _FakeAnalysis([_FakePage("redesign")])
    result = raster_render_advisories_for_page(analysis, 1, "<svg/>")
    assert calls == [(analysis, 1, "<svg/>")]
    assert result == [{"code": "dense_group_pixel_fallback", "groupId": "g1",
                        "regionId": "p1-r9", "replacedRegionIds": ["p1-r1", "p1-r2"]}]
    assert isinstance(result, list)


# --- raster_source_font_profile --------------------------------------------

def test_source_font_profile_pinned_only_for_redesign():
    from utils.svg_template_fonts import trusted_font_marker

    analysis = _FakeAnalysis([_FakePage("redesign")])
    assert raster_source_font_profile(analysis, 1) == trusted_font_marker()


@pytest.mark.parametrize("render_mode", ["reconstruct", "full_page_preserve"])
def test_source_font_profile_none_for_non_redesign(render_mode):
    analysis = _FakeAnalysis([_FakePage(render_mode)])
    assert raster_source_font_profile(analysis, 1) is None


# --- raster_required_text_for_page ------------------------------------------

def test_required_text_excludes_dense_group_fallback_replaced_regions(monkeypatch):
    monkeypatch.setattr(
        "services.raster_redesign_generation.raster_page_render_advisories",
        lambda analysis, page_number, svg: (
            {"code": "dense_group_pixel_fallback", "groupId": "g1",
             "regionId": "p1-r9", "replacedRegionIds": ["p1-r2"]},
        ),
    )
    regions = (
        _FakeRegion("p1-r1", "title", "제목"),
        _FakeRegion("p1-r2", "body", "픽셀로 대체된 본문"),
        _FakeRegion("p1-r3", "body", "일반 본문"),
        _FakeRegion("p1-r4", "screenshot", None),
    )
    page = _FakePage("redesign", page_number=1, regions=regions)
    analysis = _FakeAnalysis([page])
    pin = _FakePin(("p1-r1", "p1-r2", "p1-r3", "p1-r4"))
    result = raster_required_text_for_page(analysis, pin, 1, "<svg/>")
    assert result == ["제목", "일반 본문"]


def test_required_text_only_approved_regions():
    regions = (_FakeRegion("p1-r1", "title", "승인됨"), _FakeRegion("p1-r2", "title", "미승인"))
    page = _FakePage("redesign", page_number=1, regions=regions)
    analysis = _FakeAnalysis([page])
    pin = _FakePin(("p1-r1",))
    assert raster_required_text_for_page(analysis, pin, 1, "<svg/>") == ["승인됨"]


def test_required_text_empty_for_full_page_preserve():
    page = _FakePage("full_page_preserve", page_number=1, regions=(_FakeRegion("p1-r1", "title", "무시됨"),))
    analysis = _FakeAnalysis([page])
    pin = _FakePin(("p1-r1",))
    assert raster_required_text_for_page(analysis, pin, 1, "<svg/>") == []


# --- raster_measure_via_materialized_admission ------------------------------

def test_measure_via_materialized_admission_true_for_redesign_with_image():
    analysis = _FakeAnalysis([_FakePage("redesign", page_number=1)])
    pin = _FakePin(())
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><image href="raster:p1-r1"/></svg>'
    assert raster_measure_via_materialized_admission(analysis, pin, 1, svg) is True


def test_measure_via_materialized_admission_false_for_text_only_redesign_page():
    """A page with no <image> at all has no expanded base64 to admit."""
    analysis = _FakeAnalysis([_FakePage("redesign", page_number=1)])
    pin = _FakePin(())
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><text>hello</text></svg>'
    assert raster_measure_via_materialized_admission(analysis, pin, 1, svg) is False


def test_measure_via_materialized_admission_full_page_preserve_gated_on_pin():
    analysis = _FakeAnalysis([_FakePage("full_page_preserve", page_number=1)])
    svg = "<svg/>"
    assert raster_measure_via_materialized_admission(analysis, _FakePin(()), 1, svg) is False
    assert raster_measure_via_materialized_admission(
        analysis, _FakePin((), full_page_preserve_pages=(1,)), 1, svg,
    ) is True


def test_measure_via_materialized_admission_false_for_reconstruct():
    analysis = _FakeAnalysis([_FakePage("reconstruct", page_number=1)])
    assert raster_measure_via_materialized_admission(analysis, _FakePin(()), 1, "<svg/>") is False


# --- raster_measurement_inputs bundling -------------------------------------

def test_measurement_inputs_bundles_all_three(monkeypatch):
    monkeypatch.setattr(
        "services.raster_redesign_generation.raster_page_render_advisories",
        lambda analysis, page_number, svg: (),
    )
    regions = (_FakeRegion("p1-r1", "title", "제목"),)
    page = _FakePage("redesign", page_number=1, regions=regions)
    analysis = _FakeAnalysis([page])
    pin = _FakePin(("p1-r1",))
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><image href="raster:p1-r1"/></svg>'
    required_text, allow_image_only, font_profile = raster_measurement_inputs(analysis, pin, 1, svg)
    assert required_text == ["제목"]
    assert allow_image_only is True
    assert font_profile == raster_source_font_profile(analysis, 1)
