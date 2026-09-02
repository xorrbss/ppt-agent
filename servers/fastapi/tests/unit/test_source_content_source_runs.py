from __future__ import annotations

import pytest

from services.source_content.browser_readback import (
    BrowserSourceReadbackError,
    deterministic_source_qid,
    verify_browser_source_readback,
)
from services.source_content.readback import (
    SourceContentReadbackError,
    verify_source_verbatim_readback,
)
from services.source_content.source_runs import (
    parse_source_binding_id,
    project_source_runs,
    source_run_binding_id,
)
from services.source_content.text_stamping import (
    stamp_source_block_ids,
    terminal_punctuation_surplus,
)

BULLETS = "• 프로필 사진\n• 한 줄 소개"


def test_projection_splits_lines_bullets_arrows_pipes_and_gaps() -> None:
    bullets = project_source_runs(BULLETS)
    assert [(run.marker, run.body) for run in bullets.runs] == [("• ", "프로필 사진"), ("• ", "한 줄 소개")]
    assert bullets.lines == ((0, 0), (1, 1))
    assert bullets.accepted_texts(0, 1) == ("• 프로필 사진 • 한 줄 소개", "프로필 사진 • 한 줄 소개")
    assert bullets.accepted_texts(1, 1) == ("• 한 줄 소개", "한 줄 소개")
    flow = project_source_runs("영업 → 상담 → 제안")
    assert [run.body for run in flow.runs] == ["영업", "상담", "제안"]
    assert flow.span_text(0, 2) == "영업 → 상담 → 제안"
    assert flow.span_text(1, 2) == "상담 → 제안"
    assert [run.body for run in project_source_runs("1주차 실습 | 내 사업").runs] == ["1주차 실습", "내 사업"]
    assert [run.body for run in project_source_runs("Day 1  업체 10곳 찾기").runs] == ["Day 1", "업체 10곳 찾기"]
    fields = project_source_runs("• ① 타깃: ______")
    assert [(run.marker, run.body) for run in fields.runs] == [("• ① ", "타깃:"), ("", "______")]
    assert [run.body for run in project_source_runs("좌→우 / 우→좌").runs] == ["좌→우 / 우→좌"]
    steps = project_source_runs("가게 분석\n→ 4개 기획")
    assert [(run.marker, run.body) for run in steps.runs] == [("", "가게 분석"), ("→ ", "4개 기획")]
    arrows = project_source_runs("문제 제시\n↓\n해결")
    assert [(run.marker, run.body) for run in arrows.runs] == [("", "문제 제시"), ("", "↓"), ("", "해결")]
    inline = project_source_runs("• A • B")
    assert [run.body for run in inline.runs] == ["A", "B"]
    with pytest.raises(ValueError):
        project_source_runs("  \n ")


def test_run_binding_ids_round_trip() -> None:
    assert source_run_binding_id("b1", 0, 2, 3) == "b1"
    assert source_run_binding_id("b1", 1, 1, 3) == "b1:run:1"
    assert source_run_binding_id("b1", 1, 2, 3) == "b1:run:1-2"
    assert parse_source_binding_id("source-slide:1:text:7:run:3-4") == ("source-slide:1:text:7", 3, 4)
    assert parse_source_binding_id("source-slide:1:text:7:run:2") == ("source-slide:1:text:7", 2, 2)
    assert parse_source_binding_id("source-slide:1:text:7") == ("source-slide:1:text:7", None, None)


def test_stamp_binds_bullet_items_to_individual_elements() -> None:
    html = '<ul><li><span class="sq"></span><span>프로필 사진</span></li><li><span>한 줄 소개</span></li></ul>'
    stamped = stamp_source_block_ids(html, [("b1", BULLETS)])
    assert 'data-source-block-id="b1:run:0"' in stamped
    assert 'data-source-block-id="b1:run:1"' in stamped
    assert 'data-source-block-id="b1"' not in stamped
    assert f'data-qid="{deterministic_source_qid("b1:run:0")}"' in stamped
    assert stamp_source_block_ids(stamped, [("b1", BULLETS)]) == stamped


def test_stamp_binds_arrow_flow_chips_or_the_whole_line() -> None:
    chips = (
        '<div class="flow"><div>영업</div><div class="arrow"></div>'
        '<div>상담</div><div class="arrow"></div><div>제안</div></div>'
    )
    stamped = stamp_source_block_ids(chips, [("f", "영업 → 상담 → 제안")])
    assert all(f'data-source-block-id="f:run:{index}"' in stamped for index in range(3))
    whole = stamp_source_block_ids("<div>영업 → 상담 → 제안</div>", [("f", "영업 → 상담 → 제안")])
    assert 'data-source-block-id="f"' in whole


