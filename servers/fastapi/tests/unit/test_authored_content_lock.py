"""Content lock: the author renders the brief; it does not rewrite it.

The fixture is bench run 48, slide 3 (Korean): the brief the author was given
and the visible text it rendered, which restated the bullets and ADDED three
sentences the brief never said, plus legitimate short furniture.
"""

from __future__ import annotations

import json
from pathlib import Path

from services import authored_presentation_pipeline as authored_pipeline_mod
from services.authored_composition_policy import capacity_repair_instructions
from services.authored_presentation_generation import _capacity_gate_initial_critiques
from utils.authored_content_lock import (
    MIN_ALLOWANCE_CHARS,
    ContentLockReport,
    content_lock_gate_issue,
    content_lock_repair_instruction,
    content_lock_violation,
    find_added_text,
    parse_content_lock_violation,
)
from utils.authored_dom_quality import STYLE_REFERENCE
from utils.get_env_programme import is_authored_content_lock_enabled
from utils.llm_calls.author_slide import Brand
from utils.llm_calls.author_slide_brief import CONTENT_LOCK_CLAUSE, content_lock_clause
from utils.llm_calls.author_slide_prompt import author_prompt

BRIEF = (
    "## 검증 가능한 수행역량과 실적 "
    "- 회사 일반현황은 설립연도, 자본금, 최근 3개년 매출, 상시 종업원 수, 기술인력 수, 부채비율, "
    "신용평가등급을 동일한 증빙 기준일로 작성하여 경영·재무 안정성을 확인할 수 있게 한다. "
    "- 유사실적은 공고일 기준 최근 3~5년의 회원·고객 통합, 교육·강좌 신청, 수강료 결제·환불, CRM, "
    "데이터 이관 사업을 우선하되 최종 인정기간은 제안요청서 기준을 따른다. "
    "- 실적별 고객, 계약금액, 기간, 수행 범위, 담당 역할, 정상 개통 여부를 계약서나 실적증명서와 "
    "연결하고 단순 유지보수와 신규 구축·재구축 실적을 구분한다. "
    "- 품질·보안 인증은 실제 보유한 ISO 9001, ISO/IEC 27001, ISO/IEC 20000-1, CMMI 등의 명칭, 등급, "
    "유효기간, 적용 조직을 제시하며 미보유 인증은 유사 표현으로 대체하지 않는다. "
    "- 본 사업에 적용할 경험은 회원 중복 해소, 개인정보 이력 통합, 접수 피크 대응, 결제 대사, "
    "반복 이관 리허설, 단계별 품질 게이트의 6개 실행 영역으로 전환한다. "
    "- 4쪽"
)

ADDED = (
    "동일 기준일·인정기간·원증빙에 따른 경영 안정성 및 유사 수행경험 검증",
    "객관적 증빙에서 사업 적용까지 이어지는 5단계 검증체계 로 수행역량을 입증합니다.",
    "검증성 모든 수치·실적·인증을 원증빙과 연결하고, 확인된 경험만 본 사업의 실행계획으로 전환합니다.",
)

FURNITURE = ("제안사 소개", "1.1", "01 기준일 통일", "02 유사실적 선별", "증빙 기준 → 적용 경험", "04")

RESTATED = (
    "검증 가능한 수행역량과 실적",
    # Re-wrapped and re-punctuated bullets are still the brief's own words.
    "회사 일반현황은 설립연도, 자본금, 최근 3개년 매출,\n상시 종업원 수, 기술인력 수, 부채비율, 신용평가등급을\n"
    "동일한 증빙 기준일로 작성하여 경영·재무 안정성을 확인할 수 있게 한다",
    "• 실적별 고객, 계약금액, 기간, 수행 범위, 담당 역할, 정상 개통 여부를 계약서나 실적증명서와 연결",
    "품질·보안 인증은 실제 보유한 ISO 9001, ISO/IEC 27001, ISO/IEC 20000-1, CMMI 등의 명칭, 등급, "
    "유효기간, 적용 조직을 제시",
    # Reordered clauses: each clause is in the brief, so the run is covered.
    "접수 피크 대응 · 회원 중복 해소 · 개인정보 이력 통합",
)


def _element(qid: str, text: str, **extra) -> dict:
    return {
        "qid": qid,
        "tag": "div",
        "semantic_role": "body",
        "has_owned_text": True,
        "owned_text": text,
        "rendered_text": text,
        "text_fragments": [{"x": 0, "y": 0, "width": 10, "height": 10}],
        "font_size": 18,
        "parent_qid": None,
        **extra,
    }


