"""Selection of source-image candidates for one authored slide."""

from pathlib import Path
from typing import Optional

from services.source_images.models import SourceImageCandidate
from services.source_images.selection import select_source_images

SLIDE_HEADING = "2025년 매출 성장 전망"
SLIDE_CONTENT = "국내 매출은 전년 대비 12% 성장했고 해외 매출 비중이 확대되었다."
SLIDE_INDEX = 4


def make_candidate(
    asset_id: str,
    *,
    source_heading: Optional[str] = None,
    source_text: Optional[str] = None,
    source_slide_index: Optional[int] = SLIDE_INDEX,
    width: int = 1600,
    height: int = 900,
    mime_type: str = "image/png",
) -> SourceImageCandidate:
    return SourceImageCandidate(
        asset_id=asset_id,
        path=Path(f"/app_data/source/{asset_id}.png"),
        mime_type=mime_type,
        width=width,
        height=height,
        digest=f"sha256:{asset_id}",
        source_slide_index=source_slide_index,
        source_heading=source_heading,
        source_text=source_text,
    )


def select(candidates, **overrides):
    kwargs = {
        "slide_heading": SLIDE_HEADING,
        "slide_content": SLIDE_CONTENT,
        "slide_index": SLIDE_INDEX,
    }
    kwargs.update(overrides)
    return select_source_images(candidates, **kwargs)


def test_korean_heading_about_the_same_topic_is_selected():
    related = make_candidate("related", source_heading="2025년 매출 전망")
    unrelated = make_candidate("unrelated", source_heading="직원 복지 제도 안내")

    assert select([unrelated, related], limit=2) == [related]


def test_korean_headings_about_other_topics_return_empty():
    candidates = [
        make_candidate("a", source_heading="직원 복지 제도 안내"),
        make_candidate("b", source_heading="사무실 이전 안내"),
        make_candidate("c", source_heading="고객 만족도 조사 결과"),
    ]

    assert select(candidates, limit=2) == []


def test_korean_match_survives_different_spacing():
    # Source decks and freshly written outlines rarely agree on eojeol spacing,
    # which is exactly why matching runs on syllable n-grams.
    unspaced = make_candidate("unspaced", source_heading="2025년매출성장전망")

    assert select([unspaced]) == [unspaced]


def test_terse_heading_matched_through_slide_body():
    candidate = make_candidate("body", source_heading="국내 매출 추이")

    selected = select(
        [candidate],
        slide_heading="현황",
        slide_content="국내 매출은 전년 대비 12% 성장했다.",
    )

    assert selected == [candidate]


def test_english_heading_matches_across_inflection():
    related = make_candidate("related", source_heading="Revenue Growth by Quarter")
    unrelated = make_candidate("unrelated", source_heading="Warehouse Safety Rules")

    selected = select(
        [unrelated, related],
        slide_heading="Quarterly Revenue Growth",
        slide_content="Revenue grew 12 percent year over year across all regions.",
    )

    assert selected == [related]


def test_english_stopwords_alone_do_not_clear_the_floor():
    candidate = make_candidate("stopwords", source_heading="The Office of the CEO")

    selected = select(
        [candidate],
        slide_heading="Quarterly Revenue Growth",
        slide_content="Revenue grew 12 percent year over year.",
    )

    assert selected == []


def test_tie_breakers_cannot_lift_a_candidate_over_the_floor():
    # Same slide index and an ideal landscape shape: the strongest possible
    # tie-breakers must still not rescue an off-topic picture.
    off_topic = make_candidate(
        "off-topic",
        source_heading="사무실 이전 안내",
        source_slide_index=SLIDE_INDEX,
    )

    assert select([off_topic]) == []


def test_limit_caps_the_offer_and_keeps_the_best_first():
    strong = make_candidate("strong", source_heading="2025년 매출 전망")
    medium = make_candidate("medium", source_heading="해외 매출 비중")
    weak = make_candidate("weak", source_heading="매출 현황")

    assert select([weak, medium, strong], limit=2) == [strong, medium]


