"""svg authoring profile revision ten: layered author rules (plan item S3, with S7's stacked rule).

Revision ten replaces the new-copy author rules of a free svg page with one text in three
layers (output contract > design principles > content fidelity) and sends benchmark-specific
blocks only with a page whose source carries their signal. Revision nine keeps its prompt
bytes, pinned below as digests captured from the code before revision ten existed, and fresh
admissions keep freezing revision nine unless the internal measurement knob
``SVG_AUTHORING_PROFILE_REVISION`` selects ten.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from services.generation_core.authoring import author_rules_v10 as v10
from services.generation_core.authoring import author_slide_svg as module
from services.generation_core.drivers.svg.native_declarations import (
    NATIVE_CHART_RULE, NATIVE_STACKED_CHART_RULE, native_author_rules, native_author_rules_v10,
)
from services.ppt_writing_policy import WRITING_POLICY_ID
from services.svg_authoring_profile import (
    ART_DIRECTION_PROFILE_V8, ART_DIRECTION_PROFILE_V9, ART_DIRECTION_PROFILE_V10,
    QUALITY_RECOVERY_POLICY_V2, admission_default_profile, behaviour_profile, new_svg_admission_profile,
    read_quality_recovery_policy, uses_cacheable_author_layout, uses_layered_author_rules,
    validate_authoring_profile,
)
from tests.unit.test_svg_author_prompt_cache import (
    PAGES, SLIDE_COUNT, _messages_digest, _run, _snapshot, _stub, _Wire,
)

# Captured from origin/main (49e529d6b) before revision ten: a revision-nine deck keeps these bytes.
REVISION_NINE_PROMPT_DIGESTS = {
    "page1": "sha256:eeefe17d3627f5a128cb16ce81f0495ad77177fc5e2637342ea36c626546c6f6",
    "page2": "sha256:d3ea0b60dc65bb8d08d2543f0dacbda34cdfdc7741e5a7d7dc3a987a8997fa5d",
    "page1_repair": "sha256:ef2d9ab9206330732421ca43a4abdc4eb9553c298ec1ed0ec71470b644f8b557",
    "page1_rewrite": "sha256:e38b14652d7f64788b8da2218451f0d00036b809ed1a1e607b2902272412a396",
    "new_copy_page1": "sha256:245d70592f2d958b9403e6e5114d718a20af1e819f2bdac6104c0c47036c344b",
    "new_copy_page2": "sha256:c220144d3b21cf376dcebf22f51088a84f4584ad8497afca5626757bf5fcdf9c",
    "new_copy_page1_repair": "sha256:639961e8befefbf1f408166d8c5a9f2ffd5fdd4291aec76ca67b6df1ca400ffe",
    "no_policy_page1": "sha256:0e4ed096dfcabfe45f58ae1462675b9b69877323477f1756c8e055e834671f50",
    "no_policy_page2": "sha256:3bb91a0e416fca24d51aa15461decf2f0c3f72b415fa3624a4a9c82129f74841",
    "no_policy_page1_repair": "sha256:2cb8a1f74ddcc264956094ea19e0805d6d539ebca29962931ee6189a524b6809",
}


def _user_text(messages) -> str:
    content = messages[1].content
    return content if isinstance(content, str) else content[0].text


def _rules(messages) -> str:
    system = messages[0].content
    return system[:system.rindex(module.BLOCK_SEPARATOR)]


def test_revision_nine_decks_keep_their_original_prompt_bytes(monkeypatch):
    results, _ = _run(monkeypatch, ART_DIRECTION_PROFILE_V9)
    assert {name: _messages_digest(messages) for name, (messages, _) in results.items()} \
        == REVISION_NINE_PROMPT_DIGESTS


def test_revision_ten_new_copy_rules_are_layered_and_deck_stable(monkeypatch):
    results, wire = _run(monkeypatch, ART_DIRECTION_PROFILE_V10)
    new_copy = {name: messages for name, (messages, _) in results.items() if name.startswith("new_copy")}
    systems = {messages[0].content for messages in new_copy.values()}
    # Two pages and a local repair share one byte-identical, cached system block.
    assert len(systems) == 1
    rules = _rules(next(iter(new_copy.values())))
    assert rules.startswith(v10.PRIORITY_LINE)
    layers = [rules.index(heading) for heading in
              ("1. OUTPUT CONTRACT", "2. DESIGN PRINCIPLES", "3. CONTENT FIDELITY")]
    assert layers == sorted(layers)
    assert NATIVE_STACKED_CHART_RULE in rules and "Presentation writing policy" in rules
    # Review section 4.3 contradictions and dead sentences are gone.
    for removed in ("report a readability failure", "Do not use curves",
                    "earlier instructions to preserve exact source wording",
                    "Retain the selected identity's full design intent"):
        assert removed not in next(iter(systems))
    assert "draw a pie or donut slice as a polygon" in rules
    # The rules alone stay near the plan's 2.5k-token target (about 4 characters a token).
    assert len(rules) <= 10_500
    for _, receipt in results.values():
        assert receipt["authoring_profile"] == ART_DIRECTION_PROFILE_V10
    # The local repair rules still travel in the user message after the cached block.
    repair = _user_text(new_copy["new_copy_page1_repair"])
    assert repair.startswith(module.SVG_CALL_RULES_HEADING)
    assert module.SVG_NEW_COPY_CORRECTION_RULES["local_repair"].strip() in repair
    for body in wire.bodies:
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_revision_ten_keeps_revision_nine_rules_outside_new_copy_pages(monkeypatch):
    """Source-preserving wording and plain requests are not layered: their rules equal nine's."""
    nine, _ = _run(monkeypatch, ART_DIRECTION_PROFILE_V9)
    ten, _ = _run(monkeypatch, ART_DIRECTION_PROFILE_V10)
    for name in nine:
        if name.startswith("new_copy"):
            continue
        assert _rules(ten[name][0]) == _rules(nine[name][0]), name
        assert _user_text(ten[name][0]) == _user_text(nine[name][0]), name


