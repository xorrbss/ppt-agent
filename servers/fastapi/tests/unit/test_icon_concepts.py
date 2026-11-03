"""Curated concept matching for the bundled vector icon library."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.generation_core.assets.icon_pack import concepts, library

CORPUS = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "icon_label_corpus.json").read_text(encoding="utf-8")
)["labels"]


def _id(label: str) -> str | None:
    path = library.find_icon(label)
    return path.stem if path else None


def test_every_alias_target_is_a_themeable_library_icon():
    _aliases, by_id = library._catalog()
    assert by_id, "the bundled library must be present"
    assert concepts.referenced_icon_ids() <= set(by_id)


def test_table_keys_are_lowercase_single_spaced():
    tables = concepts.load_tables()
    for key in [*tables.concepts, *tables.standalone]:
        assert key == key.casefold() and " " not in key, key
    for phrase in tables.phrases:
        assert len(phrase) >= 2 and all(word == word.casefold() for word in phrase), phrase


@pytest.mark.parametrize(
    ("label", "icon"),
    [
        # the bare nouns an outline plan names most often
        ("수집", "data-13-data-stream"),
        ("분석", "data-07-magnifier-chart"),
        ("대응", "strategy-18-shield-plan"),
        ("보안", "infrastructure-15-firewall"),
        ("성장", "growth-01-ascending-bars"),
        ("비용", "finance-01-coin-stack"),
        ("일정", "operations-16-schedule-calendar"),
        ("고객", "people-12-customer"),
        ("데이터", "data-01-database"),
        ("자동화", "process-20-automation-arm"),
        # spacing is not meaning
        ("데이터 분석", "data-07-magnifier-chart"),
        ("데이터분석", "data-07-magnifier-chart"),
        # a compound takes its head noun, or a phrase it contains
        ("고객 확보 비용", "finance-01-coin-stack"),
        ("고객 확보", "growth-09-customer-plus"),
        ("임직원 보안 교육", "education-01-open-book"),
        ("사고 대응 절차", "strategy-18-shield-plan"),
        ("사내 업무 자동화", "process-20-automation-arm"),
        # space-less compounds split only into known words
        ("정보보안", "infrastructure-15-firewall"),
        ("위험대응", "strategy-18-shield-plan"),
        ("확장성", "growth-04-expanding-circles"),
        ("비용이", "finance-01-coin-stack"),
        # filler words and particles are dropped
        ("리스크 관리", "strategy-18-shield-plan"),
        ("대응 방안", "strategy-18-shield-plan"),
        ("AI 기반 개인화", "ai-09-recommendation"),
        # a direction word qualifies the noun before it
        ("비용 증가", "finance-01-coin-stack"),
        ("효율성 향상", "growth-11-accelerator"),
        ("증가", "growth-15-upward-elevator"),
        # english, with a plural
        ("Data Analysis", "data-07-magnifier-chart"),
        ("Cost reductions", "finance-01-coin-stack"),
        # exact library aliases keep their meaning
        ("의사", "people-08-doctor"),
        ("분석가", "people-07-analyst"),
        ("신경망", "ai-01-neural-network"),
    ],
)
def test_business_labels_resolve_to_a_fitting_icon(label, icon):
    assert _id(label) == icon


@pytest.mark.parametrize(
    "label",
    [
        "의사 표명",  # a stated intent, not a physician
        "환경 보호",  # not the firewall
        "보안관",  # contains 보안 but is a sheriff
        "대기업",  # 대기 (queue) + 업 is not a word of the table
        "시장 환경",
        "제품 개발",  # only software development is a code window
        "사고방식",
        "생성형",
        "현장",
        "계약서",
        "3단계",  # a digit makes the word unknowable
        "관리",  # filler alone names nothing
        "핵심 요약",
        "",
        "   ",
    ],
)
def test_ambiguous_or_unknown_labels_do_not_match(label):
    assert _id(label) is None


@pytest.mark.parametrize("label", ["성장 둔화", "매출 감소", "매출 손실", "Revenue decline"])
def test_a_fall_never_gets_a_rising_icon(label):
    assert _id(label) is None


def test_a_fall_of_a_cost_keeps_its_neutral_icon():
    assert _id("비용 감소") == "finance-01-coin-stack"


def test_standalone_entries_do_not_apply_inside_a_compound():
    # "고객 대응" is customer service; the bare "대응" is risk response
    assert _id("대응") == "strategy-18-shield-plan"
    assert _id("고객 대응") == "people-12-customer"


def test_exact_alias_wins_over_a_curated_concept():
    aliases, _by_id = library._catalog()
    for key, source in aliases.items():
        assert library.find_icon(key) == source


def test_lookup_tints_a_concept_match_and_skips_the_rest(tmp_path):
    found = library.lookup_reusable_icons(
        ["데이터 수집", "생성형", "계약서"], cache_directory=tmp_path, color="#112233"
    )
    assert list(found) == ["데이터 수집"]
    svg = found["데이터 수집"].read_text(encoding="utf-8")
    assert "#000000" not in svg and "#112233" in svg


def test_corpus_precision_and_coverage():
    """The corpus holds realistic labels; ``fit`` is the reviewed set of acceptable icons."""
    hit = fit = 0
    fittable = sum(1 for row in CORPUS if row["fit"])
    wrong = []
    for row in CORPUS:
        icon = _id(row["label"])
        if icon is None:
            continue
        hit += 1
        if any(icon.startswith(prefix) for prefix in row["fit"]):
            fit += 1
        else:
            wrong.append((row["label"], icon))
    assert fit / fittable >= 0.9, "coverage of labels that have a fitting icon"
    assert fit / hit >= 0.97, f"precision; wrong matches: {wrong}"
    assert hit / len(CORPUS) >= 0.7