def test_stamp_binds_multi_line_spans_to_the_coarsest_elements() -> None:
    html = '<div class="main">‘영상 1개 얼마’보다<br>패키지를 보여준다.</div><div class="sub">수정 횟수를 명확히.</div>'
    text = "‘영상 1개 얼마’보다\n패키지를 보여준다.\n\n수정 횟수를 명확히."
    stamped = stamp_source_block_ids(html, [("m", text)])
    assert 'data-source-block-id="m:run:0-1"' in stamped
    assert 'data-source-block-id="m:run:2"' in stamped
    assert stamp_source_block_ids(stamped, [("m", text)]) == stamped


def test_stamp_splits_pipe_header_and_accepts_omitted_markers() -> None:
    header = '<div class="eyebrow">1주차 실습</div><h2>내 사업 한 장으로 정리</h2>'
    stamped = stamp_source_block_ids(header, [("h", "1주차 실습 | 내 사업 한 장으로 정리")])
    assert 'data-source-block-id="h:run:0"' in stamped
    assert '<h2 data-source-block-id="h:run:1"' in stamped
    assert 'data-semantic-role="title"' in stamped
    badge = stamp_source_block_ids('<div class="num">①</div><h3>누구에게</h3>', [("e", "① 누구에게")])
    assert '<h3 data-source-block-id="e"' in badge


def test_stamp_fails_closed_when_a_run_has_no_exact_element() -> None:
    with pytest.raises(BrowserSourceReadbackError) as error:
        stamp_source_block_ids("<ul><li>프로필 사진</li><li>한 줄 소개 문구</li></ul>", [("b1", BULLETS)])
    assert "run 1" in error.value.detail
    with pytest.raises(BrowserSourceReadbackError):
        stamp_source_block_ids("<div>10</div><div>곳 찾기</div>", [("n", "10곳 찾기")])
    with pytest.raises(BrowserSourceReadbackError) as blank:
        stamp_source_block_ids('<div>타깃</div><div class="line"></div>', [("t", "타깃"), ("l", "______")])
    assert blank.value.block_ids == ("l",)


def test_stamp_tolerates_exact_decorative_repeats_only() -> None:
    html = (
        '<div class="eyebrow">2주차 PART 1 | 릴스 기획</div><h1>2주차 PART 1 | 릴스 기획</h1>'
        "<span>07</span><footer>07</footer>"
    )
    pairs = [("t", "2주차 PART 1 | 릴스 기획"), ("p", "07")]
    stamped = stamp_source_block_ids(html, pairs)
    assert stamped.count('data-source-block-id="t"') == 1
    assert stamped.count('data-source-block-id="p"') == 1
    assert '<div class="eyebrow" data-source-block-id="t"' in stamped
    assert "<h1>2주차 PART 1 | 릴스 기획</h1>" in stamped
    assert stamp_source_block_ids(stamped, pairs) == stamped
    with pytest.raises(BrowserSourceReadbackError):
        stamp_source_block_ids("<span>07 / 35</span><footer>p.07</footer>", [("p", "07")])


def test_stamp_binds_identical_text_blocks_in_document_order() -> None:
    html = '<div class="a">→</div><div class="b">→</div><div class="c">→</div>'
    pairs = [("x", "→"), ("y", "→"), ("z", "→")]
    stamped = stamp_source_block_ids(html, pairs)
    assert '<div class="a" data-source-block-id="x"' in stamped
    assert '<div class="b" data-source-block-id="y"' in stamped
    assert '<div class="c" data-source-block-id="z"' in stamped
    assert stamp_source_block_ids(stamped, pairs) == stamped
    with pytest.raises(BrowserSourceReadbackError) as error:
        stamp_source_block_ids(html, [*pairs, ("w", "→")])
    assert error.value.block_ids == ("w",)