def test_signal_blocks_ride_only_with_pages_that_carry_their_signal(monkeypatch):
    wire, seen = _Wire(), []
    _stub(monkeypatch, module.LLMProvider.ANTHROPIC, wire, seen)
    snapshot = _snapshot(ART_DIRECTION_PROFILE_V10)
    pages = {
        "plain": "# 도입 배경\n- 현장 업무가 수작업으로 처리된다",
        "relations": "# 데이터 흐름\n- 수집 → 정제: 일 1회\n- 정제 → 분석: 실시간",
        "schedule": "# 추진 일정\n- 1단계 설계 3월~5월\n- 2단계 구축 6월~9월",
    }
    users, systems = {}, set()
    for name, content in pages.items():
        asyncio.run(module.author_slide_svg(content, snapshot, "content", 1, SLIDE_COUNT,
                                            writing_policy=WRITING_POLICY_ID, language="Korean"))
        users[name] = _user_text(seen[-1])
        systems.add(seen[-1][0].content)
    assert len(systems) == 1
    assert not users["plain"].startswith(module.SVG_CALL_RULES_HEADING)
    assert v10.RELATIONSHIP_BLOCK.strip() in users["relations"]
    assert v10.SCHEDULE_BLOCK.strip() not in users["relations"]
    assert v10.SCHEDULE_BLOCK.strip() in users["schedule"]
    assert v10.RELATIONSHIP_BLOCK.strip() not in users["schedule"]


def test_grid_contract_block_needs_a_numeric_signal():
    grid = module.SVG_NATIVE_GRID_GEOMETRY_RULES_V2
    assert v10.page_signal_blocks("# 개요\n- 설명 한 줄", grid_rules=grid, requested_data="") == []
    assert v10.page_signal_blocks(PAGES[1], grid_rules=grid, requested_data="") == [grid]
    assert v10.page_signal_blocks("# 개요", grid_rules=grid, requested_data="chart: A 1, B 2") == [grid]
    # Without the second readability policy there is no grid contract to send.
    assert v10.page_signal_blocks(PAGES[1], grid_rules="", requested_data="") == []


def _typed_design(body="22-28", caption="16"):
    sentence = (f"At 1280x720 use Noto Sans KR. Body text should normally be {body}px; captions and "
                f"source notes preferably at least {caption}px. The hard minimum is 9.5pt.")
    return {"art_direction": {"identity_guidance": {"TYPOGRAPHY": [sentence]}}}


def test_type_scale_follows_the_style_typography_and_the_floor():
    snapshot = {"canvas": {"width": 1280, "height": 720}}
    text, body, width = v10._type_scale(snapshot, _typed_design(), 12.6667, header_applied=False)
    assert (body, width) == (22, 1152)
    assert "body 22-28px" in text and "labels and captions 16-22px" in text and "nothing below 13px" in text
    assert "page title 39-48px bold" in text
    # A style that states nothing gets the canvas default; a high floor lifts every size.
    plain, body, _ = v10._type_scale(snapshot, {}, 12.6667, header_applied=False)
    assert body == 20 and "body 20-24px" in plain
    lifted, body, _ = v10._type_scale(snapshot, {}, 21, header_applied=False)
    assert body == 21 and "nothing below 21px" in lifted
    headed, _, _ = v10._type_scale(snapshot, _typed_design(), 12.6667, header_applied=True)
    assert "common header, do not draw one" in headed and "page title 39" not in headed


def test_header_pages_do_not_ask_for_a_title():
    snapshot = {"canvas": {"width": 1280, "height": 720}}
    common = dict(physical_rules="", writing_policy="")
    plain = v10.svg_author_rules_v10(snapshot, {}, 12.6667, header_applied=False, **common)
    headed = v10.svg_author_rules_v10(snapshot, {}, 12.6667, header_applied=True, **common)
    assert "the title states it" in plain and "the title states it" not in headed