def _run48_elements() -> list[dict]:
    elements = [_element(f"restated.{i}", text) for i, text in enumerate(RESTATED)]
    elements += [_element(f"furniture.{i}", text, semantic_role="supporting") for i, text in enumerate(FURNITURE)]
    elements += [_element(f"added.{i}", text) for i, text in enumerate(ADDED)]
    # A wrapper whose text is all owned by nested qids must not double count.
    elements.append(
        {
            "qid": "wrapper",
            "tag": "section",
            "has_owned_text": False,
            "owned_text": "",
            "rendered_text": " ".join(ADDED),
        }
    )
    return elements


# --- the measurement -----------------------------------------------------------


def test_run48_slide3_flags_the_added_sentences_and_exempts_the_furniture():
    report = find_added_text(BRIEF, _run48_elements())

    assert [fragment.text for fragment in report.fragments] == list(ADDED)
    assert {fragment.qid for fragment in report.fragments} == {"added.0", "added.1", "added.2"}
    assert report.added_chars == sum(len(text) for text in ADDED)
    assert report.brief_chars == len(BRIEF)
    assert report.violated is True
    assert report.ratio == report.added_chars / len(BRIEF)


def test_furniture_and_restated_bullets_alone_report_nothing():
    elements = [_element(f"r{i}", text) for i, text in enumerate((*RESTATED, *FURNITURE))]

    report = find_added_text(BRIEF, elements)

    assert report.fragments == ()
    assert report.added_chars == 0
    assert report.violated is False


def test_short_labels_numbers_and_deck_furniture_are_exempt():
    elements = [
        _element("label", "핵심 성과 지표 요약"),  # 8 normalized chars: a label
        _element("step", "STEP 03"),
        _element("roman", "IV"),
        _element("chapter", "2. 사업 이해 및 추진 전략"),  # deck furniture, not in the brief
    ]

    report = find_added_text("## 제목 - 한 문장의 내용", elements, furniture=("2. 사업 이해 및 추진 전략",))

    assert report.fragments == ()


def test_rendered_text_stands_in_for_telemetry_without_owned_text():
    legacy = {"qid": "old", "has_owned_text": True, "rendered_text": "브리프에 없는 긴 요약 문장이 렌더되었습니다."}
    silent = {"qid": "img", "has_owned_text": False, "rendered_text": "브리프에 없는 긴 요약 문장이 렌더되었습니다."}

    report = find_added_text("## 제목 - 내용", [legacy, silent])

    assert [fragment.qid for fragment in report.fragments] == ["old"]


# --- the threshold ---------------------------------------------------------------


def test_threshold_is_the_larger_of_120_chars_and_a_quarter_of_the_brief():
    assert MIN_ALLOWANCE_CHARS == 120
    assert ContentLockReport(brief_chars=100, added_chars=120).violated is False
    assert ContentLockReport(brief_chars=100, added_chars=121).violated is True
    assert ContentLockReport(brief_chars=1000, added_chars=250).violated is False
    assert ContentLockReport(brief_chars=1000, added_chars=251).violated is True
    assert ContentLockReport(brief_chars=535, added_chars=134).allowance == 133
    assert ContentLockReport(brief_chars=0, added_chars=0).ratio == 0.0


# --- the violation string and the repair instruction -----------------------------


def test_violation_string_carries_the_measurement_and_the_longest_fragments():
    report = find_added_text(BRIEF, _run48_elements())

    violation = content_lock_violation(report)

    assert violation.startswith(f"content_added:{report.added_chars}>{report.allowance}|")
    added, allowance, fragments = parse_content_lock_violation(violation)
    assert (added, allowance) == (report.added_chars, report.allowance)
    assert fragments == tuple(sorted(ADDED, key=len, reverse=True))
    assert parse_content_lock_violation("browser_body:900>800") is None


def test_repair_instruction_quotes_the_added_text_and_states_the_rule():
    violation = content_lock_violation(find_added_text(BRIEF, _run48_elements()))

    instruction = content_lock_repair_instruction(violation, "BODY")

    assert len(instruction) <= 200
    assert instruction.startswith('BODY has text CONTENT never says: "')
    assert max(ADDED, key=len)[:12] in instruction
    assert "replace it with CONTENT's own words" in instruction
    assert "keep short labels" in instruction
    assert "do not add explanation, summary or kicker sentences" in instruction
    assert "do not shrink type" in instruction
    # It is the instruction the capacity road hands to the re-author call.
    assert capacity_repair_instructions("BODY", (violation,)) == (instruction,)
    # The measured-capacity branches are untouched.
    assert "exceeds its limit by 100 chars" in capacity_repair_instructions("BODY", ("browser_body:900>800",))[0]


# --- the knob --------------------------------------------------------------------


def test_knob_defaults_off_and_reads_the_env(monkeypatch):
    monkeypatch.delenv("AUTHORED_CONTENT_LOCK_ENABLED", raising=False)
    assert is_authored_content_lock_enabled() is False
    monkeypatch.setenv("AUTHORED_CONTENT_LOCK_ENABLED", "true")
    assert is_authored_content_lock_enabled() is True
    monkeypatch.setenv("AUTHORED_CONTENT_LOCK_ENABLED", "false")
    assert is_authored_content_lock_enabled() is False