def test_stamp_repairs_one_appended_full_stop_back_to_source_bytes() -> None:
    assert terminal_punctuation_surplus("꿀벌쌤 강의용 마스터 PPT.", "꿀벌쌤 강의용 마스터 PPT") == "."
    assert terminal_punctuation_surplus("분석합니다..", "분석합니다.") == "."
    assert terminal_punctuation_surplus("직접 제안!", "직접 제안") is None
    assert terminal_punctuation_surplus("검증했습니다.", "검증합니다.") is None
    stamped = stamp_source_block_ids(
        '<div class="meta">꿀벌쌤 강의용 마스터 PPT.</div>', [("m", "꿀벌쌤 강의용 마스터 PPT")]
    )
    assert ">꿀벌쌤 강의용 마스터 PPT</div>" in stamped
    nested_text = "‘편집자’가 아니라 제작 파트너"
    nested = stamp_source_block_ids("<div>‘편집자’가 아니라 <strong>제작 파트너.</strong></div>", [("n", nested_text)])
    assert "<strong>제작 파트너</strong>" in nested
    assert 'data-source-block-id="n"' in nested
    assert stamp_source_block_ids(nested, [("n", nested_text)]) == nested
    with pytest.raises(BrowserSourceReadbackError):
        stamp_source_block_ids("<div>제작 파트너&#46;</div>", [("n", "제작 파트너")])


def _manifest() -> dict[str, object]:
    return {
        "units": [{
            "sourceUnitId": "unit-1", "sourceIndex": 0,
            "blocks": [
                {"sourceBlockId": "b1", "kind": "text", "order": 0, "payload": {"text": "원문 제목"}},
                {"sourceBlockId": "b2", "kind": "text", "order": 1, "payload": {"text": BULLETS}},
            ],
        }],
    }


def _outline() -> dict[str, object]:
    return {"slides": [{"sourceUnitId": "unit-1", "sourceIndex": 0, "readingOrder": ["b1", "b2"]}]}


def _element(binding_id: str, text: str, index: int, *, role: str = "body") -> dict[str, object]:
    return {
        "sourceBlockId": binding_id,
        "qid": deterministic_source_qid(binding_id),
        "semanticRole": role,
        "renderedText": text,
        "bbox": {"x": 20, "y": 20 + index * 40, "width": 200, "height": 24},
        "cssVisible": True,
        "effectiveOpacity": 1,
        "fontLoaded": True,
        "clipped": False,
        "glyphClipped": False,
        "lineBoxClipped": False,
        "occludedFraction": 0,
        "continuousOccludedFraction": 0,
        "computedStyle": {"display": "block", "visibility": "visible"},
        "measurement": {"occlusion": True},
        "occlusionMeasurementEvidence": {"status": "measured"},
    }


def _metrics(*elements: dict[str, object]) -> list[dict[str, object]]:
    return [{"width": 1280, "height": 720, "fontsReady": True, "elements": list(elements)}]


def test_browser_readback_folds_run_partition_into_block_evidence() -> None:
    metrics = _metrics(
        _element("b1", "원문 제목", 0, role="title"),
        _element("b2:run:0", "프로필 사진", 1),
        _element("b2:run:1", "• 한 줄 소개", 2),
    )
    result = verify_browser_source_readback(manifest=_manifest(), outline=_outline(), browser_metrics=metrics)
    assert result.passed
    assert [item.source_block_id for item in result.evidence] == ["b1", "b2"]
    folded = result.as_dict()["evidence"][1]
    assert folded["renderedText"] == "프로필 사진 • 한 줄 소개"
    assert folded["bbox"] == {"x": 20.0, "y": 60.0, "width": 200.0, "height": 64.0}
    assert [run["sourceBlockId"] for run in folded["runs"]] == ["b2:run:0", "b2:run:1"]
    assert "runs" not in result.as_dict()["evidence"][0]


@pytest.mark.parametrize(
    "elements",
    [
        # incomplete partition
        [_element("b1", "원문 제목", 0), _element("b2:run:0", "프로필 사진", 1)],
        # runs out of DOM order
        [
            _element("b1", "원문 제목", 0),
            _element("b2:run:1", "한 줄 소개", 1),
            _element("b2:run:0", "프로필 사진", 2),
        ],
        # altered run text
        [
            _element("b1", "원문 제목", 0),
            _element("b2:run:0", "프로필 사진들", 1),
            _element("b2:run:1", "한 줄 소개", 2),
        ],
        # whole and run bindings for the same block
        [
            _element("b1", "원문 제목", 0),
            _element("b2", BULLETS, 1),
            _element("b2:run:0", "프로필 사진", 2),
            _element("b2:run:1", "한 줄 소개", 3),
        ],
        # span outside the projection
        [
            _element("b1", "원문 제목", 0),
            _element("b2:run:0", "프로필 사진", 1),
            _element("b2:run:1-2", "한 줄 소개", 2),
        ],
    ],
)
def test_browser_readback_rejects_broken_run_partitions(elements) -> None:
    with pytest.raises(BrowserSourceReadbackError) as error:
        verify_browser_source_readback(manifest=_manifest(), outline=_outline(), browser_metrics=_metrics(*elements))
    assert "b2" in error.value.block_ids or error.value.order_violations