def test_korean_width_guidance_matches_the_fallback_width_model():
    from services.generation_core.drivers.svg import fallback

    assert (fallback._WIDE_EM, fallback._NARROW_EM) == (1.0, 0.55)
    rules = v10.svg_author_rules_v10({"canvas": {"width": 1280, "height": 720}}, _typed_design(), 12.6667,
                                     physical_rules="", writing_policy="", header_applied=False)
    block = rules[rules.index("Korean width"):rules.index("3. CONTENT FIDELITY")]
    assert len([line for line in block.splitlines() if line[:2] in {"1.", "2.", "3.", "4.", "5."}]) == 5
    assert "1.0 em" in block and "0.55 em" in block
    assert f"a 1152px-wide row holds about {1152 // 22} syllables" in block


def test_stacked_rule_is_revision_ten_only():
    assert NATIVE_STACKED_CHART_RULE not in native_author_rules()
    assert NATIVE_STACKED_CHART_RULE not in native_author_rules(source_grounded=True)
    rules = native_author_rules_v10()
    assert rules.endswith(NATIVE_CHART_RULE + NATIVE_STACKED_CHART_RULE)
    assert '"grouping":"stacked"' in rules and "percentStacked" in rules


def test_layered_design_priorities_replace_the_contradictory_text():
    from services.generation_core.drivers.svg.art_direction import LAYERED_DESIGN_PRIORITIES

    nine = json.loads(module.svg_design_text(_snapshot(ART_DIRECTION_PROFILE_V9)))
    ten = json.loads(module.svg_design_text(_snapshot(ART_DIRECTION_PROFILE_V10)))
    assert ten["art_direction"]["design_priorities"] == LAYERED_DESIGN_PRIORITIES
    assert nine["art_direction"]["design_priorities"].startswith("Retain the selected identity's full design")
    assert "quality_recovery_plan" not in ten and "common_header_design_plan" not in ten


def test_revision_ten_behaves_as_eight_outside_the_author_request():
    assert validate_authoring_profile("art-direction-v1@10") == ART_DIRECTION_PROFILE_V10
    assert validate_authoring_profile({"id": "art-direction-v1", "version": 10}) == ART_DIRECTION_PROFILE_V10
    assert behaviour_profile(ART_DIRECTION_PROFILE_V10) == ART_DIRECTION_PROFILE_V8
    assert uses_cacheable_author_layout(ART_DIRECTION_PROFILE_V10)
    assert uses_layered_author_rules(ART_DIRECTION_PROFILE_V10)
    assert not uses_layered_author_rules(ART_DIRECTION_PROFILE_V9)
    assert read_quality_recovery_policy(_snapshot(ART_DIRECTION_PROFILE_V10)) == QUALITY_RECOVERY_POLICY_V2


def test_fresh_admissions_freeze_nine_unless_the_measurement_knob_selects_ten(monkeypatch):
    monkeypatch.delenv("SVG_AUTHORING_PROFILE_REVISION", raising=False)
    assert new_svg_admission_profile(SimpleNamespace()) == ART_DIRECTION_PROFILE_V9
    monkeypatch.setenv("SVG_AUTHORING_PROFILE_REVISION", "10")
    assert new_svg_admission_profile(SimpleNamespace()) == ART_DIRECTION_PROFILE_V10
    monkeypatch.setenv("SVG_AUTHORING_PROFILE_REVISION", "9")
    assert admission_default_profile() == ART_DIRECTION_PROFILE_V9
    for value in ("8", "11", "v10", "-1"):
        monkeypatch.setenv("SVG_AUTHORING_PROFILE_REVISION", value)
        with pytest.raises(ValueError, match="svg_authoring_profile_revision_invalid"):
            admission_default_profile()


def test_knob_selection_keeps_the_rest_of_a_fresh_admission(monkeypatch):
    """Unlike an explicit bench selection, the knob keeps readability and review plans."""
    from models.generate_presentation_request import GeneratePresentationRequest
    from services.svg_generation_admission import pin_svg_style

    snapshots = {}
    for revision in ("", "10"):
        monkeypatch.setenv("SVG_AUTHORING_PROFILE_REVISION", revision)
        request = GeneratePresentationRequest(content="deck", strategy="svg", n_slides=6)
        snapshots[revision] = pin_svg_style(request)
    nine, ten = snapshots[""], snapshots["10"]
    assert nine["authoring_profile"] == ART_DIRECTION_PROFILE_V9
    assert ten["authoring_profile"] == ART_DIRECTION_PROFILE_V10
    differing = {key for key in nine.keys() | ten.keys() if nine.get(key) != ten.get(key)}
    assert differing <= {"authoring_profile", "digest", "common_header_design_plan"}
    for key in ("readability_policy", "quality_recovery_plan", "quality_recovery_policy"):
        assert ten.get(key) is not None
