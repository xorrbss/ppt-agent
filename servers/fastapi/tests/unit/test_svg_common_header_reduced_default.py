"""Decision D1 (option C): the deterministic reduced header as the fresh default.

``SVG_COMMON_HEADER_DEFAULT`` (registered, default ``designed``) chooses the
header of a fresh prompt-only svg request that names no ``common_header_mode``.
``common_header_mode="reduced"`` asks for that header explicitly. Both admit as
"uniform" without a header design plan (no header model calls), default the
cover exception to true, and seal a reduced header style that adds a divider
and a page-position eyebrow. Unset, every default stays as before.
"""

import pytest

from models.generate_presentation_request import GeneratePresentationRequest
from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from models.public_v2_contracts import PresentationGenerationRequestV2
from services.authored_canvas import request_canvas_metadata
from services.presentation_generation_staging import _durable_request_payload
from services.svg_common_header import (
    REDUCED_HEADER_STYLE,
    REDUCED_HEADER_STYLE_KEY,
    header_snapshot_base,
    prepare_common_header,
    render_common_header,
    validate_common_header_binding,
)
from services.svg_common_header_design_plan import seal_new_header_design_plan
from utils.get_env_concurrency import get_svg_common_header_default_env
from utils.hashing import canonical_json_digest

_TITLES = ("중소기업 생성형 AI 도입 전략", "현황과 기대 효과", "단계별 로드맵")


def _request(**fields):
    return GeneratePresentationRequest(content="Operations", template="authored", strategy="svg",
        slides_markdown=["# " + title for title in _TITLES], **fields)


def _admit(request):
    payload = _durable_request_payload(request)
    return payload, request._svg_style_snapshot


def _outline():
    return PresentationOutlineModel(slides=[
        SlideOutlineModel(content="# " + title, title=title, page_id=canonical_json_digest({"index": index}))
        for index, title in enumerate(_TITLES)])


@pytest.fixture
def reduced_default(monkeypatch):
    monkeypatch.setenv("SVG_COMMON_HEADER_DEFAULT", "reduced")


@pytest.mark.parametrize("value, expected", [(None, "designed"), ("designed", "designed"),
                                             ("reduced", "reduced")])
