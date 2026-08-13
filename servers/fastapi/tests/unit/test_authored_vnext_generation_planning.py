"""Focused tests for published-master-aware outline and CJK title planning."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from models.authored_vnext.master.contract import DeckMasterContract
from models.authored_vnext.master.operations import MasterRevisionResponse
from services.authored_vnext.generation import (
    OutlineItem,
    TitleFitPlanningError,
    break_title,
    fit_and_promote_title,
    plan_outline,
    resolve_windows_font,
)
from services.authored_vnext.master.digests import seal_master_contract
from services.authored_vnext.master.errors import (
    MasterDigestConflictError,
    MasterNotPublishedError,
)
from tests.support.authored_vnext_master_fixture import master_contract_payload


def _master(*, status: str = "published") -> MasterRevisionResponse:
    contract = seal_master_contract(
        DeckMasterContract.model_validate(master_contract_payload())
    )
    return MasterRevisionResponse(
        master_id=contract.master_id,
        revision=2,
        digest=contract.digest,
        status=status,
        contract=contract,
    )


def _outline() -> list[OutlineItem]:
    return [
        OutlineItem("slide:cover", "감사 가능한 AI 운영", "핵심 메시지"),
        OutlineItem("slide:section", "품질 검증", "검증 단계", kind="section"),
        OutlineItem("slide:standard", "운영 원칙", "표준 본문"),
        OutlineItem("slide:long", "가" * 60, "긴 제목 본문"),
    ]


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(item) for item in value.values()), set())
    if isinstance(value, (list, tuple)):
        return set().union(*(_keys(item) for item in value), set())
    return set()


def test_outline_roles_are_deterministic_and_standard_promotes() -> None:
    master = _master()

    first = plan_outline(master, _outline())
    second = plan_outline(master, _outline())

    assert first == second
    assert first.master_digest == master.digest
    assert [slide.role for slide in first.slides] == [
        "cover",
        "section",
        "standard",
        "long-title",
    ]
    assert [slide.layout_id for slide in first.slides[:3]] == [
        master.contract.layouts.cover.id,
        master.contract.layouts.section.id,
        master.contract.layouts.standard.id,
    ]
    assert first.slides[3].layout_id == master.contract.layouts.long_title.id
    assert first.slides[3].title_fit.clipped is False


def test_model_payload_is_closed_to_the_body_slot() -> None:
    slide = plan_outline(_master(), _outline()).slides[2]

    payload = slide.model_input.to_model_payload()

    assert set(payload) == {"bodySlot", "body"}
    assert payload["bodySlot"]["role"] == "body"
    assert payload["body"] == "표준 본문"
    forbidden = {"logo", "header", "footer", "divider", "pageNumber", "furniture", "title"}
    assert not (_keys(payload) & forbidden)
    assert not (set(asdict(slide.model_input)) & forbidden)
    with pytest.raises(ValueError, match="unsupported fields"):
        plan_outline(
            _master(),
            [
                {
                    "slideId": "slide:cover",
                    "title": "제목",
                    "body": "본문",
                    "footer": "모델이 만들면 안 됨",
                }
            ],
        )


def test_generation_requires_a_sealed_published_master() -> None:
    with pytest.raises(MasterNotPublishedError):
        plan_outline(_master(status="draft"), _outline())

    published = _master()
    stale = published.model_copy(update={"digest": "sha256:" + "f" * 64})
    with pytest.raises(MasterDigestConflictError):
        plan_outline(stale, _outline())


def test_korean_space_first_and_grapheme_fallback_are_canonical() -> None:
    contract = _master().contract
    spaced = "한국어 단어 경계를 우선하는 제목 생성 규칙을 검증하는 사례"

    word_broken = break_title(contract, contract.layouts.standard.id, spaced)
    fallback = break_title(contract, contract.layouts.long_title.id, "한" * 60)

    assert " ".join(word_broken.lines) == spaced
    assert len(word_broken.lines) <= contract.fit_policy.standard_max_lines
    assert len(fallback.lines) == 3
    assert "".join(fallback.lines) == "한" * 60
    assert fallback.uax14_version == "17.0.0"
    assert fallback.tailoring == "ko-space-first-with-id-fallback-v1"
    assert fallback.kinsoku_policy == "cjk-no-leading-closing-no-trailing-opening"


def test_cjk_kinsoku_never_strands_opening_or_closing_punctuation() -> None:
    contract = _master().contract
    text = ("검증" * 10) + "(출처)" + ("보존" * 10) + "，완료"

    title = break_title(contract, contract.layouts.long_title.id, text)

    closing = set("!%),.:;?]}〉》」』】〕）］｝、。，．：；！？")
    opening = set("([{〈《「『【〔（［｛")
    assert all(line[0] not in closing for line in title.lines)
    assert all(line[-1] not in opening for line in title.lines)


def test_windows_font_resolution_and_powerpoint_margins_are_sealed() -> None:
    contract = _master().contract

    assert resolve_windows_font(contract) == contract.font_manifest.windows_fallback
    assert resolve_windows_font(contract, {"Arial", "Noto Sans KR"}) == "Noto Sans KR"
    with pytest.raises(TitleFitPlanningError, match="unavailable"):
        resolve_windows_font(contract, {"Comic Sans MS"})

    planned = fit_and_promote_title(contract, "PowerPoint 여백 검증")
    assert planned.fit.font_manifest_digest == contract.font_manifest.digest
    assert planned.fit.font_family == contract.font_manifest.windows_fallback
    assert planned.fit.margins == contract.semantic_styles.title.margins
    assert planned.fit.margins.model_dump(by_alias=True) == {
        "leftPt": 7.2,
        "rightPt": 7.2,
        "topPt": 3.6,
        "bottomPt": 3.6,
    }


def test_cover_and_section_fail_closed_instead_of_changing_semantic_layout() -> None:
    with pytest.raises(TitleFitPlanningError):
        plan_outline(
            _master(),
            [OutlineItem("slide:cover", "가" * 60, "본문")],
        )
