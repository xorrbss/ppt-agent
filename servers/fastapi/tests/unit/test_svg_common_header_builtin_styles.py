"""The uniform-header preflight succeeds for every built-in authored style.

A Pretendard-heading style (devax-si-proposal) used to fail every uniform
deck in preflight with svg_common_header_font_material_invalid: the reduced
header took the heading face, whose Chromium space advance was not measured.
The space advance is now measured on the renderer for every bundled header
face, so the heading face is kept; Noto Sans KR stands in only for a face the
renderer could not measure.
"""

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services import svg_common_header_design_plan as policy
from services import svg_common_header_space_advance as space_advance
from services.authored_canvas import request_canvas_metadata
from services.presentation_generation_staging import _durable_request_payload
from services.svg_common_header import (
    MEASUREMENT_VERSION,
    prepare_common_header,
    validate_common_header_binding,
)
from utils.slide_capture import find_chrome
from utils.authored_styles import AUTHORED_STYLES_DIRECTORY, load_authored_styles
from utils.hashing import canonical_json_digest

pytestmark = pytest.mark.skipif(find_chrome() is None, reason="headless Chrome measures the space advance")

_STYLE_FILES = sorted(path.stem for path in AUTHORED_STYLES_DIRECTORY.glob("*.yaml"))
_TITLES = ("2026년 국내 전기차 시장 분석", "Market share and pricing trend", "가격대별 판매 비중과 향후 전망")


def test_every_style_file_is_a_loaded_style():
    # Otherwise a style skipped by the loader would silently leave this suite.
    assert _STYLE_FILES
    assert sorted(style.id for style in load_authored_styles()) == _STYLE_FILES


@pytest.mark.parametrize("style_id", _STYLE_FILES)
def test_uniform_header_preflight_passes_for_builtin_style(style_id):
    request = GeneratePresentationRequest(content="Operations", template="authored", strategy="svg",
        common_header_mode="uniform", authored_style=style_id,
        slides_markdown=["# " + title for title in _TITLES])
    # The actual admission path, which runs preflight_admitted_header.
    _durable_request_payload(request)
    snapshot = request._svg_style_snapshot
    plan = snapshot["common_header_design_plan"]
    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="# " + title, title=title, page_id=canonical_json_digest({"index": index}))
        for index, title in enumerate(_TITLES)])
    canvas = request_canvas_metadata(request)

    policy.preflight_admitted_header(snapshot, request, canvas)
    outcome = policy.reduced_header_outcome(plan, request, snapshot, outline, canvas)

    assert outcome["mode"] == "reduced"
    contract = outcome["contract"]
    assert contract["measurement_version"] == MEASUREMENT_VERSION
    measured = {(face["family"], face["weight"], face["digest"]) for face in contract["space_advance"]["faces"]}
    assert (contract["font"]["family"], contract["font"]["weight"], contract["font"]["digest"]) in measured
    for index, binding in enumerate(outcome["bindings"]):
        validate_common_header_binding(contract, binding, index=index, count=len(_TITLES),
            content=outline.slides[index].content, title=outline.slides[index].title)


def _outline():
    return PresentationOutlineModel(slides=[SlideOutlineModel(content="# Title", title="Title", page_id="p")])


def test_pretendard_heading_keeps_its_face_once_the_renderer_measured_it():
    contract, _ = prepare_common_header({"tokens": {"heading_font": "Pretendard, sans-serif"}},
                                        _outline(), {"width": 1280, "height": 720})
    assert contract["font"]["family"] == "Pretendard"
    assert contract["space_advance"]["faces"][0]["family"] == "Pretendard"


@pytest.fixture
def pretendard_unmeasured(monkeypatch):
    """This renderer's measurement with the Pretendard faces rejected by the probe."""
    measurement = space_advance._current_measurement()
    faces = {key: entry for key, entry in measurement["faces"].items() if key[0] != "Pretendard"}
    monkeypatch.setitem(space_advance._measurements, measurement["renderer"]["digest"],
                        {**measurement, "faces": faces})


def test_unmeasured_pretendard_heading_takes_the_measured_noto_header_face(pretendard_unmeasured):
    contract, _ = prepare_common_header({"tokens": {"heading_font": "Pretendard, sans-serif"}},
                                        _outline(), {"width": 1280, "height": 720})
    noto, _ = prepare_common_header({"tokens": {"heading_font": "Noto Sans KR"}},
                                    _outline(), {"width": 1280, "height": 720})
    assert contract["font"] == noto["font"]
    assert contract["font"]["family"] == "Noto Sans KR"


def test_an_explicit_unmeasured_header_face_still_fails_with_its_cause(pretendard_unmeasured):
    with pytest.raises(ValueError, match="svg_common_header_font_material_invalid"):
        prepare_common_header({"tokens": {}, "common_header": {"font_family": "Pretendard"}},
                              _outline(), {"width": 1280, "height": 720})