def test_limit_of_zero_returns_nothing():
    strong = make_candidate("strong", source_heading="2025년 매출 전망")

    assert select([strong], limit=0) == []


def test_empty_candidate_list_returns_empty():
    assert select([], limit=2) == []


def test_tied_scores_order_by_asset_id_regardless_of_input_order():
    first = make_candidate("aaa", source_heading="2025년 매출 전망")
    second = make_candidate("bbb", source_heading="2025년 매출 전망")

    assert select([second, first], limit=2) == [first, second]
    assert select([first, second], limit=2) == [first, second]


def test_repeated_selection_is_deterministic():
    candidates = [
        make_candidate("a", source_heading="2025년 매출 전망"),
        make_candidate("b", source_heading="해외 매출 비중", source_slide_index=9),
        make_candidate("c", source_heading="직원 복지 제도 안내"),
    ]

    assert select(candidates, limit=2) == select(list(reversed(candidates)), limit=2)


def test_nearer_source_slide_wins_a_tie():
    near = make_candidate("z-near", source_heading="매출 전망", source_slide_index=5)
    far = make_candidate("a-far", source_heading="매출 전망", source_slide_index=30)

    # The asset_id ordering would put "a-far" first, so proximity is what decides.
    assert select([far, near], limit=2) == [near, far]


def test_landscape_wins_a_tie_over_portrait():
    landscape = make_candidate("z-wide", source_heading="매출 전망")
    portrait = make_candidate(
        "a-tall", source_heading="매출 전망", width=900, height=1600
    )

    assert select([portrait, landscape], limit=2) == [landscape, portrait]


def test_aspect_ratio_is_never_a_filter():
    portrait = make_candidate(
        "portrait", source_heading="2025년 매출 전망", width=900, height=1600
    )

    assert select([portrait]) == [portrait]


def test_directly_uploaded_image_is_offered_but_ranks_below_a_text_match():
    # A picture the user handed over has no source heading to score, so it is
    # presumed relevant rather than dropped — but never ahead of a real match.
    uploaded = make_candidate("uploaded", source_slide_index=None)
    related = make_candidate("related", source_heading="2025년 매출 전망")

    assert select([uploaded, related], limit=2) == [related, uploaded]
    assert select([uploaded], limit=2) == [uploaded]


def test_unsupported_mime_type_is_never_offered():
    animated = make_candidate(
        "animated", source_heading="2025년 매출 전망", mime_type="image/gif"
    )

    assert select([animated], limit=2) == []


def test_html_markup_in_slide_content_does_not_create_matches():
    # Slide content can arrive as HTML; tag names must not match a heading.
    candidate = make_candidate("markup", source_heading="Div Span Table Layout")

    selected = select(
        [candidate],
        slide_heading="분기 실적",
        slide_content='<div class="table"><span>분기 실적이 개선되었다.</span></div>',
    )

    assert selected == []


def test_a_section_marker_heading_is_matched_through_the_source_slide_text():
    """A proposal deck labels sections, not pictures.

    Measured on a real deck, nine of ten candidates were headed "02 · SOLUTION";
    scoring headings alone found nothing for any slide, so what the source slide
    actually says has to count.
    """
    candidate = make_candidate(
        "marker",
        source_heading="02 · SOLUTION",
        source_text=(
            "02 · SOLUTION 기능 ④ AI 오케스트레이션으로 실행·차단·검토를 통제 "
            "업무 단위로 자율 실행을 관리합니다"
        ),
    )

    selected = select(
        [candidate],
        slide_heading="AI 오케스트레이션 통제",
        slide_content="자율 실행과 검토를 하나의 업무 단위로 통제한다.",
    )

    assert selected == [candidate]


def test_source_text_about_something_else_still_stays_out():
    candidate = make_candidate(
        "marker",
        source_heading="02 · SOLUTION",
        source_text="02 · SOLUTION 사무실 이전 안내와 주차 정책 변경 공지입니다",
    )

    assert select([candidate]) == []