def test_knob_is_registered_off_in_the_settings_registry():
    registry = Path(__file__).resolve().parents[2] / "constants" / "settings_registry.json"
    settings = json.loads(registry.read_text(encoding="utf-8"))["settings"]
    keys = [item["key"] for item in settings]

    entry = settings[keys.index("AUTHORED_CONTENT_LOCK_ENABLED")]

    assert entry["default"] is False
    assert entry["secret"] is False
    assert entry["targets"] == ["fastapi.runtime"]
    assert entry["group"] == "authored.quality"
    assert keys[keys.index("AUTHORED_IN_RENDER_FIT_VERIFY_SAMPLE") + 1] == "AUTHORED_CONTENT_LOCK_ENABLED"


# --- the prompt ------------------------------------------------------------------


def _prompt() -> str:
    brand = Brand(topic="회원 통합 시스템 구축", language="Korean", primary="#0B4EA2")
    return author_prompt(BRIEF, "DESIGN", brand, "BODY", 2, 8)


def test_prompt_is_byte_identical_with_the_knob_off_and_gains_one_clause_when_on(monkeypatch):
    monkeypatch.delenv("AUTHORED_CONTENT_LOCK_ENABLED", raising=False)
    assert content_lock_clause() == ""
    off = _prompt()
    assert CONTENT_LOCK_CLAUSE not in off
    assert f"CONTENT: {BRIEF}.\n\n" in off

    monkeypatch.setenv("AUTHORED_CONTENT_LOCK_ENABLED", "true")
    on = _prompt()

    assert on == off.replace(f"CONTENT: {BRIEF}.", f"CONTENT: {BRIEF}.{CONTENT_LOCK_CLAUSE}", 1)
    assert "never sentences CONTENT does not say" in on


# --- the gate seam ---------------------------------------------------------------


def test_gate_issue_is_absent_with_the_knob_off_and_rides_the_capacity_road_when_on(monkeypatch):
    metrics = {"elements": _run48_elements()}
    monkeypatch.delenv("AUTHORED_CONTENT_LOCK_ENABLED", raising=False)
    assert content_lock_gate_issue(BRIEF, metrics, role="BODY") is None

    monkeypatch.setenv("AUTHORED_CONTENT_LOCK_ENABLED", "true")
    issue = content_lock_gate_issue(BRIEF, metrics, role="BODY")

    assert issue is not None
    assert issue["category"] == "capacity"
    assert issue["severity"] == "error"
    assert issue["message"].startswith("content_added:")
    assert issue["qids"] == ["added.0", "added.1", "added.2"]
    assert issue["content_added_chars"] == sum(len(text) for text in ADDED)
    assert issue["content_added_ratio"] == round(issue["content_added_chars"] / len(BRIEF), 3)
    assert issue["detail"] == content_lock_repair_instruction(issue["message"], "BODY")
    assert content_lock_gate_issue(BRIEF, {"elements": []}, role="BODY") is None
    assert content_lock_gate_issue(BRIEF, None, role="BODY") is None

    # The same road as a measured capacity defect: one high critique whose
    # detail is the lock's instruction, feeding the bounded repair round.
    critiques = _capacity_gate_initial_critiques([{"slide_index": 0, "issues": [issue]}], [0], ["BODY"])
    assert critiques[0] is not None and critiques[0].needs_fix is True
    assert [item.detail for item in critiques[0].issues] == [issue["detail"]]


def _fixture_metrics(name: str) -> dict:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "authored_dom_text_escape" / f"{name}.metrics.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_pipeline_gate_reports_the_lock_as_a_slide_failure_only_when_enabled(monkeypatch):
    from types import SimpleNamespace

    metrics = _fixture_metrics("run33_slide03_footer_zone_clean")
    profile = SimpleNamespace(profile_id="ordinary", strict_dom_gate=True)

    def evaluate():
        return authored_pipeline_mod._evaluate_slide_quality(
            index=0,
            html="<div></div>",
            browser_metrics=metrics,
            capacity_plan=None,
            composition_profile=profile,
            reference_mode=STYLE_REFERENCE,
            brief="## 제안사 소개 - 이 슬라이드의 브리프는 짧아서 렌더된 본문 대부분이 추가 문장이 된다.",
        )

    monkeypatch.delenv("AUTHORED_CONTENT_LOCK_ENABLED", raising=False)
    _report, failure = evaluate()
    assert failure is None

    monkeypatch.setenv("AUTHORED_CONTENT_LOCK_ENABLED", "true")
    _report, failure = evaluate()

    assert failure is not None and failure["slide_index"] == 0
    messages = [issue["message"] for issue in failure["issues"]]
    assert len(messages) == 1 and messages[0].startswith("content_added:")
