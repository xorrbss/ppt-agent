"""Where a source line may and may not be cut into atomic runs.

``project_source_runs`` used to decide that from the separator glyph alone,
with no idea what surrounded it.  Three defects followed, all measured on the
deck-43 verbatim restyle (see
``docs/generation-quality-root-cause-and-composer-architecture-20260904.md``
section 7c): an arrow chain ran on into the prose after it, a separator inside
a bracket pair tore the pair in half, and a divider drawn with two spaces
either side survived as a run of its own that no template can render.  One
source line of four ``condition -> outcome`` pairs became eleven atoms, which
the classifier then promoted to an eleven-card ``process`` grid inside a 43 px
band.

These are the boundary rules, one test per rule, plus the property that ties
them together: whatever the projection decides, it may not lose a character.
"""

from __future__ import annotations

import re

import pytest

from services.source_content.source_runs import project_source_runs

# The four shapes the fixes were derived from.
ARROW_CHAIN_INTO_PROSE = (
    "의도 분류 → 문서 검색 → 근거 기반 생성 → 다국어 검증을 하나의 개발 흐름으로 연결. "
    "13종 번역 기능 연계, 재작업 5회 → 1회."
)
SEPARATOR_INSIDE_BRACKETS = (
    "변경 루프  고객 요구 변경 → 엑셀에 정리 → 설계 문서 반영 (사례: 재작업 5회 → 1회)"
)
WIDE_DIVIDER = "승인 가능  |  미통과"
# The real shape of source block "source-slide:16:text:45", which produced
# eleven fragments with a bare "|" as fragments 2, 5 and 8.
DIVIDED_CONDITION_STRIP = (
    "요건 충족 → 승인 가능  |  요건 미충족 → 반려  |  "
    "보류 조건 → 재심사  |  기한 초과 → 자동 종료"
)


def _bodies(line: str) -> list[str]:
    return [run.body for run in project_source_runs(line).runs]


def _squash(value: str) -> str:
    return re.sub(r"\s+", "", value)


def test_a_divider_cuts_the_same_line_at_every_spacing() -> None:
    """" | " is a divider however much whitespace the author left around it.

    ``\\s+`` in the divider alternative also matches the run of two or more
    spaces the gap alternative claims, and ``re`` resolves alternation left to
    right, so the gap used to win: "승인 가능  |  미통과" split on the gap and
    left a bare "|" behind as its own run, losing the order freedom the
    divider carries.  A divider is a divider at one space, two spaces or a
    tab, and all three must project identically.
    """

    single = project_source_runs("승인 가능 | 미통과")
    for spelling in (WIDE_DIVIDER, "승인 가능\t|\t미통과", "승인 가능 |  미통과"):
        widened = project_source_runs(spelling)
        assert [run.body for run in widened.runs] == [run.body for run in single.runs]
        assert [run.order_free_before for run in widened.runs] == [
            run.order_free_before for run in single.runs
        ]
    assert [run.body for run in single.runs] == ["승인 가능", "미통과"]
    assert [run.order_free_before for run in single.runs] == [False, True]


def test_a_divider_is_never_a_run_of_its_own_nor_dropped_from_the_block() -> None:
    """The divider is presentation, but it is still source text.

    A run whose whole body is "|" reaches the renderer and is drawn as a card,
    which is how one strip became an eleven-card grid.  Folding it away must
    not delete it either: the verbatim contract owns every character, so the
    divider stays in the projected block text and inside any span that crosses
    it, exactly as an inline arrow does.
    """

    for line in (WIDE_DIVIDER, DIVIDED_CONDITION_STRIP, "꿀벌쌤 | 숏폼대행"):
        projection = project_source_runs(line)
        assert "|" not in [run.body for run in projection.runs]
        assert "|" in projection.text
        assert projection.text.count("|") == line.count("|")
        assert projection.span_text(*projection.whole).count("|") == line.count("|")