def test_html_readback_accepts_projected_runs_in_source_order() -> None:
    manifest = _manifest()
    manifest["units"][0]["blocks"].append(
        {"sourceBlockId": "b3", "kind": "text", "order": 2, "payload": {"text": "영업 → 상담"}}
    )
    outline = {"slides": [{"sourceUnitId": "unit-1", "sourceIndex": 0, "readingOrder": ["b1", "b2", "b3"]}]}
    html = "<h1>원문 제목</h1><ul><li>프로필 사진</li><li>한 줄 소개</li></ul><div>영업</div><div>상담</div>"
    assert verify_source_verbatim_readback(manifest=manifest, outline=outline, htmls=[html]).passed
    reordered = "<h1>원문 제목</h1><ul><li>한 줄 소개</li><li>프로필 사진</li></ul><div>영업</div><div>상담</div>"
    with pytest.raises(SourceContentReadbackError) as error:
        verify_source_verbatim_readback(manifest=manifest, outline=outline, htmls=[reordered])
    assert error.value.block_ids == ("b2",)


def test_a_bullet_before_another_glyph_never_becomes_its_own_run() -> None:
    """``• □ item`` splits before both glyphs, orphaning the bullet.

    Authored markup draws a bullet with CSS or an empty decoration span, so a
    run whose whole body is the glyph can never find a DOM owner and failed
    every block that paired a bullet with a checkbox.
    """
    runs = project_source_runs("• □ 누구에게 팔지 안다 • □ 무엇을 팔지 안다").runs

    assert [run.body for run in runs] == ["누구에게 팔지 안다", "무엇을 팔지 안다"]
    assert all(run.body.strip() for run in runs)


def test_a_number_is_content_even_though_a_glyph_is_not() -> None:
    # A verbatim restyle must keep a source page number; only glyphs and
    # enumerators are presentation.
    assert [run.body for run in project_source_runs("16").runs] == ["16"]
    assert [run.body for run in project_source_runs("• ① 타깃: ______").runs] == [
        "타깃:",
        "______",
    ]


def test_a_block_of_only_glyphs_still_projects_a_run() -> None:
    # Nothing to fold the glyph into, and a block must never project empty.
    assert [run.body for run in project_source_runs("•").runs] == ["•"]


def test_a_pipe_frees_the_order_of_the_run_after_it() -> None:
    """``brand | course title`` are independent labels, not a sequence.

    The template owns which header slot each half lands in and regularly
    renders the second before the first, so requiring source order there
    rejected correct slides. A sequence the source author ordered -- bullets,
    arrows, lines -- keeps its order.
    """
    piped = project_source_runs("꿀벌쌤 | 숏폼대행 1인사업자 홀로서기").runs
    assert [run.order_free_before for run in piped] == [False, True]

    for sequence in ("영업 → 상담 → 제안", "• □ 하나 • □ 둘", "첫 줄\n둘째 줄"):
        assert not any(run.order_free_before for run in project_source_runs(sequence).runs)


def test_accepts_treats_markers_and_edge_separators_as_presentation() -> None:
    fields = project_source_runs("• ① 타깃: __________________\n• ② 상품: __________________")
    assert fields.accepts(0, 1, "① 타깃: __________________")
    assert fields.accepts(0, 1, "타깃: __________________")
    assert fields.accepts(0, 1, "• ① 타깃: __________________")
    assert not fields.accepts(0, 1, "① 타깃 __________________")
    assert not fields.accepts(0, 0, "① 상품:")
    header = project_source_runs("꿀벌쌤 | 숏폼대행 1인사업자 홀로서기")
    assert header.accepts(0, 0, "꿀벌쌤 |")
    assert header.accepts(1, 1, "| 숏폼대행 1인사업자 홀로서기")
    assert header.accepts(0, 1, "꿀벌쌤 | 숏폼대행 1인사업자 홀로서기")
    assert not header.accepts(0, 0, "꿀벌쌤 | 숏폼")
    assert not header.accepts(0, 1, "꿀벌쌤 숏폼대행 1인사업자 홀로서기")
    bullets = project_source_runs("• 사업 정의\n• 편집자와의 차이\n• 업무 범위")
    assert bullets.accepts(0, 1, "사업 정의 편집자와의 차이")
    assert bullets.accepts(0, 2, "사업 정의 • 편집자와의 차이 업무 범위")
    assert not bullets.accepts(0, 1, "편집자와의 차이 사업 정의")
    arrows = project_source_runs("영업 → 상담 → 제안")
    assert arrows.accepts(0, 0, "영업 →")
    assert arrows.accepts(1, 1, "→ 상담 →")
    assert not arrows.accepts(0, 2, "영업 상담 제안")