def test_setting_reads_designed_unless_reduced(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("SVG_COMMON_HEADER_DEFAULT", raising=False)
    else:
        monkeypatch.setenv("SVG_COMMON_HEADER_DEFAULT", value)
    assert get_svg_common_header_default_env() == expected


def test_unset_setting_keeps_the_designed_header_default(monkeypatch):
    monkeypatch.delenv("SVG_COMMON_HEADER_DEFAULT", raising=False)
    payload, snapshot = _admit(_request())
    assert payload["common_header_mode"] == "uniform"
    assert payload["common_header_cover_exception"] is False
    assert "common_header_design_plan" in snapshot
    assert REDUCED_HEADER_STYLE_KEY not in snapshot


def test_reduced_default_admits_the_reduced_header_without_header_calls(reduced_default):
    request = _request()
    payload, snapshot = _admit(request)
    assert payload["common_header_mode"] == "uniform"
    assert payload["common_header_cover_exception"] is True
    assert "_internal_svg_header_design_plan" not in payload
    assert "common_header_design_plan" not in snapshot
    assert snapshot[REDUCED_HEADER_STYLE_KEY] == REDUCED_HEADER_STYLE
    assert snapshot["digest"] == canonical_json_digest(
        {key: value for key, value in snapshot.items() if key != "digest"}, allow_nan=True)
    # Admission is repeatable: a second seal never buys a design plan.
    assert seal_new_header_design_plan(request) is None


def test_reduced_header_style_draws_divider_and_page_position_eyebrow(reduced_default):
    request = _request()
    _, snapshot = _admit(request)
    outline = _outline()
    contract, bindings = prepare_common_header(snapshot, outline, request_canvas_metadata(request),
                                               cover_exception=request.common_header_cover_exception)
    assert contract["version"] == "svg-common-header/v1"
    assert contract["divider"]["enabled"] and contract["eyebrow"]["enabled"]
    assert contract["eyebrow"]["box"]["y"] < contract["title_box"]["y"]
    assert contract["divider"]["y"] < contract["content_top_px"]
    assert [binding["eyebrow"] for binding in bindings] == ["01 / 03", "02 / 03", "03 / 03"]
    assert [binding["applied"] for binding in bindings] == [False, True, True]
    for index, binding in enumerate(bindings):
        validate_common_header_binding(contract, binding, index=index, count=len(_TITLES),
            content=outline.slides[index].content, title=outline.slides[index].title)
    assert render_common_header(contract, bindings[0], index=0, count=len(_TITLES)) is None
    group = render_common_header(contract, bindings[1], index=1, count=len(_TITLES))
    ids = {node.get("id") for node in group.iter()}
    assert {"svg-system-common-header-eyebrow", "svg-system-common-header-divider"} <= ids
    eyebrow = next(node for node in group.iter() if node.get("id") == "svg-system-common-header-eyebrow")
    assert eyebrow.text == "02 / 03"


def test_reduced_header_takes_one_smaller_title_size_for_the_whole_deck(reduced_default):
    # About 100 characters: three lines at 34 px, two at 30 px.
    long_title = ("중소기업이 생성형 AI를 도입할 때 반드시 점검해야 할 데이터 보안 개인정보 보호 저작권 규제 준수 "
                  "요구사항과 단계별 대응 방안 및 담당 조직 구성 계획과 예산 그리고 분기별 성과")[:100]
    titles = ("표지", long_title, "단계별 로드맵")
    request = GeneratePresentationRequest(content="Operations", template="authored", strategy="svg",
        slides_markdown=["# " + title for title in titles])
    _, snapshot = _admit(request)
    outline = PresentationOutlineModel(slides=[
        SlideOutlineModel(content="# " + title, title=title, page_id=canonical_json_digest({"index": index}))
        for index, title in enumerate(titles)])
    canvas = request_canvas_metadata(request)
    plain = {key: value for key, value in snapshot.items() if key != REDUCED_HEADER_STYLE_KEY}
    with pytest.raises(ValueError, match="svg_common_header_title_overflow"):
        prepare_common_header(plain, outline, canvas)
    contract, bindings = prepare_common_header(snapshot, outline, canvas, cover_exception=True)
    assert contract["font"]["size_px"] in REDUCED_HEADER_STYLE["title_px"][1:]
    assert contract["title_slot_lines"] == 2
    for index, binding in enumerate(bindings):
        validate_common_header_binding(contract, binding, index=index, count=len(titles),
            content=outline.slides[index].content, title=outline.slides[index].title)


def test_frozen_reduced_contract_is_reused_not_remeasured(reduced_default, monkeypatch):
    request = _request()
    _, snapshot = _admit(request)
    outline = _outline()
    canvas = request_canvas_metadata(request)
    contract, bindings = prepare_common_header(snapshot, outline, canvas, cover_exception=True)
    for slide, binding in zip(outline.slides, bindings, strict=True):
        slide.common_header = binding
    monkeypatch.delenv("SVG_COMMON_HEADER_DEFAULT")
    frozen = {**snapshot, "common_header": contract}
    assert prepare_common_header(frozen, outline, canvas, cover_exception=True) == (contract, bindings)


def test_snapshot_without_the_style_keeps_the_plain_reduced_header(reduced_default):
    request = _request()
    _, snapshot = _admit(request)
    plain = {key: value for key, value in snapshot.items() if key != REDUCED_HEADER_STYLE_KEY}
    contract, bindings = prepare_common_header(plain, _outline(), request_canvas_metadata(request))
    assert contract["eyebrow"] == {"enabled": False} and contract["divider"] == {"enabled": False}
    assert all("eyebrow" not in binding for binding in bindings)
    # header_snapshot_base keeps the seal for a designed plan's reduced fallback.
    assert header_snapshot_base({**snapshot, "common_header": contract})[REDUCED_HEADER_STYLE_KEY] \
        == REDUCED_HEADER_STYLE


def test_unknown_reduced_header_style_fails_closed(reduced_default):
    request = _request()
    _, snapshot = _admit(request)
    tampered = {**snapshot, REDUCED_HEADER_STYLE_KEY: {**REDUCED_HEADER_STYLE, "version": "x/v9"}}
    with pytest.raises(ValueError, match="svg_common_header_contract_invalid"):
        prepare_common_header(tampered, _outline(), request_canvas_metadata(request))


def test_company_page_number_suppresses_the_eyebrow_only(reduced_default):
    request = _request()
    _, snapshot = _admit(request)
    furnished = {**snapshot, "system_furniture": {"elements": [
        {"id": "page", "kind": "pageNumber", "x": 1180, "y": 680, "width": 40, "height": 20}]}}
    contract, bindings = prepare_common_header(furnished, _outline(), request_canvas_metadata(request))
    assert contract["eyebrow"] == {"enabled": False}
    assert contract["divider"]["enabled"] is True
    assert all("eyebrow" not in binding for binding in bindings)


def test_explicit_uniform_stays_the_designed_opt_in(reduced_default):
    payload, snapshot = _admit(_request(common_header_mode="uniform"))
    assert "common_header_design_plan" in snapshot
    assert REDUCED_HEADER_STYLE_KEY not in snapshot
    assert payload["common_header_cover_exception"] is True
    payload, _ = _admit(_request(common_header_mode="uniform", common_header_cover_exception=False))
    assert payload["common_header_cover_exception"] is False


def test_header_design_input_keeps_the_designed_header_under_the_reduced_default(reduced_default):
    _, snapshot = _admit(_request(common_header_design={"instructions": "navy band"}))
    assert "common_header_design_plan" in snapshot
    assert REDUCED_HEADER_STYLE_KEY not in snapshot


def test_explicit_reduced_mode_needs_no_setting(monkeypatch):
    monkeypatch.delenv("SVG_COMMON_HEADER_DEFAULT", raising=False)
    request = _request(common_header_mode="reduced")
    assert request.common_header_mode == "uniform"
    payload, snapshot = _admit(request)
    assert payload["common_header_mode"] == "uniform"
    assert payload["common_header_cover_exception"] is True
    assert "common_header_design_plan" not in snapshot
    assert snapshot[REDUCED_HEADER_STYLE_KEY] == REDUCED_HEADER_STYLE
    payload, _ = _admit(_request(common_header_mode="reduced", common_header_cover_exception=False))
    assert payload["common_header_cover_exception"] is False


def test_reduced_mode_rejects_a_header_design_and_the_authored_strategy():
    with pytest.raises(ValueError, match="common_header_design_requires_svg_uniform"):
        _request(common_header_mode="reduced", common_header_design={"instructions": "x"})
    with pytest.raises(ValueError, match="common_header_svg_strategy_required"):
        GeneratePresentationRequest(content="x", template="authored", strategy="authored",
                                    common_header_mode="reduced")


def test_free_and_source_defaults_are_unchanged_by_the_reduced_default(reduced_default):
    payload, snapshot = _admit(_request(common_header_mode="free"))
    assert payload["common_header_cover_exception"] is False
    assert REDUCED_HEADER_STYLE_KEY not in snapshot


def test_restored_reduced_payload_never_acquires_a_design_plan(reduced_default):
    payload, snapshot = _admit(_request())
    restored = GeneratePresentationRequest.model_validate(payload)
    restored._svg_style_snapshot = snapshot
    assert restored.common_header_mode == "uniform"
    assert restored.common_header_cover_exception is True
    assert seal_new_header_design_plan(restored) is None


def test_public_v2_contract_accepts_reduced_mode():
    request = PresentationGenerationRequestV2.model_validate({
        "content": "x", "strategy": "svg", "common_header_mode": "reduced",
        "common_header_cover_exception": True})
    assert request.common_header_mode == "reduced"
    with pytest.raises(ValueError, match="common_header_design_requires_svg_uniform"):
        PresentationGenerationRequestV2.model_validate({
            "content": "x", "strategy": "svg", "common_header_mode": "reduced",
            "common_header_design": {"instructions": "x"}})