def test_the_divided_condition_strip_projects_one_atom_per_condition() -> None:
    """The deck-43 slide 16 strip: eight labels, not eleven fragments."""

    projection = project_source_runs(DIVIDED_CONDITION_STRIP)

    assert [run.body for run in projection.runs] == [
        "요건 충족",
        "승인 가능",
        "요건 미충족",
        "반려",
        "보류 조건",
        "재심사",
        "기한 초과",
        "자동 종료",
    ]
    # Each divider frees the order of the pair it introduces; the arrow inside
    # a pair does not, because the author chose that order.
    assert [run.order_free_before for run in projection.runs] == [
        False, False, True, False, True, False, True, False,
    ]


def test_a_separator_inside_a_bracket_pair_is_content_not_structure() -> None:
    """"(사례: 재작업 5회 → 1회)" is one parenthetical, not two runs.

    Splitting inside the pair left "설계 문서 반영 (사례: 재작업 5회" and the orphan
    "1회)", so neither run carried balanced text and no element could own
    either half as the author wrote it.
    """

    assert _bodies(SEPARATOR_INSIDE_BRACKETS) == [
        "변경 루프",
        "고객 요구 변경",
        "엑셀에 정리",
        "설계 문서 반영 (사례: 재작업 5회 → 1회)",
    ]
    # Every pair kind the source decks use, and the gap rule as well as arrows.
    assert _bodies("표준  「지침  적용」  완료") == ["표준", "「지침 적용」", "완료"]
    assert _bodies("설계 검토 → 승인 [1차  2차] → 종료") == [
        "설계 검토",
        "승인 [1차 2차]",
        "종료",
    ]
    assert _bodies('"승인  가능" 검토') == ['"승인 가능" 검토']
    assert _bodies("‘영상 1개 얼마’보다  패키지") == ["‘영상 1개 얼마’보다", "패키지"]


def test_an_unclosed_bracket_protects_nothing_at_all() -> None:
    """Only a pair the line actually closes protects the text between them.

    An opener with no closer is a smiley, a stray keystroke or a truncated
    source cell.  Treating it as open would swallow the whole remainder of the
    line into a single run -- the failure mode of a naive depth counter -- so
    an unmatched opener is ignored and the line falls back to the plain
    separator rules.  A pair nested inside a closed one is resolved the same
    way: "[" below never closes, "(" does, so only "(" protects.
    """

    assert _bodies("A  (B  C") == ["A", "(B", "C"]
    assert _bodies("(참고 자료  검토 → 승인") == ["(참고 자료", "검토", "승인"]
    assert _bodies("(A  [B  C)  D") == ["(A [B C)", "D"]
    assert _bodies("A  B)  C") == ["A", "B)", "C"]


def test_an_apostrophe_never_opens_a_quoted_region() -> None:
    """A straight quote only pairs where a word could start and end.

    "don't" carries one apostrophe.  Toggling on it would open a region that
    never closes and suppress every later boundary on the line, which is the
    same swallow the unclosed-bracket rule exists to prevent.
    """

    assert _bodies("don't stop  do it") == ["don't stop", "do it"]
    assert _bodies("it's here  and it's there") == ["it's here", "and it's there"]


