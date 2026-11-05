"""``SVG_TEMPLATE_OUTLINE_SECTIONS``: the template's page structure in a template-redesign outline."""

from types import SimpleNamespace

from services.ppt_writing_policy import WRITING_POLICY_ID
from services.presentation_generation_pipeline import template_outline_sections
from services.svg_templates.body_composition import content_kinds
from utils.content_structure_policy import StructurePolicy
from utils.get_env_concurrency import get_svg_template_outline_sections_env
from utils.llm_calls.generate_presentation_outlines import get_messages, get_system_prompt
from utils.outline_utils import explicit_outline_title
from utils.svg_template_outline import TEMPLATE_SECTIONS_CLAUSE, outline_sections_clause


def _prompt(**kwargs):
    return get_system_prompt("text-heavy", True, False, writing_policy=WRITING_POLICY_ID, language="Korean", **kwargs)


def test_the_prompt_is_byte_identical_until_the_flag_is_given():
    plain = _prompt()
    assert _prompt(template_sections=False) == plain
    assert TEMPLATE_SECTIONS_CLAUSE not in plain
    assert outline_sections_clause(False) == ""


def test_the_clause_closes_the_prompt_and_keeps_the_rest():
    plain = _prompt()
    prompt = _prompt(template_sections=True)
    assert prompt == plain + TEMPLATE_SECTIONS_CLAUSE
    # It also follows the table clause of the content structure policy, when that is on.
    with_policy = _prompt(structure_policy=StructurePolicy(), template_sections=True)
    assert with_policy.endswith(TEMPLATE_SECTIONS_CLAUSE)
    assert with_policy.replace(TEMPLATE_SECTIONS_CLAUSE, "") == _prompt(structure_policy=StructurePolicy())
    system = get_messages("c", 3, "Korean", writing_policy=WRITING_POLICY_ID, template_sections=True)[0].content
    assert system.endswith(TEMPLATE_SECTIONS_CLAUSE)


def test_the_clause_asks_for_the_template_structure_without_inventing_content():
    assert "key-message line" in TEMPLATE_SECTIONS_CLAUSE
    assert "### 1. Heading" in TEMPLATE_SECTIONS_CLAUSE
    assert "two to four sub-sections" in TEMPLATE_SECTIONS_CLAUSE
    assert "never padding" in TEMPLATE_SECTIONS_CLAUSE
    assert "Keep every figure, date, label and named item" in TEMPLATE_SECTIONS_CLAUSE
    # The title slide and the single ## title are left as they are.
    assert "not the title slide" in TEMPLATE_SECTIONS_CLAUSE
    assert "only ## heading" in TEMPLATE_SECTIONS_CLAUSE


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
