from types import SimpleNamespace

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services.generation_core.style.build import authored_title
from services.outline_chart_grounding import ground_outline_charts
from utils.outline_citations import strip_outline_citations
from utils.outline_utils import clean_deck_title, parse_outline_response

_SLIDES = [{"content": "## 현황 문제: 수작업 보고\n- 본문"}, {"content": "## 개선안\n- 본문"}]


def _request(content: str = "", title: str | None = None):
    return SimpleNamespace(content=content, title=title)


def _outline(title: str | None = None) -> PresentationOutlineModel:
    return PresentationOutlineModel(slides=[SlideOutlineModel(**s) for s in _SLIDES], title=title)


def test_outline_title_beats_first_heading_and_content():
    assert authored_title(_request("주제 한 줄"), _outline("업무 자동화 제안")) == "업무 자동화 제안"


def test_stored_outline_title_used_when_outline_has_none():
    assert authored_title(_request(), _outline(), "저장된 제목") == "저장된 제목"


def test_request_title_then_content_then_first_heading():
    assert authored_title(_request("주제", "요청 제목"), _outline()) == "요청 제목"
    assert authored_title(_request("# 주제 한 줄\n본문"), _outline()) == "주제 한 줄"
    assert "현황 문제" in authored_title(_request(), _outline())


def test_title_is_plain_single_line_and_capped():
    assert clean_deck_title("## A\nB  C") == "A B C"
    assert clean_deck_title("x" * 200) == "x" * 80
    assert clean_deck_title("   ") is None
    assert clean_deck_title(None) is None


def test_title_roundtrips_and_is_omitted_when_absent():
    assert "title" not in _outline().model_dump()
    assert _outline("제목").model_dump()["title"] == "제목"
    parsed = parse_outline_response('{"title": "전체 제목", "slides": [{"content": "a"}]}', None)
    assert parsed.title == "전체 제목"


def test_title_survives_outline_transforms():
    outline = _outline("전체 제목")
    assert strip_outline_citations(outline).title == "전체 제목"
    grounded, dropped = ground_outline_charts(outline, "")
    assert grounded.title == "전체 제목"
