"""``SVG_TEMPLATE_OUTLINE_SECTIONS``: the template's page structure in a template-redesign outline."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from models.presentation_outline_model import PresentationOutlineModel, SlideOutlineModel
from services import presentation_generation_pipeline as pipeline
from services.ppt_writing_policy import WRITING_POLICY_ID
from services.presentation_generation_pipeline import template_outline_sections
from services.svg_templates.body_composition import content_kinds
from utils.content_structure_policy import TABLE_SYNTAX_CLAUSE, StructurePolicy
from utils.get_env_concurrency import get_svg_template_outline_sections_env
from utils.llm_calls import generate_presentation_outlines as outlines_module
from utils.llm_calls.generate_presentation_outlines import get_messages, get_system_prompt
from utils.outline_utils import explicit_outline_title
from utils.svg_template_outline import (
    TEMPLATE_SECTIONS_CLAUSE, TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES, demote_extra_titles, outline_sections_clause,
)
from tests.unit.test_outline_count_retry_call_identity import _run_generation, ledger  # noqa: F401 - fixture


def _prompt(**kwargs):
    return get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean", **kwargs)


def test_the_prompt_is_byte_identical_until_the_flag_is_given():
    plain = _prompt()
    assert _prompt(template_sections=False) == plain
    assert TEMPLATE_SECTIONS_CLAUSE not in plain
    assert outline_sections_clause(False) == "" and outline_sections_clause(False, StructurePolicy()) == ""


def test_the_clause_closes_the_prompt_and_keeps_the_rest():
    plain = _prompt()
    prompt = _prompt(template_sections=True)
    assert prompt == plain + TEMPLATE_SECTIONS_CLAUSE
    system = get_messages("c", 3, "Korean", writing_policy=WRITING_POLICY_ID, template_sections=True)[0].content
    assert system.endswith(TEMPLATE_SECTIONS_CLAUSE)


def test_table_rows_are_material_only_with_the_tables_part_of_the_structure_policy():
    # Every queued deck carries the writing policy, whose pipe rule forbids table pipes unless the
    # content structure policy's tables part adds its exception; the clause follows that part.
    assert "Markdown table rows" not in TEMPLATE_SECTIONS_CLAUSE
    assert "its bullets or its series of figures" in TEMPLATE_SECTIONS_CLAUSE
    assert "its bullets, its Markdown table rows or its series of figures" in TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES
    assert outline_sections_clause(True) == TEMPLATE_SECTIONS_CLAUSE
    assert outline_sections_clause(True, StructurePolicy(tables=False)) == TEMPLATE_SECTIONS_CLAUSE
    assert outline_sections_clause(True, StructurePolicy()) == TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES
    with_policy = _prompt(structure_policy=StructurePolicy(), template_sections=True)
    assert with_policy.endswith(TABLE_SYNTAX_CLAUSE + TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES)
    assert with_policy.replace(TEMPLATE_SECTIONS_CLAUSE_WITH_TABLES, "") == _prompt(structure_policy=StructurePolicy())
    connectors_only = _prompt(structure_policy=StructurePolicy(tables=False), template_sections=True)
    assert connectors_only.endswith(TEMPLATE_SECTIONS_CLAUSE) and "Markdown table rows" not in connectors_only


def test_the_clause_asks_for_the_template_structure_without_inventing_content():
    for phrase in ("key-message line", "with its main figure when it has one", "### 1. Heading",
                   "one to four sub-sections, as many as the source gives aspects", "never padding",
                   "name each group with words the source uses for it, never with a heading the source does not",
                   "Keep every figure, date, label and named item", "not the title slide",
                   "replaces the rule above that the body is bullets and nothing else", "only ## heading"):
        assert phrase in TEMPLATE_SECTIONS_CLAUSE, phrase


def test_an_outline_page_in_that_shape_keeps_one_title_and_gains_sections():
    page = ("## 운영 체계\n"
            "서비스 데스크가 모든 문의를 한 곳에서 받는다.\n"
            "### 1. 문의 처리 단계\n"
            "- 접수: 5분 안에 등록한다.\n"
            "- 분류: 유형과 영향 범위를 확인한다.\n"
            "- 처리: 담당 팀이 조치한다.\n"
            "### 2. 장애 등급별 서비스 수준\n"
            "| 등급 | 대응 | 복구 |\n"
            "| --- | --- | --- |\n"
            "| 1등급 | 10분 | 2시간 |\n"
            "| 2등급 | 30분 | 4시간 |\n"
            "### 3. 정기 보고\n"
            "- 주간 보고는 매주 월요일에 한다.\n")
    assert explicit_outline_title(page) == "운영 체계"
    kinds, sections = content_kinds(page)
    assert sections == 3 and "table" in kinds and "cards" in kinds
    # A single-digit numbered heading is not a figure the page is checked for.
    from services.svg_presentation_checkpoint import _required_numeric_claims
    assert not any(claim in {"1", "2", "3"} for claim in _required_numeric_claims(page))


def test_extra_level_two_headings_are_demoted_so_the_page_keeps_one_title():
    written = ("## 운영 체계\n"
               "핵심 메시지 한 줄.\n"
               "## 1. 문의 처리 단계\n"
               "- 접수: 5분 안에 등록한다.\n"
               "| 구분 | ## 아님 |\n"
               "  ## 2. 보고\n"
               "- 주간 보고.\n")
    assert explicit_outline_title(written) is None
    outline = PresentationOutlineModel(slides=[SlideOutlineModel(content=written),
                                               SlideOutlineModel(content="## 제목\n- a\n")])
    fixed = demote_extra_titles(outline)
    assert fixed.slides[0].content == ("## 운영 체계\n"
                                       "핵심 메시지 한 줄.\n"
                                       "### 1. 문의 처리 단계\n"
                                       "- 접수: 5분 안에 등록한다.\n"
                                       "| 구분 | ## 아님 |\n"
                                       "  ### 2. 보고\n"
                                       "- 주간 보고.")  # the model strips trailing whitespace
    assert explicit_outline_title(fixed.slides[0].content) == "운영 체계"
    assert content_kinds(fixed.slides[0].content)[1] == 2
    # Nothing to demote: the same bytes, the same model content.
    assert fixed.slides[1].content == outline.slides[1].content == "## 제목\n- a"
    assert demote_extra_titles(fixed).model_dump() == fixed.model_dump()
    # No title at all is left alone: nothing is invented.
    none = PresentationOutlineModel(slides=[SlideOutlineModel(content="### 1. 부분\n- a\n")])
    assert demote_extra_titles(none).slides[0].content == "### 1. 부분\n- a"


def test_the_knob_reads_on_true_and_1_and_defaults_to_off(monkeypatch):
    monkeypatch.delenv("SVG_TEMPLATE_OUTLINE_SECTIONS", raising=False)
    assert get_svg_template_outline_sections_env() is False
    for value in ("on", "TRUE", "1"):
        monkeypatch.setenv("SVG_TEMPLATE_OUTLINE_SECTIONS", value)
        assert get_svg_template_outline_sections_env() is True
    monkeypatch.setenv("SVG_TEMPLATE_OUTLINE_SECTIONS", "grammar")
    assert get_svg_template_outline_sections_env() is False


def test_only_a_template_redesign_deck_gets_the_sections_and_only_with_the_knob(monkeypatch):
    redesign = SimpleNamespace(svg_template_pin={"templateId": "t"}, svg_template_mode="template-redesign")
    free = SimpleNamespace(svg_template_pin={"templateId": "t"}, svg_template_mode="free-design")
    plain = SimpleNamespace(svg_template_pin=None, svg_template_mode=None)
    monkeypatch.delenv("SVG_TEMPLATE_OUTLINE_SECTIONS", raising=False)
    assert template_outline_sections(redesign) is False
    monkeypatch.setenv("SVG_TEMPLATE_OUTLINE_SECTIONS", "on")
    assert template_outline_sections(redesign) is True
    assert template_outline_sections(free) is False
    assert template_outline_sections(plain) is False


def test_the_pipeline_passes_the_flag_it_derives_from_the_request(monkeypatch, fake_async_session, ledger):  # noqa: F811
    """The outline call carries ``template_sections`` from :func:`template_outline_sections`."""
    seen = {}
    real = outlines_module.generate_ppt_outline

    async def recording(*args, **kwargs):
        seen["kwargs"] = kwargs
        async for chunk in real(*args, **kwargs):
            yield chunk

    monkeypatch.setattr(pipeline, "generate_ppt_outline", recording)
    monkeypatch.setattr(pipeline, "template_outline_sections", lambda request: seen.setdefault("asked", True))
    monkeypatch.setattr(pipeline, "demote_extra_titles", AsyncMock(), raising=False)
    _run_generation(monkeypatch, fake_async_session, ledger, [10])
    assert seen["asked"] is True and seen["kwargs"]["template_sections"] is True