def test_stamp_binds_what_the_verbatim_prompt_allows() -> None:
    # A list that keeps the circled number but draws the bullet with CSS,
    # each label sharing one element with its blank field.
    fields = "• ① 타깃: __________________\n• ② 상품: __________________"
    html = "<ul><li>① 타깃: __________________</li><li>② 상품: __________________</li></ul>"
    stamped = stamp_source_block_ids(html, [("f", fields)])
    assert '<li data-source-block-id="f:run:0-1"' in stamped
    assert '<li data-source-block-id="f:run:2-3"' in stamped
    assert stamp_source_block_ids(stamped, [("f", fields)]) == stamped
    # A pipe left attached to the eyebrow beside the title it joined.
    header = '<div class="eyebrow">꿀벌쌤 |</div><div class="title">숏폼대행 1인사업자 홀로서기</div>'
    stamped = stamp_source_block_ids(header, [("h", "꿀벌쌤 | 숏폼대행 1인사업자 홀로서기")])
    assert '<div class="eyebrow" data-source-block-id="h:run:0"' in stamped
    assert '<div class="title" data-source-block-id="h:run:1"' in stamped
    # Two items sharing one element without their bullets, in source order.
    bullets = "• 사업 정의\n• 편집자와의 차이\n• 업무 범위"
    shared = '<div class="copy">사업 정의<br>편집자와의 차이</div><div class="side">업무 범위</div>'
    stamped = stamp_source_block_ids(shared, [("b", bullets)])
    assert '<div class="copy" data-source-block-id="b:run:0-1"' in stamped
    assert '<div class="side" data-source-block-id="b:run:2"' in stamped
    # The same items out of source order still fail closed.
    with pytest.raises(BrowserSourceReadbackError) as error:
        stamp_source_block_ids(
            '<div class="side">업무 범위</div><div class="copy">사업 정의<br>편집자와의 차이</div>', [("b", bullets)]
        )
    assert "run 2" in error.value.detail
    # A dropped internal pipe is dropped source text, not presentation.
    with pytest.raises(BrowserSourceReadbackError):
        stamp_source_block_ids(
            "<div>꿀벌쌤 숏폼대행 1인사업자 홀로서기</div>", [("h", "꿀벌쌤 | 숏폼대행 1인사업자 홀로서기")]
        )


def test_stamp_repairs_an_appended_full_stop_after_a_drawn_marker() -> None:
    stamped = stamp_source_block_ids('<div class="num">①</div><h3>누구에게.</h3>', [("e", "① 누구에게")])
    assert '<h3 data-source-block-id="e"' in stamped
    assert ">누구에게</h3>" in stamped


def test_browser_readback_accepts_the_same_presentation_drift_as_stamping() -> None:
    manifest = _manifest()
    manifest["units"][0]["blocks"].append(
        {"sourceBlockId": "b3", "kind": "text", "order": 2, "payload": {"text": "꿀벌쌤 | 숏폼대행 1인사업자 홀로서기"}}
    )
    outline = {"slides": [{"sourceUnitId": "unit-1", "sourceIndex": 0, "readingOrder": ["b1", "b2", "b3"]}]}
    metrics = _metrics(
        _element("b1", "원문 제목", 0, role="title"),
        _element("b2", "프로필 사진 한 줄 소개", 1),
        _element("b3:run:0", "꿀벌쌤 |", 2),
        _element("b3:run:1", "숏폼대행 1인사업자 홀로서기", 3),
    )
    result = verify_browser_source_readback(manifest=manifest, outline=outline, browser_metrics=metrics)
    assert result.passed
    drifted = _metrics(
        _element("b1", "원문 제목", 0, role="title"),
        _element("b2", "한 줄 소개 프로필 사진", 1),
        _element("b3", "꿀벌쌤 | 숏폼대행 1인사업자 홀로서기", 2),
    )
    with pytest.raises(BrowserSourceReadbackError) as error:
        verify_browser_source_readback(manifest=manifest, outline=outline, browser_metrics=drifted)
    assert "b2" in error.value.block_ids