def test_an_arrow_chain_stops_where_the_prose_starts() -> None:
    """A chain enumerates steps; it may not run past a full stop.

    "다국어 검증을 ... 연결." finishes a sentence and the line keeps writing, so
    everything after it is prose and the "5회 → 1회" arrow inside that prose is
    the author's punctuation, not a fifth and sixth step.  Splitting there
    produced a whole sentence as one "step" and the orphan "1회." as another.
    """

    assert _bodies(ARROW_CHAIN_INTO_PROSE) == [
        "의도 분류",
        "문서 검색",
        "근거 기반 생성",
        "다국어 검증을 하나의 개발 흐름으로 연결. 13종 번역 기능 연계, 재작업 5회 → 1회.",
    ]
    # A single arrow already inside prose is prose too.
    assert _bodies("분석 완료. 다음은 배포 → 운영") == ["분석 완료. 다음은 배포 → 운영"]
    # A genuine chain is untouched, closing period and all.
    assert _bodies("영업 → 상담 → 제안") == ["영업", "상담", "제안"]
    assert _bodies("영업 → 상담 → 제안.") == ["영업", "상담", "제안."]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # A decimal point is not a full stop.
        ("속도 3.5배 → 비용 절감", ["속도 3.5배", "비용 절감"]),
        # Nor is an enumerator, which the marker peeler owns.
        ("1. 첫째 → 2. 둘째", ["첫째", "둘째"]),
        # Nor a Korean date part.
        ("2026. 09. 04. 착수 → 검수", ["2026. 09. 04. 착수", "검수"]),
        # An ellipsis is a pause, not a terminator.
        ("준비... → 실행", ["준비...", "실행"]),
        # A period glued to the next character is a host name.
        ("www.example.com 확인 → 배포", ["www.example.com 확인", "배포"]),
        # A terminator that merely ends the piece ends no chain.
        ("검토 완료. → 승인 완료. → 종료", ["검토 완료.", "승인 완료.", "종료"]),
    ],
)
def test_a_period_that_is_not_a_sentence_end_keeps_the_chain(
    line: str, expected: list[str]
) -> None:
    """The narrow rule: only a real sentence break, mid-piece, ends a chain."""

    assert _bodies(line) == expected


ALL_FIXTURES = (
    ARROW_CHAIN_INTO_PROSE,
    SEPARATOR_INSIDE_BRACKETS,
    WIDE_DIVIDER,
    DIVIDED_CONDITION_STRIP,
    "승인 가능 | 미통과",
    "승인 가능\t|\t미통과",
    "꿀벌쌤 | 숏폼대행 1인사업자 홀로서기",
    "표준  「지침  적용」  완료",
    "설계 검토 → 승인 [1차  2차] → 종료",
    '"승인  가능" 검토',
    "‘영상 1개 얼마’보다  패키지",
    "A  (B  C",
    "(참고 자료  검토 → 승인",
    "(A  [B  C)  D",
    "don't stop  do it",
    "영업 → 상담 → 제안.",
    "속도 3.5배 → 비용 절감",
    "1. 첫째 → 2. 둘째",
    "2026. 09. 04. 착수 → 검수",
    "준비... → 실행",
    "www.example.com 확인 → 배포",
    "검토 완료. → 승인 완료. → 종료",
    "매출 3.5억 → 5억  |  비용 2억 → 1억",
    "• ① 타깃: ______",
    "Day 1  업체 10곳 찾기",
    "16",
)


@pytest.mark.parametrize("line", ALL_FIXTURES)
def test_the_projection_loses_no_source_character(line: str) -> None:
    """The verbatim contract, asserted directly over every boundary fixture.

    Whitespace belongs to the template, so it is squashed on both sides; every
    other character of the source line must survive into the projected block
    text, which is what a whole-block binding renders and what readback
    compares against.  Markers and dividers are folded out of a *run body*,
    never out of the block.
    """

    projection = project_source_runs(line)

    assert _squash(projection.text) == _squash(line)
    assert _squash(projection.span_text(*projection.whole)) == _squash(line)
    # And every run still carries text of its own: a run whose body is empty,
    # or is nothing but a divider, has no possible DOM owner.
    assert all(run.body.strip() for run in projection.runs)
    assert all(run.body.strip() not in {"|", "→"} for run in projection.runs)


@pytest.mark.parametrize("line", ALL_FIXTURES)
def test_every_run_span_is_a_contiguous_slice_of_the_block_text(line: str) -> None:
    """Offsets stay consistent, which is what binding ids are derived from."""

    projection = project_source_runs(line)
    previous_end = 0
    for run in projection.runs:
        assert previous_end <= run.begin < run.end <= len(projection.text)
        assert projection.text[run.body_begin : run.end] == run.body
        assert projection.text[run.begin : run.body_begin] == run.marker
        previous_end = run.end
